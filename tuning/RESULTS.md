# Tuning results

What the tournaments found, in the order they were run. Numbers are self-play Elo at the
stated depth, which runs two to three times hotter than Elo against varied opponents; read
them against each other, not against the world. Intervals are 95%. "H0" and "H1" are SPRT
verdicts, alpha = beta = 0.05.

Raw results live in `tuning/results/*.jsonl` (not tracked); every game there replays from
its tokens.

## Calibration (2026-10-06, depth 3, book openings)

Two deliberately broken evaluators against the default, as non-regression SPRTs [-10, 0],
to see what "clearly worse" looks like in these units and to exercise the pipeline end to
end.

| Candidate | Pairs | Pentanomial | Elo | Verdict |
|---|---|---|---|---|
| `SPREAD_WEIGHT = 0` | 95 | [31, 5, 55, 1, 3] | −114 [−156, −74] | H0 |
| `DIAG_WEIGHT = 0` | 1077 | [268, 5, 589, 11, 204] | −20 [−34, −6] | H0 |

The comments in `ai.py` said the spread penalty "is what actually drives play" and that the
mobility term is "a nudge". Both are borne out, and the ratio between them gives the scale:
the spread penalty is worth about six mobility terms.

Game lengths: mean 58 plies for the spread run, 6 of 190 games hit the 300-ply cap.

## Cost of the batch-1 candidate terms (CPython, depth 5, nodes/s, median of 5)

| Setting | nodes/s | vs defaults |
|---|---|---|
| defaults | 35,600–36,300 | — |
| `SPY_DIST_WEIGHT = 1` | 33,900 | −5% |
| `THREAT_PENALTY = 1` | 34,000 | −5% |
| `SPY_STACK_WEIGHT = 1` | 36,000 | 0% |
| `WRONG_COLOUR_PENALTY = 1` | 31,700 | −11% (noise: the term is trivial) |
| all four | 28,700 | −21% |

The bench swings ±10% run to run on this machine, so single-term costs are inside the
noise; all four together are not. Any adoption of several terms at once should look at
`candidateTerms` for a cheaper formulation first.

## Probe sizes for SPSA (`tuning/scale.py`, 570 positions from the calibration games)

Typical |evaluation| 12,476. Raw size per unit weight, and the c that moves the evaluation
by a tenth of that: `SPY_DIST_WEIGHT` 20.4 → c 61; `THREAT_PENALTY` 0.42 → c 2969;
`SPY_STACK_WEIGHT` 1.35 → c 927; `WRONG_COLOUR_PENALTY` 1.33 → c 936.

## Run 1: SPSA over the twelve existing weights (`retune12`, depth 3, 20,000 pairs)

Probe sizes an eighth of each default; R_end 0.002; base = the shipped defaults.

| Weight | Start | End | Moved |
|---|---|---|---|
| `DIAG_WEIGHT` | 250 | 251 | 0% |
| `GROUP_PENALTY` | 1500 | 1491 | −1% |
| `PRISONER_PAWN_WEIGHT` | 800 | 818 | +2% |
| `PRISONER_SPY_WEIGHT` | 800 | 796 | −1% |
| `STACK_1` | 1000 | 1013 | +1% |
| `STACK_2` | 4000 | 4156 | +4% |
| `STACK_3` | 9000 | 8784 | −2% |
| `STACK_4` | 16000 | 15094 | −6% |
| `STACK_5` | 25000 | 25380 | +2% |
| `CAPTIVE_PCT` | 200 | 199 | 0% |
| `SPREAD_WEIGHT` | 1500 | 1511 | +1% |
| `ROYAL_SPY_PENALTY` | 5000 | 4972 | −1% |

**The hand tuning holds.** Nothing moved by more than its probe size, and the one move of
note (`STACK_4`, −906) is about two standard deviations of the random walk a parameter of
that step size makes over 20,000 iterations when it has nothing to say -- weak evidence at
best, and in the direction of flattening the stack table towards the top. The trajectory
report shows every parameter's last-quarter drift inside its quarter-before drift. The
`ai.py` comment "nothing beat where they already stand" was right about the terms it had.

Run statistics: 40,000 games, 12.3 CPU-hours (about 1.5 h wall on 8 PyPy workers), mean 57
plies, 1.0% of games hit the 300-ply cap, and only **36% of pairs were decisive** -- in the
other 64% each side won once, so the opening decided the pair and it said nothing about the
weights. That is the price of deterministic play from a book: a pair carries about a third
of the information a naive estimate assumes, and SPRT runs should be expected to take
correspondingly longer.

## Run 2: SPSA over the twelve plus the four batch-1 terms (`features16`, depth 3, 20,000 pairs)

Same settings as run 1, with the candidate terms starting at 0 and probing at the sizes
`scale.py` gave (61, 2969, 927, 936). A term that defaults to 0 can only be probed upward --
the downward probe clamps to 0 -- so the estimate is one-sided but correctly signed: a term
that helps gets pushed off the floor, a term that doesn't stays on it.

| Weight | Start | End | Probe c |
|---|---|---|---|
| `SPY_DIST_WEIGHT` | 0 | 5 | 61 |
| `THREAT_PENALTY` | 0 | 5 | 2969 |
| `SPY_STACK_WEIGHT` | 0 | 1 | 927 |
| `WRONG_COLOUR_PENALTY` | 0 | 31 | 936 |

**None of the four left the floor.** Each ended at a few percent of its own probe size,
which is what a term does when the version with it on wins no more often than the version
with it off. The twelve old weights repeated run 1 within noise -- including `STACK_4`
drifting down again (15,990 → 15,314), the one movement to recur across two independent
runs -- and the end vector is otherwise the default with the serial numbers filed off.

Follow-up, since one-sided probing at a single size is a blunt instrument: each term is
tested on its own at a fixed, deliberately substantial weight against the default (next
section), and `STACK_4 = 15200` with it.

## Fixed-weight tests of each term (depth 3, gain SPRT [0, 10], cap 1500 pairs)

Each candidate is the default with one weight changed, against the default.

| Candidate | Pairs | Pentanomial | Elo | Verdict |
|---|---|---|---|---|
| `SPY_DIST_WEIGHT = 100` | 322 | [86, 7, 177, 2, 50] | −42 [−67, −17] | H0 |
| `THREAT_PENALTY = 1000` | 1061 | [214, 13, 636, 4, 194] | −8 [−21, +5] | H0 |
| `SPY_STACK_WEIGHT = 1000` | 278 | [73, 2, 164, 1, 38] | −45 [−71, −19] | H0 |
| `WRONG_COLOUR_PENALTY = 500` | 1500 | [260, 14, 901, 15, 310] | +12 [+1, +23] | inconclusive |
| `STACK_4 = 15200` | 1500 | [106, 3, 1260, 6, 125] | +5 [−2, +12] | inconclusive |

Spy-distance and spy-stack at a substantial weight are clearly harmful at depth 3: pulling
pieces towards the spy, or paying for pawns already on it, overrides the stacking and
spread terms that actually win games. The threat term is a wash -- the search sees the
capture itself one ply on, as expected. Wrong-colour is the one term with a positive sign,
not yet a significant one.

## The tuned sixteen-weight vector against the default (depth 3, 1500 pairs)

`features16/theta.json` -- the default to within a few percent, plus `SPY_DIST_WEIGHT = 5`,
`WRONG_COLOUR_PENALTY = 31`, `THREAT_PENALTY = 5`, `SPY_STACK_WEIGHT = 1`:

    1500 pairs  pent [172, 11, 942, 17, 358]  elo +44.0 [+33.6, +54.5]

A large, significant gain from a vector that is almost the default. The likely mechanism is
tie-breaking: the default evaluator scores a great many positions identically (integer
terms, nothing that cares where a group stands beyond its spread), and a few thousandths of
a point per piece of jump distance to the spy resolves those ties towards gathering without
ever overriding a real term -- which is exactly what the same term at 100 does and is
punished for. Ablations follow before anything is believed.

## Ablations of the tuned vector (depth 3, 1000 pairs each, against the default)

| Candidate | Pentanomial | Elo |
|---|---|---|
| default as "a" vs `theta16` as "b" (swap check) | [227, 12, 628, 7, 126] | −36 [−49, −23] |
| `theta12` (run 1's twelve, no new terms) | [109, 4, 719, 4, 164] | +19 [+8, +30] |
| `theta16` with its four new terms zeroed | [105, 1, 748, 5, 141] | +13 [+3, +24] |
| `SPY_DIST_WEIGHT = 5` alone | [129, 6, 664, 7, 194] | +23 [+11, +35] |
| `WRONG_COLOUR_PENALTY = 31` alone | [95, 10, 733, 7, 155] | +20 [+10, +31] |
| both of those | [135, 7, 647, 8, 203] | +24 [+11, +36] |
| all four terms at their tuned values (5, 5, 1, 31) | [137, 6, 638, 8, 211] | +26 [+13, +39] |

The swap check flips sign as it should: the harness is not favouring a side. But **every
perturbation of the default beats the default by a similar margin**, including the retuned
twelve alone and each tiny term alone. Four unrelated changes winning by the same amount is
not four improvements; it is one mechanism they share. The suspect is tie density: with
round-number weights a great many different positions score exactly equal, and any
perturbation lets the search tell them apart. The test of that is a perturbation too small
to mean anything -- `STACK_1 = 1001` -- which is running next.

## `theta16` at depth 4 (SPRT [0, 5], cap 1500)

    1112 pairs  pent [100, 137, 557, 162, 156]  elo +21.4 [+10.3, +32.6]  -> H1

The gain holds at depth 4, whatever it turns out to be made of. Separate red flag: 22.5% of
depth-4 games hit the 300-ply cap (mean 142 plies, against 57 at depth 3). Depth-4 self-play
is far more drawish than depth 3 and the cap needs raising for any serious depth-4 work.

## Is it just that any change beats the default? No.

| Candidate | Pentanomial | Elo |
|---|---|---|
| `STACK_1 = 1001` vs default | [2, 0, 997, 0, 1] | 0 [−2, +1] |
| `DIAG_WEIGHT = 251` vs default | [5, 0, 989, 0, 6] | 0 [−2, +3] |
| `SPY_DIST=5, WRONG_COLOUR=31` vs `theta12` | [182, 6, 628, 10, 174] | −2 [−15, +11] |

A perturbation too small to mean anything plays the identical game in 997 pairs out of a
thousand and gains nothing. So the gains above are what they look like: real. The two
different kinds of gain -- the reshaped stack table and the tiny gather terms -- are the same
size against each other, and roughly additive: `theta16` (+44) is close to the sum of its
stack-table part (+13) and its gather-term part (+26).

Tie density, measured over 300 positions from played games: under the default only 54% of a
position's child evaluations are distinct and the best move is an exact tie in 17.5% of
positions, decided by board order. `SPY_DIST_WEIGHT = 5` brings that to 61% and 13%: at five
thousandths of a point per piece per jump it never outweighs a real term, it only says which
of two otherwise-equal moves is the one that gathers. At 100 it outweighs real terms and
costs 42 Elo. The stack-table change does not touch the tie structure (54%, 17.3%), so it is
a different kind of improvement -- a 4-stack is worth a little less relative to a 2 and a 5
than the square law said.

## What the mechanism means for the candidate terms

- `SPY_DIST_WEIGHT`: harmful as a term, valuable as a tie-breaker. Adopt small.
- `WRONG_COLOUR_PENALTY`: positive at 31 and still positive at 500; the same fact at a
  different scale. Adopt small; it and spy-distance together add nothing over each alone
  (tiny2 +24 vs each ~+20), so one of them is probably enough.
- `THREAT_PENALTY`: a wash at 1000, stayed at the floor under SPSA. The search sees the
  capture itself. Not adopted; left in at 0 for a future depth-dependent experiment.
- `SPY_STACK_WEIGHT`: harmful at 1000, floor under SPSA. Not adopted.

## The adoption candidate: `rounded` (depth 3, 1000 pairs each)

`theta16` with its twelve old weights rounded to the nearest ten, `THREAT_PENALTY` and
`SPY_STACK_WEIGHT` back to 0 (they did nothing and cost a loop), and the two gather terms
kept: `SPY_DIST_WEIGHT = 5`, `WRONG_COLOUR_PENALTY = 31`.

| Match | Pentanomial | Elo |
|---|---|---|
| `rounded` vs default | [123, 8, 619, 10, 240] | +41 [+28, +54] |
| `rounded` vs `theta16` | [53, 4, 896, 8, 39] | −4 [−11, +3] |

Rounding cost nothing measurable, so the candidate is the readable one. Depth-5 and depth-6
checks follow (600-ply cap, since depth 4 showed how drawish deeper self-play gets).

## Adopted (2026-10-06): `rounded` is the new default, as `tuning/pool/champ-001.json`

`ai.DEFAULT_WEIGHTS` now holds it; the pre-tuning evaluator is kept as
`tuning/pool/original.json` and stays in every gauntlet. Both goldens were re-recorded. The
non-eval content of `golden.txt` -- every placement, every move, every resulting board -- is
byte-identical before and after (same hash, 7,151 lines); 12,664 `eval` lines moved, which is
the deliberate change. Beware that `git diff` shows two unchanged move lines as
removed-and-added because the eval lines around them changed; compare the non-eval content
directly, as CONTRIBUTING.md now says.

Evidence against the original, self-play Elo:

| Depth | Pairs | Elo |
|---|---|---|
| 3 | 1000 | +41 [+28, +54] |
| 4 | 1112 | +21 [+10, +33] (SPRT [0, 5] accepted; measured on `theta16`, which `rounded` ties) |
| 4 | 1390 | +19 [+9, +30] -- the adopted set itself, SPRT [0, 5] accepted (H1) |
| 5 | 400 | +19 [−0.2, +38] (600-ply cap; 1 of 800 games capped) |
| 6 | 40 | −4 [−59, +51] -- a sanity check, not a measurement: nothing alarming, no precision |
| 7 | 40 | +70 [+20, +124] -- 9 of the 10 decisive pairs went to the adopted set |

The depth-7 check (80 games, mean 56 plies, ten minutes a game, 13 CPU-hours) was meant
as the same kind of sanity check as depth 6 and came out sharper than 40 pairs usually
allow: pentanomial [1, 0, 30, 0, 9], so of the ten openings where the two evaluators did
not simply trade wins, nine went to the tuned set. The interval is wide and the point
estimate should not be quoted as a number, but the sign is clear, and it does not support
the idea that the gain fades away at the depths where a tie-breaker ought to matter least.

Depth-6 games take about three minutes each, so 40 pairs was the budget; the interval is as
wide as the effect being looked for is small. The depth-5 figure is the same size as depth
4's and just grazes zero. Taken together, depths 3 through 5 say the gain is real and
shrinks slowly with depth, as a tie-breaker's should: the deeper the search, the more often
it can tell two moves apart by itself.

Cost: the two gather terms turn the post-pass on at every leaf. Benched against the
pre-tuning weights in the same session (CPython, depth 5, median of 5): 35,300 and 31,900
nodes/s for the adopted defaults against 37,900 -- about 10% slower, at the top of the
5–10% expected. At fixed depth that is wall time, not strength: the web's "royal" level
goes from roughly 1.9 to 2.1 seconds a move. `candidateTerms` is the place to look if that
ever matters.

The depth-4 SPRT is also a lesson in SPRT windows: the adopted set read +36 Elo [+15, +57]
after 359 pairs and +22 [+9, +35] after 882, both plainly positive, yet the test ran to 1390
pairs before accepting, because with bounds at 0 and 5 Elo the log-likelihood ratio grows in
proportion to a 5-Elo score difference however large the true effect is. A narrow window
buys certainty about a small gain at the price of needing many games even for a big one.

One bug caught on the way in: `ANY_POST` was a literal `False` recomputed only by
`setWeights`, so with the new defaults the gather terms would have been skipped in the
desktop and web apps until something called the setter -- which only the harness did. Test
ordering hid it (an earlier test's teardown had already rebuilt the flag). It is now computed
from the defaults at import, and `test_defaults_are_the_tuned_set` checks it first thing.

## On GitHub Actions (2026-10-07)

The repo is public, so GitHub-hosted runners are free: 4 cores each, 20 at once, 6 hours a
job. `tune-match`, `tune-gauntlet`, `tune-spsa` and `tune-book` shard the work across them
(`--shard i/N`: every Nth opening) and pool it at the end; results are committed to the
`tuning-results` branch so they outlive the runner. The proving run, run 37654964831:
champ-001 vs original at depth 3, 4 shards x 50 pairs.

    200 pairs  pent [24, 0, 131, 1, 44]  elo +35.7 [+7.9, +64.0]     83 seconds wall time

Inside the laptop's +41 [+28, +54]. Better than that: the laptop had played the same 400
games (same openings, same weights) and **394 are identical move for move**; the other six
are games the laptop cut off at its 300-ply cap and the runner played on to 600, and in each
the laptop's game is an exact prefix of the runner's. A macOS PyPy 3.11 laptop and Linux
PyPy 3.10 runners play the same games, which is what the integer evaluator was for. Three of
those six capped games went on to a gather between plies 300 and 600, so the 300 cap had been
calling decidable games draws; 600 is the default everywhere now.

## Step 6 on Actions (2026-10-07): the adopted set at scale

Gauntlets of champ-001 against `original.json`, judged as non-regression SPRTs [-10, 0] on
the pooled pairs (both passed, both in fact accepting the *gain*):

| Depth | Pairs | Pentanomial | Elo | Caps |
|---|---|---|---|---|
| 3 | 1000 | [125, 3, 625, 3, 244] | +41.5 [+28.6, +54.6] | 8 of 2000 games |
| 4 | 500 | [49, 63, 234, 65, 89] | +28.6 [+10.9, +46.3] | 174 of 1000 games (17%), mean 201 plies |

The depth-3 figure reproduces the laptop's +41 on the same thousand openings, as
determinism says it must (the small differences are the 600-ply cap). Depth-4 self-play is
drawish even at 600 plies: one game in six runs to the cap. That is a property of the two
evaluators shuffling, not of the harness, and it is worth a look of its own some day.

Batched SPSA proved live: `tune-spsa` run `proof` (champ-001's 16 weights, 20 shards × 50
pairs) applied its first wave -- 1,000 iterations, every weight within a few units of where
it started, which is what a plateau looks like -- committed its checkpoint, and dispatched
its own second wave through `GITHUB_TOKEN`, which the concurrency group held until the
first run finished. The second wave took the run to 2,000 of 2,000 and dispatched nothing
further; the checkpoint and trajectory are on `tuning-results` under `spsa/proof/`. A full
20,000-iteration run is twenty such waves, about a day unattended. The depth 4/5/6/7
re-measurements follow below when their runs land.

### Re-measurements at scale (champ-001 vs original, 20 shards each)

| Depth | Pairs | Pentanomial | Elo | Caps (600 plies) | Mean plies |
|---|---|---|---|---|---|
| 4 | 5000 | [507, 541, 2596, 583, 773] | +20.0 [+14.6, +25.3] | 1684 of 10000 (17%) | 195 |
| 5 | 2000 | [301, 7, 1313, 5, 374] | +12.5 [+3.7, +21.4] | 16 of 4000 (0.4%) | 63 |
| 6 | 500 | [50, 48, 272, 60, 70] | +18.1 [+1.6, +34.6] | 176 of 1000 (18%) | 183 |
| 7 | 200 | [12, 0, 151, 0, 37] | +43.7 [+20.4, +67.3] | 0 of 400 | 59 |

**Summary of the first tuning cycle, as measured at scale.** The adopted weights beat the
pre-tuning evaluator at every depth from 3 to 7, with every interval clear of zero:

| Depth | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|
| Elo | +41 | +20 | +12 | +18 | +44 |
| Pairs | 1000 | 5000 | 2000 | 500 | 200 |

The size varies -- lowest at depth 5, highest at 3 and 7 -- and the intervals at 6 and 7
are wide, so the honest reading is "a real gain of roughly 10 to 40 self-play Elo at every
depth", not a trend. The story that a tie-breaker's value should fade with depth is not
borne out: depth 7 is as good as depth 3. Whatever the terms are doing, deeper search is
not doing it on its own.

All of Step 6 -- 7,700 colour-swapped pairs plus two gauntlets and the SPSA proof, about
40,000 games -- ran in two hours of wall time on the free runners, on a day when the laptop
was busy being used.

Depth 4 is now a tight number and agrees with the laptop's 1,390-pair reading (+19). The
gain is real at depth 5 too, and smaller: the ordering so far is +41 (d3), +20 (d4), +12
(d5), which is what a tie-breaker's value should do as the search gets better at telling
moves apart by itself.

**Even depths are drawish; odd depths are decisive.** Depth-4 games average 195 plies and
one in six reaches the 600-ply cap, where depth-5 games average 63 and almost none do --
and depths 2 and 6 show the same long, undecided shuffling (depth 6: 183 plies, 18% capped). A search that ends on the
opponent's reply (even depth) sees every committal move answered and plays safe; one that
ends on its own move (odd depth) sees the gain and takes it. The web app's "strong" level
is depth 4. Whether that passivity is worth fixing (a quiescence-like extension, or odd
depths only) is a question for the search, not the evaluator, and is noted here for later.

## A balanced book: what the data says (2026-10-07)

Two thirds of pairs split one game each and say nothing. Is "decided by the opening" a
property of the opening that could be used to prune the book?

**Across depths, no.** Selecting openings that split in every match at the other depths
and testing them at a held-out depth: they were decisive there 35% / 35% / 27% of the time
against 38% / 35% / 24% for the openings kept (depths 3, 5, 7 held out). No signal. A deeper
search changes what a position means.

**At one depth, yes, strongly.** The laptop's sixteen depth-3 match-ups over the same
thousand openings allow a leave-one-match-up-out test. Openings that always split in the
other fifteen were decisive in the held-out match-up 0–3% of the time (for match-ups
between similar evaluators; 22–27% against the deliberately broken ones), against 30–40%
for the rest. Per-opening decisive fractions spread widely: 341 openings under 20%, 15 over
80%. Ranking openings by their decisive fraction on the other match-ups and keeping the top
share, judged on the held-out match-up:

| Keep | 100% | 90% | 75% | 50% | 33% |
|---|---|---|---|---|---|
| Decisive pairs | 33% | 36% | 41% | 49% | 53% |

Half the book carries half again the information per game. So the balanced book is a
**per-depth ranking**, built from several match-ups at that depth over the whole book, and
SPSA at depth 3 should play the top half. Three probe weight sets (`tuning/probes/`) give
Actions distinct match-ups to rank the full 10,000-opening book with.
