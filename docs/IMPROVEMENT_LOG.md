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

## 2026-11-09 — Day rules (pre-registered before the 2026-11-10 session)

**Trigger:** 9 Nov live day: 8 trades on a flat Nifty (+0.06%), 5 short and 3 long, about -2R before costs
(-₹8,335 at 5% risk). Three of the trades existed only in the live list (10-minute checks), not in the
15-minute replay.

**Changes (all in one rule set, `engine/dayrules.py`, used identically by the live app and the replays):**
1. **Direction lock:** after the day's first trade, trades in the opposite direction are skipped. Within one
   checkpoint the strongest |composite| signal counts as first.
2. **Daily loss limit:** no new trades once trades closed earlier that day total -2R or worse.
3. **15-minute checkpoint grid live:** live signals are evaluated on the latest 10:00, 10:15, … 14:30 bar,
   exactly like the replays. Journal outcomes still use every completed bar.
4. **No cap on trades per day** (owner's choice): every signal that passes all filters is taken.
5. Default risk 1% per trade (was 5%). Conviction is no longer shown as a score, because it has not predicted results.

**Evidence and its limits (in-sample, found on the same data, so likely optimistic):** in both backtests
trades against the day's first signal averaged -0.13R. Direction lock alone on the stored trades:
5-minute (69 sessions) -0.098R → -0.082R per trade; hourly (2 years) -0.053R → -0.016R. **Still negative
after costs.** No edge is claimed.

**Decision rule (judged only on sessions after 2026-11-09):** after 20 forward sessions, keep the day rules
if the forward mean net R per trade is better than the same sessions replayed without them
(`apply_filters(day_rules=False)`). The H1 forward test restarts: sessions after 2026-11-09 count.
