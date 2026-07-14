"""
run.py
Backtest launcher — runs each strategy under multiple configs and prints a report.

Usage:
    python3 -m backtest.run                    # run all strategies, all configs
    python3 -m backtest.run TF UNCAPPED        # one strategy, one config
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from backtest.engine import Backtest, BacktestConfig
from strategies.breakout import BreakoutStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend_following import TrendFollowingStrategy

CSV_PATH = _PROJ_ROOT / "data" / "eurusd_h1_utc.csv"
TRADES_DIR = _PROJ_ROOT / "data" / "trades"


STRATEGIES = {
    "TF": TrendFollowingStrategy,
    "MR": MeanReversionStrategy,
    "BO": BreakoutStrategy,
}


# ---------------------------------------------------------------------------
# Configurations — sweep what matters
# ---------------------------------------------------------------------------

def make_configs(strategy_key: str) -> dict[str, BacktestConfig]:
    """Build a set of configs to compare for one strategy."""
    base_params = {
        "TF": {
            "fast_ema_period": 12, "slow_ema_period": 26, "adx_period": 14,
            "adx_threshold": 15.0, "sl_atr_multiplier": 2.0, "tp_rr": 2.0,
            "min_sl_pips": 0.0, "max_atr_pips": 1e9,
        },
        "MR": {
            "bollinger_period": 20, "bollinger_deviation": 2.0,
            "rsi_period": 14, "rsi_oversold": 35.0, "rsi_overbought": 65.0,
            "adx_filter_period": 14, "adx_filter_threshold": 25.0,
            "sl_atr_multiplier": 1.5, "min_sl_pips": 0.0, "max_atr_pips": 1e9,
        },
        "BO": {
            "donchian_period": 40, "atr_period": 14, "atr_bo_period": 14,
            "atr_min_threshold": 0.0005, "sl_atr_multiplier": 1.5, "tp_rr": 3.0,
            "adx_threshold": 15.0, "min_sl_pips": 0.0, "max_atr_pips": 1e9,
        },
    }[strategy_key]

    cfgs = {}

    # 1) UNCAPPED — pure strategy expectancy: no SL cap, no throttles, no session.
    #    Answers "does this strategy have any edge at all?"
    cfgs["UNCAPPED"] = BacktestConfig(
        initial_balance=698.0, risk_per_trade_pct=1.0,
        max_sl_pips=None, min_sl_pips=None,
        max_trades_per_day=None, post_loss_cooldown_hours=None,
        daily_dd_pct=None, total_dd_pct=None,
        session_start_utc=None, session_end_utc=None, friday_close_hour_utc=None,
        trailing_enabled=True,
        strategy_params=dict(base_params),
    )

    # 2) LIVE_CONFIG — current production config (from utils/constants.py defaults)
    cfgs["LIVE_CONFIG"] = BacktestConfig(
        initial_balance=698.0, risk_per_trade_pct=1.0,
        max_sl_pips=20.0, min_sl_pips=20.0,
        max_trades_per_day=2, post_loss_cooldown_hours=4.0,
        daily_dd_pct=5.0, total_dd_pct=10.0,
        session_start_utc=7, session_end_utc=13, friday_close_hour_utc=12,
        trailing_enabled=True,
        strategy_params=dict(base_params),
    )

    # 3) RELAXED_SL — uncap SL but keep session and risk caps.
    #    Tests whether the SL cap is the killing constraint.
    cfgs["RELAXED_SL"] = BacktestConfig(
        initial_balance=698.0, risk_per_trade_pct=1.0,
        max_sl_pips=50.0, min_sl_pips=10.0,
        max_trades_per_day=None, post_loss_cooldown_hours=None,
        daily_dd_pct=10.0, total_dd_pct=20.0,
        session_start_utc=7, session_end_utc=20, friday_close_hour_utc=20,
        trailing_enabled=True,
        strategy_params=dict(base_params),
    )

    return cfgs


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def fmt_summary(name: str, cfg_name: str, summary: dict) -> str:
    return (
        f"{name:>3} | {cfg_name:<11} | "
        f"trades={summary['trades']:>4d} "
        f"win%={summary['win_rate_pct']:>5.1f} "
        f"PF={summary['profit_factor']:>5.2f} "
        f"net=${summary['net_usd']:>+8.2f} "
        f"final=${summary['final_balance']:>7.2f} "
        f"DD={summary['max_dd_pct']:>5.1f}% "
        f"avgW=${summary['avg_win_usd']:.2f} avgL=${summary['avg_loss_usd']:.2f} "
        f"E=${summary['expectancy_usd']:+.3f}"
    )


def write_trade_csv(strategy_key: str, cfg_name: str, trades) -> Path:
    TRADES_DIR.mkdir(parents=True, exist_ok=True)
    out = TRADES_DIR / f"{strategy_key}_{cfg_name}.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entry_time", "exit_time", "direction", "strategy",
                    "entry_price", "exit_price", "sl_price", "tp_price",
                    "sl_pips", "tp_pips", "volume_units", "pnl_pips",
                    "pnl_usd", "exit_reason", "bars_held"])
        for t in trades:
            w.writerow([
                t.entry_time.isoformat(), t.exit_time.isoformat(),
                t.direction, t.strategy,
                f"{t.entry_price:.5f}", f"{t.exit_price:.5f}",
                f"{t.sl_price:.5f}", f"{t.tp_price:.5f}",
                f"{t.sl_pips:.1f}", f"{t.tp_pips:.1f}",
                int(t.volume_units), f"{t.pnl_pips:+.2f}", f"{t.pnl_usd:+.4f}",
                t.exit_reason, t.bars_held,
            ])
    return out


def main(argv: list[str]) -> None:
    only_strategy = argv[0].upper() if len(argv) >= 1 else None
    only_config = argv[1].upper() if len(argv) >= 2 else None

    print(f"Loading bars from: {CSV_PATH}")
    print()
    header = (
        f"{'STR':>3} | {'CONFIG':<11} | "
        f"{'STATS':<55} {'extra'}"
    )
    print(header)
    print("-" * len(header))

    for strat_key, strat_cls in STRATEGIES.items():
        if only_strategy and strat_key != only_strategy:
            continue
        configs = make_configs(strat_key)
        for cfg_name, cfg in configs.items():
            if only_config and cfg_name != only_config:
                continue
            bt = Backtest(strat_cls, CSV_PATH, cfg)
            summary = bt.run()
            print(fmt_summary(strat_key, cfg_name, summary))
            write_trade_csv(strat_key, cfg_name, bt.trades)

    print()
    print(f"Trade lists: {TRADES_DIR}")


if __name__ == "__main__":
    main(sys.argv[1:])
