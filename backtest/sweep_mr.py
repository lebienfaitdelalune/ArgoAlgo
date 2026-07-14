"""
sweep_mr.py
MR parameter sweep + walk-forward validation.

Approach:
  1. Run every combination on the FULL 2023-2025 dataset.
  2. Split each run's trade list into IS (2023-2024) and OOS (2025).
  3. Rank configs by IS net P/L. Take top N.
  4. Score the top N on OOS.
  5. Report the top configs by OOS expectancy AND IS-stable expectancy.
     A config is "robust" if it is profitable in IS AND OOS.

This tells us:
  - Does any MR config show consistent edge across train/test?
  - If yes, what params? → deployment candidate
  - If no, MR doesn't generalise → move to Option B
"""

from __future__ import annotations

import csv
import datetime as dt
import itertools
import sys
import time
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from backtest.engine import Backtest, BacktestConfig
from strategies.mean_reversion import MeanReversionStrategy

CSV_PATH = _PROJ_ROOT / "data" / "eurusd_h1_utc.csv"
OUT_PATH = _PROJ_ROOT / "data" / "MR_SWEEP.csv"

# In-sample / out-of-sample boundary
OOS_START = dt.datetime(2025, 1, 1)


# ---------------------------------------------------------------------------
# Search grid
# ---------------------------------------------------------------------------

GRID = {
    "bollinger_period":      [15, 20, 25, 30],
    "bollinger_deviation":   [1.5, 2.0, 2.5],
    "rsi_oversold":          [25.0, 30.0, 35.0],   # overbought = 100 - oversold
    "adx_filter_threshold":  [20.0, 25.0, 30.0],
    "sl_atr_multiplier":     [1.0, 1.5, 2.0],
}

# Fixed params (not swept)
FIXED = {
    "rsi_period": 14,
    "adx_filter_period": 14,
    "min_sl_pips": 0.0,
    "max_atr_pips": 1e9,
}


def grid_combos():
    keys = list(GRID.keys())
    for combo in itertools.product(*[GRID[k] for k in keys]):
        d = dict(zip(keys, combo))
        d["rsi_overbought"] = 100.0 - d["rsi_oversold"]
        d.update(FIXED)
        yield d


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def split_trades(trades, oos_start: dt.datetime):
    is_trades = [t for t in trades if t.entry_time < oos_start]
    oos_trades = [t for t in trades if t.entry_time >= oos_start]
    return is_trades, oos_trades


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


# ---------------------------------------------------------------------------
# Run one config
# ---------------------------------------------------------------------------

def run_config(strategy_params: dict) -> list:
    cfg = BacktestConfig(
        initial_balance=698.0,
        risk_per_trade_pct=1.0,
        max_sl_pips=None,
        min_sl_pips=None,
        max_trades_per_day=None,
        post_loss_cooldown_hours=None,
        daily_dd_pct=None,
        total_dd_pct=None,
        session_start_utc=None,
        session_end_utc=None,
        friday_close_hour_utc=None,
        trailing_enabled=True,
        strategy_params=strategy_params,
    )
    bt = Backtest(MeanReversionStrategy, CSV_PATH, cfg)
    bt.run()
    return bt.trades


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def main(top_n: int = 10) -> None:
    combos = list(grid_combos())
    print(f"Sweeping {len(combos)} MR configs over {OOS_START.year}-1 IS/OOS split...")
    print()

    rows = []
    t0 = time.time()
    for i, params in enumerate(combos, 1):
        trades = run_config(params)
        is_t, oos_t = split_trades(trades, OOS_START)
        is_s = stats(is_t)
        oos_s = stats(oos_t)
        rows.append({
            "params": params,
            "is": is_s,
            "oos": oos_s,
        })
        if i % 25 == 0 or i == len(combos):
            elapsed = time.time() - t0
            rate = i / elapsed
            eta = (len(combos) - i) / rate
            print(f"  [{i:>3}/{len(combos)}] {rate:.1f}/s  ETA {eta:.0f}s")

    # Persist all results
    with open(OUT_PATH, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "bb_period", "bb_dev", "rsi_os", "adx_thr", "sl_atr",
            "is_n", "is_wr", "is_pf", "is_net", "is_exp",
            "oos_n", "oos_wr", "oos_pf", "oos_net", "oos_exp",
        ])
        for r in rows:
            p = r["params"]
            w.writerow([
                p["bollinger_period"], p["bollinger_deviation"], p["rsi_oversold"],
                p["adx_filter_threshold"], p["sl_atr_multiplier"],
                r["is"]["n"], f"{r['is']['wr']:.1f}", f"{r['is']['pf']:.2f}",
                f"{r['is']['net']:.2f}", f"{r['is']['exp']:.4f}",
                r["oos"]["n"], f"{r['oos']['wr']:.1f}", f"{r['oos']['pf']:.2f}",
                f"{r['oos']['net']:.2f}", f"{r['oos']['exp']:.4f}",
            ])

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    def fmt(r):
        p = r["params"]
        return (
            f"bb={p['bollinger_period']:>2d}/{p['bollinger_deviation']:.1f} "
            f"rsi={int(p['rsi_oversold']):>2d}/{int(p['rsi_overbought']):>2d} "
            f"adx={int(p['adx_filter_threshold']):>2d} "
            f"sl={p['sl_atr_multiplier']:.1f} | "
            f"IS n={r['is']['n']:>3d} pf={r['is']['pf']:.2f} net=${r['is']['net']:>+7.2f} | "
            f"OOS n={r['oos']['n']:>3d} pf={r['oos']['pf']:.2f} net=${r['oos']['net']:>+7.2f}"
        )

    print()
    print("=" * 100)
    print(f"TOP {top_n} BY IS NET (the in-sample winners — does their edge generalise?)")
    print("=" * 100)
    by_is = sorted(rows, key=lambda r: r["is"]["net"], reverse=True)
    for r in by_is[:top_n]:
        print(fmt(r))

    print()
    print("=" * 100)
    print(f"TOP {top_n} BY OOS NET (best OOS performance — likely cherry-picked, beware)")
    print("=" * 100)
    by_oos = sorted(rows, key=lambda r: r["oos"]["net"], reverse=True)
    for r in by_oos[:top_n]:
        print(fmt(r))

    print()
    print("=" * 100)
    print("ROBUST: positive in BOTH IS and OOS, sorted by OOS expectancy")
    print("=" * 100)
    robust = [r for r in rows if r["is"]["net"] > 0 and r["oos"]["net"] > 0]
    if not robust:
        print("  (none — no MR config is profitable in BOTH train AND test)")
    else:
        for r in sorted(robust, key=lambda r: r["oos"]["exp"], reverse=True)[:top_n]:
            print(fmt(r))

    print()
    print(f"Sweep CSV: {OUT_PATH}")
    print(f"Total configs: {len(rows)}")
    print(f"  Profitable in IS only:  {sum(1 for r in rows if r['is']['net'] > 0 and r['oos']['net'] <= 0)}")
    print(f"  Profitable in OOS only: {sum(1 for r in rows if r['is']['net'] <= 0 and r['oos']['net'] > 0)}")
    print(f"  Profitable in BOTH:     {sum(1 for r in rows if r['is']['net'] > 0 and r['oos']['net'] > 0)}")


if __name__ == "__main__":
    main()
