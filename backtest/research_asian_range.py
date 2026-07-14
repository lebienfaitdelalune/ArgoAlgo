"""
research_asian_range.py
RESEARCH candidate — Asian-range / London-open strategy family.

NOT production code: lives in backtest/ until (if ever) it passes the full
protocol (IS sweep -> OOS single shot -> sensitivity). Rationale is structural,
not indicator-mined: the 00:00-07:00 UTC range is built under thin Asian
liquidity; London open brings a liquidity regime change that either resolves
the range (breakout) or punishes the overnight extension (fade).

Variants (params):
  mode           : "breakout" (enter with the range break)
                   | "fade" (range poke closes back -> enter toward range mid)
  entry_hour     : evaluate at the close of this UTC hour's bar (7 or 8)
  buffer_pips    : how far beyond the range edge counts as a break
  min/max_range_pips : skip degenerate / news-blown ranges
  sl_mode        : "atr" (sl_atr_mult x ATR14) | "range" (opposite range edge)
  sl_atr_mult    : used when sl_mode == "atr"

TP is the engine's job: tp_mode "rr" for breakout; for fade the signal's
take_profit_pips is the distance back to range mid (use tp_mode "band").

Usage: python3 -m backtest.research_asian_range   # IS sweep 2015-2021
"""

from __future__ import annotations

import csv
import itertools
import math
import sys
from datetime import datetime
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from backtest.m1_engine import M1Backtest, M1Config
from models.trade_signal import TradeSignal
from utils.constants import Direction

H1_CSV = _PROJ_ROOT / "data" / "eurusd_h1_utc_2015_2025.csv"
OUT_IS = _PROJ_ROOT / "data" / "AR_SWEEP_IS.csv"
PIP = 0.0001


class AsianRangeStrategy:
    """Asian-range breakout/fade, evaluated once per day at entry_hour close."""

    name = "AsianRange"

    def __init__(self, api=None, data_provider=None, logger=None, params=None):
        self._dp = data_provider
        self._p = params or {}

    def _no_signal(self, symbol):
        return TradeSignal(strategy_name=self.name, symbol=symbol,
                           direction=Direction.NONE, stop_loss_pips=0.0,
                           take_profit_pips=0.0, entry_price=0.0,
                           timestamp=datetime(2000, 1, 1), metadata={})

    def evaluate(self, symbol):
        p = self._p
        bars = self._dp.get_bars(symbol, self._dp.primary_timeframe)
        ts = bars.OpenTimes.LastValue
        if ts.hour != p["entry_hour"]:
            return self._no_signal(symbol)

        # Collect today's Asian bars (hours 0 .. entry_hour-1)
        hi, lo = -1e9, 1e9
        count = 0
        for i in range(1, p["entry_hour"] + 2):
            t = bars.OpenTimes.Last(i)
            if t.date() != ts.date() or t.hour >= p["entry_hour"]:
                break
            h = bars.HighPrices.Last(i)
            l = bars.LowPrices.Last(i)
            if math.isnan(h) or math.isnan(l):
                return self._no_signal(symbol)
            hi, lo = max(hi, h), min(lo, l)
            count += 1
        if count < 4:  # holiday / gap day, range not representative
            return self._no_signal(symbol)

        range_pips = (hi - lo) / PIP
        if not (p["min_range_pips"] <= range_pips <= p["max_range_pips"]):
            return self._no_signal(symbol)

        close = bars.ClosePrices.Last(0)
        buffer = p["buffer_pips"] * PIP
        mid = (hi + lo) / 2.0

        try:
            atr = float(self._dp.get_indicator(symbol, "atr").Result.Last(0))
        except BaseException:
            return self._no_signal(symbol)
        if math.isnan(atr) or atr <= 0:
            return self._no_signal(symbol)

        def sl_pips(direction_is_buy: bool) -> float:
            if p["sl_mode"] == "range":
                edge = lo if direction_is_buy else hi
                return abs(close - edge) / PIP
            return (atr / PIP) * p["sl_atr_mult"]

        def sig(direction, sl, tp):
            return TradeSignal(strategy_name=self.name, symbol=symbol,
                               direction=direction, stop_loss_pips=sl,
                               take_profit_pips=tp, entry_price=close,
                               timestamp=ts, metadata={"range_pips": range_pips})

        if p["mode"] == "breakout":
            # tp_pips unused (engine tp_mode="rr")
            if close > hi + buffer:
                return sig(Direction.BUY, sl_pips(True), 0.0)
            if close < lo - buffer:
                return sig(Direction.SELL, sl_pips(False), 0.0)
        else:  # fade: poke beyond range -> revert toward mid (tp_mode="band")
            if close > hi + buffer:
                return sig(Direction.SELL, sl_pips(False), (close - mid) / PIP)
            if close < lo - buffer:
                return sig(Direction.BUY, sl_pips(True), (mid - close) / PIP)

        return self._no_signal(symbol)

    def should_close(self, position) -> bool:
        return False


# ---------------------------------------------------------------------------
# IS sweep
# ---------------------------------------------------------------------------

GRID = dict(
    mode=["breakout", "fade"],
    entry_hour=[7, 8],
    buffer_pips=[0.0, 5.0],
    max_range_pips=[80.0, 999.0],
    sl_mode=["atr", "range"],
    exit=["rr15", "rr20", "band"],   # band only meaningful for fade
)

EXITS = {
    "rr15": dict(tp_mode="rr", tp_rr=1.5),
    "rr20": dict(tp_mode="rr", tp_rr=2.0),
    "band": dict(tp_mode="band"),
}


def config_key(c: dict) -> str:
    return (f"{c['mode']}_h{c['entry_hour']}_buf{int(c['buffer_pips'])}"
            f"_maxr{int(c['max_range_pips'])}_{c['sl_mode']}_{c['exit']}")


def run_one(c: dict, year_from: int, year_to: int) -> dict:
    params = dict(mode=c["mode"], entry_hour=c["entry_hour"],
                  buffer_pips=c["buffer_pips"], min_range_pips=20.0,
                  max_range_pips=c["max_range_pips"], sl_mode=c["sl_mode"],
                  sl_atr_mult=1.5,
                  # indicator params for precompute (ATR uses adx_period)
                  adx_period=14)
    cfg = M1Config(max_sl_pips=50.0, strat_exit=False,
                   friday_close_hour_utc=None, daily_dd_pct=None,
                   total_dd_pct=None, year_from=year_from, year_to=year_to,
                   strategy_params=params, **EXITS[c["exit"]])
    return M1Backtest(AsianRangeStrategy, H1_CSV, cfg).run()


if __name__ == "__main__":
    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    # band TP only makes sense for fade (breakout signals carry tp=0)
    combos = [c for c in combos if not (c["exit"] == "band" and c["mode"] == "breakout")]
    print(f"Asian-range IS sweep 2015-2021: {len(combos)} configs")
    rows = []
    for i, c in enumerate(combos, 1):
        s = run_one(c, 2015, 2021)
        worst = min(s["by_year"].values()) if s["by_year"] else 0.0
        rows.append({"key": config_key(c), "n": s["trades"],
                     "pf": round(s["profit_factor"], 3),
                     "net": round(s["net_usd"], 2),
                     "wr": round(s["win_rate_pct"], 1),
                     "pos_years": s["positive_years"], "years": s["years"],
                     "worst_year": round(worst, 2),
                     "max_dd": round(s["max_dd_pct"], 1)})
        if i % 10 == 0:
            print(f"  {i}/{len(combos)}", flush=True)

    with open(OUT_IS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    passed = [r for r in rows if r["pf"] >= 1.15 and r["n"] >= 250
              and r["pos_years"] >= 6]
    rows.sort(key=lambda r: (-r["pos_years"], -r["pf"]))
    print(f"\nGates passed: {len(passed)}")
    print("Top 10 by (pos_years, pf):")
    for r in rows[:10]:
        print(f"  {r['key']:40s} n={r['n']:4d} pf={r['pf']:.2f} "
              f"net=${r['net']:+8.2f} posY={r['pos_years']}/{r['years']} "
              f"worst=${r['worst_year']:+.0f}")
    print(f"\nFull results -> {OUT_IS}")
