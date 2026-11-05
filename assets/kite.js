"use strict";
// Kite redirects here after login with ?status=success&request_token=…. The token is exchanged by the
// "Kite login (daily)" workflow, which holds the API secret; this page never sees the secret.
const REPO = "ramanauday1561/intraday-trading", WORKFLOW = "kite-session.yml";
const $ = (id) => document.getElementById(id);
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch { /* private mode */ } },
};
const q = new URLSearchParams(location.search);
const token = q.get("request_token"), ok = q.get("status") === "success";
$("wf").href = `https://github.com/${REPO}/actions/workflows/${WORKFLOW}`;

function manual(text) {
  $("msg").textContent = text;
  $("tok").value = token || "";
  $("manual").hidden = !token;
}

async function dispatch(gh) {
  $("msg").textContent = "Logged in to Kite ✓ — sending the one-time token to GitHub…";
  try {
    const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
      method: "POST",
      headers: { Accept: "application/vnd.github+json", Authorization: `Bearer ${gh}`, "X-GitHub-Api-Version": "2022-11-28" },
      body: JSON.stringify({ ref: "main", inputs: { request_token: token } }),
    });
    if (r.status === 204) {
      $("msg").textContent = "Done ✓ Kite is connecting. The app switches to Kite data within about 2 minutes. You can go back to the app.";
      return;
    }
    manual(`GitHub refused the request (${r.status}). Check the saved token's permissions, or finish by hand below.`);
  } catch (e) {
    manual("Could not reach GitHub. Finish by hand below.");
  }
}

$("copy").onclick = async () => {
  try { await navigator.clipboard.writeText($("tok").value); } catch { $("tok").select(); document.execCommand("copy"); }
  $("copy").textContent = "Copied ✓";
};
$("saveGh").onclick = () => { store.set("gh_dispatch_token", $("gh").value.trim()); $("saveGh").textContent = "Saved ✓"; if (ok && token) dispatch($("gh").value.trim()); };
$("clearGh").onclick = () => { store.set("gh_dispatch_token", null); $("gh").value = ""; $("clearGh").textContent = "Removed ✓"; };

if (!token) manual(q.get("status") ? `Kite login did not complete (status: ${q.get("status")}). Go back and try again.` : "Open this page from the app: ⚙ → Connect Kite.");
else if (!ok) manual("Kite login did not complete. Go back and try again.");
else if (store.get("gh_dispatch_token")) dispatch(store.get("gh_dispatch_token"));
else manual("Logged in to Kite ✓ One more step:");
