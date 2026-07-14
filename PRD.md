# Product Requirements Document: Algorithmic Trading Bot for cTrader on IC Markets

---

## 1. Introduction

### 1.1 Purpose

This Product Requirements Document (PRD) defines the complete set of specifications for the design, development, testing, and deployment of an algorithmic trading bot. The bot will operate as a cBot on the **cTrader** platform, connected to **IC Markets** as the brokerage provider. The primary objective is to create a robust, consistently profitable, and fully automated trading system that eliminates emotional decision-making and minimizes the need for manual intervention.

This document serves as the single source of truth for all stakeholders involved in the project. It translates the high-level goal of "building a successful trading bot" into concrete, measurable, and actionable technical requirements.

### 1.2 Scope

The scope of this project encompasses the full lifecycle of the trading bot, from initial strategy design through to production deployment and ongoing monitoring. Specifically, the project includes the following deliverables:

The **in-scope** items are the development of a multi-strategy cBot in **Python** using the cTrader Algo API, a comprehensive risk management engine, a backtesting and optimization framework, a logging and reporting module, a simple on-chart control panel, and deployment via the **cTrader Cloud** environment.

The **out-of-scope** items are the development of a standalone web or mobile application for monitoring, the creation of a machine-learning or AI-based strategy engine (though the architecture should not preclude future integration), and the management of the IC Markets account itself (deposits, withdrawals, and account settings).

---

## 2. Product Overview

### 2.1 Vision

The vision for this product is a "set-and-monitor" trading system. Once configured and deployed, the bot should autonomously analyze market conditions, select the appropriate trading strategy, execute trades with precise risk management, and generate detailed performance reports. The user's role shifts from active trading to strategic oversight and periodic parameter review.

### 2.2 Key Value Propositions

The trading bot delivers value through several critical dimensions. First, it provides **emotion-free execution** by strictly adhering to predefined rules, removing fear, greed, and hesitation from the trading process. Second, it enables **24/5 market coverage**, operating continuously during market hours without fatigue, ensuring no trading opportunity within its strategy parameters is missed. Third, it enforces **disciplined risk management** through a hard-coded risk framework that cannot be overridden by emotional impulses. Fourth, it offers **speed and precision**, executing trades in milliseconds with exact position sizing and order placement. Finally, it supports **data-driven improvement** through comprehensive logging and backtesting, enabling continuous, evidence-based refinement of strategies.

### 2.3 Product Classification

According to the cTrader documentation [5], there are four key types of cBots: Automated Trading Strategies, Scripts, Trading Assistants, and Trading Panels. This product is primarily an **Automated Trading Strategy** cBot, with an integrated **Trading Panel** component for on-chart status display and manual override controls. The bot will autonomously execute trading strategies, manage risk, and log performance, while the panel component will provide real-time visibility and emergency controls.

---

## 3. Target Environment and Infrastructure

### 3.1 Brokerage: IC Markets

IC Markets is an Australian-founded, globally regulated CFD and Forex broker known for its institutional-grade trading conditions. It is consistently ranked among the best brokers for algorithmic trading due to its tight spreads, deep liquidity sourced from up to 25 institutional-grade providers, and support for automated trading on all platforms [1].

The following table summarizes the IC Markets cTrader Raw Spread account, which is the recommended account type for this bot:

| Parameter                      | Specification                              |
| ------------------------------ | ------------------------------------------ |
| **Account Type**               | cTrader Raw Spread                         |
| **Trading Platform**           | cTrader, TradingView                       |
| **Commission**                 | $3.0 per USD 100,000 traded                |
| **Spreads**                    | From 0.0 pips (average EUR/USD: 0.1 pips)  |
| **Minimum Deposit**            | $0                                         |
| **Leverage**                   | Up to 1:1000                               |
| **Maximum Positions**          | 2,000 per account                          |
| **Stop Out Level**             | 50%                                        |
| **Server Location**            | London (Equinix LD5)                       |
| **Programming Language**       | **Python**, C#                             |
| **Tradable Instruments**       | 121 (on cTrader)                           |
| **Trading Styles**             | All (scalping, hedging, day trading, etc.) |
| **Order Distance Restriction** | None                                       |
| **Swap Free**                  | Available                                  |

> **Rationale for Account Choice:** The cTrader Raw Spread account is selected over the MetaTrader Raw Spread account for several reasons. It offers a lower commission structure ($3.0 per $100k vs. $3.5 per lot per side), supports up to 2,000 concurrent positions (vs. 200 on MetaTrader), and provides **native Python support**, which enables rapid development and access to a vast ecosystem of data science libraries. The cTrader platform also offers superior built-in backtesting and optimization tools compared to MetaTrader's Strategy Tester [1] [17].

### 3.2 Available Instruments

The bot will initially focus on major Forex pairs due to their high liquidity and tight spreads. The following table outlines the asset classes available on IC Markets via cTrader [14]:

| Asset Class        | Count      | Leverage              | Notes                       |
| ------------------ | ---------- | --------------------- | --------------------------- |
| Forex CFD          | 61 pairs   | Up to 1:1000          | Primary focus for the bot   |
| Indices CFD        | 25 indices | Up to 1:200           | Commission-free             |
| Commodities CFD    | 20+        | Up to 1:1000          | Energy, agriculture, metals |
| Bonds CFD          | 9+         | Up to 1:200           | Commission-free             |
| Cryptocurrency CFD | 21         | Up to 1:300 (cTrader) | 7 days/week                 |
| Stocks CFD         | 2,100+     | Up to 1:20            | MT5 only, not on cTrader    |
| Futures CFD        | 5          | Up to 1:200           | MT4 only, not on cTrader    |

### 3.3 Platform: cTrader Algo

The cTrader Algo environment is the integrated development environment (IDE) within cTrader for building, testing, and running algorithmic trading strategies. It is the only major trading platform to offer **native Python support**, allowing for the development of cBots and indicators without external adapters or complex workarounds [17]. This project will use **Python** as the primary development language to leverage its simplicity, extensive data science libraries, and rapid development cycle. While C# is also supported and offers slightly higher raw performance, the benefits of the Python ecosystem are more aligned with the project's goals [18].

The cTrader Algo API provides access to the following key categories of functionality, all of which will be leveraged by the bot:

| API Category      | Key Features                                                                                                          |
| ----------------- | --------------------------------------------------------------------------------------------------------------------- |
| **Trading**       | Market orders, pending orders (stop, limit, stop-limit), position management, order modification, event subscriptions |
| **Market Data**   | Real-time tick data, bar data (OHLCV), multi-timeframe data, symbol information                                       |
| **Indicators**    | 50+ built-in indicators (RSI, MACD, Bollinger Bands, ATR, ADX, SMA, EMA, etc.) with ready-made constructors           |
| **Account**       | Account balance, equity, margin, leverage, transaction history                                                        |
| **Chart**         | Drawing tools, text display, custom panels                                                                            |
| **HTTP**          | External API calls for news feeds, custom data                                                                        |
| **WebSocket**     | Real-time external data streams                                                                                       |
| **Timer**         | Scheduled operations independent of market events                                                                     |
| **Local Storage** | Persistent key-value storage                                                                                          |
| **Notifications** | Email, push, and sound alerts                                                                                         |

### 3.4 Platform Rate Limits

The cTrader platform enforces rate limits on trading operations to protect server stability. The bot must be designed to operate well within these limits [9]:

| Operation                    | Limit                                        | Consequence of Exceeding |
| ---------------------------- | -------------------------------------------- | ------------------------ |
| Placing a new order          | 500 per minute                               | No trading for 1 minute  |
| Cancelling an order          | 100 per minute                               | No trading for 1 minute  |
| Amending an order            | 100 per minute                               | No trading for 1 minute  |
| Closing a position           | 2,000 per minute                             | No trading for 1 minute  |
| Modifying protection (SL/TP) | 1,000 per minute (L1); 5,000 per 15 min (L2) | 1 min (L1); 30 min (L2)  |

---

## 4. Functional Requirements

This section details the specific features and capabilities the bot must possess.

### FR-001: Multi-Strategy Trading Engine

The bot shall implement a modular strategy engine that supports multiple trading strategies. Each strategy shall be a self-contained module that can be enabled, disabled, and configured independently through cBot parameters. The engine shall evaluate market conditions and delegate trade execution to the active strategy or strategies. The detailed specifications for each strategy are provided in Section 7.

### FR-002: Risk Management Module

The bot shall include a comprehensive risk management module that operates independently of the strategy engine. This module shall enforce position sizing rules, stop-loss and take-profit placement, drawdown limits, and exposure controls. No trade shall be executed without passing all risk management checks. The detailed specifications are provided in Section 8.

### FR-003: Order Execution

The bot shall execute all trading operations through the cTrader Algo API [8]. The following order types must be supported:

**Market Orders** will be the primary execution method, using `api.ExecuteMarketOrder()`. The bot shall specify the symbol, trade type (buy/sell), volume (in units), label, stop-loss (in pips), and take-profit (in pips) for each order.

**Pending Orders** shall be supported for breakout strategies, using `api.PlaceStopOrder()` and `api.PlaceLimitOrder()`. The bot shall manage the lifecycle of pending orders, including modification and cancellation.

**Position Management** shall include the ability to modify stop-loss and take-profit levels on open positions using `position.ModifyStopLossPrice()` / `position.ModifyTakeProfitPrice()`, close positions using `position.Close()`, and reverse positions using `api.ReversePosition()` if the strategy requires it.

### FR-004: Multi-Symbol Support

The bot shall be capable of monitoring and trading multiple currency pairs simultaneously. It shall use the `Symbols` collection to access data for instruments other than the one on which the cBot instance is running [10]. The list of tradable symbols shall be configurable through cBot parameters.

### FR-005: Multi-Timeframe Analysis

The bot shall support multi-timeframe analysis, allowing strategies to use data from higher timeframes for trend confirmation while using lower timeframes for entry signals. For example, a strategy might use the H4 chart to determine the overall trend direction and the M15 chart for precise entry timing.

### FR-006: Event-Driven Architecture

The bot shall respond to the following cTrader events:

`on_start(self)` will be used for initialization, including loading configuration, initializing indicators via the `api` object, subscribing to events, and performing initial market analysis [18].

`on_bar_closed(self)` will be the primary event for strategy evaluation on the configured timeframe. Most trading decisions will be made here to avoid excessive computation on every tick.

`on_tick(self)` will be used sparingly for time-sensitive operations such as trailing stop management and spread monitoring.

`on_stop(self)` will be used for cleanup operations, including sending summary notifications and optionally closing all open positions.

`on_error(self, error)` will be used for error handling, logging, and alerting.

### FR-007: Trading Event Subscriptions

The bot shall subscribe to the following trading events for monitoring and logging purposes [8]:

For the `Positions` collection: `Opened`, `Modified`, and `Closed` events. For the `PendingOrders` collection: `Created`, `Modified`, `Filled`, and `Cancelled` events. Each event handler shall log the relevant details and update internal state accordingly.

### FR-008: Configurable Parameters

All key bot parameters shall be exposed as cBot parameters using the Python parameter system (via `getattr(api, ...)` or the cTrader parameter declaration syntax), allowing them to be configured through the cTrader UI and used in the optimization process [21]. Parameters shall be organized into logical groups:

| Group               | Parameters                                                                 |
| ------------------- | -------------------------------------------------------------------------- |
| **Strategy**        | Strategy type, indicator periods, entry/exit thresholds                    |
| **Risk Management** | Risk per trade (%), max drawdown (%), max concurrent trades, spread filter |
| **Execution**       | Slippage tolerance, order comment/label prefix                             |
| **Filters**         | Trading session hours, news filter enabled/disabled, day-of-week filter    |
| **Logging**         | Log level (debug, info, warning, error), file logging enabled/disabled     |

### FR-009: On-Chart Control Panel

The bot shall display a simple on-chart panel (Trading Panel component) showing the following information in real-time:

The panel shall display the bot's current status (running, paused, or stopped), the active strategy, the number of open positions, today's profit/loss, current drawdown percentage, and the account balance and equity. It shall also include a **Panic Button** that, when clicked, immediately closes all open positions managed by the bot and pauses all trading activity until manually resumed.

### FR-010: Logging and Reporting

The bot shall implement a multi-level logging system. All log entries shall include a timestamp, log level, and descriptive message. Logs shall be written to both the cTrader Log tab (using `Print()`) and to a local CSV or text file for persistent record-keeping. The bot shall require `AccessRights.FullAccess` to enable file I/O for logging if deployed locally. When deployed to cTrader Cloud, file I/O is not supported, and logging will be restricted to the cTrader Log tab [19].

The following events must be logged at a minimum: bot start and stop events, every trade entry and exit with full details (symbol, direction, volume, entry price, exit price, P/L, strategy used), all risk management actions (position sizing calculations, drawdown limit triggers), all errors and exceptions, and daily performance summaries.

### FR-011: Notification System

The bot shall support configurable notifications for critical events. Using the cTrader Notifications API, it shall send alerts for trade executions, drawdown limit breaches, bot errors or crashes, and daily performance summaries. Notification channels shall include cTrader push notifications and email (if configured).

### FR-012: Session and Time Filters

The bot shall include configurable trading session filters. The user shall be able to define specific trading hours (in UTC) during which the bot is allowed to trade. Outside these hours, the bot shall not open new positions but shall continue to manage existing ones (e.g., trailing stops). A day-of-week filter shall also be available to disable trading on specific days (e.g., Fridays to avoid weekend gap risk).

### FR-013: Spread Filter

The bot shall monitor the real-time spread for each instrument before executing a trade. If the current spread exceeds a configurable threshold (in pips), the trade shall be skipped. This is critical for protecting against execution during periods of low liquidity or high volatility when spreads widen significantly.

---

## 5. Non-Functional Requirements

### NFR-001: Performance

The bot's code must be optimized for execution speed. All indicator calculations and strategy evaluations performed within `on_bar_closed()` must complete within a few milliseconds. The `on_tick()` handler must be extremely lightweight, performing only essential operations such as trailing stop updates. Memory usage must remain stable over extended periods of operation, with no memory leaks.

### NFR-002: Reliability and Fault Tolerance

The bot must be designed for continuous 24/5 operation. It must handle network disconnections gracefully, using the cTrader fault tolerance mechanisms to resume operation upon reconnection [15]. The bot must not enter an inconsistent state after a disconnection (e.g., it must correctly reconcile its internal position tracking with the actual account state upon reconnection). A "heartbeat" mechanism should be implemented to detect and log connectivity issues.

### NFR-003: Security

The bot shall operate with the minimum required access rights. If `AccessRights.FullAccess` is needed for file logging or HTTP requests, all external connections must be to trusted endpoints only. No sensitive account information shall be logged in plain text. The cBot source code shall be protected and not distributed publicly.

### NFR-004: Scalability

The bot's modular architecture must allow for the straightforward addition of new trading strategies without requiring changes to the core risk management or execution engine. Adding a new instrument to the bot's watchlist should require only a parameter change, not a code modification.

### NFR-005: Maintainability

The codebase must follow **Python (PEP 8)** coding conventions and best practices. All classes, methods, and complex logic blocks must be documented with docstrings. The project structure must clearly separate concerns (strategy logic, risk management, execution, logging, UI).

### NFR-006: Testability

Every strategy module and the risk management module must be designed to be independently testable through the cTrader backtesting engine. Custom fitness functions shall be implemented to allow optimization against specific performance metrics (e.g., maximizing Sharpe Ratio rather than just Net Profit) [16].

---

## 6. Technical Architecture

### 6.1 High-Level Architecture

The bot shall be structured as a single Python cBot project with a modular internal architecture. The following components shall be implemented as separate Python classes or modules within the project:

**`TradingBot` (Main Class):** The entry point class for the cBot. It handles the cBot lifecycle through Python methods (`on_start`, `on_bar_closed`, `on_tick`, `on_stop`, `on_error`), initializes all modules via the `api` object, and orchestrates the flow of data between them [18].

**`StrategyEngine`:** Contains the logic for each trading strategy. Each strategy is implemented as a separate Python class following a common abstract base pattern. The engine evaluates market conditions and returns trade signals.

**`RiskManager`:** Receives trade signals from the `StrategyEngine` and applies all risk management rules before allowing execution. It calculates position size, validates drawdown limits, checks spread filters, and determines stop-loss and take-profit levels.

**`OrderExecutor`:** Handles all interactions with the cTrader trading API. It receives validated trade instructions from the `RiskManager` and executes them, handling errors and retries.

**`DataProvider`:** Manages access to market data, including multi-symbol and multi-timeframe data. It initializes indicators and provides a clean interface for the `StrategyEngine` to query market conditions.

**`Logger`:** A centralized logging service used by all other modules. It writes to the cTrader log via `api.Print()`. When running in Cloud, this is the primary logging mechanism.

**`UIPanel`:** Manages the on-chart control panel, displaying status information and handling user interactions (e.g., the panic button).

### 6.2 Data Flow

The operational data flow follows a clear pipeline. On each `on_bar_closed` event, the `DataProvider` updates its internal state with the latest market data. The `StrategyEngine` then queries the `DataProvider` and evaluates its active strategies, producing a trade signal (Buy, Sell, or No Action). If a signal is generated, it is passed to the `RiskManager`, which validates the signal against all risk rules and calculates the appropriate position size. If the signal passes all checks, the `RiskManager` creates a validated trade instruction and passes it to the `OrderExecutor`. The `OrderExecutor` sends the order to the cTrader server and reports the result. Throughout this process, the `Logger` records all significant events, and the `UIPanel` is updated with the latest status.

### 6.3 Class Diagram (Conceptual)

```
# Main cBot Class (inherits from cTrader's Robot)
class TradingBot:
    # Modules (implemented as separate classes/files)
    self.strategy_engine = StrategyEngine()
    self.risk_manager = RiskManager()
    self.order_executor = OrderExecutor()
    self.data_provider = DataProvider()
    self.logger = Logger()
    self.ui_panel = UIPanel()

# Strategy Interface (conceptual)
class IStrategy:
    def evaluate(self, data):
        pass
```

---

## 7. Strategy Specifications

This section provides detailed specifications for each trading strategy that the bot must implement. All strategies share a common interface and can be enabled or disabled independently.

### 7.1 Strategy 1: Trend Following

**Objective:** To identify and trade in the direction of established market trends, capturing the "meat" of significant price movements [6].

**Core Logic:** The strategy uses a combination of moving average crossovers for signal generation and the Average Directional Index (ADX) as a trend strength filter.

**Entry Conditions for a Buy Signal:** The fast Exponential Moving Average (EMA) crosses above the slow EMA, the ADX value is above a configurable threshold (default: 25), indicating a strong trend, and the price is above the slow EMA.

**Entry Conditions for a Sell Signal:** The fast EMA crosses below the slow EMA, the ADX value is above the threshold, and the price is below the slow EMA.

**Exit Conditions:** The position is closed when the fast EMA crosses back in the opposite direction, or when the stop-loss or take-profit is hit.

**Configurable Parameters:**

| Parameter               | Type   | Default | Description                                         |
| ----------------------- | ------ | ------- | --------------------------------------------------- |
| `FastEmaPeriod`         | int    | 12      | Period for the fast EMA                             |
| `SlowEmaPeriod`         | int    | 26      | Period for the slow EMA                             |
| `AdxPeriod`             | int    | 14      | Period for the ADX indicator                        |
| `AdxThreshold`          | double | 25.0    | Minimum ADX value to confirm a trend                |
| `StopLossAtrMultiplier` | double | 2.0     | Stop-loss distance as a multiple of ATR             |
| `TakeProfitRiskReward`  | double | 2.0     | Take-profit as a multiple of the stop-loss distance |

**Best Market Conditions:** Trending markets with clear directional momentum. Performs well during economic events and sustained market moves [6].

### 7.2 Strategy 2: Mean Reversion

**Objective:** To identify overbought and oversold conditions and trade the expected reversion of price to its historical mean [6].

**Core Logic:** The strategy uses Bollinger Bands to define the price channel and the Relative Strength Index (RSI) as a confirmation filter.

**Entry Conditions for a Buy Signal:** The price closes below the lower Bollinger Band, the RSI is below a configurable oversold threshold (default: 30), and the ADX is below a configurable threshold (default: 25), indicating a ranging market (not a strong trend).

**Entry Conditions for a Sell Signal:** The price closes above the upper Bollinger Band, the RSI is above a configurable overbought threshold (default: 70), and the ADX is below the threshold.

**Exit Conditions:** The position is closed when the price reaches the middle Bollinger Band (the moving average), or when the stop-loss or take-profit is hit.

**Configurable Parameters:**

| Parameter            | Type   | Default | Description                                              |
| -------------------- | ------ | ------- | -------------------------------------------------------- |
| `BollingerPeriod`    | int    | 20      | Period for the Bollinger Bands                           |
| `BollingerDeviation` | double | 2.0     | Standard deviation multiplier for the bands              |
| `RsiPeriod`          | int    | 14      | Period for the RSI indicator                             |
| `RsiOversold`        | double | 30.0    | RSI level below which the asset is considered oversold   |
| `RsiOverbought`      | double | 70.0    | RSI level above which the asset is considered overbought |
| `AdxFilterPeriod`    | int    | 14      | Period for the ADX filter                                |
| `AdxFilterThreshold` | double | 25.0    | Maximum ADX value (trade only in non-trending markets)   |

**Best Market Conditions:** Ranging or sideways markets with low to moderate volatility [6].

### 7.3 Strategy 3: Breakout

**Objective:** To enter trades at the onset of a new trend when the price breaks through significant support or resistance levels [6].

**Core Logic:** The strategy uses Donchian Channels to identify breakout levels and the Average True Range (ATR) to filter out false breakouts and set dynamic stop-losses.

**Entry Conditions for a Buy Signal:** The price closes above the upper Donchian Channel (highest high of the last N periods), the ATR is above a configurable minimum threshold, confirming sufficient volatility for a genuine breakout, and no existing long position is open for this symbol.

**Entry Conditions for a Sell Signal:** The price closes below the lower Donchian Channel (lowest low of the last N periods), the ATR is above the minimum threshold, and no existing short position is open for this symbol.

**Exit Conditions:** The position is closed when the price touches the middle Donchian Channel line (average of the upper and lower channels), or when the stop-loss or take-profit is hit.

**Configurable Parameters:**

| Parameter               | Type   | Default | Description                                         |
| ----------------------- | ------ | ------- | --------------------------------------------------- |
| `DonchianPeriod`        | int    | 20      | Lookback period for the Donchian Channel            |
| `AtrPeriod`             | int    | 14      | Period for the ATR indicator                        |
| `AtrMinThreshold`       | double | 0.0005  | Minimum ATR value to confirm a valid breakout       |
| `StopLossAtrMultiplier` | double | 1.5     | Stop-loss distance as a multiple of ATR             |
| `TakeProfitRiskReward`  | double | 2.0     | Take-profit as a multiple of the stop-loss distance |

**Best Market Conditions:** Volatile markets following periods of consolidation, during major economic announcements [6].

### 7.4 Strategy Selection Logic

The bot shall include a configurable strategy selection mechanism. The simplest mode is **Manual Selection**, where the user explicitly enables one or more strategies via parameters. A more advanced mode is **ADX-Based Switching**, where the bot automatically selects between Trend Following (when ADX is above the threshold) and Mean Reversion (when ADX is below the threshold). The Breakout strategy can be run concurrently or as a standalone option.

---

## 8. Risk Management Framework

The risk management framework is the most critical component of the bot. Its purpose is to preserve capital and ensure the bot's long-term survival through inevitable losing streaks. Every trade must pass through the risk management module before execution [7].

### 8.1 Position Sizing

The bot shall use a **fixed fractional risk model**. The volume of each trade is calculated so that if the stop-loss is hit, the loss equals a fixed percentage of the current account balance.

> **Formula:** `Volume = (Account Balance * Risk Percentage) / (Stop Loss in Pips * Pip Value)`

The calculated volume must be rounded to the nearest valid volume step for the instrument and must fall within the instrument's minimum and maximum volume limits, as obtained from the `Symbol` object [10].

| Parameter             | Type   | Default | Description                                               |
| --------------------- | ------ | ------- | --------------------------------------------------------- |
| `RiskPerTradePercent` | double | 1.0     | Maximum risk per trade as a percentage of account balance |

### 8.2 Stop-Loss Management

Every trade opened by the bot must have a stop-loss. There are no exceptions to this rule. The stop-loss type shall be configurable per strategy:

**ATR-Based Stop-Loss** is the recommended default. The stop-loss distance is calculated as a multiple of the current Average True Range (ATR) value. This dynamically adapts to market volatility, providing wider stops in volatile conditions and tighter stops in calm conditions.

**Fixed Pip Stop-Loss** sets the stop-loss at a fixed pip distance from the entry price. This is simpler but does not adapt to changing volatility.

### 8.3 Take-Profit Management

Take-profit is optional but recommended. It can be configured as a **Risk/Reward Ratio** (e.g., 2:1, meaning the take-profit distance is twice the stop-loss distance), a **Fixed Pip Value**, or **Technical Level** (e.g., the middle Bollinger Band for mean reversion).

### 8.4 Trailing Stop-Loss

The bot shall support a configurable trailing stop-loss mechanism. Once a trade moves a specified number of pips in profit, the stop-loss is moved to breakeven. As the trade continues to move favorably, the stop-loss trails the price by a configurable distance. This is managed within the `on_tick()` handler for responsiveness.

| Parameter                  | Type   | Default | Description                                       |
| -------------------------- | ------ | ------- | ------------------------------------------------- |
| `TrailingStopEnabled`      | bool   | true    | Enable or disable trailing stop                   |
| `TrailingStopTriggerPips`  | double | 15.0    | Pips in profit before trailing stop activates     |
| `TrailingStopDistancePips` | double | 10.0    | Distance the stop trails behind the current price |

### 8.5 Drawdown Controls

The bot shall implement multiple layers of drawdown protection:

**Daily Drawdown Limit:** If the bot's losses for the current trading day exceed a configurable percentage of the starting daily balance (default: 3%), all new trading is halted for the remainder of the day. Existing positions are managed but no new ones are opened.

**Total Drawdown Limit:** If the account equity drops below a configurable percentage of the initial account balance or a high-water mark (default: 10%), the bot closes all open positions and halts all trading until manually restarted. This is the ultimate safety net.

| Parameter                 | Type   | Default | Description                                               |
| ------------------------- | ------ | ------- | --------------------------------------------------------- |
| `MaxDailyDrawdownPercent` | double | 3.0     | Maximum allowed daily loss as % of daily starting balance |
| `MaxTotalDrawdownPercent` | double | 10.0    | Maximum allowed total loss as % of initial balance        |

The following table illustrates why strict drawdown control is essential [7]:

| Drawdown | Required Gain to Recover |
| -------- | ------------------------ |
| 10%      | 11.1%                    |
| 20%      | 25.0%                    |
| 30%      | 42.9%                    |
| 50%      | 100.0%                   |

### 8.6 Exposure Controls

**Maximum Concurrent Positions:** The bot shall limit the total number of open positions at any time (default: 5). This prevents over-exposure during volatile periods.

**Maximum Exposure Per Instrument:** The bot shall limit the number of open positions per instrument (default: 1). This prevents doubling down on a single trade.

**Correlation Filter (Future Enhancement):** A future version may include a correlation filter to prevent opening positions in highly correlated pairs (e.g., EUR/USD and GBP/USD simultaneously), which would effectively double the risk exposure.

### 8.7 Spread Filter

Before executing any trade, the bot shall check the current spread. If the spread exceeds a configurable maximum (default: 3.0 pips for major pairs), the trade is skipped and logged. This protects against poor fills during low-liquidity periods such as the daily rollover or during unexpected news events.

### 8.8 Session and Time Filters

The bot shall only open new trades during configurable trading hours. The default configuration shall be optimized for the London and New York sessions (07:00 to 20:00 UTC), which represent the highest liquidity periods for Forex. A Friday close filter shall be available to close all positions before the weekend to avoid gap risk.

---

## 9. Backtesting and Optimization

### 9.1 Backtesting Methodology

All strategies must be rigorously backtested before deployment to a live account. The cTrader built-in backtesting engine will be the primary tool [12].

**Data Source:** Tick data from the server shall be used for all backtests. This provides the highest accuracy by simulating real spread conditions and tick-by-tick price movements.

**Duration:** A minimum of 5 years of historical data shall be used. This ensures the strategy is tested across a variety of market regimes, including trending, ranging, and crisis periods.

**Transaction Costs:** All backtests must include realistic transaction costs, specifically the IC Markets cTrader commission of $3.0 per $100k and realistic spread simulation.

### 9.2 Optimization Approach

Optimization shall follow a disciplined, multi-step process to prevent overfitting [13]:

**Step 1: In-Sample Optimization.** The first 70% of the historical data is used as the "in-sample" period. The cTrader optimizer is used to find the best parameter combinations within this period, using either Grid Search or a genetic algorithm.

**Step 2: Out-of-Sample Validation.** The remaining 30% of the data is used as the "out-of-sample" period. The best parameter sets from Step 1 are tested on this unseen data. Only parameter sets that perform well on both in-sample and out-of-sample data are considered valid.

**Step 3: Walk-Forward Analysis (WFA).** For the most robust validation, the data is divided into multiple sequential windows. Each window consists of an optimization period followed by a validation period. The bot is optimized on the first window, tested on the next, then the window slides forward, and the process repeats. This simulates real-world conditions where the bot is periodically re-optimized [13].

**Step 4: Sensitivity Analysis.** The chosen parameters are perturbed by +/- 10-20%. If the strategy's performance degrades significantly with small parameter changes, it is likely overfitted and should be rejected.

### 9.3 Key Performance Indicators (KPIs)

The following metrics shall be used to evaluate the bot's performance during backtesting and live trading [12]:

| Metric                         | Target   | Description                                            |
| ------------------------------ | -------- | ------------------------------------------------------ |
| **Net Profit**                 | Positive | Total profit after all costs                           |
| **Profit Factor**              | > 1.5    | Gross profit divided by gross loss                     |
| **Sharpe Ratio**               | > 1.0    | Risk-adjusted return                                   |
| **Max Equity Drawdown**        | < 15%    | Largest peak-to-trough decline in equity               |
| **Win Rate**                   | > 40%    | Percentage of winning trades                           |
| **Average Win / Average Loss** | > 1.5    | Ratio of average winning trade to average losing trade |
| **Recovery Factor**            | > 3.0    | Net profit divided by max drawdown                     |
| **Total Trades**               | > 200    | Sufficient sample size for statistical significance    |

### 9.4 Custom Fitness Function

For optimization, a custom fitness function shall be implemented to guide the optimizer toward robust, risk-adjusted performance rather than simply maximizing net profit [16]. A recommended fitness function is:

> **Fitness = Net Profit \* Profit Factor / Max Drawdown**

This formula rewards strategies that are profitable, have a good win/loss ratio, and maintain low drawdowns.

---

## 10. Deployment and Operations

### 10.1 Deployment Environment: cTrader Cloud

The primary deployment environment for the bot shall be **cTrader Cloud**. This modern, integrated solution provides significant advantages over traditional VPS hosting [19].

| Feature            | Specification                                      |
| ------------------ | -------------------------------------------------- |
| **Cost**           | Free                                               |
| **Uptime**         | 24/7 (managed by cTrader)                          |
| **VPS Required**   | No                                                 |
| **Latency**        | Low (optimized connectivity to broker servers)     |
| **Setup**          | One-click deployment from any cTrader app          |
| **Accessibility**  | Manage cBot instances from Web, Mobile, or Desktop |
| **Instance Limit** | Up to 10 on live accounts (broker-dependent)       |

> **Rationale for Cloud Deployment:** cTrader Cloud is the recommended deployment method as it drastically simplifies operations, reduces costs, and provides a highly reliable, low-latency environment without the complexities of managing a separate VPS. Python cBots, including those with third-party dependencies like NumPy and Pandas, are fully compatible with Cloud execution [20].

### 10.2 Alternative Deployment: Local Execution

For debugging and development purposes, the bot can also be run via **Local Execution** on cTrader Desktop (Windows/Mac). This allows for real-time log inspection and rapid iteration but is not suitable for live trading as it requires the cTrader application to be running continuously [19]. A VPS is no longer the recommended or necessary option for this project.

### 10.3 Operational Procedures

**Startup Checklist:** Before starting the bot on a live account, the following checks must be completed: verify the account balance and margin, confirm Cloud synchronisation is enabled, review and confirm all bot parameters, ensure the latest version of the cBot is built and synced to Cloud, and start with a reduced risk percentage for the first week.

**Monitoring:** The bot's performance shall be reviewed daily. Key metrics to check include daily P/L, number of trades, maximum drawdown, and any error logs. Weekly and monthly performance reviews shall be conducted to assess whether the strategy parameters need re-optimization.

**Emergency Procedures:** If the bot exhibits unexpected behavior, the user can stop the Cloud instance from any cTrader app (Web, Mobile, Windows, or Mac) to immediately halt trading. Positions can be manually closed from any cTrader app. Since the bot runs in Cloud, there is no risk of VPS downtime or connectivity loss on the user's side [19].

---

## 11. Development Roadmap

The development of the bot shall follow an iterative approach, divided into the following phases:

| Phase                                   | Duration     | Deliverables                                                                                                                  |
| --------------------------------------- | ------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| **Phase 1: Foundation**                 | 2 weeks      | Project setup, core architecture (TradingBot, RiskManager, OrderExecutor, Logger, DataProvider), basic parameter framework    |
| **Phase 2: Strategy Implementation**    | 3 weeks      | Trend Following strategy, Mean Reversion strategy, Breakout strategy, strategy selection logic                                |
| **Phase 3: Risk Management**            | 2 weeks      | Position sizing, stop-loss/take-profit management, trailing stops, drawdown controls, spread filter, session filter           |
| **Phase 4: Backtesting & Optimization** | 3 weeks      | Backtesting on 5+ years of data, Walk-Forward Analysis, parameter optimization, custom fitness function, sensitivity analysis |
| **Phase 5: UI & Reporting**             | 1 week       | On-chart control panel, logging to file, notification system                                                                  |
| **Phase 6: Demo Testing**               | 2 weeks      | Deploy on IC Markets demo account, monitor 24/5 operation, fix bugs, validate performance against backtest results            |
| **Phase 7: Live Deployment**            | 1 week       | Cloud deployment, live deployment with reduced risk, monitoring and validation                                                |
| **Total Estimated Duration**            | **14 weeks** |                                                                                                                               |

---

## 12. Acceptance Criteria

The bot shall be considered ready for live deployment when all of the following criteria are met:

**AC-001:** All three trading strategies (Trend Following, Mean Reversion, Breakout) are implemented and independently testable.

**AC-002:** The risk management module correctly calculates position sizes, enforces stop-losses on every trade, and halts trading when drawdown limits are breached.

**AC-003:** Backtesting on 5 years of tick data produces a Profit Factor greater than 1.5 and a maximum equity drawdown less than 15%.

**AC-004:** Walk-Forward Analysis confirms that the strategy is not overfitted, with out-of-sample performance within 70% of in-sample performance.

**AC-005:** The bot runs continuously on a demo account for a minimum of 2 weeks without crashes, memory leaks, or unhandled exceptions.

**AC-006:** The on-chart panel correctly displays real-time status, and the Panic Button successfully closes all positions and halts trading.

**AC-007:** All trades, risk management actions, and errors are logged to the cTrader log via `api.Print()`.

**AC-008:** The bot correctly respects all configurable filters (session, spread, day-of-week, drawdown limits).

---

## 13. Assumptions, Dependencies, and Constraints

### 13.1 Assumptions

The user possesses or will open an active IC Markets cTrader Raw Spread account. The user has an active cTrader ID (cTID) to enable Cloud features. IC Markets will continue to offer the cTrader platform with its current trading conditions and API capabilities. The cTrader Algo API will remain stable and backward-compatible.

### 13.2 Dependencies

The project depends on the cTrader platform (developed by Spotware Systems), the IC Markets brokerage infrastructure, the Python runtime environment integrated into cTrader, and optionally, an external economic calendar API for the news filter feature.

### 13.3 Constraints

The bot is limited to the 121 instruments available on IC Markets' cTrader platform. The cTrader rate limits (Section 3.4) constrain the maximum trading frequency. The bot cannot guarantee profits; all trading involves risk, and past performance (including backtesting results) is not indicative of future results.

### 13.4 Risk Disclaimer

> **Important:** Algorithmic trading carries significant financial risk. The bot described in this document is a tool that executes predefined strategies, but it cannot eliminate market risk. The user is solely responsible for the financial outcomes of using this bot. It is strongly recommended to start with a demo account, then transition to live trading with minimal capital, and only increase exposure after a sustained period of validated performance.

---

## 14. Glossary

| Term              | Definition                                                                                                                  |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **ADX**           | Average Directional Index; a technical indicator that measures the strength of a trend.                                     |
| **ATR**           | Average True Range; a technical indicator that measures market volatility.                                                  |
| **cBot**          | An automated trading robot that runs on the cTrader platform.                                                               |
| **cTID**          | cTrader ID; a unique user identity that enables Cloud synchronisation and cross-device access.                              |
| **cTrader Cloud** | A free, built-in cloud execution environment in cTrader that runs cBots 24/7 without a VPS.                                 |
| **CFD**           | Contract for Difference; a financial derivative that allows trading on price movements without owning the underlying asset. |
| **Drawdown**      | The peak-to-trough decline in an account's equity, expressed as a percentage.                                               |
| **EMA**           | Exponential Moving Average; a type of moving average that gives more weight to recent prices.                               |
| **FIX API**       | Financial Information eXchange protocol; an industry-standard for electronic trading communication.                         |
| **Forex**         | Foreign Exchange Market; the global decentralized market for trading currencies.                                            |
| **MACD**          | Moving Average Convergence Divergence; a trend-following momentum indicator.                                                |
| **PRD**           | Product Requirements Document.                                                                                              |
| **RSI**           | Relative Strength Index; a momentum oscillator that measures the speed and magnitude of price movements.                    |
| **SMA**           | Simple Moving Average; a type of moving average calculated by averaging prices over a specified period.                     |
| **PEP 8**         | Python Enhancement Proposal 8; the official style guide for Python code.                                                    |
| **VPS**           | Virtual Private Server; a virtual machine sold as a service by hosting providers (not required for this project).           |
| **WFA**           | Walk-Forward Analysis; a backtesting methodology that tests strategies on sequential, unseen data windows.                  |

---

## 15. References

[1] IC Markets. "Account Overview." [https://www.icmarkets.com/global/en/trading-accounts/overview](https://www.icmarkets.com/global/en/trading-accounts/overview)

[2] Spotware. "Algorithmic trading using cTrader Algo." [https://help.ctrader.com/ctrader-algo/](https://help.ctrader.com/ctrader-algo/)

[3] IC Markets. "cTrader Raw Spread Account." [https://www.icmarkets.com/global/en/trading-accounts/ctrader-raw](https://www.icmarkets.com/global/en/trading-accounts/ctrader-raw)

[4] IC Markets. "Forex Trading Servers." [https://www.icmarkets.com/global/en/forex-trading-tools/trading-servers](https://www.icmarkets.com/global/en/forex-trading-tools/trading-servers)

[5] cTrader. "Key types of cBots." [https://help.ctrader.com/ctrader-algo/how-tos/cbots/key-types-of-cbots/](https://help.ctrader.com/ctrader-algo/how-tos/cbots/key-types-of-cbots/)

[6] Bookmap. "Key Algorithmic Trading Strategies: From Trend Following to Mean Reversion and Beyond." [https://bookmap.com/blog/key-algorithmic-trading-strategies-from-trend-following-to-mean-reversion-and-beyond](https://bookmap.com/blog/key-algorithmic-trading-strategies-from-trend-following-to-mean-reversion-and-beyond)

[7] NURP. "7 Risk Management Strategies for Automated Algorithmic Trading." [https://nurp.com/algorithmic-trading-blog/7-risk-management-strategies-for-algorithmic-trading/](https://nurp.com/algorithmic-trading-blog/7-risk-management-strategies-for-algorithmic-trading/)

[8] cTrader. "cBot trading operations." [https://help.ctrader.com/ctrader-algo/how-tos/cbots/cbot-trading-operations/](https://help.ctrader.com/ctrader-algo/how-tos/cbots/cbot-trading-operations/)

[9] cTrader. "Rate limits." [https://help.ctrader.com/ctrader-algo/documentation/rate-limits/](https://help.ctrader.com/ctrader-algo/documentation/rate-limits/)

[10] cTrader. "Advanced operations with cBots." [https://help.ctrader.com/ctrader-algo/documentation/cbots/cbot-advanced-operations/](https://help.ctrader.com/ctrader-algo/documentation/cbots/cbot-advanced-operations/)

[11] ClickAlgo. "cTrader cBot Cloud Environment vs VPS." [https://clickalgo.com/cloud-cbots](https://clickalgo.com/cloud-cbots)

[12] AlgoBuilderX. "Complete Guide to Backtesting and Optimization on cTrader." [https://news.algobuilderx.com/?p=819](https://news.algobuilderx.com/?p=819)

[13] QuantInsti. "Walk-Forward Optimization: How It Works, Its Limitations, and Implementation." [https://blog.quantinsti.com/walk-forward-optimization-introduction/](https://blog.quantinsti.com/walk-forward-optimization-introduction/)

[17] cTrader. "Python for algorithmic trading in cTrader." [https://help.ctrader.com/ctrader-algo/documentation/python-basics/](https://help.ctrader.com/ctrader-algo/documentation/python-basics/)

[18] cTrader. "How to create a trading bot in Python." [https://help.ctrader.com/ctrader-algo/how-tos/cbots/create-a-trading-bot-in-python/](https://help.ctrader.com/ctrader-algo/how-tos/cbots/create-a-trading-bot-in-python/)

[19] cTrader. "Cloud features in cTrader." [https://help.ctrader.com/ctrader-algo/documentation/cloud-features/](https://help.ctrader.com/ctrader-algo/documentation/cloud-features/)

[20] cTrader. "Third-party packages in Python algorithms." [https://help.ctrader.com/ctrader-algo/documentation/third-party-python-packages/](https://help.ctrader.com/ctrader-algo/documentation/third-party-python-packages/)

[21] cTrader. "Python parameters." [https://help.ctrader.com/ctrader-algo/documentation/python-parameters/](https://help.ctrader.com/ctrader-algo/documentation/python-parameters/)

[14] IC Markets. "Range of Products." [https://www.icmarkets.com/global/en/trading-markets/range-of-markets](https://www.icmarkets.com/global/en/trading-markets/range-of-markets)

[15] cTrader. "Fault tolerance." [https://help.ctrader.com/ctrader-algo/documentation/fault-tolerance/](https://help.ctrader.com/ctrader-algo/documentation/fault-tolerance/)

[16] cTrader. "Custom fitness functions." [https://help.ctrader.com/ctrader-algo/how-tos/cbots/custom-fitness-functions/](https://help.ctrader.com/ctrader-algo/how-tos/cbots/custom-fitness-functions/)
