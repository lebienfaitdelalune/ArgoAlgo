"""
verify_candidate.py
Drill down on the top MR candidate to verify edge quality.

Checks:
  - Monthly P/L breakdown (is performance concentrated in a few months?)
  - Trade-size distribution (any single trade > 5% of net?)
  - Equity-curve shape (smooth or lumpy?)
  - Max drawdown depth and duration
  - Win/loss streaks
  - Direction balance (Buy vs Sell)
  - Per-day-of-week stats
  - Sensitivity: ±1 step on each param
"""

from __future__ import annotations

import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from backtest.engine import Backtest, BacktestConfig
from backtest.sweep_mr import run_config
from strategies.mean_reversion import MeanReversionStrategy

CSV_PATH = _PROJ_ROOT / "data" / "eurusd_h1_utc.csv"

# Top candidate from the sweep
CANDIDATE = {
    "bollinger_period": 15,
    "bollinger_deviation": 2.5,
    "rsi_period": 14,
    "rsi_oversold": 35.0,
    "rsi_overbought": 65.0,
    "adx_filter_period": 14,
    "adx_filter_threshold": 30.0,
    "sl_atr_multiplier": 1.0,
    "min_sl_pips": 0.0,
    "max_atr_pips": 1e9,
}


def fmt_summary(label, trades, initial_balance):
    n = len(trades)
    if n == 0:
        return f"{label}: no trades"
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    gw = sum(t.pnl_usd for t in wins)
    gl = -sum(t.pnl_usd for t in losses)
    net = gw - gl
    return (
        f"{label}: n={n} wr={len(wins)/n*100:.1f}% pf={(gw/gl) if gl>0 else float('inf'):.2f} "
        f"net=${net:+.2f} exp=${net/n:+.3f} avgW=${gw/len(wins):.2f} avgL=${gl/len(losses):.2f}"
    )


def monthly_pnl(trades):
    by_month = defaultdict(float)
    for t in trades:
        key = t.entry_time.strftime("%Y-%m")
        by_month[key] += t.pnl_usd
    return dict(sorted(by_month.items()))


def equity_curve_stats(trades, start_balance):
    eq = [start_balance]
    peak = start_balance
    max_dd = 0.0
    max_dd_pct = 0.0
    dd_start = None
    longest_dd_days = 0
    cur_dd_days = 0
    bal = start_balance
    last_peak_time = trades[0].entry_time if trades else dt.datetime.now()
    for t in trades:
        bal += t.pnl_usd
        eq.append(bal)
        if bal > peak:
            peak = bal
            longest_dd_days = max(longest_dd_days, cur_dd_days)
            cur_dd_days = 0
            last_peak_time = t.exit_time
        else:
            dd = peak - bal
            dd_pct = dd / peak * 100.0
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = dd_pct
            cur_dd_days = (t.exit_time - last_peak_time).days
    longest_dd_days = max(longest_dd_days, cur_dd_days)
    return {
        "max_dd_usd": max_dd,
        "max_dd_pct": max_dd_pct,
        "longest_dd_days": longest_dd_days,
        "final_balance": bal,
    }


def streaks(trades):
    cur_w = cur_l = 0
    max_w = max_l = 0
    for t in trades:
        if t.pnl_usd > 0:
            cur_w += 1
            cur_l = 0
            max_w = max(max_w, cur_w)
        else:
            cur_l += 1
            cur_w = 0
            max_l = max(max_l, cur_l)
    return max_w, max_l


def main():
    print(f"Top candidate params: {CANDIDATE}")
    print()
    trades = run_config(CANDIDATE)
    initial_balance = 698.0

    # Overall + per-year + per-half stats
    print("=" * 80)
    print(fmt_summary("ALL", trades, initial_balance))
    for label, (s, e) in [
        ("2023", (dt.datetime(2023,1,1), dt.datetime(2024,1,1))),
        ("2024", (dt.datetime(2024,1,1), dt.datetime(2025,1,1))),
        ("2025", (dt.datetime(2025,1,1), dt.datetime(2026,1,1))),
        ("H1-23", (dt.datetime(2023,1,1), dt.datetime(2023,7,1))),
        ("H2-23", (dt.datetime(2023,7,1), dt.datetime(2024,1,1))),
        ("H1-24", (dt.datetime(2024,1,1), dt.datetime(2024,7,1))),
        ("H2-24", (dt.datetime(2024,7,1), dt.datetime(2025,1,1))),
        ("H1-25", (dt.datetime(2025,1,1), dt.datetime(2025,7,1))),
        ("H2-25", (dt.datetime(2025,7,1), dt.datetime(2026,1,1))),
    ]:
        sub = [t for t in trades if s <= t.entry_time < e]
        print(fmt_summary(label, sub, initial_balance))

    print()
    print("=" * 80)
    print("MONTHLY P/L (no compounding)")
    print("=" * 80)
    months = monthly_pnl(trades)
    pos_months = sum(1 for v in months.values() if v > 0)
    neg_months = sum(1 for v in months.values() if v < 0)
    for k, v in months.items():
        bar = "+" * max(1, int(v)) if v > 0 else "-" * max(1, int(-v))
        print(f"  {k}: ${v:>+7.2f}  {bar}")
    print(f"  positive months: {pos_months}/{len(months)}  ({pos_months/len(months)*100:.0f}%)")

    print()
    print("=" * 80)
    print("EQUITY CURVE")
    print("=" * 80)
    eq = equity_curve_stats(trades, initial_balance)
    print(f"  initial:           ${initial_balance:.2f}")
    print(f"  final:             ${eq['final_balance']:.2f}")
    print(f"  max drawdown:      ${eq['max_dd_usd']:.2f} ({eq['max_dd_pct']:.1f}%)")
    print(f"  longest DD period: {eq['longest_dd_days']} days")

    print()
    print("=" * 80)
    print("STREAKS / DIRECTION / OUTLIERS")
    print("=" * 80)
    max_w, max_l = streaks(trades)
    print(f"  max win streak:  {max_w}")
    print(f"  max loss streak: {max_l}")
    buys = [t for t in trades if t.direction == "Buy"]
    sells = [t for t in trades if t.direction == "Sell"]
    print(f"  Buy:  n={len(buys)} net=${sum(t.pnl_usd for t in buys):+.2f}")
    print(f"  Sell: n={len(sells)} net=${sum(t.pnl_usd for t in sells):+.2f}")

    sorted_pnl = sorted(trades, key=lambda t: t.pnl_usd)
    print(f"  worst trade:  ${sorted_pnl[0].pnl_usd:+.2f}  on {sorted_pnl[0].entry_time}")
    print(f"  best trade:   ${sorted_pnl[-1].pnl_usd:+.2f}  on {sorted_pnl[-1].entry_time}")
    total = sum(t.pnl_usd for t in trades)
    top5_share = sum(t.pnl_usd for t in sorted_pnl[-5:]) / total * 100
    bot5_share = sum(t.pnl_usd for t in sorted_pnl[:5]) / abs(total) * 100
    print(f"  top 5 winners contribute {top5_share:.1f}% of net P/L")
    print(f"  worst 5 losers are {bot5_share:.1f}% of |net P/L|")

    # Exit reason mix
    from collections import Counter
    ec = Counter(t.exit_reason for t in trades)
    print(f"  exit reasons: {dict(ec.most_common())}")

    # Day-of-week
    print()
    print("=" * 80)
    print("DAY-OF-WEEK / HOUR-OF-DAY")
    print("=" * 80)
    by_dow = defaultdict(list)
    by_hour = defaultdict(list)
    for t in trades:
        by_dow[t.entry_time.weekday()].append(t.pnl_usd)
        by_hour[t.entry_time.hour].append(t.pnl_usd)
    days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    for d in range(7):
        if not by_dow[d]:
            continue
        s = sum(by_dow[d])
        n = len(by_dow[d])
        print(f"  {days[d]}: n={n:>3} net=${s:>+7.2f} avg=${s/n:+.3f}")
    for h in range(24):
        if not by_hour[h]:
            continue
        s = sum(by_hour[h])
        n = len(by_hour[h])
        print(f"  {h:>2}h UTC: n={n:>3} net=${s:>+7.2f} avg=${s/n:+.3f}")

    # Sensitivity: ±1 step on each parameter
    print()
    print("=" * 80)
    print("SENSITIVITY: vary one param at a time")
    print("=" * 80)
    print(f"BASELINE: {fmt_summary('', trades, initial_balance)}")
    perturbations = [
        ("bollinger_period", [10, 20, 25]),
        ("bollinger_deviation", [2.0, 3.0]),
        ("rsi_oversold", [30.0, 40.0]),  # 100-x for overbought adjusted manually
        ("adx_filter_threshold", [25.0, 35.0]),
        ("sl_atr_multiplier", [0.75, 1.5, 2.0]),
    ]
    for key, alts in perturbations:
        for v in alts:
            params = dict(CANDIDATE)
            params[key] = v
            if key == "rsi_oversold":
                params["rsi_overbought"] = 100.0 - v
            sub_trades = run_config(params)
            print(f"  {key}={v}: {fmt_summary('', sub_trades, initial_balance)}")


if __name__ == "__main__":
    main()
