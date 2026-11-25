# FX "bold play" from KSh 5,000 — 2026-11-25

Goal set by the user: turn KSh 5,000 (~$39) into KSh 10,000 as fast as possible, on demo.

Constraint: Pepperstone's minimum forex trade is 0.01 lot (1,000 units, ~$1,170 for EURUSD),
so any forex position on a $39 account is ~30x leverage.

Theory: with no positive edge, maximal bets maximise the probability of reaching a target
before ruin ("bold play", Dubins & Savage). Timid sizing lets a non-positive edge grind down.

Strategy: one 0.01-lot position in the pair with the strongest 1/3/6/12-month trend agreement
(ties: 3-month risk-adjusted momentum), re-checked each weekday. Costs ~1.2 bps per side.
Stop: double (KSh 10,000) or margin stop-out (~85% loss at 1:100).

Simulation on real daily data, a new KSh 5,000 account started every month 2007–2026:

| Outcome | Starts | Median trading days (25–75%) |
|---|---|---|
| Doubled | 96 / 234 (41%) | 31 (16–58) |
| Wiped out | 138 / 234 (59%) | 29 (15–56) |

By start era, share doubled: 2005–09 0.43, 2010–14 0.47, 2015–19 0.44, 2020–24 0.29, 2025– 0.50.

**Verdict:** fastest available route to the target, and worse than a coin flip. Demo only.
Not a candidate for real money.
