"use strict";
const DATA_URL = "data/signals.json";
const VAPID_PUBLIC = "BISlGtzJw0dzaj5ikAkMcMRqCsv4VHbSulg9dtFFWR6XKlsp4bprM8nVnw1aAwnvo7EXRksw_zcD-gfqhSymRg4"; // public half only; the private key lives in a GitHub secret
const $ = (id) => document.getElementById(id);
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const VIEWS = ["TODAY", "ACTION", "LONG", "SHORT", "ALL", "RECORD", "BACKTEST"];
const state = { data: null, filter: VIEWS.includes(store.get("view", "TODAY")) ? store.get("view", "TODAY") : "TODAY", capital: store.get("capital", null), riskPct: store.get("riskPct", null) };

// DOM builder: text is always set via textContent, so RSS headlines can never inject markup.
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
}
const fmt = (n, d = 2) => (n == null || Number.isNaN(n) ? "—" : Number(n).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));
const sgn = (n, d = 2) => (n == null ? "—" : (n > 0 ? "+" : "") + fmt(n, d));
const cls = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "mu");
const label = (s) => String(s || "").replace(/_/g, " ").toLowerCase().replace(/^\w|\s\w/g, (m) => m.toUpperCase());

// Paper money: every suggested trade is "taken" with the ⚙ capital and risk %, sized exactly like the app's
// suggested quantity (risk / stop distance, capped at 5x capital), with 0.06% round-trip costs.
const COST_PCT = 0.06, LEVERAGE = 5;
function account() {
  return { cap: state.capital ?? state.data?.meta.capital ?? 100000, risk: state.riskPct ?? state.data?.meta.risk_pct ?? 5 };
}
function paper(long, fill, exit, sd, sizeMult) {
  const { cap, risk } = account();
  if (!(sd > 0) || fill == null || exit == null) return null;
  const q = Math.max(0, Math.min(Math.floor((cap * risk / 100) * (sizeMult ?? 1) / sd), Math.floor((cap * LEVERAGE) / fill)));
  const pnl = (long ? 1 : -1) * (exit - fill) * q - (fill * q * COST_PCT) / 100;
  return { q, invested: q * fill, pnl };
}
const rupees = (n) => (n < 0 ? "−₹" : "₹") + fmt(Math.abs(n), 0);
const signedRupees = (n) => (n > 0 ? "+" : "") + rupees(n);

function qty(s) {
  const risk = (state.capital ?? state.data.meta.capital) * ((state.riskPct ?? state.data.meta.risk_pct) / 100) * s.risk.size_multiplier;
  const d = s.execution.stop_distance;
  return d > 0 ? (() => { const cap = (state.capital ?? state.data.meta.capital) * 5; const qq = Math.min(Math.floor(risk / d), Math.floor(cap / s.price.ltp)); return { q: qq, risk: qq * d }; })() : { q: 0, risk };
}

// One quiet status line instead of stacked banners. Warnings only when they matter for trading right now:
// demo data, stale data during market hours, or missing stocks.
function banners(offline) {
  const m = state.data.meta, hl = m.health || {};
  const phase = { ACTIVE: "Live · new signals until 14:40", MORATORIUM: "Live · first signals from 10:10", PRE_OPEN: "Pre-open · first signals from 10:10",
    LATE: "Live · no new entries · exits by 15:15", CLOSED: "Market closed" }[m.session_phase] || m.session_phase;
  const updated = new Date(m.generated_at).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit" });
  const age = (Date.now() - new Date(m.generated_at)) / 60000;
  const marketHours = m.session_phase !== "CLOSED";
  const kite = (hl.kite || {}).connected;
  const parts = [m.mode === "demo" ? "Demo data — not real prices" : phase, `updated ${updated}`];
  if (m.mode === "live") parts.push(kite ? "Kite data" : "free data");
  let level = m.mode === "demo" ? "warn" : marketHours ? "ok" : "idle";
  if (m.mode === "live" && marketHours && age > 25) { parts.push(`last update ${Math.round(age)} min ago`); level = "warn"; }
  if (m.mode === "live" && hl.missing && hl.missing.length) { parts.push(`missing ${hl.missing.join(", ")}`); level = "warn"; }
  if (offline) { parts.push("offline — showing saved data"); level = "warn"; }
  $("status").replaceChildren(h("span", { class: "dot " + level }), parts.join(" · "));
}

function macro() {
  const M = state.data.macro;
  const item = (k, v, c) => h("span", { class: "strip-item" }, h("span", { class: "k" }, k), " ", h("b", { class: c || "" }, v));
  $("macro").replaceChildren(
    item("NIFTY", `${fmt(M.nifty.ltp, 0)} ${sgn(M.nifty.chg_pct)}%`, cls(M.nifty.chg_pct)),
    M.banknifty ? item("BANK", `${fmt(M.banknifty.ltp, 0)} ${sgn(M.banknifty.chg_pct)}%`, cls(M.banknifty.chg_pct)) : null,
    item("VIX", fmt(M.india_vix, 1)),
    item("", label(M.regime)));
}

async function backtest() {
  const el = $("list");
  state.btKey = state.btKey || "backtest"; state.bts = state.bts || {};
  if (state.bts[state.btKey] === undefined) {
    try { const r = await fetch(`data/${state.btKey}.json?t=${Date.now()}`, { cache: "no-store" }); state.bts[state.btKey] = r.ok ? await r.json() : null; } catch { state.bts[state.btKey] = null; }
  }
  const b = state.bts[state.btKey];
  const switcher = h("nav", { class: "chips" }, [["backtest", "5-minute"], ["backtest_hourly", "Hourly (2 yr)"], ["forward_test", "Forward test"]].map(([k, t]) =>
    h("button", { class: "chip" + (state.btKey === k ? " on" : ""), onclick: () => { state.btKey = k; backtest(); } }, t)));
  if (state.btKey === "forward_test" && b && b.hypothesis) {
    const H = b.hypothesis, mm = (x) => (x && x.trades ? `${x.trades} trades · win ${x.win_rate_pct}% · avg net ${sgn(x.avg_net_r, 3)}R` : "no trades yet");
    const vmap = { INSUFFICIENT_DATA: ["", "Insufficient data — no conclusion yet"], SUPPORTED: ["up", "H1 supported out-of-sample"], NOT_SUPPORTED: ["dn", "H1 not supported"] };
    const v = vmap[H.verdict] || ["", H.verdict];
    return el.replaceChildren(switcher,
      h("div", { class: "banner info" }, h("b", {}, "Pre-registered hypothesis H1"),
        "Trades that oppose a material news signal have mean net R > 0 after costs. Registered 2026-10-25; restarted after rule changes on 27 Oct and 1 Nov; only sessions after 1 Nov count. See preregistration.md in the repo."),
      h("div", { class: "box" },
        h("div", { class: "hl" }, b.period ? `Forward sessions: ${b.period.from} → ${b.period.to} (${b.period.sessions})` : "No forward sessions yet — data accumulates daily."),
        h("div", { class: "hl" }, h("b", {}, "Opposed-news trades: "), mm(H.opposed), H.opposed_ci95 ? ` · 95% CI ${sgn(H.opposed_ci95[0], 3)}…${sgn(H.opposed_ci95[1], 3)}R` : ""),
        h("div", { class: "hl" }, `Needs ≥ ${H.min_trades_required} trades before any verdict.`),
        h("div", { class: "hl mu" }, "All other trades: " + mm(H.other_trades)),
        h("div", { class: "hl " + v[0] }, h("b", {}, v[1]))));
  }
  if (!b || !b.with_news) return el.replaceChildren(switcher, h("div", { class: "empty" }, "Not available yet. Run the “Backtest” workflow in GitHub Actions."));
  const tile = (k, v, c) => h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v " + (c || "") }, v));
  const pct = (n) => (n == null ? "—" : sgn(n, 1) + "%");
  const block = (title, S) => S.trades ? h("div", {},
    h("h4", {}, title),
    h("div", { class: "macro" }, tile("Trades", S.trades), tile("Win rate", S.win_rate_pct + "%"),
      tile("Avg R gross ± SE", `${sgn(S.avg_r, 2)} ± ${fmt(S.avg_r_se)}`, cls(S.avg_r)), tile("Profit factor", S.profit_factor ?? "—"),
      tile("Avg R after costs", sgn(S.avg_net_r, 3), cls(S.avg_net_r)), tile("Avg cost / trade", fmt(S.avg_cost_r, 3) + "R"),
      tile("Return @1% risk", pct(S.return_pct_risk1), cls(S.return_pct_risk1)), tile("Return @5% risk", pct(S.return_pct_risk5), cls(S.return_pct_risk5))),
    h("div", { class: "sub" }, `Worst day: ${pct(S.worst_day_pct_risk1)} @1% · ${pct(S.worst_day_pct_risk5)} @5%`)) : h("div", { class: "empty" }, title + ": no trades");
  const m = (x) => (x && x.trades ? `${x.trades} trades · win ${x.win_rate_pct}% · avg net ${sgn(x.avg_net_r, 3)}R · PF ${x.profit_factor ?? "—"}` : "no trades");
  const cfg = (c) => `conviction ≥ ${c.min_conviction}, cost ≤ ${c.max_cost_r ?? "any"}R, max ${c.max_per_day ?? "∞"}/day`;
  const verdicts = { NO_EDGE_DEMONSTRATED: ["dn", "No edge demonstrated out-of-sample"], POSITIVE_BUT_NOT_SIGNIFICANT: ["", "Positive but not statistically significant"], EDGE_SUPPORTED_OUT_OF_SAMPLE: ["up", "Edge supported out-of-sample (CI excludes 0)"] };
  const wfBlock = (title, S) => {
    const w = S.walk_forward;
    if (!w) return null;
    const sel = w.selected;
    return h("div", {}, h("h4", {}, `Walk-forward · ${title}`),
      h("div", { class: "box" },
        h("div", { class: "hl" }, `Train ${w.train_sessions[0]} → ${w.train_sessions[1]} (${w.train_sessions[2]} sessions) · Test ${w.test_sessions[0]} → ${w.test_sessions[1]} (${w.test_sessions[2]})`),
        sel ? [h("div", { class: "hl" }, h("b", {}, "Chosen on train: "), cfg(sel.config)),
          h("div", { class: "hl" }, "Train: " + m(sel.train)),
          h("div", { class: "hl" }, h("b", {}, "Test (unseen): "), m(sel.test), sel.test_ci95 ? ` · 95% CI ${sgn(sel.test_ci95[0], 3)}…${sgn(sel.test_ci95[1], 3)}R` : ""),
          h("div", { class: "hl " + verdicts[sel.verdict][0] }, h("b", {}, verdicts[sel.verdict][1]))]
          : h("div", { class: "hl" }, "No config had enough training trades to be eligible."),
        h("div", { class: "hl mu" }, `Baseline (no filters) on test: ${m(w.baseline_test)}${w.baseline_test_ci95 ? ` · CI ${sgn(w.baseline_test_ci95[0], 3)}…${sgn(w.baseline_test_ci95[1], 3)}R` : ""}`),
        h("div", { class: "hl mu" }, `${w.configs_tried} filter configs were tried; best-of-many selection flatters train results, so judge the TEST line.`)));
  };
  const split = (title, obj) => h("div", { class: "box" }, h("b", {}, title), Object.entries(obj || {}).map(([k, v]) =>
    h("div", { class: "hl" }, `${label(k)}: ${v.trades} trades · win ${v.win_rate_pct}% · avg ${sgn(v.avg_r, 2)}R`)));
  el.replaceChildren(switcher,
    h("div", { class: "banner " + (b.mode === "demo" ? "info" : "medium") }, h("b", {}, `${b.resolution} backtest ${b.period.from} → ${b.period.to} · ${b.period.sessions} sessions · ${b.mode}`),
      `${b.scan}. ${b.news_headlines} headlines replayed point-in-time; ${b.trades_using_news} trades had a material news signal.`),
    block("With news", b.with_news), block("Price & flow only (no news)", b.without_news),
    wfBlock("with news", b.with_news), wfBlock("no news", b.without_news),
    h("h4", {}, "Where the edge (or lack of it) comes from"),
    split("News alignment (with-news run)", b.with_news.by_news), split("Direction", b.with_news.by_direction), split("Setup", b.with_news.by_setup), split("Regime", b.with_news.by_regime),
    h("h4", {}, "Latest trades"),
    ...(b.trades || []).slice(-25).reverse().map((x) => h("div", { class: "card " + x.direction },
      h("div", { class: "row" }, h("span", { class: "sym" }, x.symbol), h("span", {}, h("span", { class: "pill " + x.direction }, x.direction), " ", h("span", { class: "sub" }, `${x.reason} · ${label(x.news_bucket)}`))),
      h("div", { class: "row", style: "margin-top:6px" }, h("span", { class: "sub" }, `${x.date} ${x.signal_time} · ${fmt(x.entry)} → ${fmt(x.exit)}`), h("span", { class: cls(x.r_multiple) }, `${sgn(x.r_multiple, 2)}R`)))),
    h("h4", {}, "Read this before trusting it"), h("div", { class: "box" }, b.caveats.map((c) => h("div", { class: "hl" }, c))));
}

async function getJSON(name) {
  try { const r = await fetch(`data/${name}.json?t=${Date.now()}`, { cache: "no-store" }); return r.ok ? await r.json() : null; } catch { return null; }
}
const STATE_LABEL = { OPEN: "Open", TARGET: "Target hit", STOPPED: "Stopped out", BREAKEVEN: "Breakeven exit", CLOSED_1515: "Closed 15:15", PENDING: "Waiting for entry", NO_DATA: "No data" };

// Where the price sits on the trade's path: stop-loss (-1R) on the left, entry, the breakeven trigger
// (+1R) and the target (+rr R) on the right. Works the same for longs and shorts because it is in R.
function tradeBar(e, st) {
  if (st.fill == null || st.r == null) return null;
  const rr = e.rr || 2, lo = -1, span = rr - lo, be = e.breakeven_after_r;
  const pos = (r) => Math.min(100, Math.max(0, ((r - lo) / span) * 100));
  const now = pos(st.r), zero = pos(0), up = st.r >= 0;
  const moved = st.stop_now != null && st.stop_now === st.fill;
  let note;
  if (st.state === "TARGET") note = "Target reached.";
  else if (st.state === "STOPPED") note = "Stop-loss hit.";
  else if (st.state === "BREAKEVEN") note = "Came back to entry after +1R: closed at breakeven.";
  else if (st.state === "CLOSED_1515") note = `Closed at 15:15 at ${sgn(st.r, 2)}R.`;
  else if (st.r < 0) note = `${Math.round(-st.r * 100)}% of the way to the stop-loss.`;
  else if (be != null && st.r < be && !moved) note = `${Math.round((st.r / be) * 100)}% of the way to the breakeven trigger (then the stop moves to entry).`;
  else note = `${Math.round((st.r / rr) * 100)}% of the way to the target${moved ? "; stop is now at entry" : ""}.`;
  return h("div", { class: "tbar", "aria-label": note },
    h("div", { class: "tbar-track" },
      h("i", { class: "tbar-fill " + (up ? "up" : "dn"), style: `left:${Math.min(zero, now)}%;width:${Math.abs(now - zero)}%` }),
      h("span", { class: "tbar-tick", style: `left:${zero}%` }),
      be != null ? h("span", { class: "tbar-tick be", style: `left:${pos(be)}%` }) : null,
      h("span", { class: "tbar-dot " + (up ? "up" : "dn"), style: `left:${now}%` })),
    h("div", { class: "tbar-labels" },
      h("span", { style: "left:0" }, moved ? "SL→entry" : "SL"),
      h("span", { style: `left:${zero}%` }, "Entry"),
      be != null ? h("span", { style: `left:${pos(be)}%` }, `+${be}R`) : null,
      h("span", { style: "left:100%" }, "Target")),
    h("div", { class: "sub tbar-note" }, note));
}

const istToday = () => new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
const shortDate = (d) => new Date(d + "T12:00:00+05:30").toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", weekday: "short", day: "numeric", month: "short" });

// Paper results for one day's journal (live or replayed): trades taken, booked and still-open P&L.
function dayStats(L) {
  const legs = (L.entries || []).map((e) => { const st = e.status || {}; return { e, st, p: paper(e.direction === "LONG", st.fill, st.price, e.stop_distance, e.size_mult) }; });
  const taken = legs.filter((x) => x.p && x.p.q > 0);
  const booked = taken.filter((x) => x.st.state !== "OPEN").reduce((a, x) => a + x.p.pnl, 0);
  const open = taken.filter((x) => x.st.state === "OPEN").reduce((a, x) => a + x.p.pnl, 0);
  return { taken: taken.length, booked, open, total: booked + open, wins: taken.filter((x) => x.p.pnl > 0).length };
}

// Days before live journals were saved exist only as after-close replays (Record): show them in the same shape.
function replayLog(R, date) {
  const d = (R?.days || []).find((x) => x.date === date);
  if (!d) return null;
  const REASON = { EOD: "CLOSED_1515", STOP: "STOPPED", TARGET: "TARGET", BREAKEVEN: "BREAKEVEN" };
  const entries = d.trades.map((t) => {
    const sgn = t.direction === "LONG" ? 1 : -1, sd = t.stop_distance;
    return { symbol: t.symbol, direction: t.direction, setup: t.setup, signal_time: t.signal_time, conviction: t.conviction, stop_distance: sd, rr: 2,
      breakeven_after_r: 1, size_mult: t.size_mult, option_idea: null, contradiction: null,
      status: { state: REASON[t.reason] || t.reason, fill: t.entry, stop: sd ? t.entry - sgn * sd : null, target: sd ? t.entry + sgn * 2 * sd : null,
        price: t.exit, at: t.reason === "EOD" ? "15:15" : "", r: t.r_multiple } };
  });
  return { date, mode: "replay", entries, summary: { signals: entries.length, open: 0 } };
}

function journalView(L, isLive) {
  const A = account(), D = dayStats(L), S = L.summary || {};
  const tile = (k, v, c) => h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v " + (c || "") }, v));
  return [
    h("h4", {}, `Paper money · ${rupees(A.cap)} account at ${A.risk}% risk per trade`),
    h("div", { class: "macro" }, tile("Trades taken", D.taken), tile("Day P&L", signedRupees(D.total), cls(D.total)),
      tile("Booked", signedRupees(D.booked), cls(D.booked)), tile("Still open", signedRupees(D.open), cls(D.open))),
    h("div", { class: "sub" }, `${shortDate(L.date)} · ${S.signals ?? D.taken} signal${(S.signals ?? D.taken) === 1 ? "" : "s"} · ${S.open ?? 0} open · ${sgn((100 * D.total) / A.cap, 2)}% on the account` +
      (L.mode === "replay" ? " · after-close replay (the live list was not saved for this day)" : "")),
    h("details", { class: "how" }, h("summary", {}, "How this is calculated"),
      "Every signal stays listed after it leaves the main list. Each is bought/shorted at the next 5-minute open with the app's suggested " +
      "quantity (5× intraday margin cap), follows its stop / breakeven / target, and is closed by 15:15; costs included. Change the account in ⚙." +
      " Past days come from the live list saved after each close; earlier days, before saving started, show the after-close replay."),
    ...(L.entries.length ? L.entries.map((e) => {
      const st = e.status || {}, oi = e.option_idea;
      return h("button", { class: "card " + e.direction, onclick: () => (isLive ? openSheet(e.symbol) : null) },
        h("div", { class: "row" }, h("div", {}, h("div", { class: "sym" }, e.symbol), h("div", { class: "sub" }, `${e.signal_time} · ${label(e.setup)}${e.contradiction ? " · " + label(e.contradiction) : ""}`)),
          h("div", { style: "text-align:right" }, h("span", { class: "pill " + e.direction }, e.direction),
            h("div", { class: "sub " + cls(st.r) }, `${STATE_LABEL[st.state] || st.state}${st.r != null ? " · " + sgn(st.r, 2) + "R" : ""}`))),
        tradeBar(e, st),
        st.fill != null ? h("div", { class: "sub", style: "margin-top:6px" }, `Entry ₹${fmt(st.fill)} · SL ₹${fmt(st.stop_now ?? st.stop)}${st.stop_now != null && st.stop_now === st.fill ? " (moved to entry)" : ""} · T ₹${fmt(st.target)} · ${st.state === "OPEN" ? "now" : "exit"} ₹${fmt(st.price)}${st.at ? " at " + st.at : ""}`) : null,
        (() => { const p = paper(e.direction === "LONG", st.fill, st.price, e.stop_distance, e.size_mult);
          return p && p.q > 0 ? h("div", { class: "row", style: "margin-top:6px" }, h("span", { class: "sub" }, `Paper: ${p.q} sh · ${rupees(p.invested)} position`),
            h("b", { class: cls(p.pnl) }, signedRupees(p.pnl) + (st.state === "OPEN" ? " (open)" : ""))) : null; })(),
        oi ? h("div", { class: "opt " + e.direction }, `${oi.action} ${oi.strike} · exp ${oi.expiry}`) : null);
    }) : [h("div", { class: "empty" }, isLive ? "No signals so far today." : "No signals that day.")]),
  ];
}

async function today() {
  const el = $("list");
  const [L, I, R] = await Promise.all([getJSON("today_log"), getJSON("journal/index"), getJSON("track_record")]);
  if (state.filter !== "TODAY") return;
  const liveDays = new Set((I?.days || []).map((d) => d.date));
  const latest = L && L.entries ? L.date : null;
  const past = [...new Set([...liveDays, ...(R?.days || []).map((d) => d.date)])].filter((d) => d !== latest).sort().reverse();
  state.day = state.day || "today";
  const chip = (k, t) => h("button", { class: "chip" + (state.day === k ? " on" : ""), onclick: () => { state.day = k; today(); } }, t);
  const days = h("nav", { class: "chips days", "aria-label": "Day" },
    chip("today", latest && latest !== istToday() ? "Latest" : "Today"), ...past.slice(0, 10).map((d) => chip(d, shortDate(d))), past.length ? chip("all", "All days") : null);
  const pastLog = async (d) => (liveDays.has(d) ? await getJSON("journal/" + d) : null) || replayLog(R, d);

  if (state.day === "all") {
    const all = [...(latest ? [latest] : []), ...past];
    const rows = [];
    for (const d of all) {
      const log = d === latest ? L : await pastLog(d);
      if (log) rows.push({ d, src: d === latest ? "live" : log.mode === "replay" ? "replay" : "live", ...dayStats(log) });
    }
    if (state.filter !== "TODAY" || state.day !== "all") return;
    const A = account(), total = rows.reduce((a, r) => a + r.total, 0), trades = rows.reduce((a, r) => a + r.taken, 0);
    const winDays = rows.filter((r) => r.total > 0).length, maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.total)));
    const tile = (k, v, c) => h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v " + (c || "") }, v));
    return el.replaceChildren(days,
      h("h4", {}, `All days · ${rupees(A.cap)} paper account at ${A.risk}% risk`),
      h("div", { class: "macro" }, tile("Total P&L", signedRupees(total), cls(total)), tile("Return", sgn((100 * total) / A.cap, 2) + "%", cls(total)),
        tile("Days won", `${winDays} / ${rows.length}`), tile("Trades", trades)),
      h("div", { class: "sub", style: "margin-bottom:8px" }, "Fixed account size each day (no compounding). Tap a day for its trades."),
      ...rows.map((r) => h("button", { class: "card dayrow", onclick: () => { state.day = r.d === latest ? "today" : r.d; today(); } },
        h("div", { class: "row" }, h("div", {}, h("b", {}, shortDate(r.d)), h("div", { class: "sub" }, `${r.taken} trade${r.taken === 1 ? "" : "s"} · ${r.wins} won${r.src === "replay" ? " · replay" : ""}`)),
          h("b", { class: cls(r.total) }, signedRupees(r.total))),
        h("div", { class: "bar" }, h("span", { class: "t" }, h("i", { style: `left:${r.total >= 0 ? 50 : 50 - (50 * Math.abs(r.total)) / maxAbs}%;width:${(50 * Math.abs(r.total)) / maxAbs}%;background:var(--${r.total >= 0 ? "up" : "dn"})` }))))));
  }
  if (state.day !== "today") {
    const log = await pastLog(state.day);
    if (state.filter !== "TODAY") return;
    return el.replaceChildren(days, ...(log ? journalView(log, false) : [h("div", { class: "empty" }, "No data saved for that day.")]));
  }
  if (!L || !L.entries) return el.replaceChildren(days, h("div", { class: "empty" }, "No journal yet. It starts with the first signal of the day."));
  el.replaceChildren(days, ...journalView(L, true));
}

async function record() {
  const el = $("list"), R = await getJSON("track_record");
  if (state.filter !== "RECORD") return;
  if (!R || !R.days || !R.days.length) return el.replaceChildren(h("div", { class: "empty" }, "The track record starts after the first market close with this version (replayed daily at 16:15 IST)."));
  const S = R.summary, tile = (k, v, c) => h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v " + (c || "") }, v));
  const maxAbs = Math.max(0.5, ...R.days.map((d) => Math.abs(d.net_r)));
  const A = account();
  const dayPnl = (d) => d.trades.reduce((a, t) => { const p = paper(t.direction === "LONG", t.entry, t.exit, t.stop_distance, t.size_mult); return a + (p ? p.pnl : 0); }, 0);
  let bal = A.cap, peak = A.cap, maxDd = 0;
  const equity = R.days.map((d) => { const p = dayPnl(d); bal += p; peak = Math.max(peak, bal); maxDd = Math.max(maxDd, (peak - bal) / peak); return { date: d.date, pnl: p, bal }; });
  const total = bal - A.cap, pnlByDate = Object.fromEntries(equity.map((x) => [x.date, x.pnl]));
  el.replaceChildren(
    h("div", { class: "banner " + (total >= 0 ? "info" : "medium") }, h("b", {}, `Paper account: ${rupees(A.cap)} → ${rupees(bal)}`),
      `${signedRupees(total)} (${sgn((100 * total) / A.cap, 2)}%) since ${S.from} at ${A.risk}% risk per trade, max drawdown ${fmt(100 * maxDd, 1)}%. ` +
      "Fixed account size per trade (no compounding), every suggested trade taken, costs included. Not real money."),
    h("div", { class: "banner info" }, h("b", {}, `Track record ${S.from} → ${S.to}`), R.note),
    h("div", { class: "macro" }, tile("Sessions", S.sessions), tile("Trades", S.trades || 0),
      tile("Win rate", S.trades ? S.win_rate_pct + "%" : "—"), tile("Avg net R", S.trades ? sgn(S.avg_net_r, 3) : "—", cls(S.avg_net_r)),
      tile("Total net R", S.trades ? sgn(S.total_net_r, 1) : "—", cls(S.total_net_r)),
      tile("95% CI (avg)", S.net_r_ci95 ? `${sgn(S.net_r_ci95[0], 2)}…${sgn(S.net_r_ci95[1], 2)}` : "need more days")),
    h("h4", {}, "By day"),
    ...R.days.slice().reverse().map((d) => h("div", { class: "box", style: "margin-bottom:8px" },
      h("div", { class: "row" }, h("b", {}, d.date), h("span", { class: cls(d.net_r) }, `${d.trades.length} trade${d.trades.length === 1 ? "" : "s"} · ${sgn(d.net_r, 2)}R · ${signedRupees(pnlByDate[d.date] || 0)}`)),
      h("div", { class: "bar" }, h("span", { class: "t" }, h("i", { style: `left:${d.net_r >= 0 ? 50 : 50 - 50 * Math.abs(d.net_r) / maxAbs}%;width:${50 * Math.abs(d.net_r) / maxAbs}%;background:var(--${d.net_r >= 0 ? "up" : "dn"})` }))),
      d.trades.map((t) => { const p = paper(t.direction === "LONG", t.entry, t.exit, t.stop_distance, t.size_mult);
        return h("div", { class: "hl sub" }, `${t.signal_time} ${t.symbol} ${t.direction} · ${fmt(t.entry)} → ${fmt(t.exit)} (${label(t.reason)}) · ${sgn(t.net_r, 2)}R` + (p ? ` · ${p.q} sh ${signedRupees(p.pnl)}` : "")); }))));
}

function list() {
  if (state.filter === "TODAY") return today();
  if (state.filter === "RECORD") return record();
  if (state.filter === "BACKTEST") return backtest();
  const f = state.filter, rows = state.data.signals.filter((s) => f === "ALL" || (f === "ACTION" ? s.direction !== "NO_TRADE" : s.direction === f));
  const el = $("list");
  if (!rows.length) return el.replaceChildren(h("div", { class: "empty" }, f === "ACTION" ? "No actionable setups right now. Capital preserved." : "Nothing here."));
  el.replaceChildren(...rows.map((s) => {
    const tags = [];
    if (s.contradiction) tags.push(h("span", { class: "tag w" }, label(s.contradiction.type)));
    s.risk.warnings.slice(0, 2).forEach((w) => tags.push(h("span", { class: "tag " + (w.level === "high" ? "r" : "w") }, label(w.code))));
    s.risk.blockers.slice(0, 2).forEach((b) => tags.push(h("span", { class: "tag" }, label(b.code))));
    return h("button", { class: "card " + s.direction, onclick: () => openSheet(s.symbol) },
      h("div", { class: "row" }, h("div", {}, h("div", { class: "sym" }, s.symbol), h("div", { class: "sub" }, `${s.name} · ${s.sector}`)),
        h("div", { style: "text-align:right" }, h("div", {}, h("span", { class: "pill " + s.direction }, s.direction === "NO_TRADE" ? "NO TRADE" : s.direction)),
          h("div", { class: "sub" }, label(s.setup)))),
      h("div", { class: "row", style: "margin-top:8px" }, h("span", {}, "₹" + fmt(s.price.ltp), " ", h("span", { class: cls(s.price.chg_pct) }, sgn(s.price.chg_pct) + "%")),
        h("span", { class: "sub" }, `Conviction ${s.conviction}`)),
      s.execution.option_idea ? h("div", { class: "opt " + s.direction }, `${s.execution.option_idea.action} ${s.execution.option_idea.strike} · exp ${s.execution.option_idea.expiry}`) : null,
      h("div", { class: "meter" }, h("i", { style: `width:${s.conviction}%` })), tags.length ? h("div", { class: "tags" }, tags) : null);
  }));
}

function bar(name, score, weight) {
  const pct = Math.abs(score) * 50, left = score >= 0 ? 50 : 50 - pct;
  return h("div", { class: "bar" }, h("span", { class: "n" }, name), h("span", { class: "t" }, h("i", { style: `left:${left}%;width:${pct}%;background:var(--${score >= 0 ? "up" : "dn"})` })),
    h("span", { class: "x" }, `${sgn(score)} ×${(weight * 100) | 0}%`));
}
const kv = (k, v) => h("div", { class: "kv" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v));

function openSheet(sym) {
  const s = state.data.signals.find((x) => x.symbol === sym); if (!s) return;
  const e = s.execution, r = s.risk, mi = s.microstructure, q = qty(s), act = s.direction !== "NO_TRADE";
  $("sheetTitle").textContent = `${s.symbol} · ${s.direction === "NO_TRADE" ? "No trade" : s.direction}`;
  const b = $("sheetBody"); b.replaceChildren();
  r.warnings.forEach((w) => b.append(h("div", { class: "banner " + w.level }, h("b", {}, label(w.code)), w.message)));
  r.blockers.forEach((x) => b.append(h("div", { class: "banner info" }, h("b", {}, "Blocked · " + label(x.code)), x.message)));
  if (s.contradiction) b.append(h("div", { class: "banner medium" }, h("b", {}, label(s.contradiction.type)), s.contradiction.note));
  const oi = e.option_idea;
  if (oi) b.append(h("h4", {}, "Option idea (approximate)"),
    h("div", { class: "box opt-box " + s.direction },
      h("div", { class: "opt-big" }, `${oi.action} ${oi.strike} · expiry ${oi.expiry} (${oi.days_to_expiry}d)`),
      oi.live ? h("div", { class: "sub" }, `${oi.tradingsymbol} · 1 lot = ${oi.lot_size} sh · ≈ ${rupees(oi.lot_cost)} per lot · live from Kite`) : null,
      h("div", { class: "grid", style: "margin-top:8px" },
        kv(oi.live ? "Premium (live)" : "Premium (est.)", "₹" + fmt(oi.premium_est)), kv("Exit if premium ≤ (est.)", "₹" + fmt(oi.premium_stop_est)),
        kv("Target premium (est.)", "₹" + fmt(oi.premium_target_est)), kv("Stock stop / target", `₹${fmt(e.stop)} / ₹${fmt(e.target)}`)),
      h("div", { class: "sub", style: "margin-top:8px" }, oi.note + " Exit on the STOCK levels if they are hit first; they are the real thesis.")));
  b.append(h("h4", {}, "Execution & risk matrix"),
    h("div", { class: "grid" },
      kv("Entry trigger", "₹" + fmt(e.entry_trigger)), kv("Risk : Reward", e.risk_reward),
      kv("Stop-loss", `₹${fmt(e.stop)}`), kv("Distance to stop", `₹${fmt(e.stop_distance)} (${fmt(e.stop_distance_pct)}%)`),
      kv("Move stop to entry at", e.breakeven_trigger != null ? `₹${fmt(e.breakeven_trigger)} (+${e.breakeven_after_r}R)` : "—"),
      kv("Target", "₹" + fmt(e.target) + (e.gap_close_target ? ` (gap close ₹${fmt(e.gap_close_target)})` : "")),
      kv("Suggested qty", act ? `${q.q} sh` : "—"), kv("Capital at risk", act ? "₹" + fmt(q.risk, 0) : "—"),
      kv("VWAP fail-safe", "₹" + fmt(e.invalidation_vwap)), kv("Daily ATR", `₹${fmt(s.price.atr_daily)} (${fmt(s.price.atr_pct)}%)`)),
    h("h4", {}, "Structural invalidation"), h("div", { class: "box inval" }, e.invalidation_text),
    h("h4", {}, "Thesis"), h("div", { class: "box" }, s.thesis),
    h("h4", {}, "Pillar scores (weight by regime: " + label(s.regime) + ")"),
    bar("Fundamental", s.pillars.fundamental.score, s.pillars.fundamental.weight), bar("Price action", s.pillars.price_action.score, s.pillars.price_action.weight),
    bar("Institutional", s.pillars.institutional.score, s.pillars.institutional.weight), bar("Macro", s.pillars.macro.score, s.pillars.macro.weight),
    h("div", { class: "sub" }, `Composite ${sgn(s.composite)}`),
    h("h4", {}, "Microstructure"),
    h("div", { class: "grid" }, kv("Order-flow imbalance", sgn(mi.ofi)), kv("VPIN (toxicity)", fmt(mi.vpin) + (mi.toxic ? " ⚠" : "")),
      kv("Hawkes branching B/S", `${fmt(mi.hawkes_buy)} / ${fmt(mi.hawkes_sell)}`), kv("VWAP", "₹" + fmt(s.price.vwap)),
      kv("POC", "₹" + fmt(mi.poc)), kv("Value area", `${fmt(mi.val)} – ${fmt(mi.vah)}`),
      kv("F&O quadrant", label(mi.oi_quadrant)), kv("IV skew (put−call)", mi.iv_skew == null ? "n/a" : sgn(mi.iv_skew)),
      kv("ADX / gap", `${fmt(s.price.adx, 0)} / ${sgn(s.price.gap_pct)}%`), kv("Avg turnover", "₹" + fmt(s.price.turnover_cr, 0) + " cr")),
    h("h4", {}, "Exchange limits"),
    h("div", { class: "grid" }, kv("Circuit band", `±${r.circuit.band_pct}%`), kv("To upper / lower", `${fmt(r.circuit.dist_upper_pct, 1)}% / ${fmt(r.circuit.dist_lower_pct, 1)}%`),
      kv("India VIX · σ/day", `${fmt(r.vix, 1)} · ${fmt(r.expected_daily_move_pct)}%`), kv("Size multiplier", "×" + r.size_multiplier),
      ...(r.lpp.applicable ? [kv("LPP band (30s ref, modelled)", `${fmt(r.lpp.lower)} – ${fmt(r.lpp.upper)}`), kv(`${r.lpp.option} entry limit`, `₹${fmt(r.lpp.entry_limit)} · ${r.lpp.headroom_pct}% headroom`)] : [])),
    h("h4", {}, `Catalysts${s.fundamental.engine ? " · " + s.fundamental.engine : ""}`),
    h("div", { class: "box" }, s.fundamental.headlines.length ? s.fundamental.headlines.map((x) =>
      h("div", { class: "hl" }, x.url ? h("a", { href: x.url, target: "_blank", rel: "noopener noreferrer" }, x.title) : x.title,
        h("div", { class: "sub" }, `${x.source} · Tier ${x.tier} ${label(x.event)} · sentiment ${sgn(x.sentiment)}${x.mcap_ratio_pct ? " · order = " + x.mcap_ratio_pct + "% of mcap" : ""}`))) : h("span", { class: "muted" }, "No material headlines in the last 36h.")),
    h("p", { class: "muted" }, state.data.meta.disclaimer));
  $("sheet").hidden = false; $("sheet").scrollTop = 0;
  history.pushState({ sheet: sym }, "");
}
function closeSheet() { $("sheet").hidden = true; }

function foot() {
  const m = state.data.meta;
  $("foot").textContent = `Updated ${new Date(m.generated_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST · ${m.mode.toUpperCase()} · ${m.analysed}/${m.universe_size} analysed · ${m.sources.join(" · ")}`;
}

function render() { banners(); macro(); list(); foot(); }

async function load(manual) {
  try {
    const r = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    state.data = await r.json();
    render();
    const sym = new URLSearchParams(location.search).get("sym");
    if (sym && !state.deepLinked) { state.deepLinked = true; openSheet(sym); }
  } catch (err) {
    if (!state.data) $("list").replaceChildren(h("div", { class: "empty" }, "Unable to load data. Check your connection and pull to refresh."));
    else if (manual) banners(true);
  }
}

$("filters").addEventListener("click", (ev) => {
  const b = ev.target.closest("button"); if (!b) return;
  state.filter = b.dataset.f; store.set("view", state.filter);
  document.querySelectorAll("#filters .chip").forEach((c) => c.classList.toggle("on", c === b));
  if (state.data) list();
});
$("refreshBtn").onclick = () => load(true);
$("closeSheet").onclick = () => history.back();
window.addEventListener("popstate", closeSheet);
function refreshKiteUI() {
  const m = state.data?.meta || {}, k = (m.health || {}).kite || {};
  $("kiteBtn").hidden = !m.kite_api_key;
  $("kiteStatus").textContent = !m.kite_api_key ? "Add the KITE_API_KEY and KITE_API_SECRET repository secrets to enable Kite."
    : k.connected ? `Connected today ✓ ${k.bars_from_kite || 0} price series and ${k.option_chains || 0} option chains from Kite.`
    : "Not connected today — the app is using free data (Yahoo, no option chains).";
}
$("kiteBtn").onclick = () => {
  const key = state.data?.meta?.kite_api_key;
  if (key) location.href = `https://kite.zerodha.com/connect/login?v=3&api_key=${encodeURIComponent(key)}`;
};
$("settingsBtn").onclick = () => { refreshNotifUI(); refreshKiteUI(); $("capital").value = state.capital ?? state.data?.meta.capital ?? 100000; $("riskPct").value = state.riskPct ?? state.data?.meta.risk_pct ?? 0.5; $("settings").showModal(); };
$("settings").addEventListener("close", () => {
  const c = parseFloat($("capital").value), r = parseFloat($("riskPct").value);
  if (c > 0 && r > 0 && r <= 100) { state.capital = c; state.riskPct = r; store.set("capital", c); store.set("riskPct", r); }
  if (state.data) list(); // paper-money figures depend on the account size
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
setInterval(() => { if (!document.hidden) load(); }, 120000);

// Push notifications (iOS 16.4+: only works when opened from the Home Screen icon)
const isStandalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const urlKey = (b64) => { const p = "=".repeat((4 - (b64.length % 4)) % 4); const raw = atob((b64 + p).replace(/-/g, "+").replace(/_/g, "/")); return Uint8Array.from(raw, (c) => c.charCodeAt(0)); };
function showSub(sub) {
  $("subJson").value = JSON.stringify(sub); $("subBox").hidden = false; $("notifTest").hidden = false;
  $("notifStatus").textContent = "Notifications are on for this phone.";
  $("notifBtn").textContent = "Re-show subscription";
}
async function refreshNotifUI() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    $("notifStatus").textContent = isStandalone() ? "This browser does not support web push (iOS 16.4 or newer needed)." : "Open the app from its Home Screen icon to enable notifications.";
    $("notifBtn").hidden = true; return;
  }
  if (Notification.permission === "denied") { $("notifStatus").textContent = "Notifications are blocked for this app. On iPhone: Settings → Notifications → Intraday → Allow."; return; }
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (sub) showSub(sub); else $("notifStatus").textContent = "Get an alert when a new LONG/SHORT signal appears (market hours only).";
}
$("notifBtn").onclick = async () => {
  try {
    if (await Notification.requestPermission() !== "granted") { $("notifStatus").textContent = "Permission not granted."; return; }
    const reg = await navigator.serviceWorker.ready;
    const sub = (await reg.pushManager.getSubscription()) || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlKey(VAPID_PUBLIC) });
    showSub(sub);
  } catch (err) { $("notifStatus").textContent = "Could not enable notifications: " + err.message; }
};
$("notifTest").onclick = async () => {
  const reg = await navigator.serviceWorker.ready;
  reg.showNotification("Intraday Quant Desk", { body: "Local test: this phone can show notifications.", icon: "icons/icon-192.png", tag: "local-test" });
};
$("copySub").onclick = async () => {
  try { await navigator.clipboard.writeText($("subJson").value); $("copySub").textContent = "Copied ✓"; }
  catch { $("subJson").select(); document.execCommand("copy"); $("copySub").textContent = "Copied ✓"; }
};

// PWA install
let deferred;
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferred = e; $("installBtn").hidden = false; });
$("installBtn").onclick = async () => { if (!deferred) return; deferred.prompt(); await deferred.userChoice; deferred = null; $("installBtn").hidden = true; };
window.addEventListener("appinstalled", () => { $("installBtn").hidden = true; });
const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
if (/iphone|ipad|ipod/i.test(navigator.userAgent) && !standalone && !store.get("iosHintSeen", false)) $("iosHint").hidden = false;
$("iosClose").onclick = () => { $("iosHint").hidden = true; store.set("iosHintSeen", true); };
// Updates: the service worker serves the cached app first, so a new release used to appear only on the
// launch after next. Check for a new version on open/resume and reload once the new one takes control.
if ("serviceWorker" in navigator) {
  const hadController = !!navigator.serviceWorker.controller; // false on first install: nothing to refresh
  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (!hadController || reloading) return;
    reloading = true;
    location.reload();
  });
  window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").then((reg) => {
    document.addEventListener("visibilitychange", () => { if (!document.hidden) reg.update().catch(() => {}); });
  }).catch(() => {}));
}
document.querySelectorAll("#filters .chip").forEach((c) => c.classList.toggle("on", c.dataset.f === state.filter)); // restore last view
load();
