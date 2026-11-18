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

## 2026-11-11 — ATR range gate experiment (pre-registered before running it)

**Trigger:** 11 Nov live: the ATR_RANGE blocker vetoed 7-8 of 28 stocks at every checkpoint, often the strongest
candidates, while in the 5-minute backtest it was ~5% of all blocks. The gate compares `rr x base stop` with 90% of
the daily ATR, where the base stop widens with VIX (k = 1.0 / 1.25 / 1.5) and rr = 3 for strong TRENDING signals.
So at VIX ≥ 20 every trade is blocked, and strong trending signals are always blocked, although every trade has
used a 2R target (on a 2x stop) since 2026-11-01.

**Variants (`ATR_RANGE_MODE`):** `current` (as tested so far), `rr2` (judge every signal on rr = 2), `off` (no gate).

**Decision rule (fixed now, before the results):** selection = the live rules (first signal per stock per day +
day rules). Sessions split 60/40 chronologically. A variant replaces `current` only if its TEST mean net R per trade
beats `current`'s TEST mean in **both** the 5-minute archive replay **and** the 2-year hourly backtest, and its TRAIN
mean is not worse than `current`'s TRAIN mean in either. If both variants qualify, the one with the higher average
TEST mean across the two datasets wins. Otherwise keep `current`. An adoption restarts the forward test.

## 2026-11-17 — Realism fixes, then two pre-registered experiments

**Realism fixes (not tuning; they make the tests match what the VPS order server does):**
- Costs: Zerodha's published intraday schedule (`engine/charges.py`: brokerage min(Rs 20, 0.03%) an order, STT 0.025%
  sell side, exchange 0.00297%, SEBI, GST 18%, stamp 0.003% buy side) + 0.01% slippage a side, at the server's
  Rs 20,000 / 5% sizing. ≈ 0.126% of position value a round trip (≈ 0.06R), about double the old flat 0.06%.
- Exit time 15:05 instead of 15:15 (the server closes at 15:05; stocks enter the closing auction at 15:15).

**Experiment A — ATR range gate** (rule registered 2026-11-11 above), re-run on the new baseline.

**Experiment B — profit target (`EXIT_TARGET_MODE`):** `2R` (current) vs `none` (no target: stop, breakeven after +1R,
flat at 15:05). Trigger: almost every live trade since 9 Nov closed at the time exit without reaching 2R.
**Decision rule (fixed before running):** same as A — `none` replaces `2R` only if its TEST mean net R beats `2R`'s in
**both** the 5-minute (Yahoo 60 days + archive) and the 2-year hourly backtests, with TRAIN not worse in either.
Otherwise keep `2R`. Selection = the live rules (first signal per stock per day + day rules). An adoption restarts
the forward test.

**Results (2026-11-17, real charges, 15:05 exit, live selection rules):**

| Experiment | Data | Variant | TRAIN mean R [95% CI] | TEST mean R [95% CI] |
|---|---|---|---|---|
| A | 1h, 2y | current | −0.101 [−0.178, −0.023] | −0.119 [−0.204, −0.030] |
| A | 1h, 2y | rr2 | identical to current | identical to current |
| A | 1h, 2y | off | — | −0.118 |
| A | 5m, 75 days | current | −0.154 | +0.001 |
| A | 5m | rr2 | −0.159 | +0.000 |
| A | 5m | off | −0.154 | −0.010 |
| B | 1h, 2y | 2R | −0.101 [−0.179, −0.021] | −0.077 [−0.166, +0.017] |
| B | 1h, 2y | none | −0.099 [−0.179, −0.017] | −0.076 [−0.168, +0.020] |
| B | 5m, 75 days | 2R | −0.154 [−0.229, −0.085] | +0.001 [−0.140, +0.158] |
| B | 5m | none | −0.163 [−0.238, −0.093] | +0.001 [−0.140, +0.158] |

**Decisions:** A — keep `current` (no variant beats it on TEST in both datasets). B — keep `2R` (`none` ties on the
5-minute TEST and is worse on 5-minute TRAIN). No engine change; the forward test continues.

**Finding that matters more than either experiment:** with real charges, the 2-year hourly backtest loses about
0.08–0.12R a trade and the 5-minute TRAIN period loses 0.15R, with confidence intervals below zero. Live paper so far
(2–16 Nov, 20 trades) is +0.165R a trade but its CI still includes zero. Next evidence: a multi-year 5-minute test from
Kite history on the VPS (`server/README.md`, "Long backtests with Kite history").

**4-year 5-minute results (Kite history on the VPS, 2022-10-11..2026-11-17, 1,011 sessions, 606 TRAIN):**

| Experiment | Variant | TRAIN mean R [95% CI] | TEST mean R [95% CI] |
|---|---|---|---|
| B | 2R | −0.067 [−0.100, −0.034] (n=1391) | −0.106 [−0.149, −0.065] (n=1137) |
| B | none | −0.065 [−0.099, −0.032] | −0.099 [−0.145, −0.057] |
| A | current | −0.067 [−0.100, −0.034] | −0.106 [−0.149, −0.065] |
| A | rr2 | −0.069 [−0.101, −0.040] (n=1451) | −0.107 [−0.149, −0.069] |
| A | off | −0.069 [−0.101, −0.035] (n=1478) | −0.104 [−0.143, −0.065] (n=1226) |

**Decisions unchanged:** A — `off` edges `current` on TEST by 0.002R but is worse on TRAIN, so it fails the rule; keep
`current`. B — `none` is better by ≤0.007R on 4 years but tied on the 60-day data named in the rule; keep `2R`.
Neither knob matters: every variant loses about 0.07–0.11R a trade after charges over 4 years, with the CI below zero
in both halves. The next step is to find *where* it loses (`engine.analyze`, `server/analyze.sh`), not to tune exits.

## 2026-11-18 — Entry filters (owner's decision, adopted before testing)

**Trigger:** 18 Nov live: four momentum shorts, −1.33R gross (≈ −1.6R net). Entries came after most of the morning
fall (NTPC, BEL near their day lows), one at 14:00 with an hour left (AXISBANK), and costs of ~0.085R a trade.

**Change (`ENTRY_FILTERS`, default `late,chase,cost`), adopted on the owner's instruction before the 4-year test:**
- `late`: no new entries on bars after 13:30.
- `chase`: no short once price is more than 0.75 daily ATR below the day's high (long: above the day's low). 0.75 is
  about the 80th percentile of that distance at checkpoints in the 5-minute archive.
- `cost`: no entry when the round trip's charges at the server's sizing exceed 0.10R.

**Replay of 18 Nov (archive):** off 5 trades −0.44R net each (−2.19R); on 4 trades −0.58R each (−2.33R). The late
filter removed the day's only winner (AXISBANK +0.23R); chase did not catch NTPC/BEL. One day is anecdotal.

**Test (rule fixed now):** `ENTRY_FILTERS` none vs `late,chase,cost` on the 4-year Kite 5-minute history and the
2-year hourly data (the hourly grid ends at 13:15, so there it tests chase and cost only). The filters stay only if
their TEST mean net R is not worse than `none` on the 4-year data with TRAIN not worse either; otherwise they revert
to `none`.

## 2026-11-18 — Where it loses: 4-year breakdown (`engine.analyze`, current settings, before entry filters)

2,528 trades, 1,011 sessions. **Gross +0.013R a trade before charges, charges 0.097R, net −0.084R [−0.110, −0.058].**
The loss is the charges: the signals have no measurable edge before costs.

- **Exits:** 90% of trades (2,284) close at 15:05; 2R target hit 28 times (1.1%), stop 196 (7.8%). In practice each
  trade is "hold from signal to 15:05", and its direction is right about as often as wrong (EOD trades: gross +0.076R).
- **No group has a real edge.** Every positive group is small and its two halves disagree. Losses (CI < 0) in both
  directions, both setups, all regimes, every year 2023–2026, nearly every signal time.
- **Signal strength doesn't rank trades:** conviction 100 −0.063R, |composite| 0.6–0.8 −0.047R vs 0–0.3 −0.095R:
  no clear gradient.
- **Cost:** net loss grows with cost (0.04–0.06R: −0.016; 0.12–0.2R: −0.133) while gross is flat (≈ +0.01), and
  wider stops cost less (stop ≥ 2% of price: −0.023R). So the `cost` filter removes the most expensive trades for a
  logical reason; it cannot make the strategy profitable because gross ≈ 0.
- **Time:** 14:00 is the worst slot (30% win, −0.110R) but 13:30 and 14:30 are better than 10:00–11:30. A blanket
  13:30 cut-off is not supported by this table; the `late` filter's 4-year test decides.
- **Stocks:** ICICIBANK, HDFCBANK, HINDUNILVR lose in both halves; with ~30 stocks some extremes are expected by
  chance, so no stock list is adopted from this.

**Conclusion:** filters can only shrink the loss toward zero by trading less. A profitable version needs a signal with
an edge before costs that is larger than ~0.1R a trade; the current pillars and blockers don't have one.
