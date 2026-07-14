# Demo Issues Log — ArgoAlgo Phase 9

Tracks all bugs, anomalies, and observations discovered during the 2-week
IC Markets **demo account** validation run (Phase 9).

---

## Format

| Field | Description |
|---|---|
| **ID** | Sequential issue identifier (e.g. `DEMO-001`) |
| **Date/Time** | UTC timestamp when issue was first observed |
| **Severity** | `P0` critical / `P1` major / `P2` minor / `P3` cosmetic |
| **Module** | Affected module (`RiskManager`, `OrderExecutor`, etc.) |
| **Description** | What went wrong or what unexpected behaviour was observed |
| **Reproduction** | Steps or conditions that trigger the issue |
| **Root Cause** | Analysis of the underlying cause |
| **Fix** | Code change applied (PR/commit reference if applicable) |
| **Status** | `Open` / `In Progress` / `Fixed` / `Won't Fix` |

---

## Open Issues

<!-- Add new issues here. Use the template row below. -->

| ID | Date/Time (UTC) | Severity | Module | Description | Status |
|---|---|---|---|---|---|

---

## Resolved Issues

| ID | Date/Time (UTC) | Severity | Module | Description | Root Cause | Fix | Closed |
|---|---|---|---|---|---|---|---|
| DEMO-001 | 2026-03-10 16:46 | P0 | All imports | `ImportError` on all package-style imports (`utils.constants`, `core.logger`, etc.) — bot crashed immediately at startup | cTrader Cloud `InMemoryModuleFinder` uses flat namespace; `utils.constants` resolves to `None`, `constants` resolves fine. Already handled by `_alias()` in `ArgoAlgo_main.py`; added `try/except ImportError` fallback to all source files for extra resilience | `try/except ImportError` added to 12 source files | 2026-03-10 |
| DEMO-002 | 2026-03-10 16:52 | P0 | `main.py` / `RiskManager` | `'NoneType' object has no attribute 'Balance'` crash in `_bootstrap_risk_manager` during `on_start` | `self._api` is the cTrader Robot instance but `self._api.Account` is `None` during early `on_start` initialisation; the existing `if self._api else` guard did not protect against a non-None api with a null `Account` | Wrapped `initial_balance` assignment in `try/except AttributeError`, falls back to `10_000.0` (`main.py:~473`) | 2026-03-10 |

---

## Issue Template

```
### DEMO-XXX — <Short Title>

- **Date/Time (UTC):** YYYY-MM-DD HH:MM
- **Severity:** P0 / P1 / P2 / P3
- **Module:** <module name>
- **Description:**
  <What happened. Include log excerpts if relevant.>
- **Reproduction:**
  1. <Step 1>
  2. <Step 2>
- **Root Cause:**
  <Analysis>
- **Fix:**
  <Code change description. Commit SHA or PR link.>
- **Status:** Open / In Progress / Fixed / Won't Fix
```

---

## Demo Run Summary

| Metric | Value |
|---|---|
| Demo start date | 2026-03-10 |
| Demo end date | |
| Total trading days | |
| Crashes / exceptions | 2 (DEMO-001, DEMO-002) — both fixed |
| Drawdown halts triggered | |
| Friday close events | |
| Total trades executed | |
| Win rate | |
| Net P/L | |
| Max daily drawdown | |
| Backtest win rate (baseline) | |
| Win rate deviation from baseline | |
| All AC passed (AC-001 to AC-008) | Yes / No |

---

## Monitoring Log

Record brief notes from each 4-hour monitoring session below.

| Date/Time (UTC) | Observer | Log Status | P/L | Open Positions | Notes |
|---|---|---|---|---|---|
| 2026-03-10 18:44 | Alberto | Clean | — | 0 | Bot started successfully after fixing DEMO-001 and DEMO-002. Awaiting first bar close and trade signals. |
