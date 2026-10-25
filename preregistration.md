# Pre-registration: hypothesis H1

Registered: 2026-10-25 (see git history for the commit timestamp).

**Hypothesis H1.** Within this app's own signals, trades that **oppose** a material news signal
(news sentiment score |s| > 0.2 pointing the opposite way to the trade) have a **mean net R > 0**
after costs (0.06% round trip), using the engine rules exactly as committed at registration.

**Why this is declared:** it was noticed *after* looking at the 2026-07 to 2026-10 (5-minute) and
2024-10 to 2026-10 (hourly) backtests, where opposed-news trades did best (+0.19R, +0.10R gross).
Because it was found by inspecting that data, that data is **development data and cannot test H1**.

**Test data:** only sessions strictly **after 2026-10-25**, collected by the daily archive job
(real prices and headlines with publication times). Run weekly by `backtest.yml`
(`python -m engine.backtest --after 2026-10-25 --out data/forward_test.json`).

**Decision rule (fixed in advance):**
- fewer than 100 qualifying trades -> `INSUFFICIENT_DATA` (no conclusion either way);
- >= 100 trades and the 95% session-bootstrap CI of mean net R entirely above 0 -> `SUPPORTED`;
- otherwise -> `NOT_SUPPORTED`.

No threshold, rule or cost assumption may be changed after forward data starts accumulating.
If any is changed, the test restarts with a new registration date and the old data becomes development data.
