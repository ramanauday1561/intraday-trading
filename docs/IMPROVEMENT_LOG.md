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
