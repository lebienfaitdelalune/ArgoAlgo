"""
research_asian_range_xpair.py
Cross-pair robustness probe for the Asian-range family.

On EURUSD IS the family's best region was h8 breakout / buffered / rr15 at
PF ~1.05 — indistinguishable from noise alone. The test here: run the SAME
80-config grid on GBPUSD, AUDUSD, NZDUSD (IS 2015-2021 only). A structural
London-open effect must show the same region rising on all pairs; a data
fluke won't. This is a robustness probe, not config selection — no OOS is
spent, and no config is promoted from these results alone.

Usage: python3 -m backtest.research_asian_range_xpair
"""

from __future__ import annotations

import csv
import itertools
import sys
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from backtest.m1_engine import M1Backtest, M1Config
from backtest.research_asian_range import (
    EXITS, GRID, AsianRangeStrategy, config_key,
)

DATA = _PROJ_ROOT / "data"
OUT = DATA / "AR_XPAIR_IS.csv"

PAIRS = {  # pair -> (h1 csv, effective entry spread pips)
    "gbpusd": (DATA / "gbpusd_h1_utc_2015_2025.csv", 1.3),
    "audusd": (DATA / "audusd_h1_utc_2015_2025.csv", 1.2),
    "nzdusd": (DATA / "nzdusd_h1_utc_2015_2025.csv", 1.8),
}


def run_one(pair: str, c: dict) -> dict:
    csv_path, spread = PAIRS[pair]
    params = dict(mode=c["mode"], entry_hour=c["entry_hour"],
                  buffer_pips=c["buffer_pips"], min_range_pips=20.0,
                  max_range_pips=c["max_range_pips"], sl_mode=c["sl_mode"],
                  sl_atr_mult=1.5, adx_period=14)
    cfg = M1Config(pair=pair, spread_pips=spread, max_sl_pips=50.0,
                   strat_exit=False, friday_close_hour_utc=None,
                   daily_dd_pct=None, total_dd_pct=None,
                   year_from=2015, year_to=2021,
                   strategy_params=params, **EXITS[c["exit"]])
    return M1Backtest(AsianRangeStrategy, csv_path, cfg).run()


if __name__ == "__main__":
    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    combos = [c for c in combos if not (c["exit"] == "band" and c["mode"] == "breakout")]
    rows = []
    for pair in PAIRS:
        print(f"{pair}: {len(combos)} configs", flush=True)
        for i, c in enumerate(combos, 1):
            s = run_one(pair, c)
            worst = min(s["by_year"].values()) if s["by_year"] else 0.0
            rows.append({"pair": pair, "key": config_key(c), "n": s["trades"],
                         "pf": round(s["profit_factor"], 3),
                         "net": round(s["net_usd"], 2),
                         "pos_years": s["positive_years"],
                         "worst_year": round(worst, 2)})
            if i % 20 == 0:
                print(f"  {i}/{len(combos)}", flush=True)

    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # Cross-pair view: configs by minimum PF across the 3 pairs
    by_key: dict[str, list] = {}
    for r in rows:
        by_key.setdefault(r["key"], []).append(r)
    scored = []
    for k, rs in by_key.items():
        if len(rs) == len(PAIRS):
            scored.append((min(r["pf"] for r in rs),
                           sum(r["pos_years"] for r in rs), k, rs))
    scored.sort(reverse=True)
    print("\nTop 10 configs by MIN PF across pairs (need min PF >= 1.15 to matter):")
    for min_pf, sum_py, k, rs in scored[:10]:
        detail = "  ".join(f"{r['pair']}:pf{r['pf']:.2f}/n{r['n']}/posY{r['pos_years']}"
                           for r in rs)
        print(f"  minPF={min_pf:.2f} {k:40s} {detail}")
    print(f"\nFull results -> {OUT}")
