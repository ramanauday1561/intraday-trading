"use strict";
const DATA_URL = "data/signals.json";
const $ = (id) => document.getElementById(id);
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const state = { data: null, filter: "ACTION", capital: store.get("capital", null), riskPct: store.get("riskPct", null) };

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

function qty(s) {
  const risk = (state.capital ?? state.data.meta.capital) * ((state.riskPct ?? state.data.meta.risk_pct) / 100) * s.risk.size_multiplier;
  const d = s.execution.stop_distance;
  return d > 0 ? (() => { const cap = (state.capital ?? state.data.meta.capital) * 5; const qq = Math.min(Math.floor(risk / d), Math.floor(cap / s.price.ltp)); return { q: qq, risk: qq * d }; })() : { q: 0, risk };
}

function banners() {
  const m = state.data.meta, box = $("banners");
  box.replaceChildren();
  const add = (lvl, title, msg) => box.append(h("div", { class: "banner " + lvl }, h("b", {}, title), msg));
  if (m.mode === "demo") add("info", "Demo data", "Live sources were unreachable, so this is a synthetic market for demonstration only. Not tradable.");
  const age = (Date.now() - new Date(m.generated_at)) / 60000;
  if (m.mode === "live" && age > 30) add("medium", "Stale data", `Last analysis ran ${Math.round(age)} min ago.`);
  const hl = m.health;
  if (m.mode === "live" && hl) {
    const bad = [];
    if (hl.prices_ok < hl.universe) bad.push(`prices for ${hl.prices_ok}/${hl.universe} stocks`);
    if (!hl.news_items) bad.push("no news fetched");
    if (!hl.nse_derivatives) bad.push("NSE derivatives (GEX, OI, IV skew) unavailable");
    add(bad.length ? "medium" : "info", "Live data check", bad.length ? "Partial: " + bad.join("; ") + "." : `All sources OK: ${hl.prices_ok} stocks, ${hl.news_items} headlines, NSE derivatives.`);
  }
  const phases = { CLOSED: "Market closed — showing the last completed session.", MORATORIUM: "15-minute opening moratorium (until 09:30 IST). Execution disabled.",
    PRE_OPEN: "Pre-open call auction. Engine inactive until 09:30 IST.", LATE: "After 15:00 IST — no fresh entries." };
  if (phases[m.session_phase]) add("medium", "Session", phases[m.session_phase]);
}

function macro() {
  const M = state.data.macro, w = M.weights;
  const t = (k, v, s, c) => h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v " + (c || "") }, v), h("div", { class: "s" }, s || ""));
  $("macro").replaceChildren(
    t("Nifty 50", fmt(M.nifty.ltp), sgn(M.nifty.chg_pct) + "%", cls(M.nifty.chg_pct)),
    M.banknifty ? t("Bank Nifty", fmt(M.banknifty.ltp), sgn(M.banknifty.chg_pct) + "%", cls(M.banknifty.chg_pct)) : t("Bank Nifty", "—"),
    t("India VIX", fmt(M.india_vix), `σ/day ≈ ${fmt(M.expected_daily_move_pct)}%`),
    t("Regime", label(M.regime), `F${w.fundamental * 100 | 0} P${w.price_action * 100 | 0} I${w.institutional * 100 | 0} M${w.macro * 100 | 0}`));
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
        "Trades that oppose a material news signal have mean net R > 0 after costs. Registered 2026-10-25; only sessions after that date count. See preregistration.md in the repo."),
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

function list() {
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
  b.append(h("h4", {}, "Execution & risk matrix"),
    h("div", { class: "grid" },
      kv("Entry trigger", "₹" + fmt(e.entry_trigger)), kv("Risk : Reward", e.risk_reward),
      kv("Stop-loss", `₹${fmt(e.stop)}`), kv("Distance to stop", `₹${fmt(e.stop_distance)} (${fmt(e.stop_distance_pct)}%)`),
      kv("Trailing stop", `₹${fmt(e.trail_distance)} behind price`), kv(s.setup === "TRAP_FADE" ? "Target (gap close)" : "Target", "₹" + fmt(e.gap_close_target ?? e.target)),
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
  } catch (err) {
    if (!state.data) $("list").replaceChildren(h("div", { class: "empty" }, "Unable to load data. Check your connection and pull to refresh."));
    else if (manual) $("banners").prepend(h("div", { class: "banner medium" }, h("b", {}, "Offline"), "Showing the last saved analysis."));
  }
}

$("filters").addEventListener("click", (ev) => {
  const b = ev.target.closest("button"); if (!b) return;
  state.filter = b.dataset.f; document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === b));
  if (state.data) list();
});
$("refreshBtn").onclick = () => load(true);
$("closeSheet").onclick = () => history.back();
window.addEventListener("popstate", closeSheet);
$("settingsBtn").onclick = () => { $("capital").value = state.capital ?? state.data?.meta.capital ?? 100000; $("riskPct").value = state.riskPct ?? state.data?.meta.risk_pct ?? 0.5; $("settings").showModal(); };
$("settings").addEventListener("close", () => {
  const c = parseFloat($("capital").value), r = parseFloat($("riskPct").value);
  if (c > 0 && r > 0 && r <= 100) { state.capital = c; state.riskPct = r; store.set("capital", c); store.set("riskPct", r); }
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
setInterval(() => { if (!document.hidden) load(); }, 120000);

// PWA install
let deferred;
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferred = e; $("installBtn").hidden = false; });
$("installBtn").onclick = async () => { if (!deferred) return; deferred.prompt(); await deferred.userChoice; deferred = null; $("installBtn").hidden = true; };
window.addEventListener("appinstalled", () => { $("installBtn").hidden = true; });
const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
if (/iphone|ipad|ipod/i.test(navigator.userAgent) && !standalone && !store.get("iosHintSeen", false)) $("iosHint").hidden = false;
$("iosClose").onclick = () => { $("iosHint").hidden = true; store.set("iosHintSeen", true); };
if ("serviceWorker" in navigator) window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
load();
