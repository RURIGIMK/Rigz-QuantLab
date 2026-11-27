# FX bold-play variants, with swap — 2026-11-27

Same setup as 2026-11-25 (KSh 5,000, one 0.01-lot position, double or stop-out at 15%),
now with daily swap: (side × interest differential − 1% broker markup) on the position's notional,
using point-in-time OECD short rates. Selection rule fixed in advance: best share of doubled starts
on 2007–16, kept only if it also holds on 2017–26.

| Variant | 2007–16 (pick) | 2017–26 (check) | Median days to double |
|---|---|---|---|
| Slow: 12-month trend | **0.46** | **0.49** | 40 |
| Carry only (highest swap) | 0.45 | 0.38 | 41 |
| Current: 1/3/6/12-month trend | 0.44 | 0.38 | 31.5 |
| Fast: 1-month trend | 0.40 | 0.45 | 36 |
| Carry-aligned trend | 0.32 | 0.28 | 35 |

Current strategy without swap (the 2026-11-25 simulation): 0.41.

**Decision:** switch fx_bold_5k to the 12-month trend (fx_bold_slow). Better on both halves,
~11 points better than the current strategy on the unseen decade, ~9 trading days slower.
Caveat: ~117 overlapping starts per half, so differences of a few points are within noise; still
below a coin flip overall. Surprise: only trading the swap-earning side made things worse
(high-yield pairs crash).
