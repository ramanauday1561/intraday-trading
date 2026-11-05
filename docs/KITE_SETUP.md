# Zerodha Kite Connect setup

The app uses Kite **only to read market data**: 5-minute and daily candles, option chains (premiums, open
interest, implied volatility, lot sizes) and futures open interest. It never places, changes or cancels orders.

## One-time setup

1. **Repository secrets** (GitHub → repo → Settings → Secrets and variables → Actions), named exactly:
   - `KITE_API_KEY`: the API key of your Kite Connect app
   - `KITE_API_SECRET`: its API secret
2. **Redirect URL** in the Kite developer console (developers.kite.trade → your app):
   `https://ramanauday1561.github.io/intraday-trading/kite.html`
3. *(Optional, for one-tap login)* Open the app → ⚙ → **Connect Kite**, log in, and on the page you return to
   save a fine-grained GitHub token limited to this repository with **Actions: Read and write** only (the same
   kind of token as for cron-job.org). It is kept only on your phone.

## Every trading day (before 10:00)

Zerodha expires access tokens every morning around 06:00, and requires you to log in yourself. Automating
that login with your password or TOTP is against Kite's terms, so the app does not do it.

App → ⚙ → **Connect Kite** → log in to Zerodha → you return to `kite.html`:
- with a saved GitHub token, it finishes automatically and the app switches to Kite data within ~2 minutes;
- without one, copy the token it shows, open the **Kite login (daily)** workflow → *Run workflow* → paste
  it → *Run workflow*. The token works once and only for a few minutes.

If you don't log in, the app keeps working on free data (Yahoo, no option chains), as before.

## What Kite adds

| Without Kite | With Kite |
|---|---|
| Yahoo candles (rate-limited, occasional gaps) | Exchange candles from Zerodha |
| No option chains (NSE blocks cloud servers) | Option chain per stock: GEX, call/put walls, gamma flip, IV skew |
| No futures OI → "F&O quadrant: unavailable" | Long/short build-up and unwinding from futures OI change |
| Estimated ATM strike, expiry and premium | Real listed contract, live premium and **lot size** |

Futures OI change needs the previous day's close: the nightly archive job saves it, so OI-based signals start
from the second day after you connect.

## Security

- Exchanging the login token needs `KITE_API_SECRET`, which only GitHub Actions has. The phone never sees it.
- The daily access token is encrypted with a key derived from `KITE_API_SECRET` before it goes into the Actions
  cache. Workflows without the secret, such as pull requests from forks, cannot read it. It expires every morning.
- The request token typed into the workflow input is single-use, expires in minutes, and can't be used without
  the secret.

## Cost

Kite Connect is a paid Zerodha product. If your plan doesn't include market data or historical candles, Kite
answers "not permitted": the app shows **Not connected / free data** and carries on with Yahoo.
