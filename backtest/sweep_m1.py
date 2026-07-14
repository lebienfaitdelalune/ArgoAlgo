"""
sweep_m1.py
Disciplined MR-family sweep at M1 fidelity.

Protocol (do not violate):
  - IS (in-sample) = 2015-2021. Sweep and select ONLY here.
  - OOS (out-of-sample) = 2022-2025. Evaluated ONCE, on <=5 survivors,
    with --oos. Never iterate on OOS results.
  - Survivors are ranked by robustness (positive years, worst year),
    NOT by net profit.
  - Caveat recorded: 2023-2025 has been examined extensively in prior
    sessions, so OOS is not fully virgin. Demand consistency everywhere.

Selection gates (IS): PF >= 1.15, n >= 250 (~36/yr), positive years >= 6/7.

Usage:
  python3 -m backtest.sweep_m1            # IS sweep -> data/M1_SWEEP_IS.csv
  python3 -m backtest.sweep_m1 --oos K1 K2 ...   # single-shot OOS on named configs
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
from strategies.mean_reversion import MeanReversionStrategy

H1_CSV = _PROJ_ROOT / "data" / "eurusd_h1_utc_2015_2025.csv"
OUT_IS = _PROJ_ROOT / "data" / "M1_SWEEP_IS.csv"

EXIT_STYLES = {
    "bandTP":    dict(tp_mode="band", strat_exit=True,  trail_trigger_atr=0.0, trail_distance_atr=0.0),
    "rr15":      dict(tp_mode="rr", tp_rr=1.5, strat_exit=False, trail_trigger_atr=0.0, trail_distance_atr=0.0),
    "rr20":      dict(tp_mode="rr", tp_rr=2.0, strat_exit=False, trail_trigger_atr=0.0, trail_distance_atr=0.0),
    "bandTPtrW": dict(tp_mode="band", strat_exit=True,  trail_trigger_atr=1.0, trail_distance_atr=1.0),
}

GRID = dict(
    bollinger_period=[15, 20],
    bollinger_deviation=[2.0, 2.5],
    rsi=[(30.0, 70.0), (35.0, 65.0)],
    adx=[25.0, 100.0],          # 100 = filter off
    sl_mult=[1.0, 1.5],
    session=[None, (8, 16)],
    exit=list(EXIT_STYLES),
)


def config_key(combo: dict) -> str:
    sess = "24h" if combo["session"] is None else f"{combo['session'][0]}-{combo['session'][1]}"
    return (f"bb{combo['bollinger_period']}d{combo['bollinger_deviation']}"
            f"_rsi{int(combo['rsi'][0])}_adx{int(combo['adx'])}"
            f"_sl{combo['sl_mult']}_{sess}_{combo['exit']}")


def run_one(combo: dict, year_from: int, year_to: int) -> dict:
    params = {
        "bollinger_period": combo["bollinger_period"],
        "bollinger_deviation": combo["bollinger_deviation"],
        "rsi_period": 14,
        "rsi_oversold": combo["rsi"][0],
        "rsi_overbought": combo["rsi"][1],
        "adx_filter_period": 14,
        "adx_filter_threshold": combo["adx"],
        "sl_atr_multiplier": combo["sl_mult"],
        "min_sl_pips": 0.0,
        "max_atr_pips": 150.0,
    }
    style = EXIT_STYLES[combo["exit"]]
    sess = combo["session"]
    cfg = M1Config(
        max_sl_pips=50.0,
        session_start_utc=sess[0] if sess else None,
        session_end_utc=sess[1] if sess else None,
        friday_close_hour_utc=None,
        daily_dd_pct=None, total_dd_pct=None,   # halts off: measure raw edge
        year_from=year_from, year_to=year_to,
        strategy_params=params, **style,
    )
    return M1Backtest(MeanReversionStrategy, H1_CSV, cfg).run()


def all_combos():
    keys = list(GRID)
    for values in itertools.product(*GRID.values()):
        yield dict(zip(keys, values))


def sweep_is() -> None:
    combos = list(all_combos())
    print(f"IS sweep 2015-2021: {len(combos)} configs")
    rows = []
    for i, combo in enumerate(combos, 1):
        s = run_one(combo, 2015, 2021)
        worst = min(s["by_year"].values()) if s["by_year"] else 0.0
        rows.append({
            "key": config_key(combo), "n": s["trades"],
            "pf": round(s["profit_factor"], 3),
            "net": round(s["net_usd"], 2),
            "exp": round(s["expectancy_usd"], 3),
            "wr": round(s["win_rate_pct"], 1),
            "max_dd": round(s["max_dd_pct"], 1),
            "pos_years": s["positive_years"], "years": s["years"],
            "worst_year": round(worst, 2),
            "drought_d": s["longest_drought_days"],
        })
        if i % 16 == 0:
            print(f"  {i}/{len(combos)}", flush=True)

    with open(OUT_IS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    passed = [r for r in rows if r["pf"] >= 1.15 and r["n"] >= 250
              and r["pos_years"] >= 6]
    passed.sort(key=lambda r: (-r["pos_years"], -r["worst_year"], -r["pf"]))
    print(f"\nGates passed (PF>=1.15, n>=250, pos_years>=6/7): {len(passed)}")
    for r in passed[:15]:
        print(f"  {r['key']:44s} n={r['n']:4d} pf={r['pf']:.2f} net=${r['net']:+8.2f} "
              f"posY={r['pos_years']}/7 worst=${r['worst_year']:+.0f} dd={r['max_dd']}%")
    print(f"\nFull results -> {OUT_IS}")


def oos(keys: list[str]) -> None:
    print("OOS single shot 2022-2025 — these results are FINAL for these configs.")
    by_key = {config_key(c): c for c in all_combos()}
    for k in keys:
        combo = by_key.get(k)
        if combo is None:
            print(f"  {k}: unknown key")
            continue
        s = run_one(combo, 2022, 2025)
        yr = "  ".join(f"{y}:${v:+.0f}" for y, v in s["by_year"].items())
        print(f"  {k}\n    n={s['trades']} pf={s['profit_factor']:.2f} "
              f"net=${s['net_usd']:+.2f} wr={s['win_rate_pct']:.1f}% "
              f"maxDD={s['max_dd_pct']:.1f}%  {yr}")


if __name__ == "__main__":
    if "--oos" in sys.argv:
        oos(sys.argv[sys.argv.index("--oos") + 1:])
    else:
        sweep_is()
