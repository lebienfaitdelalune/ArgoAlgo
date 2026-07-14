"""
research_xsect.py
RESEARCH candidate — cross-sectional relative strength on a USD-quote basket.

Hypothesis (the most-replicated in currency literature — Menkhoff et al. 2012,
Asness/Moskowitz/Pedersen 2013): currencies that outperformed their peers over
the recent past continue to outperform over the near future. Traded relatively
(long the strongest pair, short the weakest), most USD/market noise cancels.
The reversal variant (long weakest) tests cross-sectional value/mean-reversion.

Mechanics:
  - Basket: EURUSD, GBPUSD, AUDUSD, NZDUSD (all USD quote, pip $0.0001/unit).
  - At 21:00 UTC every R trading days: rank pairs by N-day return.
    Long the top pair, short the bottom pair, equal notional per leg.
  - Hold until next rebalance; close & re-open only if the rank changed
    (avoids paying spread to re-establish an identical position).
  - Costs per round trip and per leg: full spread + $0.07/1k commission.
  - Optional disaster stop at K x ATR(14, H1); tested SL-first on H1 extremes
    (conservative — no trailing, so no intra-bar ordering artifact).

Exits are at bar closes or wide stops, so H1 fidelity is honest for this
family (the M1-artifact problem only affects exits tighter than bar range).

Usage: python3 -m backtest.research_xsect            # IS sweep 2015-2021
       python3 -m backtest.research_xsect --oos K...  # single-shot OOS 2022-2025
"""

from __future__ import annotations

import csv
import itertools
import math
import sys
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from backtest.engine import load_h1_csv
from backtest.indicators import atr as atr_calc

DATA = _PROJ_ROOT / "data"
OUT_IS = DATA / "XSECT_SWEEP_IS.csv"
PIP = 0.0001
PIP_VALUE = 0.0001          # USD per pip per unit
COMMISSION_PER_1K = 0.07    # round trip
NOTIONAL = 10_000           # units per leg (fixed for measurement)
REBAL_HOUR = 21             # after NY close, before Asia

PAIRS = {
    "eurusd": 1.0, "gbpusd": 1.3, "audusd": 1.2, "nzdusd": 1.8,  # spread pips
}


def load_basket():
    """Aligned H1 timeline across the basket: ts -> {pair: (h, l, c)}."""
    series = {}
    for pair in PAIRS:
        path = DATA / (f"{pair}_h1_utc_2015_2025.csv" if pair != "eurusd"
                       else "eurusd_h1_utc_2015_2025.csv")
        times, _, highs, lows, closes = load_h1_csv(path)
        atr_arr = atr_calc(highs, lows, closes, 14)
        series[pair] = {t: (h, l, c, a) for t, h, l, c, a in
                        zip(times, highs, lows, closes, atr_arr)}
    common = sorted(set.intersection(*(set(s) for s in series.values())))
    return common, series


class XSectBacktest:
    def __init__(self, timeline, series, lookback_days: int, rebal_days: int,
                 sign: int, sl_atr_mult: float | None,
                 year_from: int, year_to: int) -> None:
        self.timeline = [t for t in timeline if year_from <= t.year <= year_to]
        self.series = series
        self.lookback_h = lookback_days * 24
        self.rebal_days = rebal_days
        self.sign = sign                    # +1 momentum, -1 reversal
        self.sl_atr_mult = sl_atr_mult
        self.trades = []                    # (entry_ts, exit_ts, pair, dir, pnl_usd, reason)
        self.legs = {}                      # pair -> dict(dir, entry, ts, sl)
        # index timeline per pair for lookback returns
        self._ts_index = {t: i for i, t in enumerate(timeline)}
        self._full_timeline = timeline

    def _ret(self, pair: str, ts) -> float | None:
        i = self._ts_index[ts]
        j = i - self.lookback_h
        if j < 0:
            return None
        past_ts = self._full_timeline[j]
        now = self.series[pair][ts][2]
        past = self.series[pair].get(past_ts)
        if past is None or past[2] <= 0:
            return None
        return now / past[2] - 1.0

    def _close_leg(self, pair: str, ts, price: float, reason: str) -> None:
        leg = self.legs.pop(pair)
        pips = ((price - leg["entry"]) if leg["dir"] == 1
                else (leg["entry"] - price)) / PIP
        cost = (PAIRS[pair] * PIP * PIP_VALUE / PIP) * NOTIONAL  # spread $ per round trip
        pnl = pips * PIP_VALUE * NOTIONAL - cost - COMMISSION_PER_1K * NOTIONAL / 1000
        self.trades.append((leg["ts"], ts, pair, leg["dir"], pnl, reason))

    def _open_leg(self, pair: str, ts, direction: int) -> None:
        _, _, close, a = self.series[pair][ts]
        sl = None
        if self.sl_atr_mult and a and not math.isnan(a):
            sl = close - direction * self.sl_atr_mult * a
        self.legs[pair] = dict(dir=direction, entry=close, ts=ts, sl=sl)

    def run(self) -> dict:
        last_rebal_date = None
        days_since = 0
        for ts in self.timeline:
            row_ok = all(ts in self.series[p] for p in PAIRS)
            if not row_ok:
                continue

            # disaster stops, checked every bar on H1 extremes (SL first = conservative)
            for pair in list(self.legs):
                leg = self.legs[pair]
                if leg["sl"] is None:
                    continue
                h, l, c, _ = self.series[pair][ts]
                if leg["dir"] == 1 and l <= leg["sl"]:
                    self._close_leg(pair, ts, leg["sl"], "SL")
                elif leg["dir"] == -1 and h >= leg["sl"]:
                    self._close_leg(pair, ts, leg["sl"], "SL")

            if ts.hour != REBAL_HOUR or ts.weekday() >= 5:
                continue
            if last_rebal_date is not None:
                days_since += (ts.date() - last_rebal_date).days
                if days_since < self.rebal_days:
                    continue
            last_rebal_date, days_since = ts.date(), 0

            rets = {}
            for pair in PAIRS:
                r = self._ret(pair, ts)
                if r is not None:
                    rets[pair] = r
            if len(rets) < len(PAIRS):
                continue
            ranked = sorted(rets, key=rets.get)
            top, bottom = ranked[-1], ranked[0]
            want = ({top: 1, bottom: -1} if self.sign == 1
                    else {top: -1, bottom: 1})

            for pair in list(self.legs):
                if want.get(pair) != self.legs[pair]["dir"]:
                    self._close_leg(pair, ts, self.series[pair][ts][2], "Rebal")
            for pair, direction in want.items():
                if pair not in self.legs:
                    self._open_leg(pair, ts, direction)

        # close remaining at end
        end_ts = self.timeline[-1]
        for pair in list(self.legs):
            price = self.series[pair].get(end_ts, (0, 0, self.legs[pair]["entry"]))[2]
            self._close_leg(pair, end_ts, price, "End")
        return self.summary()

    def summary(self) -> dict:
        pnls = [t[4] for t in self.trades]
        n = len(pnls)
        gw = sum(p for p in pnls if p > 0)
        gl = -sum(p for p in pnls if p <= 0)
        by_year = {}
        for t in self.trades:
            by_year[t[0].year] = by_year.get(t[0].year, 0.0) + t[4]
        return {
            "trades": n,
            "profit_factor": gw / gl if gl > 0 else float("inf"),
            "net_usd": gw - gl,
            "win_rate_pct": sum(1 for p in pnls if p > 0) / n * 100 if n else 0.0,
            "by_year": dict(sorted(by_year.items())),
            "positive_years": sum(1 for v in by_year.values() if v > 0),
            "years": len(by_year),
        }


GRID = dict(
    lookback_days=[5, 10, 20, 60],
    rebal_days=[1, 5, 20],
    sign=[1, -1],                    # momentum / reversal
    sl_atr_mult=[None, 3.0],
)


def config_key(c):
    s = "mom" if c["sign"] == 1 else "rev"
    sl = "nosl" if c["sl_atr_mult"] is None else f"sl{c['sl_atr_mult']}"
    return f"{s}_lb{c['lookback_days']}_rb{c['rebal_days']}_{sl}"


def run_one(timeline, series, c, y0, y1):
    return XSectBacktest(timeline, series, c["lookback_days"], c["rebal_days"],
                         c["sign"], c["sl_atr_mult"], y0, y1).run()


if __name__ == "__main__":
    print("Loading basket...", flush=True)
    timeline, series = load_basket()
    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]

    if "--oos" in sys.argv:
        keys = sys.argv[sys.argv.index("--oos") + 1:]
        by_key = {config_key(c): c for c in combos}
        print("OOS single shot 2022-2025 — FINAL for these configs.")
        for k in keys:
            c = by_key.get(k)
            if c is None:
                print(f"  {k}: unknown")
                continue
            s = run_one(timeline, series, c, 2022, 2025)
            yr = "  ".join(f"{y}:${v:+.0f}" for y, v in s["by_year"].items())
            print(f"  {k}: n={s['trades']} pf={s['profit_factor']:.2f} "
                  f"net=${s['net_usd']:+.2f} posY={s['positive_years']}/{s['years']}  {yr}")
        sys.exit(0)

    print(f"XSect IS sweep 2015-2021: {len(combos)} configs")
    rows = []
    for c in combos:
        s = run_one(timeline, series, c, 2015, 2021)
        worst = min(s["by_year"].values()) if s["by_year"] else 0.0
        rows.append({"key": config_key(c), "n": s["trades"],
                     "pf": round(s["profit_factor"], 3),
                     "net": round(s["net_usd"], 2),
                     "wr": round(s["win_rate_pct"], 1),
                     "pos_years": s["positive_years"], "years": s["years"],
                     "worst_year": round(worst, 2)})
    with open(OUT_IS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    rows.sort(key=lambda r: (-r["pos_years"], -r["pf"]))
    passed = [r for r in rows if r["pf"] >= 1.15 and r["n"] >= 250
              and r["pos_years"] >= 6]
    print(f"\nGates passed (PF>=1.15, n>=250, posY>=6/7): {len(passed)}")
    for r in rows[:12]:
        print(f"  {r['key']:24s} n={r['n']:4d} pf={r['pf']:.2f} net=${r['net']:+8.2f} "
              f"wr={r['wr']}% posY={r['pos_years']}/{r['years']} worst=${r['worst_year']:+.0f}")
    print(f"\nFull results -> {OUT_IS}")
