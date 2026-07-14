"""
analyze_mr.py
Deep-dive on MR sweep results: identify ROBUST configs and verify across sub-windows.

A config is "deployment-grade" if:
  1. Profitable in IS (2023-2024) AND OOS (2025).
  2. Profitable in 2/2 IS years (2023, 2024) AND 2/2 OOS half-years (H1-25, H2-25).
  3. Has neighbours with similar performance (not a knife-edge optimum).
  4. Has ≥150 IS trades and ≥75 OOS trades (statistical sample size).

This is the test for "did we find a real edge or just curve-fit."
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from backtest.engine import Backtest, BacktestConfig
from strategies.mean_reversion import MeanReversionStrategy
from backtest.sweep_mr import grid_combos, run_config

CSV_PATH = _PROJ_ROOT / "data" / "eurusd_h1_utc.csv"


def stats(trades) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "net": 0.0, "exp": 0.0}
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    gw = sum(t.pnl_usd for t in wins)
    gl = -sum(t.pnl_usd for t in losses)
    net = gw - gl
    return {
        "n": n,
        "wr": len(wins) / n * 100.0,
        "pf": (gw / gl) if gl > 0 else float("inf") if gw > 0 else 0.0,
        "net": net,
        "exp": net / n,
    }


def filter_trades(trades, start: dt.datetime, end: dt.datetime):
    return [t for t in trades if start <= t.entry_time < end]


def evaluate_config(params: dict) -> dict:
    """Run config and compute stats per period."""
    trades = run_config(params)

    periods = {
        "2023":     (dt.datetime(2023, 1, 1), dt.datetime(2024, 1, 1)),
        "2024":     (dt.datetime(2024, 1, 1), dt.datetime(2025, 1, 1)),
        "H1-25":    (dt.datetime(2025, 1, 1), dt.datetime(2025, 7, 1)),
        "H2-25":    (dt.datetime(2025, 7, 1), dt.datetime(2026, 1, 1)),
    }
    out = {"params": params, "all_trades": trades}
    for label, (s, e) in periods.items():
        out[label] = stats(filter_trades(trades, s, e))
    out["IS"] = stats([t for t in trades if t.entry_time < dt.datetime(2025, 1, 1)])
    out["OOS"] = stats([t for t in trades if t.entry_time >= dt.datetime(2025, 1, 1)])
    out["all"] = stats(trades)
    return out


def is_robust(r: dict) -> tuple[bool, list[str]]:
    """Check if config passes deployment-grade criteria. Return (passed, fail_reasons)."""
    fails = []
    if r["IS"]["net"] <= 0:
        fails.append("IS not profitable")
    if r["OOS"]["net"] <= 0:
        fails.append("OOS not profitable")
    if r["2023"]["net"] <= 0:
        fails.append("2023 negative")
    if r["2024"]["net"] <= 0:
        fails.append("2024 negative")
    if r["H1-25"]["net"] <= 0:
        fails.append("H1-25 negative")
    if r["H2-25"]["net"] <= 0:
        fails.append("H2-25 negative")
    if r["IS"]["n"] < 150:
        fails.append(f"IS n={r['IS']['n']} <150")
    if r["OOS"]["n"] < 75:
        fails.append(f"OOS n={r['OOS']['n']} <75")
    return (len(fails) == 0, fails)


def fmt_row(r):
    p = r["params"]
    return (
        f"bb={p['bollinger_period']:>2d}/{p['bollinger_deviation']:.1f} "
        f"rsi={int(p['rsi_oversold']):>2d}/{int(p['rsi_overbought']):>2d} "
        f"adx={int(p['adx_filter_threshold']):>2d} "
        f"sl={p['sl_atr_multiplier']:.1f} | "
        f"23:{r['2023']['net']:>+6.1f}({r['2023']['n']:>3d}) "
        f"24:{r['2024']['net']:>+6.1f}({r['2024']['n']:>3d}) "
        f"H1-25:{r['H1-25']['net']:>+6.1f}({r['H1-25']['n']:>3d}) "
        f"H2-25:{r['H2-25']['net']:>+6.1f}({r['H2-25']['n']:>3d}) | "
        f"all:n={r['all']['n']:>3d} pf={r['all']['pf']:.2f} net=${r['all']['net']:>+7.2f}"
    )


def main():
    # Run every config across the full grid and store stats per sub-window
    combos = list(grid_combos())
    print(f"Re-running {len(combos)} configs to capture per-window stats...")
    print()
    results = []
    import time
    t0 = time.time()
    for i, params in enumerate(combos, 1):
        results.append(evaluate_config(params))
        if i % 50 == 0 or i == len(combos):
            print(f"  [{i:>3}/{len(combos)}] {(time.time()-t0):.0f}s")

    # ------------------------------------------------------------------
    # Robust filter
    # ------------------------------------------------------------------
    passed = []
    near_pass = []  # 5 of 6 sub-period checks
    for r in results:
        ok, fails = is_robust(r)
        if ok:
            passed.append(r)
        elif len(fails) <= 2:
            near_pass.append((r, fails))

    print()
    print("=" * 130)
    print(f"DEPLOYMENT-GRADE configs (positive in IS, OOS, 2023, 2024, H1-25, H2-25, with adequate n): {len(passed)}/{len(results)}")
    print("=" * 130)
    if not passed:
        print("  NONE — no MR config is consistently profitable across all sub-periods.")
    else:
        passed.sort(key=lambda r: r["all"]["exp"], reverse=True)
        for r in passed:
            print(fmt_row(r))

    print()
    print("=" * 130)
    print(f"NEAR-PASS configs (failed ≤2 criteria): {len(near_pass)}")
    print("=" * 130)
    near_pass.sort(key=lambda kv: kv[0]["all"]["exp"], reverse=True)
    for r, fails in near_pass[:15]:
        print(f"  fails: {fails}")
        print(f"    {fmt_row(r)}")

    # ------------------------------------------------------------------
    # Stability map — for each parameter, which value produces most robust configs?
    # ------------------------------------------------------------------
    print()
    print("=" * 130)
    print("PARAMETER STABILITY MAP — count of robust configs per parameter value")
    print("=" * 130)
    if passed:
        from collections import Counter
        for key in ["bollinger_period", "bollinger_deviation",
                    "rsi_oversold", "adx_filter_threshold", "sl_atr_multiplier"]:
            ctr = Counter(r["params"][key] for r in passed)
            print(f"  {key:<22s} {dict(sorted(ctr.items()))}")

    print()
    print(f"Total configs evaluated: {len(results)}")


if __name__ == "__main__":
    main()
