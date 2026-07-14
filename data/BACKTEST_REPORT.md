# ArgoAlgo Backtest Report

**Data**: EURUSD H1, 2023-01-01 → 2025-12-31 (17,877 bars, ~3 years).
**Source**: histdata.com M1 → resampled H1 UTC.
**Costs**: 1-pip spread on entry, $0.07 commission per 1000 units round-trip (IC Markets Raw Spread).
**Position sizing**: 1% of $698 starting balance, clamped to 1000-unit step.

## Headline result

| Strategy | Config | Trades | Win% | PF | Net $ | Expectancy | Max DD |
|----------|--------|--------|------|-----|--------|--------|--------|
| **TF** | UNCAPPED | 460 | 57.2% | 0.63 | **-$275** | -$0.60 | 39.5% |
| TF | LIVE_CONFIG | 25 | 56.0% | 1.46 | +$23 | +$0.90 | 2.6% |
| TF | RELAXED_SL | 243 | 56.0% | 0.68 | -$140 | -$0.58 | 20.1% |
| **MR** | UNCAPPED | 549 | 71.0% | 1.02 | +$20 | +$0.04 | 19.3% |
| MR | LIVE_CONFIG | 96 | 70.8% | 1.06 | +$10 | +$0.11 | 5.4% |
| MR | RELAXED_SL | 489 | 69.5% | 1.02 | +$23 | +$0.05 | 17.2% |
| **BO** | UNCAPPED | 810 | 53.7% | 0.62 | **-$487** | -$0.60 | 70.1% |
| BO | LIVE_CONFIG | 90 | 56.7% | 0.82 | -$38 | -$0.43 | 10.6% |
| BO | RELAXED_SL | 172 | 52.9% | 0.67 | -$138 | -$0.80 | 20.5% |

**Verdict**:
- **TrendFollowing has no edge.** Loses in all 3 years. Net -$275 over 460 trades.
- **Breakout has no edge.** Loses in all 3 years. Net -$487 over 810 trades.
- **MeanReversion is essentially break-even.** Slightly positive cumulatively, but only one year (2023) carries the result.

## Year-by-year (UNCAPPED — pure strategy expectancy)

| Year | TF | MR | BO |
|------|------|------|------|
| 2023 | -$108 | **+$76** | -$187 |
| 2024 | -$109 | -$57 | -$181 |
| 2025 | -$58 | +$1 | -$119 |

A real edge is positive in most years. Only MR is positive >1 year, and even then 2024 was a meaningful loser.

## Why LIVE_CONFIG looks deceptively good for TF

TF UNCAPPED: -$0.60/trade across 460 trades.
TF LIVE_CONFIG: +$0.90/trade across **only 25** trades.

The 20-pip MAX_SL_PIPS cap rejects 95% of TF signals. The 25 that pass are not a representative sample — they are the small subset where the strategy happens to have produced an SL ≤ 20 pips, which dodges the worst losses by accident. With n=25, this is statistical noise (the same TF strategy with RELAXED_SL at SL up to 50 pips returns to -$0.58/trade).

In other words: **the Phase B/C 20-pip cap was a survivorship-bias filter, not a strategy improvement.**

## Why MR's positive expectancy isn't tradeable

MR's $0.04-0.11/trade expectancy at ~150 trades/year = $5–17/year on $698 = **0.7–2.4% annual**. After tax/slippage/the inevitable bad year (2024 was -$57), this is at best an expensive way to match a savings account, at worst negative.

A real "very profitable" strategy on this account size would need expectancy of at least $0.50–$1.00/trade with 100+ trades/year — none of these three deliver that, even unconstrained.

## Implications

1. **No version of the current strategy stack should be deployed live.** All three are statistically losing or break-even.
2. **The PRD's strategy choices were textbook combinations chosen without backtesting.** EMA crossover, Bollinger+RSI mean reversion, and Donchian breakout are well-known and well-known not to work in their basic form on EURUSD H1.
3. **The Phase B/C redesign disabled the only strategy with positive expectancy** (MR) and tightened parameters around two losing strategies. Live results matched: 22 trades over 4 weeks for +$0.62 (essentially zero) and then -$2.09 in the redesign.
4. **No amount of parameter tuning can give a structurally negative-edge strategy positive expectancy.** Sweeping ADX threshold, ATR multipliers, etc. would only find local in-sample optima that fail OOS.

## What the architecture got right

- DataProvider / RiskManager / OrderExecutor / StrategyEngine separation is clean.
- The risk-management plumbing (drawdown caps, daily reset, throttling, trailing) is sound.
- The strategy interface is simple and replaceable.

The architecture supports any strategy. The strategies themselves are the problem.

## Recommendation

Stop deploying these strategies. Two viable paths forward:

**Option A — Use cTrader IDE Optimizer to search a wider space**, with the existing architecture, on EURUSD H1. The problem: optimization will find an in-sample winner (genetic optimizers always do), and the walk-forward step will likely show it doesn't generalise. This is a 1–2 week exercise that probably ends in "no parameter set works" — but worth doing once to be sure.

**Option B — Replace the strategies with a documented-edge approach.** Examples worth investigating:
- **Time-of-day mean reversion at session opens** (e.g., London open fade; Asian range breakout).
- **Reversion to VWAP/anchored VWAP**.
- **Carry-aware overnight trades** (broker offers swap; certain pairs like AUDJPY have a tradeable carry edge).
- **Statistical arbitrage / pairs trading** (requires multi-symbol data; would also need IC Markets symbols beyond EURUSD).

These need to be backtested using the harness built here BEFORE writing strategy classes.

**Don't do**: another redesign of the existing three strategies. We have proof they don't work.
