"""
m1_engine.py
M1-fidelity backtest engine — the standard validation harness.

Entries are decided at H1 bar close by the production strategy classes (same
mock layer as engine.py). Position management (SL/TP/trailing) is replayed
minute-by-minute with conservative intra-bar ordering: SL/TP are tested against
each M1 bar BEFORE the trail moves on that bar.

Motivation: the H1 engine's trail-before-SL intra-bar assumption manufactured
a fake edge (PF 1.55 -> 0.87 at M1 fidelity for the 2026-05 MR config). Any
exit tighter than the H1 bar range cannot be priced from H1 OHLC. Use THIS
engine for go/no-go decisions; engine.py remains useful for quick scans only.

Exit model is explicit and orthogonal (M1Config):
  tp_mode    : "band" (signal's tp = middle band) | "rr" (sl_pips * tp_rr) | "none"
  trail      : trail_trigger_atr / trail_distance_atr (0 = no trail)
  strat_exit : strategy.should_close() at H1 closes on/off

M1 data is loaded once per process and cached (array module, ~40 MB per
decade), so parameter sweeps pay the parse cost only once.
"""

from __future__ import annotations

import datetime as dt
import math
import sys
from array import array
from dataclasses import dataclass, field
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from backtest.ctrader_mock import MockBars, MockDataProvider, _Cursor, make_null_logger
from backtest.data_prep import iter_m1_rows
from backtest.engine import (
    COMMISSION_PER_1K_ROUND_TRIP, PIP_SIZE, PIP_VALUE_PER_UNIT,
    Trade, _PositionView, build_indicator_mocks, load_h1_csv, precompute_indicators,
)
from utils.constants import Direction

DATA_DIR = _PROJ_ROOT / "data"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class M1Config:
    initial_balance: float = 698.0
    risk_per_trade_pct: float = 1.0
    max_sl_pips: float | None = 50.0

    # Instrument (USD-quote pairs only: pip size 0.0001, pip value $0.0001/unit)
    pair: str = "eurusd"
    spread_pips: float = 1.0   # effective entry cost; wider for less liquid pairs

    # Exit model
    tp_mode: str = "band"            # "band" | "rr" | "none"
    tp_rr: float = 1.5               # used when tp_mode == "rr"
    trail_trigger_atr: float = 0.0   # 0 = trailing disabled
    trail_distance_atr: float = 0.0
    strat_exit: bool = True

    # Session / throttles / halts (None = off)
    session_start_utc: int | None = None
    session_end_utc: int | None = None
    friday_close_hour_utc: int | None = None
    daily_dd_pct: float | None = None
    total_dd_pct: float | None = None

    # Date range (inclusive years applied to H1 rows and M1 zips)
    year_from: int = 2015
    year_to: int = 2025

    strategy_params: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# M1 data cache (per process)
# ---------------------------------------------------------------------------

_M1_CACHE: dict[tuple[str, int], dict] = {}


def _load_m1_year(pair: str, year: int) -> dict:
    """Load one year of M1 bars, indexed by hour -> (start, end) slice."""
    key = (pair, year)
    if key in _M1_CACHE:
        return _M1_CACHE[key]
    highs, lows, closes = array("d"), array("d"), array("d")
    hour_index: dict[dt.datetime, list[int]] = {}
    i = 0
    for ts, o, h, l, c in iter_m1_rows(DATA_DIR / f"{pair}_{year}.zip"):
        hour = ts.replace(minute=0, second=0, microsecond=0)
        slot = hour_index.get(hour)
        if slot is None:
            hour_index[hour] = [i, i + 1]
        else:
            slot[1] = i + 1
        highs.append(h)
        lows.append(l)
        closes.append(c)
        i += 1
    _M1_CACHE[key] = {"highs": highs, "lows": lows, "closes": closes,
                      "hours": hour_index}
    return _M1_CACHE[key]


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------

@dataclass
class _Pos:
    direction: str
    entry_time: dt.datetime
    entry_price: float
    sl_price: float
    tp_price: float
    sl_pips: float
    tp_pips: float
    volume_units: float
    strategy: str
    trail_trigger_pips: float
    trail_distance_pips: float
    bars_held: int = 0
    trail_active: bool = False


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class M1Backtest:
    """One strategy, one symbol, H1 signals + M1-fidelity management."""

    def __init__(self, strategy_cls, h1_csv: Path, cfg: M1Config) -> None:
        self.cfg = cfg
        times, opens, highs, lows, closes = load_h1_csv(h1_csv)
        keep = [i for i, t in enumerate(times) if cfg.year_from <= t.year <= cfg.year_to]
        s, e = keep[0], keep[-1] + 1
        self.times = times[s:e]
        self.opens, self.highs = opens[s:e], highs[s:e]
        self.lows, self.closes = lows[s:e], closes[s:e]

        self.cursor = _Cursor()
        self.precomputed = precompute_indicators(
            self.opens, self.highs, self.lows, self.closes, cfg.strategy_params)
        self.indicators = build_indicator_mocks(self.precomputed, self.cursor)
        self.bars = MockBars(self.opens, self.highs, self.lows, self.closes,
                             self.times, self.cursor)
        self.dp = MockDataProvider(symbol="EURUSD", bars=self.bars,
                                   indicators=self.indicators, cursor=self.cursor,
                                   primary_tf="H1", spread_pips=cfg.spread_pips)
        self.strategy = strategy_cls(api=None, data_provider=self.dp,
                                     logger=make_null_logger(),
                                     params=dict(cfg.strategy_params))
        self.m1 = {y: _load_m1_year(cfg.pair, y)
                   for y in range(cfg.year_from, cfg.year_to + 1)}

        self.balance = cfg.initial_balance
        self.peak_balance = cfg.initial_balance
        self.daily_start_balance = cfg.initial_balance
        self.daily_start_date: dt.date | None = None
        self.position: _Pos | None = None
        self.trades: list[Trade] = []
        self.halted = False

    # -- helpers ---------------------------------------------------------

    def _atr_pips(self, i: int) -> float:
        v = self.precomputed["atr"][1][i]
        if isinstance(v, float) and math.isnan(v):
            return 0.0
        return v / PIP_SIZE

    def _in_session(self, ts: dt.datetime) -> bool:
        cfg = self.cfg
        if cfg.session_start_utc is None and cfg.session_end_utc is None:
            return True
        s = cfg.session_start_utc if cfg.session_start_utc is not None else 0
        e = cfg.session_end_utc if cfg.session_end_utc is not None else 24
        return s <= ts.hour < e

    def _friday_block(self, ts: dt.datetime) -> bool:
        h = self.cfg.friday_close_hour_utc
        return h is not None and ts.weekday() == 4 and ts.hour >= h

    def _close(self, ts: dt.datetime, price: float, reason: str) -> None:
        pos = self.position
        pips = ((price - pos.entry_price) if pos.direction == "Buy"
                else (pos.entry_price - price)) / PIP_SIZE
        net = pips * PIP_VALUE_PER_UNIT * pos.volume_units - \
            COMMISSION_PER_1K_ROUND_TRIP * (pos.volume_units / 1000.0)
        self.balance += net
        self.peak_balance = max(self.peak_balance, self.balance)
        self.trades.append(Trade(
            entry_time=pos.entry_time, exit_time=ts, direction=pos.direction,
            strategy=pos.strategy, entry_price=pos.entry_price, exit_price=price,
            sl_price=pos.sl_price, tp_price=pos.tp_price, sl_pips=pos.sl_pips,
            tp_pips=pos.tp_pips, volume_units=pos.volume_units, pnl_pips=pips,
            pnl_usd=net, exit_reason=reason, bars_held=pos.bars_held))
        self.position = None

    def _manage_hour_m1(self, hour: dt.datetime) -> None:
        """Walk the M1 bars of `hour`: SL/TP first, then trail (conservative)."""
        pos = self.position
        year = self.m1.get(hour.year)
        slot = year["hours"].get(hour) if year else None
        if slot is None:
            # data gap: degrade to the H1 bar as one pseudo-minute bar
            i = self.cursor.idx
            bars = [(self.highs[i], self.lows[i], self.closes[i])]
        else:
            h_arr, l_arr, c_arr = year["highs"], year["lows"], year["closes"]
            bars = ((h_arr[j], l_arr[j], c_arr[j]) for j in range(slot[0], slot[1]))

        for h, l, c in bars:
            if pos.direction == "Buy":
                if l <= pos.sl_price:
                    self._close(hour, pos.sl_price,
                                "Trail" if pos.trail_active else "SL")
                    return
                if pos.tp_price > 0 and h >= pos.tp_price:
                    self._close(hour, pos.tp_price, "TP")
                    return
            else:
                if h >= pos.sl_price:
                    self._close(hour, pos.sl_price,
                                "Trail" if pos.trail_active else "SL")
                    return
                if pos.tp_price > 0 and l <= pos.tp_price:
                    self._close(hour, pos.tp_price, "TP")
                    return
            # trail update AFTER the tests (conservative)
            if pos.trail_trigger_pips > 0:
                profit = ((h - pos.entry_price) if pos.direction == "Buy"
                          else (pos.entry_price - l)) / PIP_SIZE
                if profit >= pos.trail_trigger_pips:
                    if not pos.trail_active:
                        pos.sl_price = pos.entry_price
                        pos.trail_active = True
                    dist = pos.trail_distance_pips * PIP_SIZE
                    if pos.direction == "Buy":
                        pos.sl_price = max(pos.sl_price, h - dist)
                    else:
                        pos.sl_price = min(pos.sl_price, l + dist)

    def _open(self, ts: dt.datetime, signal) -> None:
        cfg = self.cfg
        sl_pips = signal.stop_loss_pips
        if sl_pips <= 0:
            return
        if cfg.max_sl_pips is not None and sl_pips > cfg.max_sl_pips:
            return
        if cfg.tp_mode == "band":
            tp_pips = signal.take_profit_pips
        elif cfg.tp_mode == "rr":
            tp_pips = sl_pips * cfg.tp_rr
        else:
            tp_pips = 0.0

        risk_amt = self.balance * (cfg.risk_per_trade_pct / 100.0)
        vol = max(1000, int(risk_amt / (sl_pips * PIP_VALUE_PER_UNIT) // 1000) * 1000)

        i = self.cursor.idx
        close = self.closes[i]
        if signal.direction == Direction.BUY:
            entry = close + cfg.spread_pips * PIP_SIZE
            sl_price = entry - sl_pips * PIP_SIZE
            tp_price = entry + tp_pips * PIP_SIZE if tp_pips > 0 else 0.0
        else:
            entry = close - cfg.spread_pips * PIP_SIZE
            sl_price = entry + sl_pips * PIP_SIZE
            tp_price = entry - tp_pips * PIP_SIZE if tp_pips > 0 else 0.0

        atr = self._atr_pips(i)
        self.position = _Pos(
            direction="Buy" if signal.direction == Direction.BUY else "Sell",
            entry_time=ts, entry_price=entry, sl_price=sl_price, tp_price=tp_price,
            sl_pips=sl_pips, tp_pips=tp_pips, volume_units=vol,
            strategy=self.strategy.name,
            trail_trigger_pips=atr * cfg.trail_trigger_atr,
            trail_distance_pips=atr * cfg.trail_distance_atr)

    # -- main loop -------------------------------------------------------

    def run(self) -> dict:
        cfg = self.cfg
        for i in range(len(self.times)):
            self.cursor.idx = i
            ts = self.times[i]

            d = ts.date()
            if self.daily_start_date is None:
                self.daily_start_date = d
            elif d != self.daily_start_date:
                self.daily_start_balance = self.balance
                self.daily_start_date = d

            if cfg.total_dd_pct is not None and self.peak_balance > 0 and \
                    (self.peak_balance - self.balance) / self.peak_balance * 100.0 \
                    >= cfg.total_dd_pct:
                self.halted = True

            if self.position is not None:
                self.position.bars_held += 1
                self._manage_hour_m1(ts)

            if self.position is not None and cfg.strat_exit:
                try:
                    if self.strategy.should_close(_PositionView(self.position)):
                        self._close(ts, self.closes[i], "StratExit")
                except BaseException:
                    pass

            if self.position is not None and self._friday_block(ts):
                self._close(ts, self.closes[i], "FriClose")
            if self.position is not None and self.halted:
                self._close(ts, self.closes[i], "DD-Halt")

            if self.halted or self.position is not None:
                continue
            if not self._in_session(ts) or self._friday_block(ts):
                continue
            if cfg.daily_dd_pct is not None and self.daily_start_balance > 0 and \
                    (self.daily_start_balance - self.balance) / \
                    self.daily_start_balance * 100.0 >= cfg.daily_dd_pct:
                continue

            try:
                signal = self.strategy.evaluate("EURUSD")
            except BaseException:
                continue
            if signal.direction != Direction.NONE:
                self._open(ts, signal)

        if self.position is not None:
            self._close(self.times[-1], self.closes[-1], "EndOfData")
        return self.summary()

    def summary(self) -> dict:
        n = len(self.trades)
        wins = [t for t in self.trades if t.pnl_usd > 0]
        gw = sum(t.pnl_usd for t in wins)
        gl = -sum(t.pnl_usd for t in self.trades if t.pnl_usd <= 0)
        by_year: dict[int, float] = {}
        for t in self.trades:
            by_year[t.entry_time.year] = by_year.get(t.entry_time.year, 0.0) + t.pnl_usd
        ts_sorted = sorted(t.entry_time for t in self.trades)
        drought = max(((b - a).days for a, b in zip(ts_sorted, ts_sorted[1:])),
                      default=0)
        peak = self.cfg.initial_balance
        max_dd = 0.0
        bal = self.cfg.initial_balance
        for t in self.trades:
            bal += t.pnl_usd
            peak = max(peak, bal)
            max_dd = max(max_dd, (peak - bal) / peak * 100.0 if peak > 0 else 0.0)
        return {
            "trades": n,
            "win_rate_pct": len(wins) / n * 100.0 if n else 0.0,
            "profit_factor": gw / gl if gl > 0 else float("inf"),
            "net_usd": gw - gl,
            "expectancy_usd": (gw - gl) / n if n else 0.0,
            "max_dd_pct": max_dd,
            "by_year": dict(sorted(by_year.items())),
            "positive_years": sum(1 for v in by_year.values() if v > 0),
            "years": len(by_year),
            "longest_drought_days": drought,
            "halted": self.halted,
        }
