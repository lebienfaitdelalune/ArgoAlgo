"""
constants.py
Enums, default parameter values, and shared constants for ArgoAlgo.
"""

from enum import Enum, auto


# ---------------------------------------------------------------------------
# Direction
# ---------------------------------------------------------------------------

class Direction(Enum):
    """Trade direction signal."""
    BUY = "Buy"
    SELL = "Sell"
    NONE = "None"


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class StrategyMode(Enum):
    """How the strategy engine selects active strategies."""
    MANUAL = "Manual"
    ADX_SWITCHING = "AdxSwitching"


class StrategyName(Enum):
    """Canonical names for each strategy."""
    TREND_FOLLOWING = "TrendFollowing"
    MEAN_REVERSION = "MeanReversion"
    BREAKOUT = "Breakout"


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------

class SLType(Enum):
    """Stop-loss calculation method."""
    ATR = "ATR"
    FIXED = "Fixed"


class DrawdownStatus(Enum):
    """Result of a drawdown limit check."""
    OK = auto()
    DAILY_LIMIT_BREACHED = auto()
    TOTAL_LIMIT_BREACHED = auto()


# ---------------------------------------------------------------------------
# Bot status
# ---------------------------------------------------------------------------

class BotStatus(Enum):
    """Current operational state of the bot."""
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    HALTED = "HALTED"
    STOPPED = "STOPPED"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class LogLevel(Enum):
    """Logging verbosity levels (ordered lowest → highest severity)."""
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3


class NotificationLevel(Enum):
    """Notification priority for the notification service."""
    DEBUG = auto()
    INFO = auto()
    TRADE = auto()
    CRITICAL = auto()


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

class OrderResult(Enum):
    """Outcome of an order execution attempt."""
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


# ---------------------------------------------------------------------------
# Default parameter values
# ---------------------------------------------------------------------------

class Defaults:
    """Central repository for all default parameter values."""

    # Strategy — ALL bar-signal strategies disabled as of 2026-07-14.
    # TF/BO: PF 0.62-0.63 (3-yr backtest). MR: edge falsified 2026-07-13 — the
    # PF 1.55 H1 result was an intra-bar trailing-stop artifact (M1 replay: PF 0.87).
    # The bot now runs ONLY the cross-sectional reversal forward test (XS_* below).
    STRATEGY_MODE: str = StrategyMode.MANUAL.value
    ENABLE_TREND: bool = False
    ENABLE_MEAN_REVERSION: bool = False
    ENABLE_BREAKOUT: bool = False

    # Cross-sectional reversal FORWARD TEST (demo only — no validated edge yet).
    # Config rev_lb60_rb1_nosl: passed IS 2015-21 (PF 1.20, 6/7 years) but FAILED
    # OOS 2022-25 (PF 1.05, -$1,251 in 2025). Forward demo is the final verdict —
    # do NOT move to live money on IS stats alone.
    # Mechanics: daily at 21:00 UTC, rank basket by 1440-H1-bar (~60 trading day)
    # return; long the weakest, short the strongest, equal units, no SL/TP;
    # re-trade a leg only when its wanted direction changes. Positions hold
    # over weekends by design (Friday close is disabled in xsect mode).
    ENABLE_XSECT: bool = True
    XS_REBAL_HOUR_UTC: int = 21
    XS_LOOKBACK_BARS: int = 1440          # 60 trading days of H1 bars
    XS_UNITS_PER_LEG: int = 1000          # backtest measured at 10k/leg; 1k fits the ~$700 demo account (stats scale x0.1)
    # Backtest (no-halt) maxDD at 1k/leg ~= $151 = 21% of the $702 demo account, so
    # default 5%/10% drawdown halts would fire on normal variance and corrupt the
    # forward test. These demo-only backstops replace them while xsect mode is on.
    XS_MAX_DAILY_DD_PCT: float = 15.0
    XS_MAX_TOTAL_DD_PCT: float = 30.0

    # Risk management
    RISK_PER_TRADE_PCT: float = 1.0
    MAX_DAILY_DRAWDOWN_PCT: float = 5.0
    MAX_TOTAL_DRAWDOWN_PCT: float = 10.0
    MAX_CONCURRENT_POSITIONS: int = 1     # MR is single-position by design (until edge is verified)
    MAX_POSITIONS_PER_SYMBOL: int = 1
    MAX_SPREAD_PIPS: float = 3.0

    # Trade throttling — disabled. The cap+cooldown were tuned for losing strategies;
    # for MR the drawdown caps are the real safety net. Backtest assumes no throttle.
    MAX_TRADES_PER_DAY: int = 99          # Effectively unlimited
    POST_LOSS_COOLDOWN_HOURS: float = 0.0  # No cooldown

    # Trailing stop (global defaults — used as fallback when strategy label is unrecognised)
    TRAILING_STOP_ENABLED: bool = True
    TRAILING_STOP_TRIGGER_PIPS: float = 15.0
    TRAILING_STOP_DISTANCE_PIPS: float = 10.0
    TRAILING_STOP_MIN_STEP_PIPS: float = 3.0  # Min SL improvement before modifying (reduces API calls)

    # Strategy-specific trailing stops (ATR-based, adapts to volatility)
    # Trigger = profit in ATR multiples before trail activates
    # Distance = trail distance behind price in ATR multiples
    TF_TRAILING_TRIGGER_ATR: float = 1.0    # TF: activate at 1× ATR profit (~15-25 pips)
    TF_TRAILING_DISTANCE_ATR: float = 0.75  # TF: trail at 0.75× ATR — let trends breathe
    MR_TRAILING_TRIGGER_ATR: float = 0.5    # MR: tighter trigger — smaller mean-reversion moves
    MR_TRAILING_DISTANCE_ATR: float = 0.35  # MR: tight trail — lock profit on quick reversions
    BO_TRAILING_TRIGGER_ATR: float = 1.0    # BO: activate BE at 1×ATR — protect losers sooner
    BO_TRAILING_DISTANCE_ATR: float = 1.0   # BO: wide trail — breakouts need room to run

    # Trend Following
    TF_FAST_EMA_PERIOD: int = 12
    TF_SLOW_EMA_PERIOD: int = 26
    TF_ADX_PERIOD: int = 14
    TF_ADX_THRESHOLD: float = 15.0   # ADX >= 15 confirms a trending market on H1 (lowered from 20.0)
    TF_SL_ATR_MULTIPLIER: float = 2.0
    TF_TP_RR: float = 2.0

    # Mean Reversion — params chosen via 324-config sweep (see data/MR_DEPLOYMENT_REPORT.md).
    # 3-yr backtest: 376 trades, 65% win rate, PF 1.33, +50% net, 14% max DD.
    MR_BOLLINGER_PERIOD: int = 15
    MR_BOLLINGER_DEVIATION: float = 2.5
    MR_RSI_PERIOD: int = 14
    MR_RSI_OVERSOLD: float = 35.0
    MR_RSI_OVERBOUGHT: float = 65.0
    MR_ADX_FILTER_PERIOD: int = 14
    MR_ADX_FILTER_THRESHOLD: float = 30.0

    # Breakout
    BO_DONCHIAN_PERIOD: int = 40          # 40-bar lookback (~40h on H1) captures structural breaks; 20 was too short (intraday noise)
    BO_ATR_PERIOD: int = 14
    BO_ATR_MIN_THRESHOLD: float = 0.0005
    BO_SL_ATR_MULTIPLIER: float = 1.5    # Tightened (was 2.0x) — failed breakouts should be cut faster
    BO_TP_RR: float = 3.0                # Widened — with 20-pip SL cap + breakeven snap, let winners run farther

    # Mean Reversion SL multiplier — 1.0× ATR per the deployment-grade sweep.
    # Tighter SL means more frequent stop-outs but smaller per-loss; PF still 1.33.
    MR_SL_ATR_MULTIPLIER: float = 1.0

    # Minimum stop-loss — set to 0 so ATR drives the stop. The 20-pip floor was a relic
    # of the small-account era and made low-ATR regimes oversized. With $698+ balance,
    # ATR-driven stops produce reasonable position sizes.
    MIN_SL_PIPS: float = 0.0

    # Maximum stop-loss safety cap. The MR sweep produced p99 SL ~45 pips, so 50 is a
    # comfortable ceiling that filters extreme-volatility outliers without rejecting
    # normal setups. Was 20 — confirmed killing strategy edge by rejecting 95% of signals.
    MAX_SL_PIPS: float = 50.0

    # Maximum ATR threshold — skip trading during extreme volatility (news shocks, flash crashes)
    # EURUSD H1 normal ATR: 50–100 pips. BoJ-shock ATR: 150–200+ pips.
    MAX_ATR_PIPS: float = 150.0         # Block signals when ATR > 150 pips (0.0150 in price)

    # Session filters — London core + full NY overlap (8-16 UTC).
    # The old 7-13 window was never backtested: it cut the validated 376-trade config
    # to 154 trades (net +$122 vs +$349) and produced 76-day droughts. 8-16 captures
    # MR's best hours (13-14 UTC = London/NY overlap) and skips the losing 7 UTC open
    # and 17-20 UTC chop. 3-yr backtest w/ 10% DD halt: 280 trades, PF 1.55, +$423,
    # maxDD 8.5%, never halted, worst drought 29 days. Positive every year 2023-2025.
    TRADING_START_HOUR_UTC: int = 8    # London core (7 UTC open hour was net -$18)
    TRADING_END_HOUR_UTC: int = 16     # Through NY overlap; 17-20 UTC lost -$95 in backtest
    TRADE_DAYS_OF_WEEK: str = "Mon,Tue,Wed,Thu,Fri"
    FRIDAY_CLOSE_ENABLED: bool = True
    FRIDAY_CLOSE_HOUR_UTC: int = 15    # Close 1h before Fri session end — no weekend holds

    # Instruments — the xsect basket (all USD-quote; first symbol must match the chart)
    TRADED_SYMBOLS: str = "EURUSD,GBPUSD,AUDUSD,NZDUSD"

    # Logging
    LOG_LEVEL: str = LogLevel.INFO.name
    FILE_LOGGING: bool = False

    # Execution
    LABEL_PREFIX: str = "ArgoAlgo"


# ---------------------------------------------------------------------------
# Rate limits (from cTrader platform, PRD §3.4)
# ---------------------------------------------------------------------------

class RateLimits:
    """cTrader API rate limits per minute."""
    NEW_ORDERS: int = 500
    CANCEL_ORDERS: int = 100
    AMEND_ORDERS: int = 100
    CLOSE_POSITIONS: int = 2000
    MODIFY_PROTECTION_L1: int = 1000   # per minute
    MODIFY_PROTECTION_L2: int = 5000   # per 15 minutes


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

# Minimum number of bars required before strategy signals are considered valid
MIN_BARS_REQUIRED: int = 50

# Bot version
BOT_VERSION: str = "1.0.0"
