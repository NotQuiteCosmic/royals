# Architecture

How Royals is put together, and why. For the rules of the game itself see
[RULES.md](RULES.md).

---

## The shape of it

```
                      ┌─────────────────────────┐
                      │   royals_engine         │   pure stdlib, no UI, no I/O
                      │   hasher / engine / ai  │
                      └────────────┬────────────┘
                                   │  imported by
              ┌────────────────────┼────────────────────┐
              │                    │                    │
      ┌───────▼──────┐    ┌────────▼───────┐    ┌───────▼────────┐
      │ apps/desktop │    │ apps/terminal  │    │ web/           │
      │ tkinter      │    │ ANSI           │    │ FastAPI + REST │
      └──────────────┘    └────────────────┘    └────────────────┘
```

One engine, three front ends, and a hard wall between them. The engine does not know
which of the three is calling it and is forbidden from finding out.

## The engine's one rule

`royals-engine` imports nothing but the standard library, and
[tests/test_engine_purity.py](../tests/test_engine_purity.py) fails the build if that
stops being true — or if anything in the engine imports tkinter, calls `input()`, or
prints.

This is not tidiness. The same modules run in a tkinter window, in a terminal, in a web
worker serving many games at once, and eventually in a browser under Pyodide. **A module
that prints to stdout or blocks on stdin has decided which of those it is.** It is also a
security property: no third-party code sits in the path that validates a move.

The engine targets **Python ≥ 3.10, not 3.12**, so it keeps running under PyPy — several
times faster on this search, and the intended interpreter for the server's AI worker.

### Modules

| Module | Lines | What it is |
|---|---|---|
| `hasher.py` | 327 | Board representation. 49 ints, 13 bits each. |
| `engine.py` | 949 | Move generation, validation, execution, entering, ko. |
| `ai.py` | 981 | Alpha-beta search and position evaluation. |
| `notation.py` | 352 | Text and JSON forms of moves and boards. |
| `perlin.py` | 108 | 2D noise, used only to vary the computer's opening. |
| `compat.py` | 39 | Two helpers that outlived the legacy `RoyalsLib`. |

Modules keep their original CamelCase function names, so they are conventionally imported
aliased:

```python
from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import ai as artificialPlayer
```

## The board

**A board is a `tuple` of 49 `int`s**, indexed 0-based in reading order. Each int packs
one square into 13 bits:

```
bit  0     occupied
bit  1     controlling side (0 blue, 1 red)
bit  2     dragon
bit  3     spy
bits 4-6   pawns, 0 to 4
bit  7     royal
bit  8     any prisoners
bit  9     captured spy
bits 10-12 captured pawns, 0 to 4
```

Three properties fall out of this and the whole design leans on all three:

**One arrangement, one encoding.** `Build_Space` zeroes everything past a dragon and
everything past the prisoner flag, so two identical positions are always the same 49
integers. That is what lets a board stand as its own name in the ko history and in the
transposition table.

**Tuples, not lists.** Boards are hashable and cannot be edited from under a caller.
Every executor is pure — board in, new board out — so a child node in the search is just a
return value and the parent survives untouched. The 2D version this replaced had to
`copy.deepcopy` the whole board at every node.

**A square is a subscript.** The original encoding packed squares end to end at 1, 3, 9 or
13 bits each, so finding square *n* meant measuring the *n*−1 squares in front of it —
`Get_Space_Data` walked the entire board on every call and was the most-called function in
the engine. Fixed width in a real array made it an index.

### Precomputed tables

Because a square code is only 13 bits, there are just 8192 of them, and anything a square
can be asked is precomputed for all of them at import:

- `UNPACK[code]` → the eight fields plus derived scalars, so move generation never touches
  a bit.
- `WIN_CODES` → the frozenset of codes that are a won square, derived from the field rules
  rather than restated as bit patterns.
- `JUMPRAY`, `PUSHRAY`, `BREAKRAY` → every walk across the board, drawn once at import and
  walked as tuples thereafter.

The rays matter more than they look. Which sequence of squares a jump, push or break
covers depends on *where you start and which way you go, and nothing else* — not what is
standing there, not who is moving, not how heavy they are. Weight decides how **far** along
the sequence you get, never what the sequence **is**. So the sequence is a table and the
weight is a slice.

## Moves

A move is one flat, hashable tuple:

```python
move = (origin, kind, target, movingPris)
```

| Field | Meaning |
|---|---|
| `origin` | **1-based** square it leaves from |
| `kind` | `"jump"`, `"push"`, `"free"` or `"break"` |
| `target` | **0-based** square for the first three; an index into `Engine.pushDirs` for a break |
| `movingPris` | whether the prisoners on the origin come along |

> ⚠️ **The 1-based/0-based split is real and deliberate.** `ai.performOneStep` does
> `move[MOVE_TARGET] + 1` for the first three kinds and passes `target` through untouched
> for a break. Every off-by-one this codebase can produce lives in that asymmetry, which is
> why it is spelled out once — in `notation.py` — and why **no other module performs
> arithmetic on a square.**

It used to be `[origin, [kind, target], movingPris]`, a list holding a list, which could
not be a dictionary key — so the killer and history tables were fed a freshly built tuple
every time a move was so much as looked at. In a depth-6 search that came to over 400,000
tuples built for no other purpose.

## The search

Alpha-beta with iterative deepening, a transposition table, killer moves and a history
heuristic.

### Scores are integers

Whole numbers of thousandths of a point (`SCALE = 1000`). This is not a micro-optimisation
and it is worth understanding before anyone "cleans it up".

Alpha-beta compares its numbers **exactly** — `beta <= alpha`, `score > bestScore` — so a
difference in the last place is enough to flip a cutoff and change the move played.
Measured over 3064 positions, CPython and PyPy differed on 400 of them by up to 1.5e-14
relative: never enough to show at four decimal places, occasionally enough to pick a
different move. The regression suite passed under one interpreter and failed under the
other on exactly that.

Every evaluation weight is exact at this scale, including the spread penalty, which was the
only irrational term. **CI runs the goldens under both interpreters** so this cannot come
back.

### Module-level state

`Engine.koTrack`, `Engine.koGeneration`, and the AI's transposition, killer and history
tables are **module-level globals**. That is fine for a desktop app playing one game, and
it is a correctness hazard the moment one process serves two games — game B's positions
would land in game A's ko set and the ko rule would start rejecting legal moves.

Anything running more than one game must therefore load exactly one game's state, run, and
drop it. `web/src/royals_web/ai_pool.py` is the only place in the web app that touches these
globals; `regress.py` does the same thing between sweeps with `koReset()`/`koRecord()`.

## Tuning the evaluator

`tuning/` is a self-play tournament system for the position evaluator. It is stdlib-only
like the engine, so it runs under PyPy, which is where its tens of thousands of games
belong. It imports the engine from its own checkout whatever is pip-installed -- see the
note at the top of `tuning/__init__.py` for why that matters on this machine.

### Weights

Every number `ai.evaluateSides` multiplies by is a module global named in
`ai.WEIGHT_NAMES`, with `ai.getWeights()`, `ai.setWeights()` and `ai.DEFAULT_WEIGHTS`
around them. Weights are integers in `SCALE` units and `setWeights` refuses anything else,
for the reason given under "Scores are integers" above. Each has a range in
`ai.WEIGHT_RANGES` that keeps a real position's score well short of `WIN_SCORE`.

Candidate terms -- things the evaluator did not use to look at -- live alongside the old
ones at a default of 0, and at 0 they are not computed: `golden.txt` records the
evaluation of every position it visits and stays byte-identical until a term earns a
non-zero default. **That re-record is the one legitimate way `golden.txt` moves without a
rules change**, and only its `eval` lines may differ.

### The per-side search state

The transposition table persists across moves within a game on purpose (above), and an
entry is a score the weights in force *when it was written* produced. In a game between two
weight sets that is a contamination: red would be reading blue's evaluator. So
`tuning/game.py` gives each side a `SearchState` holding its weights and its four tables,
puts it in force before that side's search and reads the tables back afterwards -- back,
because `chooseMove` rebinds those module names rather than mutating them. The ko history
is shared; it is a fact about the game. With identical weights the swap is skipped and a
default-vs-default game reproduces `golden_search.txt` ply for ply, which is the check
that the runner is transparent.

### Games, pairs and statistics

The search is deterministic, so all variety comes from openings: `tuning/book.jsonl`
holds 10,000 of them as RAN tokens (the entering under Perlin noise at intensity 1.0 plus
two random plies), built once because entering costs about a second and replaying tokens
costs microseconds. Every opening is played twice with colours swapped, and the pair's
score is what the statistics see (`tuning/stats.py`): pentanomial counts, logistic Elo
with an interval from the pair variance, a simplified GSPRT for early stopping, and
Bradley-Terry ratings for round-robins. Self-play Elo runs two or three times hotter than
Elo against the world; it is for comparing candidates with each other.

`tuning/match.py` drives a process pool through four modes -- `match`, `sprt`,
`tournament`, `gauntlet` -- writing one JSONL line per pair and resuming from it.
`tuning/spsa.py` tunes a weight vector by SPSA over paired games; `tuning/scale.py` picks
a probe size for a zero-default term by reading its raw size off the evaluator itself.
### Running on GitHub Actions

The repo is public, so GitHub-hosted runners are free: 4 cores each, up to 20 at once,
6 hours a job -- about ten times the laptop. Four `workflow_dispatch` workflows in
`.github/workflows/` use them. `tune-match` and `tune-gauntlet` shard a match across
runners (`tuning.match --shard i/N`: every Nth opening of the book, offset i) and pool the
shard files at the end (`tuning.match merge`), judging any SPRT on the pooled pairs.
`tune-spsa` runs SPSA in waves: every shard draws its own perturbations from the same
theta, an `apply` job folds them all in, and the workflow re-dispatches itself until the
iteration target is met (`tuning.spsa init|wave|apply`). `tune-book` builds a book in
slices and merges them. Determinism means a runner plays exactly the games the laptop
would, so results from the two mix freely.

Results outlive the runner because each workflow's last step commits them -- verdict
summaries, SPSA checkpoints, gzipped games when small -- to the `tuning-results` branch
with `tuning/ci/record.sh`. CI never touches `ai.py`, the goldens or `master`; a person
copies figures into `tuning/RESULTS.md`. Locally the harness defaults to half the cores so
the machine stays usable; runners pass `--workers 4` to use all of theirs.

`tuning/pool/` holds the weight sets that have earned a place: `original.json` is the
evaluator before any tuning and is in every gauntlet forever; `champ-001.json` is what the
October 2026 tuning adopted and what `ai.DEFAULT_WEIGHTS` now holds. `tuning/RESULTS.md` is
the record of every run.

## The web layer

FastAPI, in `web/src/royals_web/`:

| File | What it does |
|---|---|
| `main.py` | HTTP routing, request limits. Decides *who may ask*. |
| `game.py` | Game state and move validation. Decides *what is legal*. |
| `ai_pool.py` | Process pool, concurrency cap, wall-clock deadline per search. |
| `store.py` | In-memory game store — bounded, TTL'd, per-client create limit. |
| `static/` | The browser client — `index.html`, `app.js`, `board.js`, `style.css`. |

The design rests on one rule:

> **The board never travels from the client to the server.**

A move request carries a kind, an origin, a destination and a prisoner flag — four small
values. The server loads the position from its own store and independently regenerates
every legal move before accepting one. `AI.listAllMoves` is the same generator the computer
plays by, so a person and the machine are held to literally the same rules, and "is this
legal?" is a tuple membership test rather than a second, subtly different implementation of
the rulebook.

Two things the pool exists to prevent: a depth-6 search is seconds of pinned CPU that
anybody can request by clicking a menu, and a game holds every position it has stood in, so
an unattended endpoint that mints games is a memory attack that needs no cleverness at all.
Hence the hard concurrency cap, the queue that refuses rather than grows, the deadline, the
game ceiling and the TTL.

Storage is in memory on purpose — there are no accounts yet, so there is nothing to persist
a game against. The interface is deliberately the small one that survives the swap to
Postgres.

## Tests

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the workflow. In short:

- **`tests/golden.txt`** — the rules contract. 13,496 lines. Every legal move from a spread
  of positions, the board it produces, and what the evaluator thinks it is worth. Must stay
  byte-identical.
- **`tests/golden_search.txt`** — node counts per move. Expected to churn.
- **`tests/test_engine_purity.py`** — enforces the no-dependencies, no-UI rule. (22)
- **`tests/test_notation.py`** — round-trips for the text and JSON move forms. (51)
- **`tests/test_web_api.py`** — the REST surface, against FastAPI's `TestClient`. (22)
- **`tests/test_weights.py`** — the evaluator's weights: each weight drives only its term
  (checked under the pre-tuning weights), the defaults are the tuned set, the candidate
  terms measure what they say. (27)
- **`tests/test_tuning.py`** — the tuning harness at the two-second scale: openings replay,
  a game records and replays, the statistics are right on known inputs, the queue finishes
  itself and does nothing twice. (14)

136 in total. The web tests import `fastapi`, so the full suite needs the server installed
(`pip install -e ./web`); the engine's own tests need nothing but the standard library,
which is the point.

## The Backup directory

[Backup/](../Backup/) holds the frozen Python-2-era originals — `royals_py2.py`,
`backup_py2.py`, `Royals_py3_BACKUP.py`. They are not imported by anything and are not
maintained.

They are kept because they are the historical record of what the game did before the
rewrite, and several comments throughout the engine refer to "the backup" when explaining
why a rule is the way it is — usually to note where the old version had it wrong (its
`checkFinished` crossed the two winner slots over; its direction headings were labelled the
other way round because the board was indexed `[row][column]`).

`apps/terminal/royals_lib.py` is in the same category but is still live: the terminal driver
imports it.
