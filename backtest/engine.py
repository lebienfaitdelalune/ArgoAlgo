"""
engine.py
Bar-by-bar backtest engine for ArgoAlgo strategies.

Runs the actual strategies/*.py code unchanged via the ctrader_mock layer.
Simulates entry, SL/TP, trailing stops, spread, and commission at H1 granularity.

Conservative assumptions:
  - On a bar where both SL and TP could hit, SL fires first.
  - Trail/BE updates within a bar happen *before* SL/TP test (slightly favourable
    to the strategy; documented).
  - Entry slips by 1 pip (spread). Exit at SL/TP fills exactly.
  - Commission $0.07 per 1000 units round-trip (IC Markets Raw Spread).
"""

from __future__ import annotations

import csv
import datetime as dt
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Make project root importable so we can use strategies/*.py unchanged
_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from backtest.ctrader_mock import (  # noqa: E402
    MockBars, MockDataProvider, MockIndicator, _Cursor, make_null_logger,
)
from backtest.indicators import (  # noqa: E402
    adx as adx_calc,
    atr as atr_calc,
    bollinger as bb_calc,
    donchian as donchian_calc,
    ema as ema_calc,
    rsi as rsi_calc,
)
from utils.constants import Defaults, Direction  # noqa: E402


PIP_SIZE = 0.0001
PIP_VALUE_PER_UNIT = 0.0001  # USD pip value per 1 unit of EURUSD
COMMISSION_PER_1K_ROUND_TRIP = 0.07  # IC Markets Raw Spread
SPREAD_PIPS = 1.0  # entry slippage / spread


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    """Risk / session / throttle parameters for a backtest run."""
    initial_balance: float = 698.0
    risk_per_trade_pct: float = 1.0

    # Strategy filters (None = strategy uses its own defaults)
    max_sl_pips: float | None = None
    min_sl_pips: float | None = None

    # Throttling
    max_trades_per_day: int | None = None
    post_loss_cooldown_hours: float | None = None
    daily_dd_pct: float | None = None
    total_dd_pct: float | None = None

    # Session
    session_start_utc: int | None = None
    session_end_utc: int | None = None
    friday_close_hour_utc: int | None = None

    # Position management
    max_concurrent: int = 1
    trailing_enabled: bool = True

    # Per-strategy params — overrides what the strategy reads from its dict.
    # Keys match strategy expectations (see strategies/*.py params dicts).
    strategy_params: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Trade record
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    entry_time: dt.datetime
    exit_time: dt.datetime
    direction: str       # "Buy" or "Sell"
    strategy: str
    entry_price: float
    exit_price: float
    sl_price: float
    tp_price: float
    sl_pips: float
    tp_pips: float
    volume_units: float
    pnl_pips: float
    pnl_usd: float       # net of spread + commission
    exit_reason: str     # "SL", "TP", "Trail", "StratExit"
    bars_held: int


# ---------------------------------------------------------------------------
# Indicator precomputation
# ---------------------------------------------------------------------------

def precompute_indicators(opens, highs, lows, closes, params):
    """Compute every indicator the strategies read, return MockIndicator dict."""
    ind = {}
    cursor_ref = []  # filled in by harness; placeholder

    ema_fast_arr = ema_calc(closes, int(params.get("fast_ema_period", Defaults.TF_FAST_EMA_PERIOD)))
    ema_slow_arr = ema_calc(closes, int(params.get("slow_ema_period", Defaults.TF_SLOW_EMA_PERIOD)))
    adx_period = int(params.get("adx_period", Defaults.TF_ADX_PERIOD))
    adx_arr, _, _ = adx_calc(highs, lows, closes, adx_period)
    atr_arr = atr_calc(highs, lows, closes, adx_period)

    bb_period = int(params.get("bollinger_period", Defaults.MR_BOLLINGER_PERIOD))
    bb_dev = float(params.get("bollinger_deviation", Defaults.MR_BOLLINGER_DEVIATION))
    bb_top, bb_mid, bb_bot = bb_calc(closes, bb_period, bb_dev)

    rsi_arr = rsi_calc(closes, int(params.get("rsi_period", Defaults.MR_RSI_PERIOD)))
    adx_filter_arr, _, _ = adx_calc(highs, lows, closes,
                                     int(params.get("adx_filter_period", Defaults.MR_ADX_FILTER_PERIOD)))

    donch_period = int(params.get("donchian_period", Defaults.BO_DONCHIAN_PERIOD))
    donch_high, donch_low = donchian_calc(highs, lows, donch_period)
    atr_bo_period = int(params.get("atr_bo_period", Defaults.BO_ATR_PERIOD))
    atr_bo_arr = atr_calc(highs, lows, closes, atr_bo_period)

    return {
        "ema_fast":      ("Result", ema_fast_arr),
        "ema_slow":      ("Result", ema_slow_arr),
        "adx":           ("ADX", adx_arr),
        "atr":           ("Result", atr_arr),
        "bollinger":     ("multi", {"Top": bb_top, "Main": bb_mid, "Bottom": bb_bot}),
        "rsi":           ("Result", rsi_arr),
        "adx_filter":    ("ADX", adx_filter_arr),
        "donchian_high": ("Result", donch_high),
        "donchian_low":  ("Result", donch_low),
        "atr_bo":        ("Result", atr_bo_arr),
    }


def build_indicator_mocks(precomputed, cursor):
    out = {}
    for key, (kind, payload) in precomputed.items():
        if kind == "multi":
            out[key] = MockIndicator(payload, cursor)
        else:
            out[key] = MockIndicator({kind: payload}, cursor)
    return out


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------

def load_h1_csv(path: Path):
    times, opens, highs, lows, closes = [], [], [], [], []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            times.append(dt.datetime.strptime(row["timestamp_utc"], "%Y-%m-%d %H:%M:%S"))
            opens.append(float(row["open"]))
            highs.append(float(row["high"]))
            lows.append(float(row["low"]))
            closes.append(float(row["close"]))
    return times, opens, highs, lows, closes


# ---------------------------------------------------------------------------
# Position state
# ---------------------------------------------------------------------------

@dataclass
class _OpenPosition:
    direction: str
    entry_time: dt.datetime
    entry_price: float
    sl_price: float
    tp_price: float
    sl_pips: float
    tp_pips: float
    volume_units: float
    strategy: str
    bars_held: int = 0
    trail_active: bool = False
    trail_trigger_pips: float = 0.0
    trail_distance_pips: float = 0.0


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------

class Backtest:
    """Run one strategy over the loaded H1 bars."""

    def __init__(self, strategy_cls, csv_path: Path, cfg: BacktestConfig) -> None:
        self.cfg = cfg
        self.times, self.opens, self.highs, self.lows, self.closes = load_h1_csv(csv_path)
        self.cursor = _Cursor()
        self.precomputed = precompute_indicators(
            self.opens, self.highs, self.lows, self.closes, cfg.strategy_params
        )
        self.indicators = build_indicator_mocks(self.precomputed, self.cursor)
        self.bars = MockBars(self.opens, self.highs, self.lows, self.closes,
                             self.times, self.cursor)
        self.dp = MockDataProvider(
            symbol="EURUSD", bars=self.bars, indicators=self.indicators,
            cursor=self.cursor, primary_tf="H1", spread_pips=SPREAD_PIPS,
        )
        self.logger = make_null_logger()

        # Build the strategy instance
        self.strategy = strategy_cls(
            api=None, data_provider=self.dp, logger=self.logger,
            params=dict(cfg.strategy_params),
        )

        # State
        self.balance = cfg.initial_balance
        self.peak_balance = cfg.initial_balance
        self.daily_start_balance = cfg.initial_balance
        self.daily_start_date: dt.date | None = None
        self.trades_today: int = 0
        self.last_loss_time: dt.datetime | None = None
        self.position: _OpenPosition | None = None
        self.trades: list[Trade] = []
        self.halted: bool = False
        self.halt_reason: str = ""
        self.equity_curve: list[tuple[dt.datetime, float]] = []

        # Strategy code abbreviation for trail params (matches build_label)
        self._strat_abbrev = self.strategy.name[:2].upper()

    # ------------------------------------------------------------------
    # Risk / throttle checks
    # ------------------------------------------------------------------

    def _day_rollover(self, ts: dt.datetime) -> None:
        d = ts.date()
        if self.daily_start_date is None:
            self.daily_start_date = d
            return
        if d != self.daily_start_date:
            self.daily_start_balance = self.balance
            self.daily_start_date = d
            self.trades_today = 0

    def _in_session(self, ts: dt.datetime) -> bool:
        if self.cfg.session_start_utc is None and self.cfg.session_end_utc is None:
            return True
        s = self.cfg.session_start_utc if self.cfg.session_start_utc is not None else 0
        e = self.cfg.session_end_utc if self.cfg.session_end_utc is not None else 24
        return s <= ts.hour < e

    def _friday_close(self, ts: dt.datetime) -> bool:
        if self.cfg.friday_close_hour_utc is None:
            return False
        return ts.weekday() == 4 and ts.hour >= self.cfg.friday_close_hour_utc

    def _in_cooldown(self, ts: dt.datetime) -> bool:
        if self.cfg.post_loss_cooldown_hours is None or self.last_loss_time is None:
            return False
        elapsed_h = (ts - self.last_loss_time).total_seconds() / 3600.0
        return elapsed_h < self.cfg.post_loss_cooldown_hours

    def _drawdown_halt(self) -> bool:
        if self.cfg.daily_dd_pct is not None and self.daily_start_balance > 0:
            loss_pct = (self.daily_start_balance - self.balance) / self.daily_start_balance * 100.0
            if loss_pct >= self.cfg.daily_dd_pct:
                return True
        if self.cfg.total_dd_pct is not None and self.peak_balance > 0:
            dd_pct = (self.peak_balance - self.balance) / self.peak_balance * 100.0
            if dd_pct >= self.cfg.total_dd_pct:
                self.halted = True
                self.halt_reason = "Total DD"
                return True
        return False

    # ------------------------------------------------------------------
    # Position lifecycle
    # ------------------------------------------------------------------

    def _open_position(self, ts: dt.datetime, signal) -> None:
        cfg = self.cfg
        sl_pips = signal.stop_loss_pips
        tp_pips = signal.take_profit_pips

        # SL cap
        if cfg.max_sl_pips is not None and sl_pips > cfg.max_sl_pips:
            return
        # SL floor (the strategies already apply min_sl, but allow override)
        if cfg.min_sl_pips is not None and sl_pips < cfg.min_sl_pips:
            sl_pips = cfg.min_sl_pips
            tp_pips = sl_pips * (signal.take_profit_pips / signal.stop_loss_pips
                                 if signal.stop_loss_pips > 0 else 2.0)

        # Daily cap
        if cfg.max_trades_per_day is not None and self.trades_today >= cfg.max_trades_per_day:
            return
        if self._in_cooldown(ts):
            return

        # Volume sizing
        risk_amt = self.balance * (cfg.risk_per_trade_pct / 100.0)
        raw_vol = risk_amt / (sl_pips * PIP_VALUE_PER_UNIT)
        # Clamp to 1000 step, min 1000
        vol = max(1000, int(raw_vol // 1000) * 1000)
        if vol < 1000:
            return

        # Entry price (with 1-pip spread slippage on entry)
        bar_idx = self.cursor.idx
        market_close = self.closes[bar_idx]
        if signal.direction == Direction.BUY:
            entry = market_close + SPREAD_PIPS * PIP_SIZE
            sl_price = entry - sl_pips * PIP_SIZE
            tp_price = entry + tp_pips * PIP_SIZE if tp_pips > 0 else 0.0
        else:
            entry = market_close - SPREAD_PIPS * PIP_SIZE
            sl_price = entry + sl_pips * PIP_SIZE
            tp_price = entry - tp_pips * PIP_SIZE if tp_pips > 0 else 0.0

        # Per-strategy trail params (matches risk_manager._get_trailing_params)
        trail_trig, trail_dist = self._get_trail_params(self._strat_abbrev,
                                                       atr_pips=self._current_atr_pips())

        self.position = _OpenPosition(
            direction="Buy" if signal.direction == Direction.BUY else "Sell",
            entry_time=ts, entry_price=entry,
            sl_price=sl_price, tp_price=tp_price,
            sl_pips=sl_pips, tp_pips=tp_pips,
            volume_units=vol, strategy=self.strategy.name,
            trail_trigger_pips=trail_trig, trail_distance_pips=trail_dist,
        )
        self.trades_today += 1

    def _current_atr_pips(self) -> float:
        atr_arr = self.precomputed["atr"][1]
        i = self.cursor.idx
        if 0 <= i < len(atr_arr):
            v = atr_arr[i]
            if not (isinstance(v, float) and math.isnan(v)):
                return v / PIP_SIZE
        return 0.0

    def _get_trail_params(self, abbrev: str, atr_pips: float) -> tuple[float, float]:
        # Replicates risk_manager._get_trailing_params logic.
        # NOTE: real code checks ("TF","ME","BR") but build_label produces "TR" for TF.
        # Here we use the *correct* mapping via the strategy abbrev directly.
        if atr_pips > 0:
            if abbrev == "TR":
                return (atr_pips * Defaults.TF_TRAILING_TRIGGER_ATR,
                        atr_pips * Defaults.TF_TRAILING_DISTANCE_ATR)
            if abbrev == "ME":
                return (atr_pips * Defaults.MR_TRAILING_TRIGGER_ATR,
                        atr_pips * Defaults.MR_TRAILING_DISTANCE_ATR)
            if abbrev == "BR":
                return (atr_pips * Defaults.BO_TRAILING_TRIGGER_ATR,
                        atr_pips * Defaults.BO_TRAILING_DISTANCE_ATR)
        return (Defaults.TRAILING_STOP_TRIGGER_PIPS, Defaults.TRAILING_STOP_DISTANCE_PIPS)

    def _update_trail(self, pos: _OpenPosition, bar_high: float, bar_low: float) -> None:
        if not self.cfg.trailing_enabled:
            return
        # Profit pips reached intra-bar
        if pos.direction == "Buy":
            best_profit_pips = (bar_high - pos.entry_price) / PIP_SIZE
        else:
            best_profit_pips = (pos.entry_price - bar_low) / PIP_SIZE

        if best_profit_pips < pos.trail_trigger_pips:
            return

        # Activate (move SL to BE)
        if not pos.trail_active:
            pos.sl_price = pos.entry_price
            pos.trail_active = True

        # Advance trail
        trail_dist_price = pos.trail_distance_pips * PIP_SIZE
        if pos.direction == "Buy":
            new_sl = bar_high - trail_dist_price
            if new_sl > pos.sl_price:
                pos.sl_price = new_sl
        else:
            new_sl = bar_low + trail_dist_price
            if new_sl < pos.sl_price:
                pos.sl_price = new_sl

    def _close_position(self, ts: dt.datetime, exit_price: float, reason: str) -> None:
        pos = self.position
        if pos is None:
            return

        if pos.direction == "Buy":
            pnl_pips = (exit_price - pos.entry_price) / PIP_SIZE
        else:
            pnl_pips = (pos.entry_price - exit_price) / PIP_SIZE

        # P/L in USD: pips × pip_value × volume — minus commission round-trip
        pnl_usd = pnl_pips * PIP_VALUE_PER_UNIT * pos.volume_units
        commission = COMMISSION_PER_1K_ROUND_TRIP * (pos.volume_units / 1000.0)
        net_usd = pnl_usd - commission

        self.balance += net_usd
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance

        self.trades.append(Trade(
            entry_time=pos.entry_time, exit_time=ts,
            direction=pos.direction, strategy=pos.strategy,
            entry_price=pos.entry_price, exit_price=exit_price,
            sl_price=pos.sl_price, tp_price=pos.tp_price,
            sl_pips=pos.sl_pips, tp_pips=pos.tp_pips,
            volume_units=pos.volume_units, pnl_pips=pnl_pips, pnl_usd=net_usd,
            exit_reason=reason, bars_held=pos.bars_held,
        ))

        if net_usd < 0:
            self.last_loss_time = ts

        self.position = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> dict:
        for i in range(len(self.times)):
            self.cursor.idx = i
            ts = self.times[i]

            self._day_rollover(ts)
            self.equity_curve.append((ts, self.balance))

            if self._drawdown_halt():
                # If permanently halted, close any open and stop trading
                if self.position is not None:
                    self._close_position(ts, self.closes[i], "DD-Halt")
                if self.halted:
                    continue
                # Daily DD: don't open new, but allow current position to manage
                pass

            # First, manage open position on this bar
            if self.position is not None:
                self.position.bars_held += 1
                pos = self.position
                bar_h = self.highs[i]
                bar_l = self.lows[i]
                # Update trail BEFORE testing SL/TP (slightly favourable; documented)
                self._update_trail(pos, bar_h, bar_l)

                # Conservative ordering: SL first, then TP, then strategy exit.
                if pos.direction == "Buy":
                    if bar_l <= pos.sl_price:
                        self._close_position(ts, pos.sl_price,
                                              "Trail" if pos.trail_active else "SL")
                    elif pos.tp_price > 0 and bar_h >= pos.tp_price:
                        self._close_position(ts, pos.tp_price, "TP")
                else:
                    if bar_h >= pos.sl_price:
                        self._close_position(ts, pos.sl_price,
                                              "Trail" if pos.trail_active else "SL")
                    elif pos.tp_price > 0 and bar_l <= pos.tp_price:
                        self._close_position(ts, pos.tp_price, "TP")

                # Strategy-driven exit on bar close
                if self.position is not None:
                    try:
                        sc = self.strategy.should_close(_PositionView(pos))
                    except BaseException:
                        sc = False
                    if sc:
                        self._close_position(ts, self.closes[i], "StratExit")

            # Friday close
            if self.position is not None and self._friday_close(ts):
                self._close_position(ts, self.closes[i], "FriClose")

            # Halted: skip entry attempts
            if self.halted:
                continue

            # Skip if outside session
            if not self._in_session(ts):
                continue
            if self._friday_close(ts):
                continue

            # Skip if already in a position
            if self.position is not None:
                continue

            # Daily DD soft halt
            if self.cfg.daily_dd_pct is not None and self.daily_start_balance > 0:
                loss_pct = (self.daily_start_balance - self.balance) / self.daily_start_balance * 100.0
                if loss_pct >= self.cfg.daily_dd_pct:
                    continue

            # Evaluate strategy
            try:
                signal = self.strategy.evaluate("EURUSD")
            except BaseException as e:
                continue
            if signal.direction == Direction.NONE:
                continue

            self._open_position(ts, signal)

        # Close any open position at end of data
        if self.position is not None:
            ts = self.times[-1]
            self._close_position(ts, self.closes[-1], "EndOfData")

        return self._summary()

    # ------------------------------------------------------------------
    # Summary stats
    # ------------------------------------------------------------------

    def _summary(self) -> dict:
        n = len(self.trades)
        wins = [t for t in self.trades if t.pnl_usd > 0]
        losses = [t for t in self.trades if t.pnl_usd <= 0]
        gross_win = sum(t.pnl_usd for t in wins)
        gross_loss = -sum(t.pnl_usd for t in losses)  # positive number
        net = gross_win - gross_loss
        # Max drawdown from equity curve
        peak = self.cfg.initial_balance
        max_dd = 0.0
        for _, eq in self.equity_curve:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        return {
            "trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": (len(wins) / n * 100.0) if n else 0.0,
            "avg_win_usd": (gross_win / len(wins)) if wins else 0.0,
            "avg_loss_usd": (gross_loss / len(losses)) if losses else 0.0,
            "expectancy_usd": (net / n) if n else 0.0,
            "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
            "net_usd": net,
            "final_balance": self.balance,
            "max_dd_pct": max_dd,
            "exit_reasons": _count_reasons(self.trades),
        }


def _count_reasons(trades):
    out = {}
    for t in trades:
        out[t.exit_reason] = out.get(t.exit_reason, 0) + 1
    return out


class _PositionView:
    """Minimal cTrader-Position-like view for strategy.should_close()."""
    def __init__(self, pos: _OpenPosition) -> None:
        self.SymbolName = "EURUSD"
        self.TradeType = pos.direction  # str "Buy" or "Sell" — strategies str() it
        self.EntryPrice = pos.entry_price
        self.Pips = 0.0  # not used in should_close paths we have
        self.Id = id(pos)
        self.Label = f"ArgoAlgo_{pos.strategy[:2].upper()}_EURUSD"

    def __getattr__(self, name):
        return None
