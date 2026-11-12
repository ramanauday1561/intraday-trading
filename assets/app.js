"use strict";
const DATA_URL = "data/signals.json";
const VAPID_PUBLIC = "BISlGtzJw0dzaj5ikAkMcMRqCsv4VHbSulg9dtFFWR6XKlsp4bprM8nVnw1aAwnvo7EXRksw_zcD-gfqhSymRg4"; // public half only; the private key lives in a GitHub secret
const $ = (id) => document.getElementById(id);
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const VIEWS = ["TODAY", "ACTION", "LONG", "SHORT", "ALL", "RECORD", "BACKTEST"];
// One-time move from the old 5% default to 1% (a risk the user typed in themselves is kept unless it was exactly the old default).
if (!store.get("riskDefault1", false)) { if (store.get("riskPct", null) === 5) store.set("riskPct", 1); store.set("riskDefault1", true); }
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
// Inline icons (Lucide-style strokes, MIT). Static markup only, so innerHTML here never touches data.
const ICONS = {
  sliders: '<path d="M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
  calendar: '<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
  up: '<path d="m22 7-8.5 8.5-5-5L2 17"/><path d="M16 7h6v6"/>',
  down: '<path d="m22 17-8.5-8.5-5 5L2 7"/><path d="M16 17h6v-6"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  chart: '<path d="M3 3v18h18"/><path d="M18 17V9M13 17V5M8 17v-3"/>',
  flask: '<path d="M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3"/><path d="M7 15h10"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  target: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  enter: '<path d="m15 10 5 5-5 5"/><path d="M4 4v7a4 4 0 0 0 4 4h12"/>',
  exit: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  check: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
  x: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  wallet: '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
  ban: '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4M12 17h.01"/>',
  layers: '<path d="m12 2 10 5-10 5L2 7l10-5z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
  hash: '<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>',
  percent: '<path d="M19 5 5 19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>',
  trophy: '<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6M18 9h1.5a2.5 2.5 0 0 0 0-5H18M4 22h16M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22M18 2H6v7a6 6 0 0 0 12 0V2Z"/>',
  replay: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
  back: '<path d="m15 18-6-6 6-6"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
};
function ico(name, extra) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "ico" + (extra ? " " + extra : "")); s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || "";
  return s;
}
const dirIco = (d) => (d === "LONG" ? ico("up") : d === "SHORT" ? ico("down") : ico("ban"));
const stat = (icon, k, v, c) => h("div", { class: "stat" }, h("div", { class: "k" }, ico(icon), k), h("div", { class: "v num " + (c || "") }, v));
const fact = (icon, text, c) => h("span", { class: "fact" + (c ? " " + c : "") }, ico(icon), text);
const tip = (text) => h("details", { class: "info-tip" }, h("summary", {}, ico("info"), "How"), text);
const fmt = (n, d = 2) => (n == null || Number.isNaN(n) ? "—" : Number(n).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));
const sgn = (n, d = 2) => (n == null ? "—" : (n > 0 ? "+" : "") + fmt(n, d));
const cls = (n) => (n > 0 ? "up" : n < 0 ? "dn" : "mu");
// Replace an element's content, skipping null/false parts (optional pieces), so "null" is never printed.
const fill = (el, ...kids) => el.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));
const label = (s) => String(s || "").replace(/_/g, " ").toLowerCase().replace(/^\w|\s\w/g, (m) => m.toUpperCase());

// Paper money: every suggested trade is "taken" with the ⚙ capital and risk %, sized exactly like the app's
// suggested quantity (risk / stop distance, capped at 5x capital), with 0.06% round-trip costs.
const COST_PCT = 0.06, LEVERAGE = 5;
function account() {
  return { cap: state.capital ?? state.data?.meta.capital ?? 100000, risk: state.riskPct ?? state.data?.meta.risk_pct ?? 1 };
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
// One short status line: phase · time (· Kite · checkpoint). Warnings only when they matter right now.
function banners(offline) {
  const m = state.data.meta, hl = m.health || {};
  const phase = { ACTIVE: "Live", MORATORIUM: "Opening", PRE_OPEN: "Pre-open", LATE: "Exits only", CLOSED: "Closed" }[m.session_phase] || m.session_phase;
  const updated = new Date(m.generated_at).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
  const age = (Date.now() - new Date(m.generated_at)) / 60000, marketHours = m.session_phase !== "CLOSED";
  const parts = [m.mode === "demo" ? "Demo data" : phase, updated];
  if (m.mode === "live" && (hl.kite || {}).connected) parts.push("Kite");
  if (m.checkpoint && m.session_phase === "ACTIVE") parts.push("@" + m.checkpoint);
  let level = m.mode === "demo" ? "warn" : marketHours ? "ok" : "idle";
  if (m.mode === "live" && marketHours && age > 25) { parts.push(`${Math.round(age)} min old`); level = "warn"; }
  if (m.mode === "live" && hl.missing && hl.missing.length) { parts.push(`missing ${hl.missing.length}`); level = "warn"; }
  if (offline) { parts.push("offline"); level = "warn"; }
  fill($("status"), h("span", { class: "dot " + level }), parts.join(" · "));
}

function macro() {
  const M = state.data.macro;
  const item = (k, v, c) => h("span", { class: "strip-item" }, h("span", { class: "k" }, k), " ", h("b", { class: "num " + (c || "") }, v));
  fill($("macro"),
    item("NIFTY", `${fmt(M.nifty.ltp, 0)} ${sgn(M.nifty.chg_pct)}%`, cls(M.nifty.chg_pct)),
    M.banknifty ? item("BANK", `${fmt(M.banknifty.ltp, 0)} ${sgn(M.banknifty.chg_pct)}%`, cls(M.banknifty.chg_pct)) : null,
    item("VIX", fmt(M.india_vix, 1)));
}

async function backtest() {
  const el = $("list");
  state.btKey = state.btKey || "backtest"; state.bts = state.bts || {};
  if (state.bts[state.btKey] === undefined) {
    try { const r = await fetch(`data/${state.btKey}.json?t=${Date.now()}`, { cache: "no-store" }); state.bts[state.btKey] = r.ok ? await r.json() : null; } catch { state.bts[state.btKey] = null; }
  }
  const b = state.bts[state.btKey];
  const switcher = h("nav", { class: "chips" }, [["backtest", "5-min"], ["backtest_hourly", "Hourly 2y"], ["forward_test", "Forward"]].map(([k, t]) =>
    h("button", { class: "chip" + (state.btKey === k ? " on" : ""), onclick: () => { state.btKey = k; backtest(); } }, t)));
  const ci = (c) => (c ? `${sgn(c[0], 2)}…${sgn(c[1], 2)}` : "—");
  if (state.btKey === "forward_test" && b && b.hypothesis) {
    const H = b.hypothesis, O = H.opposed || {};
    const v = { INSUFFICIENT_DATA: ["clock", "Not enough data yet", ""], SUPPORTED: ["check", "Supported", "acc"], NOT_SUPPORTED: ["x", "Not supported", "dn"] }[H.verdict] || ["info", H.verdict, ""];
    return fill(el, switcher,
      h("div", { class: "hero" }, h("div", { class: "cap" }, ico("flask"), `H1 · opposed-news trades · ${b.period ? b.period.sessions + " days" : "no days yet"}`),
        h("div", { class: "big num " + cls(O.avg_net_r) }, O.trades ? sgn(O.avg_net_r, 3) + "R" : "—"),
        h("div", { class: "stats" }, stat("hash", "Trades", `${O.trades || 0}/${H.min_trades_required}`), stat("check", "Win", O.trades ? O.win_rate_pct + "%" : "—"),
          stat("percent", "95% CI", ci(H.opposed_ci95), "small"))),
      h("div", { class: "facts" }, fact(v[0], v[1], v[2]), fact("list", `Others: ${H.other_trades?.trades || 0} · ${sgn(H.other_trades?.avg_net_r ?? null, 3)}R`)),
      tip("Pre-registered: trades that oppose a material news signal earn > 0R after costs. Only sessions after 9 Nov count. See preregistration.md."));
  }
  if (!b || !b.with_news) return fill(el, switcher, h("div", { class: "empty" }, ico("flask"), h("div", { class: "big" }, "Not run yet")));
  const S = b.with_news, w = S.walk_forward, sel = w && w.selected;
  const verdict = { NO_EDGE_DEMONSTRATED: ["x", "No edge", "dn"], POSITIVE_BUT_NOT_SIGNIFICANT: ["info", "Positive, not significant", ""],
    EDGE_SUPPORTED_OUT_OF_SAMPLE: ["check", "Edge out-of-sample", "acc"] };
  const vv = sel ? verdict[sel.verdict] : null;
  fill(el, switcher,
    h("div", { class: "hero" },
      h("div", { class: "cap" }, ico("flask"), `${b.resolution} · ${b.period.sessions} days · avg per trade after costs`),
      h("div", { class: "big num " + cls(S.avg_net_r) }, sgn(S.avg_net_r, 3) + "R"),
      h("div", { class: "stats" }, stat("hash", "Trades", S.trades), stat("check", "Win", S.win_rate_pct + "%"), stat("chart", "PF", S.profit_factor ?? "—")),
      h("div", { class: "stats" }, stat("percent", "@1% risk", sgn(S.return_pct_risk1, 1) + "%", cls(S.return_pct_risk1)),
        stat("down", "Worst day", sgn(S.worst_day_pct_risk1, 1) + "%", "dn"), stat("wallet", "Cost", fmt(S.avg_cost_r, 3) + "R"))),
    w ? h("div", { class: "hero" },
      h("div", { class: "cap" }, ico("target"), `Unseen test · ${w.test_sessions[2]} days`),
      h("div", { class: "big num " + cls(w.baseline_test.avg_net_r) }, w.baseline_test.trades ? sgn(w.baseline_test.avg_net_r, 3) + "R" : "—"),
      h("div", { class: "stats" }, stat("hash", "Trades", w.baseline_test.trades || 0), stat("percent", "95% CI", ci(w.baseline_test_ci95), "small"),
        stat("check", "Win", w.baseline_test.trades ? w.baseline_test.win_rate_pct + "%" : "—"))) : null,
    vv ? h("div", { class: "facts" }, fact(vv[0], vv[1], vv[2]), fact("info", `No-news run: ${sgn(b.without_news.avg_net_r, 3)}R`)) : null,
    tip(`${b.scan}. ${b.news_headlines} headlines replayed point-in-time. ${(b.caveats || []).join(" ")}`),
    ...(b.trades || []).slice(-20).reverse().map((x) => h("div", { class: "card " + x.direction },
      h("div", { class: "tcard-top" },
        h("div", {}, h("div", { class: "sym" }, x.symbol), h("div", { class: "meta" }, h("span", { class: "pill " + x.direction }, dirIco(x.direction), x.direction), `${x.date.slice(5)} ${x.signal_time}`)),
        h("div", { class: "pnl num " + cls(x.r_multiple) }, `${sgn(x.r_multiple, 2)}R`)))));
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
      h("span", { style: "left:100%" }, "Target")));
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

// Day rules (engine/dayrules.py): the day's first trade fixes the direction; no new trades after -2R of closed trades.
// Day rules, checks and replay differences as short icon facts instead of sentences.
function dayFacts(L, replay) {
  const es = L.entries || [], out = [];
  const closed = ["STOPPED", "BREAKEVEN", "TARGET", "CLOSED_1515"];
  if (es.length) {
    const first = es[0].direction, booked = es.reduce((a, e) => a + (closed.includes(e.status?.state) && e.status.r != null ? e.status.r : 0), 0);
    out.push(fact(first === "LONG" ? "up" : "down", first === "LONG" ? "Longs only" : "Shorts only"));
    out.push(booked <= -2 ? fact("ban", "Loss limit hit", "dn") : fact("shield", `${sgn(booked, 2)}R of −2R`));
  } else {
    const times = Object.keys(L.checks || {}).sort();
    if (times.length) {
      out.push(fact("clock", `${times.length} checks`));
      (L.checks[times[times.length - 1]].closest || []).slice(0, 2).forEach((x) =>
        out.push(fact(x.bias === "LONG" ? "up" : "down", `${x.symbol} · ${x.blockers.length ? x.blockers.map((b) => BLOCK_SHORT[b] || label(b)).join(", ") : "ok"}`)));
    }
  }
  if (replay && L.mode !== "replay" && L.checks) {
    const live = new Set(es.map((e) => e.symbol + e.direction));
    const extra = replay.entries.filter((e) => !live.has(e.symbol + e.direction));
    if (extra.length) out.push(fact("replay", `Replay: ${extra.map((e) => e.symbol).join(", ")} ${signedRupees(dayStats({ entries: extra }).total)}`, "acc"));
  }
  return out.length ? h("div", { class: "facts" }, out) : null;
}

const BLOCK_SHORT = { ATR_RANGE: "range", STRUCTURE: "no breakout", HAWKES: "weak flow", LOW_CONVICTION: "weak", DAY_DIRECTION: "against day",
  DAY_LOSS_LIMIT: "loss limit", LIQUIDITY: "illiquid", UPPER_CIRCUIT: "circuit", LOWER_CIRCUIT: "circuit", SESSION_LATE: "late", SESSION_CLOSED: "closed",
  SESSION_PRE_OPEN: "pre-open", SESSION_MORATORIUM: "too early" };
const BLOCK_WORDS = { ATR_RANGE: "not enough range left", STRUCTURE: "no clean breakout", HAWKES: "weak order flow", LOW_CONVICTION: "weak override",
  DAY_DIRECTION: "against today's direction", DAY_LOSS_LIMIT: "daily loss limit", LIQUIDITY: "low liquidity", UPPER_CIRCUIT: "near upper circuit", LOWER_CIRCUIT: "near lower circuit" };

const STATE_SHORT = { OPEN: ["clock", "Open"], TARGET: ["target", "Target"], STOPPED: ["x", "Stopped"], BREAKEVEN: ["shield", "Breakeven"],
  CLOSED_1515: ["exit", "15:15"], PENDING: ["clock", "Waiting"], NO_DATA: ["alert", "No data"] };
const lvl = (icon, k, v) => h("div", { class: "lvl" }, h("div", { class: "k" }, ico(icon), k), h("div", { class: "v num" }, v));

function tradeCard(e, isLive) {
  const st = e.status || {}, oi = e.option_idea, ss = STATE_SHORT[st.state] || ["info", st.state || "—"];
  const p = paper(e.direction === "LONG", st.fill, st.price, e.stop_distance, e.size_mult);
  return h("button", { class: "card " + e.direction, onclick: () => (isLive ? openSheet(e.symbol) : null) },
    h("div", { class: "tcard-top" },
      h("div", {}, h("div", { class: "sym" }, e.symbol),
        h("div", { class: "meta" }, h("span", { class: "pill " + e.direction }, dirIco(e.direction), e.direction), ico("clock"), e.signal_time, ico(ss[0]), ss[1])),
      h("div", {}, h("div", { class: "pnl num " + cls(p?.pnl) }, p && p.q > 0 ? signedRupees(p.pnl) : "—"),
        h("div", { class: "sub num " + cls(st.r), style: "text-align:right" }, st.r != null ? sgn(st.r, 2) + "R" : ""))),
    tradeBar(e, st),
    st.fill != null ? h("div", { class: "levels" }, lvl("enter", "Entry", fmt(st.fill)), lvl("shield", "Stop", fmt(st.stop_now ?? st.stop)),
      lvl("target", "Target", fmt(st.target)), lvl(st.state === "OPEN" ? "clock" : "exit", st.state === "OPEN" ? "Now" : "Exit", fmt(st.price))) : null,
    p && p.q > 0 ? h("div", { class: "sub num", style: "margin-top:6px" }, `${p.q} sh · ${rupees(p.invested)}`) : null,
    oi ? h("div", { class: "opt " + e.direction }, ico("layers"), ` ${oi.action} ${oi.strike} · ${oi.expiry}`) : null);
}

function journalView(L, isLive, replay) {
  const A = account(), D = dayStats(L);
  return [
    h("div", { class: "hero" },
      h("div", { class: "cap" }, ico("wallet"), `Day P&L · ${rupees(A.cap)} @ ${A.risk}%`, L.mode === "replay" ? h("span", { class: "fact", style: "margin-left:auto" }, ico("replay"), "replay") : null),
      h("div", { class: "big num " + cls(D.total) }, signedRupees(D.total)),
      h("div", { class: "sm num " + cls(D.total) }, sgn((100 * D.total) / A.cap, 2) + "%"),
      h("div", { class: "stats" }, stat("hash", "Trades", D.taken), stat("check", "Booked", signedRupees(D.booked), cls(D.booked)),
        stat("clock", "Open", signedRupees(D.open), cls(D.open)))),
    dayFacts(L, replay),
    tip("Each signal is taken at the next 5-minute open with the suggested size (max 5× margin), follows its stop, breakeven and target, and closes by 15:15. Costs included. Account size in settings."),
    ...(L.entries.length ? L.entries.map((e) => tradeCard(e, isLive))
      : [h("div", { class: "empty" }, ico("clock"), h("div", { class: "big" }, "No trades"), isLive ? "Signals 10:05–14:40" : null)]),
  ];
}

async function today() {
  const el = $("list");
  const [L, I, R] = await Promise.all([getJSON("today_log"), getJSON("journal/index"), getJSON("track_record")]);
  if (state.filter !== "TODAY") return;
  const now = istToday();
  const liveLog = L && L.entries ? L : null;   // newest live journal: today's once trading starts, else the last session's
  const liveDays = new Set((I?.days || []).map((d) => d.date));
  const past = [...new Set([...liveDays, ...(R?.days || []).map((d) => d.date), ...(liveLog ? [liveLog.date] : [])])]
    .filter((d) => d !== now).sort().reverse();
  state.day = state.day || "today";
  const yesterday = new Date(Date.parse(now + "T12:00:00Z") - 864e5).toISOString().slice(0, 10);
  const chip = (k, t) => h("button", { class: "chip" + (state.day === k ? " on" : ""), onclick: () => { state.day = k; today(); } }, t);
  const days = h("nav", { class: "chips days", "aria-label": "Day" },
    chip("today", "Today"), ...past.slice(0, 10).map((d) => chip(d, d === yesterday ? "Yesterday" : shortDate(d))),
    past.length ? chip("all", "All") : null);
  // The live journal is the freshest copy of its day; saved journals and replays cover the rest.
  // If live updates were missing all day (empty list), the after-close replay is the better record of it.
  const logFor = async (d) => {
    const live = (liveLog && liveLog.date === d ? liveLog : null) || (liveDays.has(d) ? await getJSON("journal/" + d) : null);
    const replay = replayLog(R, d);
    // A live list that was checked most of the day (12+ of the 19 checkpoints) is the real record even when empty;
    // otherwise (refreshes missing) the after-close replay is the better account of the day.
    const covered = live && live.checks && Object.keys(live.checks).length >= 12;
    return live && (live.entries.length || covered || !replay || !replay.entries.length) ? live : replay || live;
  };

  if (state.day === "all") {
    const all = [...(liveLog && liveLog.date === now ? [now] : []), ...past];
    const rows = [];
    for (const d of all) {
      const log = await logFor(d);
      if (log) rows.push({ d, src: log.mode === "replay" ? "replay" : "live", ...dayStats(log) });
    }
    if (state.filter !== "TODAY" || state.day !== "all") return;
    const A = account(), total = rows.reduce((a, r) => a + r.total, 0), trades = rows.reduce((a, r) => a + r.taken, 0);
    const winDays = rows.filter((r) => r.total > 0).length, maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.total)));
    const tile = (k, v, c) => h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v " + (c || "") }, v));
    return fill(el, days,
      h("div", { class: "hero" },
        h("div", { class: "cap" }, ico("trophy"), `All days · ${rupees(A.cap)} @ ${A.risk}%`),
        h("div", { class: "big num " + cls(total) }, signedRupees(total)),
        h("div", { class: "sm num " + cls(total) }, sgn((100 * total) / A.cap, 2) + "%"),
        h("div", { class: "stats" }, stat("check", "Days won", `${winDays}/${rows.length}`), stat("hash", "Trades", trades),
          stat("wallet", "Per day", signedRupees(rows.length ? total / rows.length : 0), cls(total)))),
      ...rows.map((r) => h("button", { class: "card dayrow", onclick: () => { state.day = r.d === now ? "today" : r.d; today(); } },
        h("div", { class: "row" }, h("div", {}, h("b", {}, r.d === now ? "Today" : shortDate(r.d)),
          h("div", { class: "meta sub", style: "display:flex;gap:5px;align-items:center" }, ico("hash"), `${r.taken}`, ico("check"), `${r.wins}`, r.src === "replay" ? ico("replay") : null)),
          h("b", { class: "num " + cls(r.total) }, signedRupees(r.total))),
        h("div", { class: "bar" }, h("span", { class: "t" }, h("i", { style: `left:${r.total >= 0 ? 50 : 50 - (50 * Math.abs(r.total)) / maxAbs}%;width:${(50 * Math.abs(r.total)) / maxAbs}%;background:var(--${r.total >= 0 ? "up" : "dn"})` }))))));
  }
  if (state.day !== "today") {
    const log = await logFor(state.day);
    if (state.filter !== "TODAY") return;
    return fill(el, days, ...(log ? journalView(log, false, replayLog(R, state.day)) : [h("div", { class: "empty" }, "No data saved for that day.")]));
  }
  if (liveLog && liveLog.date === now) {
    const log = await logFor(now);
    if (state.filter !== "TODAY" || state.day !== "today") return;
    return fill(el, days, ...journalView(log, log === liveLog, replayLog(R, now)));
  }
  // Nothing for today yet: say why in a few words, and point to the last session.
  const dow = new Date(now + "T12:00:00+05:30").getUTCDay(), weekend = dow === 0 || dow === 6;
  const last = past[0];
  fill(el, days, h("div", { class: "empty" }, ico(weekend ? "calendar" : "clock"), h("div", { class: "big" }, weekend ? "Market closed" : "Starts 10:05"),
    weekend ? null : "First update ~10:15",
    last ? h("div", { style: "margin-top:12px" }, h("button", { class: "chip on", onclick: () => { state.day = last; today(); } }, `${shortDate(last)} →`)) : null));
}

async function record() {
  const el = $("list"), R = await getJSON("track_record");
  if (state.filter !== "RECORD") return;
  if (!R || !R.days || !R.days.length) return fill(el, h("div", { class: "empty" }, ico("chart"), h("div", { class: "big" }, "No record yet"), "Starts after the first close"));
  const S = R.summary, A = account();
  const maxAbs = Math.max(0.5, ...R.days.map((d) => Math.abs(d.net_r)));
  const dayPnl = (d) => d.trades.reduce((a, t) => { const p = paper(t.direction === "LONG", t.entry, t.exit, t.stop_distance, t.size_mult); return a + (p ? p.pnl : 0); }, 0);
  let bal = A.cap, peak = A.cap, maxDd = 0;
  const equity = R.days.map((d) => { const p = dayPnl(d); bal += p; peak = Math.max(peak, bal); maxDd = Math.max(maxDd, (peak - bal) / peak); return { date: d.date, pnl: p, bal }; });
  const total = bal - A.cap, pnlByDate = Object.fromEntries(equity.map((x) => [x.date, x.pnl]));
  fill(el,
    h("div", { class: "hero" },
      h("div", { class: "cap" }, ico("wallet"), `Paper · ${rupees(A.cap)} @ ${A.risk}% · since ${S.from}`),
      h("div", { class: "big num " + cls(total) }, signedRupees(total)),
      h("div", { class: "sm num" }, `${rupees(bal)} · `, h("span", { class: cls(total) }, sgn((100 * total) / A.cap, 2) + "%")),
      h("div", { class: "stats" }, stat("hash", "Trades", S.trades || 0), stat("check", "Win", S.trades ? S.win_rate_pct + "%" : "—"),
        stat("down", "Max DD", fmt(100 * maxDd, 1) + "%")),
      h("div", { class: "stats" }, stat("calendar", "Days", S.sessions), stat("target", "Avg R", S.trades ? sgn(S.avg_net_r, 2) : "—", cls(S.avg_net_r)),
        stat("percent", "95% CI", S.net_r_ci95 ? `${sgn(S.net_r_ci95[0], 1)}…${sgn(S.net_r_ci95[1], 1)}` : "—", "small"))),
    tip("After-close replays of each day with the live rules. Fixed account size per trade, every trade taken, costs included. Not real money. " + (R.note || "")),
    ...R.days.slice().reverse().map((d) => h("div", { class: "box dayrow", style: "margin-bottom:8px" },
      h("div", { class: "row" }, h("div", {}, h("b", {}, shortDate(d.date)), h("div", { class: "sub num" }, `${d.trades.length} · ${sgn(d.net_r, 2)}R`)),
        h("b", { class: "num " + cls(pnlByDate[d.date] || 0) }, signedRupees(pnlByDate[d.date] || 0))),
      h("div", { class: "bar" }, h("span", { class: "t" }, h("i", { style: `left:${d.net_r >= 0 ? 50 : 50 - 50 * Math.abs(d.net_r) / maxAbs}%;width:${50 * Math.abs(d.net_r) / maxAbs}%;background:var(--${d.net_r >= 0 ? "up" : "dn"})` }))),
      d.trades.map((t) => { const p = paper(t.direction === "LONG", t.entry, t.exit, t.stop_distance, t.size_mult);
        return h("div", { class: "hl sub num", style: "display:flex;gap:6px;align-items:center" }, dirIco(t.direction), `${t.signal_time} ${t.symbol}`,
          h("span", { style: "margin-left:auto", class: cls(t.net_r) }, `${sgn(t.net_r, 2)}R`), p ? h("b", { class: cls(p.pnl) }, signedRupees(p.pnl)) : null); }))));
}

function list() {
  if (state.filter === "TODAY") return today();
  if (state.filter === "RECORD") return record();
  if (state.filter === "BACKTEST") return backtest();
  const f = state.filter, rows = state.data.signals.filter((s) => f === "ALL" || (f === "ACTION" ? s.direction !== "NO_TRADE" : s.direction === f));
  const el = $("list");
  if (!rows.length) return fill(el, h("div", { class: "empty" }, ico("zap"), h("div", { class: "big" }, f === "ACTION" ? "No setups now" : "Nothing here")));
  fill(el, ...rows.map((s) => {
    const e = s.execution, act = s.direction !== "NO_TRADE";
    const tags = [];
    if (s.contradiction) tags.push(h("span", { class: "tag w" }, label(s.contradiction.type)));
    s.risk.warnings.slice(0, 2).forEach((w) => tags.push(h("span", { class: "tag " + (w.level === "high" ? "r" : "w") }, ico("alert"), " ", label(w.code))));
    s.risk.blockers.slice(0, 3).forEach((b) => tags.push(h("span", { class: "tag" }, ico("ban"), " ", BLOCK_SHORT[b.code] || label(b.code))));
    return h("button", { class: "card " + s.direction, onclick: () => openSheet(s.symbol) },
      h("div", { class: "tcard-top" },
        h("div", {}, h("div", { class: "sym" }, s.symbol),
          h("div", { class: "meta" }, h("span", { class: "pill " + s.direction }, dirIco(s.direction), act ? s.direction : "NO TRADE"), label(s.setup))),
        h("div", {}, h("div", { class: "price-big num" }, "₹" + fmt(s.price.ltp)), h("div", { class: "sub num " + cls(s.price.chg_pct), style: "text-align:right" }, sgn(s.price.chg_pct) + "%"))),
      act ? h("div", { class: "levels" }, lvl("enter", "Entry", fmt(e.entry_trigger)), lvl("shield", "Stop", fmt(e.stop)), lvl("target", "Target", fmt(e.target)),
        lvl("hash", "Qty", qty(s).q)) : null,
      e.option_idea ? h("div", { class: "opt " + s.direction }, ico("layers"), ` ${e.option_idea.action} ${e.option_idea.strike} · ${e.option_idea.expiry}`) : null,
      tags.length ? h("div", { class: "tags" }, tags) : null);
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
  const b = $("sheetBody"); fill(b);
  b.append(h("div", { class: "hero" },
    h("div", { class: "cap" }, h("span", { class: "pill " + s.direction }, dirIco(s.direction), act ? s.direction : "NO TRADE"), label(s.setup)),
    h("div", { class: "big num" }, "₹" + fmt(s.price.ltp)), h("div", { class: "sm num " + cls(s.price.chg_pct) }, sgn(s.price.chg_pct) + "%"),
    act ? h("div", { class: "levels" }, lvl("enter", "Entry", fmt(e.entry_trigger)), lvl("shield", "Stop", fmt(e.stop)), lvl("target", "Target", fmt(e.target)), lvl("hash", "Qty", q.q)) : null));
  const reasons = [...r.warnings.map((w) => fact("alert", label(w.code), w.level === "high" ? "dn" : "")), ...r.blockers.map((x) => fact("ban", BLOCK_WORDS[x.code] || label(x.code)))];
  if (s.contradiction) reasons.push(fact("info", label(s.contradiction.type), "acc"));
  if (reasons.length) b.append(h("div", { class: "facts" }, reasons),
    tip([...r.warnings.map((w) => w.message), ...r.blockers.map((x) => x.message), s.contradiction ? s.contradiction.note : ""].filter(Boolean).join(" ")));
  const oi = e.option_idea;
  if (oi) b.append(h("div", { class: "box opt-box " + s.direction },
      h("div", { class: "opt-big" }, ico("layers"), ` ${oi.action} ${oi.strike} · ${oi.expiry} (${oi.days_to_expiry}d)`),
      oi.live ? h("div", { class: "sub" }, `${oi.tradingsymbol} · lot ${oi.lot_size} · ≈ ${rupees(oi.lot_cost)}`) : null,
      h("div", { class: "levels" }, lvl("wallet", "Premium", fmt(oi.premium_est)), lvl("shield", "Exit ≤", fmt(oi.premium_stop_est)), lvl("target", "Target", fmt(oi.premium_target_est)),
        lvl("info", oi.live ? "live" : "est.", oi.live ? "Kite" : "model"))));
  b.append(h("h4", {}, "Risk"),
    h("div", { class: "grid" },
      kv("Risk : Reward", e.risk_reward), kv("Stop distance", `₹${fmt(e.stop_distance)} (${fmt(e.stop_distance_pct)}%)`),
      kv("Move stop to entry at", e.breakeven_trigger != null ? `₹${fmt(e.breakeven_trigger)} (+${e.breakeven_after_r}R)` : "—"),
      e.gap_close_target ? kv("Gap close", "₹" + fmt(e.gap_close_target)) : null,
      kv("Capital at risk", act ? "₹" + fmt(q.risk, 0) : "—"),
      kv("VWAP fail-safe", "₹" + fmt(e.invalidation_vwap)), kv("Daily ATR", `₹${fmt(s.price.atr_daily)} (${fmt(s.price.atr_pct)}%)`)),
    tip(`${s.thesis} Invalidation: ${e.invalidation_text}`),
    h("h4", {}, "Scores · " + label(s.regime)),
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
    tip(state.data.meta.disclaimer));
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
    if (!state.data) fill($("list"), h("div", { class: "empty" }, "Unable to load data. Check your connection and pull to refresh."));
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
  $("kiteStatus").textContent = !m.kite_api_key ? "Not set up (add KITE_API_KEY / KITE_API_SECRET secrets)."
    : k.connected ? `✓ Connected · ${k.bars_from_kite || 0} prices · ${k.option_chains || 0} chains` : "Not connected · free data";
}
$("kiteBtn").onclick = () => {
  const key = state.data?.meta?.kite_api_key;
  if (key) location.href = `https://kite.zerodha.com/connect/login?v=3&api_key=${encodeURIComponent(key)}`;
};
$("settingsBtn").onclick = () => { refreshNotifUI(); refreshKiteUI(); $("capital").value = state.capital ?? state.data?.meta.capital ?? 100000; $("riskPct").value = state.riskPct ?? state.data?.meta.risk_pct ?? 1; $("settings").showModal(); };
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
  $("notifStatus").textContent = "✓ On for this phone";
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
  if (sub) showSub(sub); else $("notifStatus").textContent = "New LONG/SHORT signals, market hours";
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
const TABS = { TODAY: ["calendar", "Today"], ACTION: ["zap", "Active"], LONG: ["up", "Long"], SHORT: ["down", "Short"], ALL: ["list", "All"], RECORD: ["chart", "Record"], BACKTEST: ["flask", "Tests"] };
document.querySelectorAll("#filters .chip").forEach((c) => { const t = TABS[c.dataset.f]; if (t) c.replaceChildren(ico(t[0]), t[1]); });
$("settingsBtn").replaceChildren(ico("sliders")); $("refreshBtn").replaceChildren(ico("refresh")); $("installBtn").replaceChildren(ico("download"));
$("closeSheet").replaceChildren(ico("back"));
document.querySelectorAll("#filters .chip").forEach((c) => c.classList.toggle("on", c.dataset.f === state.filter)); // restore last view
load();
