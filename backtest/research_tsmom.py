"""
research_tsmom.py
RESEARCH candidate — daily time-series momentum (TSMOM) on H1 bars.

Rationale: time-series momentum is the most-replicated anomaly in the academic
literature (Moskowitz/Ooi/Pedersen 2012 and hundreds of follow-ups), including
FX. It needs no indicators — direction is the sign of the N-day return —
and its exits don't depend on intra-bar path (enter daily, exit on sign flip),
so the M1 engine prices it essentially exactly.

Rules:
  - At the close of `entry_hour` (UTC) each day, compute r = close / close[24*N bars] - 1.
  - If |r| >= threshold and flat: enter in the direction of r.
  - Exit: momentum sign flips (checked every H1 close via should_close),
    plus a protective ATR stop. Optional RR take-profit.

Usage: python3 -m backtest.research_tsmom   # IS sweep 2015-2021
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
OUT_IS = _PROJ_ROOT / "data" / "TSMOM_SWEEP_IS.csv"
PIP = 0.0001


class TSMOMStrategy:
    """Daily momentum: direction = sign of N-day return, exit on flip."""

    name = "TSMOM"

    def __init__(self, api=None, data_provider=None, logger=None, params=None):
        self._dp = data_provider
        self._p = params or {}

    def _mom(self, bars) -> float | None:
        lb = self._p["lookback_days"] * 24
        now = bars.ClosePrices.Last(0)
        then = bars.ClosePrices.Last(lb)
        if math.isnan(now) or math.isnan(then) or then <= 0:
            return None
        return now / then - 1.0

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
        r = self._mom(bars)
        if r is None or abs(r) < p["threshold"]:
            return self._no_signal(symbol)
        try:
            atr = float(self._dp.get_indicator(symbol, "atr").Result.Last(0))
        except BaseException:
            return self._no_signal(symbol)
        if math.isnan(atr) or atr <= 0:
            return self._no_signal(symbol)
        sl_pips = (atr / PIP) * p["sl_atr_mult"]
        direction = Direction.BUY if r > 0 else Direction.SELL
        return TradeSignal(strategy_name=self.name, symbol=symbol,
                           direction=direction, stop_loss_pips=sl_pips,
                           take_profit_pips=0.0, entry_price=bars.ClosePrices.Last(0),
                           timestamp=ts, metadata={"mom": r})

    def should_close(self, position) -> bool:
        """Exit when the momentum sign no longer supports the position."""
        bars = self._dp.get_bars("EURUSD", self._dp.primary_timeframe)
        r = self._mom(bars)
        if r is None:
            return False
        is_buy = str(position.TradeType) == "Buy"
        return (r <= 0) if is_buy else (r >= 0)


GRID = dict(
    lookback_days=[5, 10, 20, 40],
    entry_hour=[0, 8],
    threshold=[0.0, 0.002],       # 0 = pure sign; 0.2% dead-zone filter
    sl_atr_mult=[1.5, 2.5],
    exit=["flip", "flip_rr20"],
)

EXITS = {
    "flip":      dict(tp_mode="none", strat_exit=True),
    "flip_rr20": dict(tp_mode="rr", tp_rr=2.0, strat_exit=True),
}


def config_key(c: dict) -> str:
    return (f"lb{c['lookback_days']}_h{c['entry_hour']}_th{c['threshold']}"
            f"_sl{c['sl_atr_mult']}_{c['exit']}")


def run_one(c: dict, year_from: int, year_to: int) -> dict:
    params = dict(lookback_days=c["lookback_days"], entry_hour=c["entry_hour"],
                  threshold=c["threshold"], sl_atr_mult=c["sl_atr_mult"],
                  adx_period=14)
    cfg = M1Config(max_sl_pips=None,   # ATR SL can exceed 50 pips on D-scale holds
                   friday_close_hour_utc=None, daily_dd_pct=None,
                   total_dd_pct=None, year_from=year_from, year_to=year_to,
                   trail_trigger_atr=0.0, trail_distance_atr=0.0,
                   strategy_params=params, **EXITS[c["exit"]])
    return M1Backtest(TSMOMStrategy, H1_CSV, cfg).run()


if __name__ == "__main__":
    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    print(f"TSMOM IS sweep 2015-2021: {len(combos)} configs")
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
        if i % 8 == 0:
            print(f"  {i}/{len(combos)}", flush=True)

    with open(OUT_IS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    passed = [r for r in rows if r["pf"] >= 1.15 and r["n"] >= 250
              and r["pos_years"] >= 6]
    rows.sort(key=lambda r: (-r["pos_years"], -r["pf"]))
    print(f"\nGates passed (PF>=1.15, n>=250, posY>=6/7): {len(passed)}")
    print("Top 10 by (pos_years, pf):")
    for r in rows[:10]:
        print(f"  {r['key']:36s} n={r['n']:4d} pf={r['pf']:.2f} "
              f"net=${r['net']:+8.2f} posY={r['pos_years']}/{r['years']} "
              f"worst=${r['worst_year']:+.0f}")
    print(f"\nFull results -> {OUT_IS}")
