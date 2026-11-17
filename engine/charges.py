"""Zerodha equity intraday (MIS) charges, the published schedule, so backtests and paper money cost what Kite charges.

Per executed order: brokerage = min(Rs 20, 0.03% of the order value). STT 0.025% on the sell side. NSE exchange
transaction charge 0.00297% and SEBI fee Rs 10/crore on both sides. GST 18% on brokerage + exchange + SEBI.
Stamp duty 0.003% on the buy side. Plus an assumed slippage (crossing the spread with a LIMIT just through the
best price) of SLIPPAGE_PCT of each side's value. Standard library only (the order server imports it too).
For real trades the order server asks Kite's charges API; this is the model for everything that is not a real fill.
"""
BROKERAGE_PCT, BROKERAGE_CAP = 0.03, 20.0
STT_SELL_PCT = 0.025
EXCHANGE_PCT = 0.00297
SEBI_PCT = 0.0001
GST_PCT = 18.0
STAMP_BUY_PCT = 0.003
SLIPPAGE_PCT = 0.01          # per side, % of value: liquid F&O stocks, limit order one tick through the best price


def intraday(entry, exit_, qty, long_=True, slippage=True):
    """Rupees for one round trip of `qty` shares (buy then sell for a long, sell then buy for a short)."""
    if qty <= 0:
        return 0.0
    buy, sell = (entry, exit_) if long_ else (exit_, entry)
    bv, sv = buy * qty, sell * qty
    brokerage = min(BROKERAGE_CAP, bv * BROKERAGE_PCT / 100) + min(BROKERAGE_CAP, sv * BROKERAGE_PCT / 100)
    exchange = (bv + sv) * EXCHANGE_PCT / 100
    sebi = (bv + sv) * SEBI_PCT / 100
    total = brokerage + exchange + sebi + (brokerage + exchange + sebi) * GST_PCT / 100
    total += sv * STT_SELL_PCT / 100 + bv * STAMP_BUY_PCT / 100
    if slippage:
        total += (bv + sv) * SLIPPAGE_PCT / 100
    return round(total, 2)


def sized_qty(entry, stop_distance, capital, risk_pct, size_mult=1.0, leverage=5.0):
    """The app's/order server's quantity: risk / stop distance, capped by intraday margin."""
    if not stop_distance or stop_distance <= 0 or not entry:
        return 0
    return max(0, min(int(capital * risk_pct / 100 * size_mult / stop_distance), int(capital * leverage / entry)))
