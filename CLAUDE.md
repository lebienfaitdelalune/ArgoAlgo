# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project

**ArgoAlgo** — a multi-strategy algorithmic trading cBot for the **cTrader** platform (IC Markets Raw Spread account), written in Python. See `PRD.md` for full specifications and `PLAN.md` for the 10-phase implementation roadmap.

---

## Commands

```bash
# Run all tests
python3 -m pytest tests/ -v

# Run a single test file
python3 -m pytest tests/test_helpers.py -v

# Run a single test by name
python3 -m pytest tests/test_helpers.py::TestRoundToStep::test_rounds_down -v

# Run tests matching a keyword
python3 -m pytest tests/ -k "drawdown" -v
```

> pytest must be installed: `pip3 install pytest`
> Python binary is `python3` (system Python 3.9 at `/usr/bin/python3`). There is no virtual environment.

---

## Architecture

The bot follows a strict pipeline: **DataProvider → StrategyEngine → RiskManager → OrderExecutor**, orchestrated by `TradingBot` (main.py) which implements cTrader's lifecycle events (`on_start`, `on_bar_closed`, `on_tick`, `on_stop`, `on_error`).

### Module responsibilities

| Module | File | Role |
|--------|------|------|
| `TradingBot` | `main.py` | Entry point. Bootstraps all modules, implements cBot lifecycle, owns `_is_halted` flag, session/day-of-week filters |
| `Logger` | `core/logger.py` | **First module initialised.** All output routes through here — no bare `print()`. Wraps `api.Print()` + optional file I/O |
| `DataProvider` | `core/data_provider.py` | Owns all market data access: Symbol objects, Bars, indicators. Strategies never call the cTrader API directly |
| `RiskManager` | `core/risk_manager.py` | Validates every `TradeSignal` via 8-check pipeline; calculates volume; tracks drawdown and daily reset; manages trailing stops on `on_tick` |
| `StrategyEngine` | `core/strategy_engine.py` | Iterates active strategies per symbol, applies ADX-switching mode, returns actionable `TradeSignal` list |
| `OrderExecutor` | `core/order_executor.py` | Single point of contact for cTrader API order calls; enforces rate limits; uses label prefix to identify bot positions |
| `UIPanel` | `ui/panel.py` | On-chart display + Panic Button (calls `close_all_positions` + `_halt_trading`) |

### Data flow (per bar close)
```
on_bar_closed
  → RiskManager.check_day_rollover()
  → RiskManager.check_drawdown_limits()  # halt if breached
  → _is_session_active()                 # hour/day/Friday filters
  → DataProvider.update()
  → StrategyEngine.evaluate(symbols)     # → list[TradeSignal]
  → RiskManager.validate(signal)         # → TradeInstruction
  → OrderExecutor.execute(instruction)
  → StrategyEngine.check_exits(positions)
```

### Key data contracts

- **`TradeSignal`** (`models/trade_signal.py`) — strategy output; contains `direction`, `stop_loss_pips`, `take_profit_pips`, `entry_price`, `metadata`
- **`TradeInstruction`** (`models/trade_instruction.py`) — risk-validated; adds `volume_units` and `validated` flag; only validated=True reaches `OrderExecutor`
- **`PerformanceSnapshot`** (`models/performance.py`) — feeds `UIPanel` and daily summary logs
- **`RiskParams`** (`core/risk_manager.py`) — dataclass holding all risk parameters, passed at construction

### Strategy pattern

All three strategies (`TrendFollowingStrategy`, `MeanReversionStrategy`, `BreakoutStrategy`) implement `IStrategy` (`strategies/base_strategy.py`), which requires:
- `evaluate(symbol) -> TradeSignal` — stateless evaluation per bar
- `should_close(position) -> bool` — strategy-driven exit logic

The `_no_signal(symbol)` helper on `IStrategy` returns a `Direction.NONE` signal and is the correct early-return pattern.

### Coding conventions

- PEP 8, line length 100, Google-style docstrings
- Type annotations on every function signature
- Every class needs `__repr__`
- No magic numbers — all defaults live in `utils/constants.py` (`Defaults` class)
- No `print()` — use `self._logger.info/debug/warning/error()`
- Order labels follow `ArgoAlgo_{2-char-strategy-abbrev}_{SYMBOL}` (e.g. `ArgoAlgo_TF_EURUSD`)

### Implementation status

| Phase | Status | Notes |
|-------|--------|-------|
| 1 — Scaffold & architecture | ✅ Complete | All stubs, models, constants, Logger, TradingBot lifecycle |
| 2 — Core infrastructure | ✅ Complete | Full lifecycle events, session/Friday filters, halt+push notification, daily summary on stop |
| 3 — DataProvider | ✅ Complete | Symbol/bars loading, 10 indicators per symbol, multi-TF, spread check, has_sufficient_history |
| 4 — RiskManager | ✅ Complete | 7-check validate pipeline, volume sizing, drawdown monitoring, day rollover, ATR/fixed SL, trailing stops |
| 5 — Strategies | ✅ Complete | TF (EMA crossover+ADX), MR (BB+RSI+ADX filter), BO (Donchian+ATR); all evaluate()+should_close() |
| 6 — OrderExecutor | ✅ Complete | execute() market orders, close_position/all, modify_sl, rate limiting (NEW_ORDERS/MODIFY_PROTECTION_L1) |
| 7 — UIPanel + Notifications | ✅ Complete | initialize/update/set_status, Panic Button, _format_panel_text, graceful chart API degradation |
| 8 — Backtesting & optimization | ✅ Complete | fitness.py, BacktestValidator (7 KPIs), ParamRange + strategy ranges, WFA windows/efficiency, SensitivityAnalyzer |
| 9 — Demo testing | ✅ Complete | 65 integration tests (AC-001–AC-008); demo_issues.md tracking template |
| 10 — Live deployment | ✅ Complete | 44 deployment tests; ramp-up protocol, rate limits, survivability (672 bars) |

Methods marked `# Implemented in Phase N` are stubs returning safe defaults — they compile and test but produce no real behaviour yet.

### cTrader API notes

- The real `api` object is injected by cTrader at runtime; in tests, pass a `MagicMock()`.
- `api.Print(str)` is the only log output available in cTrader Cloud (no file I/O).
- Rate limits to respect: 500 new orders/min, 100 cancels/min, 1000 SL modifications/min.
- Bot Cloud deployment supports Python with third-party packages (NumPy, Pandas allowed).
