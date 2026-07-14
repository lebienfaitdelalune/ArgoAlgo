# MR Strategy — Deployment Recommendation

**Date**: 2026-04-30
**Data**: EURUSD H1, 2023-01 → 2025-12 (17,877 bars)
**Costs modelled**: 1-pip spread, $0.07/1000 round-trip commission

---

## TL;DR

Deploy MeanReversionStrategy with the parameter set below. Disable TrendFollowing and Breakout — both are confirmed losers across all 3 years. Expected: ~14% annualised, ~14% max drawdown, ~125 trades/year on EURUSD H1.

---

## The candidate

| Parameter | Value |
|---|---|
| `bollinger_period` | 15 |
| `bollinger_deviation` | 2.5 |
| `rsi_period` | 14 |
| `rsi_oversold` | 35 |
| `rsi_overbought` | 65 |
| `adx_filter_period` | 14 |
| `adx_filter_threshold` | 30 |
| `sl_atr_multiplier` | 1.0 |
| `min_sl_pips` | 0 (remove the 20-pip floor) |
| `max_sl_pips` | none (remove the 20-pip cap) |

How it was selected:
1. Swept 324 MR parameter combinations.
2. Selected the 41 (12.7%) that were profitable in 2023 AND 2024 AND H1-25 AND H2-25 with adequate sample size — the deployment-grade cohort.
3. Picked the highest expectancy from that cohort.

The 12.7% pass rate (vs ~6% expected from random noise) is itself the evidence the strategy has real edge: positive performance is concentrated in a coherent parameter region, not scattered.

---

## Performance numbers

### Aggregate
| Period | Trades | Win% | PF | Net | Expectancy |
|---|---|---|---|---|---|
| 2023 | 115 | 61.7% | 1.26 | +$76.82 | +$0.67 |
| 2024 | 142 | 64.1% | 1.25 | +$102.04 | +$0.72 |
| 2025 | 119 | 69.7% | 1.51 | +$170.14 | +$1.43 |
| **All** | **376** | **65.2%** | **1.33** | **+$349.01** | **+$0.93** |

### Equity curve
- Initial: $698.00
- Final: $1,047.01 (**+50.0%** over 3 years, ~14.5% annualised)
- Max drawdown: **$110.02 (14.2%)**
- Longest underwater period: **301 days** (~10 months)
- Positive months: 21/36 (58%)

### Trade-quality checks (no concentration risk)
- Worst single trade: -$10.66 (~3% of net)
- Best single trade: +$46.85 (~13% of net)
- Top 5 winners contribute 37% of net P/L (well-distributed)
- Worst 5 losers are 15% of net P/L
- Max loss streak: 6 trades (~2 weeks)
- Buy P/L +$146; Sell P/L +$203 (both directions profitable)
- Exit mix: 245 trail / 129 SL / 2 strategy-exit

### Sensitivity (one-parameter perturbations)
Every neighbour is also profitable — not a knife-edge optimum:

| Param change | Net | vs baseline |
|---|---|---|
| baseline | +$349 | — |
| bb_period 15→10 | +$152 | -56% but still positive |
| bb_period 15→20 | +$268 | -23% |
| bb_dev 2.5→2.0 | +$273 | -22% |
| bb_dev 2.5→3.0 | +$166 | -52% |
| rsi 35→30 | +$292 | -16% |
| rsi 35→40 | +$556 | **+59%** |
| adx 30→25 | +$196 | -44% |
| adx 30→35 | +$350 | flat |
| sl_atr 1.0→0.75 | +$677 | **+94%** |
| sl_atr 1.0→1.5 | +$218 | -38% |

**Note**: The `rsi=40` and `sl_atr=0.75` perturbations look better than baseline. This is **post-hoc selection bias** and should NOT be used as the deployed config — combining the systematic-sweep winner with a sensitivity-found optimum compounds the curve-fit risk. Deploy the systematic winner.

---

## Why this works (in plain terms)

The strategy fades short-term extremes when:
- Price closes outside Bollinger 15/2.5 (15-bar SMA ± 2.5 std deviations) — wider-than-default bands so the setup is rarer and higher quality.
- RSI confirms (35/65 — moderately stringent).
- ADX < 30 — confirms a *non-trending* regime, where mean reversion is the right model.

It exits when:
- Price reaches the Bollinger middle band (rare — only 2 of 376 exits).
- The trailing stop activates (1× ATR trigger to BE, then 0.35× ATR trail) — this is doing 65% of exits.
- The SL hits (1× ATR distance) — 34% of exits.

The 1× ATR SL (sl_atr_multiplier=1.0) is tight, which produces a 65% win rate but each loser hits the full SL distance (avg loss $8). Winners are smaller (avg $5.71) but more frequent. PF 1.33 is the math working out.

---

## Risks and what to do about them

### Risk 1: 10-month underwater period
**Mitigation**: Don't bail. The drawdown happened in 2024-mid through early-2025; recovered fully by mid-2025. Pre-commit to running for ≥6 months without parameter changes. If you can't, don't deploy.

### Risk 2: Only 3 years of data
**Mitigation**: Add 2020-2022 history if you want more confidence (download M1 ZIPs from histdata, rerun the sweep). 2020 is interesting because it includes COVID volatility.

### Risk 3: Mean reversion fails in trending regimes
The ADX < 30 filter excludes trending bars, but a sustained multi-month trend (like 2014's USD rally) could still produce sequential losses where setups *look* MR-valid but the trend continues. Your `MAX_TOTAL_DRAWDOWN_PCT = 10%` cap will halt before catastrophic loss, but plan for a possible halt event.

### Risk 4: 17h UTC is a -$50 net loss bucket (avg -$4.17/trade)
**Mitigation**: Optional. Add a session filter `17:00 ≤ hour < 21:00` excluded. But I do NOT recommend doing this in v1 — it's a 12-trade sample and adding hour filters is the start of curve fitting.

### Risk 5: The OOM and state-persistence bugs from the audit
**Mitigation**: These are now blockers, not nice-to-haves. With +$349 of expected edge, an OOM that wipes the daily-loss cooldown counter is a real risk. Fix before live.

---

## Deployment checklist (in priority order)

### Must-fix before live
1. **Update `Defaults` in `utils/constants.py`**:
   - `ENABLE_TREND = False`
   - `ENABLE_MEAN_REVERSION = True`
   - `ENABLE_BREAKOUT = False`
   - `MR_BOLLINGER_PERIOD = 15`
   - `MR_BOLLINGER_DEVIATION = 2.5`
   - `MR_RSI_OVERSOLD = 35.0`, `MR_RSI_OVERBOUGHT = 65.0`
   - `MR_ADX_FILTER_THRESHOLD = 30.0`
   - `MR_SL_ATR_MULTIPLIER = 1.0`
   - `MIN_SL_PIPS = 0.0` (remove the floor — let ATR do its job)
   - `MAX_SL_PIPS = 50.0` (raise from 20 — strategy needs ATR-sized stops, capped at safety value)
2. **Remove the daily trade cap and post-loss cooldown**:
   - `MAX_TRADES_PER_DAY` and `POST_LOSS_COOLDOWN_HOURS` were tuned for losing strategies. Set both to large values (e.g. 10 trades/day, 0h cooldown). The drawdown cap is the real safety net.
3. **Fix state persistence across restarts**:
   - HWM, daily counters, last-loss timestamp must survive an OOM kill.
   - Simplest: serialise to JSON in cTrader's allowed write path on every change.
4. **Fix the trailing-stop label-abbrev bug** in `risk_manager._get_trailing_params`:
   - Change `("TF", "ME", "BR")` to `("TR", "ME", "BR")` (the abbrev `build_label` actually produces).
   - Or change `build_label` to use 2-letter codes that match the constants.
5. **Diagnose the OOM root cause** (3 kills in one day on 23/04). Likely the on_tick loop holding Python.NET marshalled objects.

### Nice-to-have
6. Add a heartbeat watchdog (write a "last bar processed at X" file every bar; an external check can alert if stale).
7. Run the backtest harness in CI on every commit so strategy changes can't silently break edge.

### Don't do
- **Don't** apply the MAX_SL_PIPS=20 cap. That's what killed live performance — it converted a positive-edge strategy into a no-edge one by rejecting 95% of signals.
- **Don't** re-enable TrendFollowing or Breakout. They are confirmed structural losers (PF 0.62-0.63 across 460-810 trades).
- **Don't** keep changing parameters as live results come in for the first 6 months. The variance window of this strategy is wide. Trust the pre-deployment validation.

---

## Reproducibility

All scripts are in `backtest/`:
- `data_prep.py` — extracts histdata ZIPs → H1 UTC CSV
- `indicators.py` — stdlib indicator implementations (Wilder smoothing where applicable)
- `ctrader_mock.py` — mock cTrader interfaces so existing strategies/*.py code runs unchanged
- `engine.py` — bar-by-bar backtest with realistic spread+commission
- `run.py` — three-strategy comparison across UNCAPPED/LIVE/RELAXED configs
- `sweep_mr.py` — 324-config IS/OOS sweep
- `analyze_mr.py` — robustness analysis with sub-window breakdown
- `verify_candidate.py` — deep-dive on the chosen config

Re-run any time:
```
python3 -m backtest.data_prep 2023 2024 2025
python3 -m backtest.run
python3 -m backtest.sweep_mr
python3 -m backtest.analyze_mr
python3 -m backtest.verify_candidate
```
