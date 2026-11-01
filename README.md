# Intraday Quant Desk

A serverless, installable PWA that turns open data into institutional-style intraday trade theses for liquid NSE F&O equities.

- **Backend** (`engine/`, Python, runs on GitHub Actions every 10 min in market hours): ingests prices (Yahoo Finance), India VIX, news (Google News + ET/Moneycontrol/Mint RSS), NSE public derivatives data; scores four pillars with regime-dependent weights; applies the contradiction matrix (distribution trap, priced-in exhaustion, genuine override); builds risk/execution parameters; writes `data/signals.json`.
- **Frontend** (`index.html`, `assets/`, `sw.js`): static, mobile-first, offline-capable PWA on GitHub Pages. Reads the JSON; recalculates quantity on-device from your capital/risk settings.

## Open-source / open-data stack
Yahoo Finance chart API · NSE public endpoints · Google News & publisher RSS · FinBERT (`ProsusAI/finbert`, optional) with a finance-lexicon fallback · NumPy · pandas · requests · GitHub Actions & Pages.

## Deploy
1. Repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. Run the **Analyse & deploy** workflow (or push to `main`). Site: `https://<user>.github.io/intraday-trading/`.
3. Optional: add repository variable `USE_FINBERT=1` for FinBERT sentiment.

## In the app
- **Actionable / Long / Short / All** – the latest 10-minute snapshot; tap a card for the trade plan and the ATM call/put idea.
- **Today** – every signal that appeared today stays listed with its live outcome (open, target hit, stopped, closed 15:15), even after it drops off the main list.
- **Record** – running track record: each session is replayed after the close (16:15 IST) with the rules in force that day and appended to `data/track_record.json`.
- **Backtest** – 5-minute, 2-year hourly and the pre-registered forward test.
- **Notifications** – ⚙ → Enable notifications (iPhone: open from the Home Screen icon, iOS 16.4+). Needs the `VAPID_PRIVATE_KEY` and `PUSH_SUBSCRIPTIONS` repository secrets.

## Reliable refresh
GitHub's built-in schedule can skip runs. For a dependable 10-minute refresh, follow [docs/RELIABLE_REFRESH.md](docs/RELIABLE_REFRESH.md) (free cron-job.org timer + a repo-scoped token).

## Install on your phone
- **Android/Chrome**: open the site → *Install* button (or ⋮ → Install app).
- **iPhone/Safari**: Share → *Add to Home Screen*.

## Run locally
```
pip install -r requirements.txt pytest
python -m pytest -q tests
python -m engine.run --demo        # synthetic data; omit --demo for live
python -m http.server 8000         # open http://localhost:8000
```

## Implemented from the blueprint
Regime weight matrix (trending / range / event / macro-shock + a transitional blend) · VWAP, volume profile (POC/VAH/VAL), ATR, ADX · BVC-signed order-flow imbalance · VPIN on a volume clock · Hawkes branching-ratio approximation · F&O OI quadrants · GEX, call/put walls, gamma flip, IV skew (when NSE data is reachable) · news taxonomy tiers, order-value/market-cap materiality, headline-based SUE proxy, cross-media divergence · VIX→daily σ (÷√252) stop scaling and size reduction · 15-min opening moratorium, no entries after 15:00 · circuit-proximity suppression/warnings · modelled 30-second LPP band (±40%, premium > ₹50) · structural invalidation text.

## Honest limitations
- Free data is delayed/incomplete; NSE frequently blocks cloud IPs, so GEX/IV-skew/OI may be missing (weights renormalise, UI shows `n/a`). Tick-level data and pre-open order-book depth are not available from open sources; 5-minute bars are used instead.
- SUE needs analyst consensus (not open): a headline beat/miss proxy is used. Market caps in `engine/config.py` are static approximations. MQSOS is not computable; a 20-day turnover proxy is used.
- The Hawkes estimate, regime thresholds and scoring weights are heuristics, **not backtested**. No orders are ever placed. Not investment advice.
