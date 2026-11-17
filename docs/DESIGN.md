# Design rule — colour (60 · 30 · 10)

| Share | Colour | Hex (reference) | Used for |
|---|---|---|---|
| **60%** | **Sage Stone** | `#A9B8A3` | Everything the eye rests on, in dark sage shades: page background (`#171D18`), cards and tiles (`#212922`, `#2C362D`), dividers and borders (`#455446`), the iPhone status bar / PWA theme colour. Primary text is a sage-tinted white (`#F1F5EF`). |
| **30%** | **Terracotta Clay** | `#C4673F` (borders, tints) · `#E0896A` (text) | Secondary information: muted text, labels, section headings, inactive chips, info banners, tags. The lighter tone is used for text so it stays readable on dark sage. |
| **10%** | **Cobalt Spark** | `#5B8CFF` (text, borders) · `#2F5FE0` (button fill) | The accent, used sparingly: primary buttons (white on cobalt), the active tab/day chip, links and "How", the logo, focus rings, the breakeven tick on trade bars, highlighted chips. |

**Functional exceptions (not part of the 60-30-10):** profit/long green `#4BD58C`, loss/short pink-red `#FF6B88`,
warning amber `#F5C542`. The loss colour is pink-red (not orange-red) so it is never confused with terracotta text,
and money values always carry a + or − sign. Cobalt never means profit, loss or warning.

**Rules of thumb**
- New surfaces use the sage shades; never introduce a new background colour.
- Secondary text is always `--mu` (light terracotta); do not invent new greys.
- If more than one or two things on a screen are cobalt, it is no longer an accent — pick the most important one.
- All text pairs meet WCAG AA (≥ 4.5:1): terracotta text 4.8–5.7:1, cobalt 5.4:1, white on cobalt buttons 5.5:1, loss red 5.5:1.

All colours are CSS variables in `assets/styles.css` (`:root`); the app and the Kite page use only those variables.
