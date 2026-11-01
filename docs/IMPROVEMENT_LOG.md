# Improvement log (decisions written down BEFORE results are seen)

## 2026-11-01 — Exit policy review

**Evidence that prompted it** (backtests to 2026-10-30, baseline trades):
- ~59% of trades exit at 15:15 with mean gross +0.21R (5m) / +0.26R (hourly);
  ~33% hit the stop at -1R (-1.1R net); only 8-11% reach the 2R target.
- Costs are ~0.10-0.13R per trade, larger than the gross edge.
- Conviction score shows no monotonic relation to results in either dataset, so conviction
  filters are NOT pursued (they would only fit noise).
- Time-of-day and long/short splits disagree between datasets, so they are NOT pursued.

**Hypothesis:** stops at 0.35 x daily ATR sit inside normal intraday noise; a wider stop
(fewer noise stop-outs, lower cost in R) and/or holding to the close should improve net R.

**Variants (18, fixed in advance):** stop width x1.0 / x1.5 / x2.0 of the current stop;
target 2R / 1.5R / none (hold to 15:15); move stop to breakeven after +1R yes / no.

**Decision rule:** in each dataset the policy with the best TRAIN (first 60% of sessions) mean
net R is selected. Adopt a new exit policy in the live engine only if the selected policy
beats the current policy (`stop1.0x_tgt2.0_be0`) on TEST mean net R in BOTH the 2-year hourly
and the 5-minute runs **and** both datasets select the same stop width. Otherwise keep the
current policy. An adoption is a rule change, so the H1 forward test restarts.

### Result (run 41294574030, data to 2026-10-30) — ADOPTED

Mean net R per trade (after 0.06% costs), with-news runs, which is what the live app uses:

| Dataset | Train-selected policy | TEST selected | TEST current | 95% CI (selected) |
|---|---|---|---|---|
| Hourly, 487 sessions (test n=208) | stop 2.0x, no target, breakeven | -0.047 | -0.077 | -0.119 to +0.025 |
| 5-minute, 64 sessions (test n=96) | stop 2.0x, 2R target, no breakeven | -0.066 | -0.167 | -0.167 to +0.040 |

Both conditions hold (beats current on TEST in both; both pick stop 2.0x), so the 2.0x stop is adopted.
The without-news runs agree (both also pick 2.0x: TEST -0.075 vs -0.112 hourly, -0.074 vs -0.216 5-minute).

**Target / breakeven tie-break (decided on TRAIN data only):** within the 2.0x family the variants
differ by <=0.005R on test. Combining both datasets' TRAIN results weighted by trades
(hourly n=535, 5-minute n=266): 2R + breakeven -0.0676, 2R no breakeven -0.0683,
no target + breakeven -0.0696, no target no breakeven -0.0696. Adopted: **stop 2.0x, 2R target,
breakeven after +1R** (`stop2.0x_tgt2.0_be1`).

**What this is and is not:** it cuts the loss per trade by roughly 0.03-0.10R out of sample, mainly
through fewer noise stop-outs and lower costs in R terms. It is **not** an edge: every policy is
still negative after costs and every CI includes zero. Position size halves at the same risk %
because the stop is twice as wide. Entry filters (ATR range etc.) still use the base stop, exactly
as tested. Per preregistration.md the H1 forward test restarts: sessions after 2026-11-01 count.
