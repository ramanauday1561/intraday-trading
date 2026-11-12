# Design rule — colour (60 · 30 · 10)

| Share | Colour | Hex (reference) | Used for |
|---|---|---|---|
| **60%** | **Terracotta Clay** | `#C4673F` | Everything the eye rests on, in dark clay shades: page background (`#2A1813`), cards and tiles (`#3A2119`, `#4A2A20`), dividers and borders (`#6E3B2C`), the iPhone status bar / PWA theme colour. |
| **30%** | **Sage Stone** | `#A9B8A3` | Secondary information: muted text, labels, section headings, inactive chips, info banners, tags. Primary text is a warm off-white (`#FBEFE9`). |
| **10%** | **Cobalt Spark** | `#5B8CFF` (text, borders) · `#2F5FE0` (button fill) | The accent, used sparingly: primary buttons (white on cobalt), the active tab/day chip, links and "How", the logo, focus rings, the breakeven tick on trade bars, highlighted chips. |

**Functional exceptions (not part of the 60-30-10):** profit/long green `#4BD58C`, loss/short coral red `#FF7A6B`,
warning amber `#F5B33B`. They carry meaning, so they are kept apart from the palette: the green is far more
saturated than Sage Stone, the coral is brighter than the clay surfaces, and cobalt never means profit, loss or warning.

**Rules of thumb**
- New surfaces use the clay shades; never introduce a new background colour.
- Secondary text is always `--mu` (Sage Stone); do not invent new greys.
- If more than one or two things on a screen are cobalt, it is no longer an accent — pick the most important one.
- All text pairs meet WCAG AA (≥ 4.5:1): cobalt on the background 5.4:1, white on cobalt buttons 5.5:1, sage on cards 6.1:1+.

All colours are CSS variables in `assets/styles.css` (`:root`); the app and the Kite page use only those variables.
