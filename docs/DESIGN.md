# Design rule — colour (60 · 30 · 10)

| Share | Colour | Hex (reference) | Used for |
|---|---|---|---|
| **60%** | **Smoked Plum** | `#5B4156` | Everything the eye rests on: page background (`#24182A`), cards and tiles (`#311F33`, `#3F2A3E`), dividers and borders (`#5B4156`), the iPhone status bar / PWA theme colour. |
| **30%** | **Dusty Rose** | `#D4A5AE` | Secondary information: muted text, labels, section headings, inactive chips, info banners, tags. Primary text is a rose-white (`#F8ECEF`) from the same family. |
| **10%** | **Marigold Yellow** | `#EAA221` | The accent, used sparingly: primary buttons (plum text on marigold), the active tab/day chip, links and "How this is calculated", the logo, focus rings, the breakeven tick on trade bars. |

**Functional exceptions (not part of the 60-30-10):** profit/long green `#3DBE9C`, loss/short red `#F06A6A`,
warning orange `#E8793A`. They mean something (money up/down, caution), so they are kept apart from the accent:
marigold never means "warning" and is never used for P&L.

**Rules of thumb**
- New surfaces use the plum shades; never introduce a new background colour.
- Secondary text is always `--mu` (Dusty Rose); do not invent new greys.
- If more than one or two things on a screen are marigold, it is no longer an accent — pick the most important one.
- All text pairs meet WCAG AA (≥ 4.5:1); lowest today is loss red on a card at 5.1:1.

All colours are CSS variables in `assets/styles.css` (`:root`); the app and the Kite page use only those variables.
