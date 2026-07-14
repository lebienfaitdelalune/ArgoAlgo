"""M1-fidelity replay of the deployed MR config.

Usage: python3 m1_replay.py [--no-trail] [--favorable] [--period 2015-2022|2023-2025]

Entries come from the H1 engine (entry decisions happen at bar close — sound at
any granularity); position management (SL/TP/trail/strategy-exit) is replayed
minute-by-minute. Default ordering is conservative: SL/TP tested against the M1
bar BEFORE the trail moves on that bar.
"""
import datetime as dt
import statistics
import sys
from collections import defaultdict
from pathlib import Path

PROJ = Path("/Users/alberto/Documents/ArgoAlgo")
sys.path.insert(0, str(PROJ))

from backtest.data_prep import iter_m1_rows
from backtest.engine import Backtest, BacktestConfig, PIP_SIZE, PIP_VALUE_PER_UNIT, \
    COMMISSION_PER_1K_ROUND_TRIP, load_h1_csv
from backtest.indicators import bollinger as bb_calc
from strategies.mean_reversion import MeanReversionStrategy

NO_TRAIL = "--no-trail" in sys.argv
FAVORABLE = "--favorable" in sys.argv
NO_HALT = "--no-halt" in sys.argv
PERIOD = "2015-2022" if "--period" in sys.argv and "2015-2022" in sys.argv else "2023-2025"
if PERIOD == "2015-2022":
    CSV = PROJ / "data" / "eurusd_h1_utc_2015_2022.csv"
    YEARS = range(2015, 2023)
else:
    CSV = PROJ / "data" / "eurusd_h1_utc.csv"
    YEARS = range(2023, 2026)

PARAMS = {
    "bollinger_period": 15, "bollinger_deviation": 2.5, "rsi_period": 14,
    "rsi_oversold": 35.0, "rsi_overbought": 65.0,
    "adx_filter_period": 14, "adx_filter_threshold": 30.0,
    "sl_atr_multiplier": 1.0, "min_sl_pips": 0.0, "max_atr_pips": 150.0,
}


class RecordingBacktest(Backtest):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.open_records = []

    def _open_position(self, ts, signal):
        super()._open_position(ts, signal)
        if self.position is not None and self.position.entry_time == ts:
            p = self.position
            self.open_records.append(dict(
                entry_time=p.entry_time, direction=p.direction,
                entry_price=p.entry_price, sl_price=p.sl_price,
                tp_price=p.tp_price, volume=p.volume_units,
                trail_trigger_pips=p.trail_trigger_pips,
                trail_distance_pips=p.trail_distance_pips,
            ))


cfg = BacktestConfig(initial_balance=698.0, risk_per_trade_pct=1.0, max_sl_pips=50.0,
    session_start_utc=8, session_end_utc=16, friday_close_hour_utc=15,
    daily_dd_pct=None if NO_HALT else 5.0, total_dd_pct=None if NO_HALT else 10.0,
    trailing_enabled=not NO_TRAIL, strategy_params=PARAMS)
bt = RecordingBacktest(MeanReversionStrategy, CSV, cfg)
h1 = bt.run()
print(f"[{PERIOD}] H1 engine (trail={'off' if NO_TRAIL else 'on'}): "
      f"n={h1['trades']} pf={h1['profit_factor']:.2f} net=${h1['net_usd']:+.2f} "
      f"maxDD={h1['max_dd_pct']:.1f}% halted={bt.halted}")

times, opens, highs, lows, closes = load_h1_csv(CSV)
_, bb_mid, _ = bb_calc(closes, PARAMS["bollinger_period"], PARAMS["bollinger_deviation"])
mid_by_hour = dict(zip(times, bb_mid))
close_by_hour = dict(zip(times, closes))

records = sorted(bt.open_records, key=lambda r: r["entry_time"])
trades_out = []
skipped_overlap = 0
ri = 0
pos = None


def close_pos(ts, price, reason):
    global pos
    r = pos["rec"]
    pips = ((price - r["entry_price"]) if r["direction"] == "Buy"
            else (r["entry_price"] - price)) / PIP_SIZE
    usd = pips * PIP_VALUE_PER_UNIT * r["volume"] - \
        COMMISSION_PER_1K_ROUND_TRIP * (r["volume"] / 1000.0)
    trades_out.append(dict(rec=r, exit_time=ts, pips=pips, usd=usd, reason=reason))
    pos = None


def update_trail(r, h, l):
    if NO_TRAIL:
        return
    profit_pips = ((h - r["entry_price"]) if r["direction"] == "Buy"
                   else (r["entry_price"] - l)) / PIP_SIZE
    if profit_pips < r["trail_trigger_pips"]:
        return
    if not pos["trail_active"]:
        pos["sl"] = r["entry_price"]
        pos["trail_active"] = True
    dist = r["trail_distance_pips"] * PIP_SIZE
    if r["direction"] == "Buy":
        new_sl = h - dist
        if new_sl > pos["sl"]:
            pos["sl"] = new_sl
    else:
        new_sl = l + dist
        if new_sl < pos["sl"]:
            pos["sl"] = new_sl


prev_hour = None
for y in YEARS:
    for ts, o, h, l, c in iter_m1_rows(PROJ / "data" / f"eurusd_{y}.zip"):
        hour = ts.replace(minute=0, second=0, microsecond=0)

        if pos is not None and prev_hour is not None and hour != prev_hour:
            mid = mid_by_hour.get(prev_hour)
            h1_close = close_by_hour.get(prev_hour)
            if mid is not None and h1_close is not None and mid == mid:
                r = pos["rec"]
                if (r["direction"] == "Buy" and h1_close >= mid) or \
                   (r["direction"] == "Sell" and h1_close <= mid):
                    close_pos(prev_hour + dt.timedelta(hours=1), h1_close, "StratExit")
        prev_hour = hour

        while pos is None and ri < len(records):
            start = records[ri]["entry_time"] + dt.timedelta(hours=1)
            if ts < start:
                break
            rec = records[ri]
            ri += 1
            pos = dict(rec=rec, sl=rec["sl_price"], tp=rec["tp_price"], trail_active=False)

        if pos is None:
            continue
        r = pos["rec"]

        if FAVORABLE:
            update_trail(r, h, l)

        if r["direction"] == "Buy":
            if l <= pos["sl"]:
                close_pos(ts, pos["sl"], "Trail" if pos["trail_active"] else "SL")
            elif pos["tp"] > 0 and h >= pos["tp"]:
                close_pos(ts, pos["tp"], "TP")
        else:
            if h >= pos["sl"]:
                close_pos(ts, pos["sl"], "Trail" if pos["trail_active"] else "SL")
            elif pos["tp"] > 0 and l <= pos["tp"]:
                close_pos(ts, pos["tp"], "TP")

        if pos is None:
            while ri < len(records) and \
                    records[ri]["entry_time"] + dt.timedelta(hours=1) <= ts:
                skipped_overlap += 1
                ri += 1
            continue

        if ts.weekday() == 4 and ts.hour >= 15:
            close_pos(ts, c, "FriClose")
            continue

        if not FAVORABLE:
            update_trail(r, h, l)

if pos is not None:
    close_pos(prev_hour, close_by_hour.get(prev_hour, pos["rec"]["entry_price"]), "EndOfData")

n = len(trades_out)
if n == 0:
    print("M1 replay: no trades")
    sys.exit(0)
net = sum(t["usd"] for t in trades_out)
wins = [t for t in trades_out if t["usd"] > 0]
gw = sum(t["usd"] for t in wins)
gl = -sum(t["usd"] for t in trades_out if t["usd"] <= 0)
pf = gw / gl if gl > 0 else float("inf")
print(f"M1 replay: n={n} (skipped {skipped_overlap}) wr={len(wins)/n*100:.1f}% "
      f"pf={pf:.2f} net=${net:+.2f} exp=${net/n:+.3f}")

reasons = defaultdict(lambda: [0, 0.0])
for t in trades_out:
    reasons[t["reason"]][0] += 1
    reasons[t["reason"]][1] += t["usd"]
for r, (cnt, tot) in sorted(reasons.items(), key=lambda kv: -kv[1][1]):
    print(f"  {r:10s} n={cnt:3d} net=${tot:+8.2f}")

by_year = defaultdict(float)
for t in trades_out:
    by_year[t["rec"]["entry_time"].year] += t["usd"]
print("Per-year M1 net: " + "  ".join(f"{y}: ${v:+.0f}" for y, v in sorted(by_year.items())))
