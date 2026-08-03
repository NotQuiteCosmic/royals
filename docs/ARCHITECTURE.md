# Architecture

How Royals is put together, and why. For the rules of the game itself see
[RULES.md](RULES.md).

---

## The shape of it

```
                      ┌─────────────────────────┐
                      │   royals_engine         │   pure stdlib, no UI, no I/O
                      │   hasher / engine / ai  │   (+ royals_accel, if installed)
                      └────────────┬────────────┘
                                   │  imported by
              ┌────────────────────┼────────────────────┐
              │                    │                    │
      ┌───────▼──────┐    ┌────────▼───────┐    ┌───────▼────────┐
      │ apps/desktop │    │ apps/terminal  │    │ web/           │
      │ tkinter      │    │ ANSI           │    │ FastAPI + REST │
      └──────────────┘    └────────────────┘    └───────┬────────┘
                                                        │ serves
                                                ┌───────▼────────┐
                                                │ the browser    │
                                                │ + royals.wasm  │
                                                └────────────────┘
```

One engine, three front ends, and a hard wall between them. The engine does not know
which of the three is calling it and is forbidden from finding out.

The browser is the fourth consumer and the odd one out: it is served *by* the web front end
rather than importing anything, and it carries its own copy of the rules — the same rules,
compiled from the Rust to WebAssembly. That copy answers one question, quickly, and the server
still decides. See "The engine exists twice" below, and "The web layer" for the one rule the
page is allowed to get wrong.

## The engine exists twice

The rules are implemented in Python and again in Rust, and **both are kept**.

```
   engine/src/royals_engine/          engine-rs/
   Python, stdlib only                Rust
   the reference implementation       the same rules, ~36x faster
            │                               │
            │                  ┌────────────┴────────────┐
            │                  │                         │
            │           royals_accel wheel           royals.wasm
            │           optional; import          shipped in static/,
            │           it and ai.py uses it      the browser's own copy
            │                  │                         │
            └──────────────────┴──── tests/golden_moves.txt ────┘
              two of them emit it byte for byte; the third is
                    held to the same rules, move by move
```

**~36x** is measured, and worth stating precisely because everything else here rests on it: a
depth-6 search from the position `golden_search.txt`'s first game starts from takes 3.16s in
CPython and 0.091s through the wheel — 23,000 nodes a second against 810,000 — on the laptop
this was developed on. At that ratio the compiled engine reaches depth 9 in the time Python
takes for depth 6, which is what "three plies deeper" means elsewhere in the docs.
`cargo run --release --example bench` in `engine-rs/` reproduces the Rust half.

Not a migration with a leftover. The Python is the **reference implementation** — it is what
`golden_moves.txt` was recorded from, it is the fallback when no wheel is present, and it is
the other half of a differential check that runs on every commit. Delete it and the Rust
becomes unfalsifiable.

Three consumers, one contract:

| | how it arrives | if it is missing |
|---|---|---|
| **Python** | always there, stdlib only | n/a — it is the floor |
| **`royals_accel`** | an optional wheel; `ai.py` shims to it when importable | the game runs in Python, identical and slower |
| **`royals.wasm`** | checked in under `static/`, loaded by `engine.js` | the browser asks the server, as it always did |

`ROYALS_NO_ACCEL=1` forces the Python path even where the wheel is installed. That switch is
not a debugging aid — CI runs the whole suite twice on it and requires byte-identical goldens
from both, which is what stops the two implementations drifting apart.

**Why the accelerator is a separate package.** `engine/pyproject.toml` stays pure setuptools
with no build requirements, so `pip install -e ./engine` needs no Rust toolchain and
`install-desktop.sh` can keep promising that nothing has to be installed. It is a top-level
`royals_accel` rather than `royals_engine._rust` because an editable install points
`royals_engine.__path__` at the source tree while a wheel lands in site-packages — a submodule
would be unimportable in exactly the layout this repo is developed in, and would fail by
silently falling back.

**`static/royals.wasm` is the one artifact that can go stale silently.** Edit `movegen.rs`,
forget to rebuild, and the page highlights yesterday's rules while every test stays green.
`engine-rs/tests/wasm_parity.py` is the guard: it asks the *shipped* module what the Python
engine is asked — every position along the port fixtures' walks, both sides, every square,
carrying prisoners and not, and it prints how many questions that came to — and CI checks a
freshly built one too, so "the rules diverged" and "somebody forgot to rebuild" are
distinguishable.

It is checked behaviourally rather than by diffing the artifact against a fresh build, and
that is a deliberate choice: a byte comparison would fail the day the CI runner's rustc moves,
for a module that plays exactly the same game. A check nobody can act on is a check that gets
switched off.

For the spec all of them are written against, see [PORTING.md](PORTING.md).

### Building the two artifacts

Neither is produced by `pip install -e ./engine`, and both are needed to check a rules change
end to end. From the repo root:

```bash
maturin build --release --manifest-path engine-rs/Cargo.toml   # the royals_accel wheel

cargo build --release --features wasm --target wasm32-unknown-unknown \
  --manifest-path engine-rs/Cargo.toml                          # the browser's copy
cp engine-rs/target/wasm32-unknown-unknown/release/royals_engine.wasm \
   web/src/royals_web/static/royals.wasm
```

The wheel is `abi3-py310`, so one build per platform covers every CPython from 3.10 up; CI
builds four (Linux x86-64, macOS arm64 and x86-64, Windows) on every commit and never publishes
them — an accelerator that ships itself is one nobody decided to ship. The wasm needs
`rustup target add wasm32-unknown-unknown` and nothing else: no npm, no wasm-bindgen, no
generated glue. [PORTING.md](PORTING.md) §10 says why.

## The engine's one rule

`royals-engine` imports nothing but the standard library, and
[tests/test_engine_purity.py](../tests/test_engine_purity.py) fails the build if that
stops being true — or if anything in the engine imports tkinter, calls `input()`, or
prints.

This is not tidiness. The same modules run in a tkinter window, in a terminal, and in a web
worker serving many games at once. **A module that prints to stdout or blocks on stdin has
decided which of those it is.** It is also a security property: no third-party code sits in the
path that validates a move — `royals_accel` is whitelisted and is the only exception, because
it is these same rules compiled rather than somebody else's code.

The engine targets **Python ≥ 3.10, not 3.12**, so it keeps running under PyPy — several
times faster than CPython on this search, which is what the fallback has to work with on a
machine the wheel was never built for.

### Modules

| Module | What it is |
|---|---|
| `hasher.py` | Board representation. 49 ints, 13 bits each. |
| `engine.py` | Move generation, validation, execution, entering, ko. |
| `ai.py` | Alpha-beta search and position evaluation. |
| `notation.py` | Text and JSON forms of moves and boards, and of whole games. |
| `record.py` | Walks a saved game back into the boards it passed through. Applies plies; decides nothing. |
| `perlin.py` | 2D noise, used only to vary the computer's opening. |
| `compat.py` | Two helpers that outlived the legacy `RoyalsLib`. |
| `_accel.py` | Finds `royals_accel` or doesn't, honours `ROYALS_NO_ACCEL`, and reports which engine is answering. |

(No line counts. They moved every commit and told a reader nothing they could act on; what the
table is for is saying what each module is *for*.)

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

**"A desktop app playing one game" is worth reading carefully — it means one game at a
time, and two games can overlap in time without either of them being concurrent.** A search
runs on a worker thread and cannot be called off; pressing NEW GAME while the computer is
thinking leaves a search that is still going to answer, about a position that is no longer on
the board. Answering it wrote the old game's board over the new one, appended a ply to a
record nobody played, and put an old position into the ko set the new game had just cleared —
the last of which made the new game start refusing legal moves as repetitions.

So `royals_gui.py` carries a `gameGen` counter, bumped by `buildSetup` and `resetGameState`,
and every deferred thing the window hands to the event loop carries the number the game had
when it was asked for: `pollAI` drops an answer whose generation has moved, and `later()` is
`root.after` with the same check. The search is not stopped, because it cannot be; its answer
is simply never delivered to a game it was not computed for. `tests/test_desktop_lifecycle.py`
is what holds that.

### What the desktop is painted with

The palette is thirty-three module constants at the top of `royals_gui.py`, and
`apps/desktop/theme.py` is the same thirty-three as a schema, so a look can be written to a
file and handed round. `applyTheme` assigns into those names — which is exactly as complete as
threading a palette object through three thousand lines, because every colour here is read by
a plain global lookup at the moment it is drawn, and it costs one function instead of every
call site. What that function has to know is the two places a colour is *captured* rather than
looked up: `CHIP_WALL`, a table built from colours, which is rebuilt; and `_MIXED`, keyed by
the colour values themselves, which is merely cleared.

Nothing repaints on apply. The board takes a new palette on its next `redraw()`, since that
clears the canvas and reads every colour again; the widgets take it when their builder next
runs, since a tk option is copied into the widget at construction — and both screen builders
already start by destroying their frame, so "rebuild the screen" was a mechanism this app
had before it had themes.

The appearance screen previews a theme it has not applied to anything, which works because
`polyPlate`, `chip` and `obelisk` take every colour as an argument and read no globals. The
miniature is therefore the real drawing code with different arguments, rather than an
impression of it. Gradients are board-canvas only and drawn as bands — the canvas has no
gradient primitive and no alpha, and the side panel is a `tk.Frame` rather than a canvas.

### Two cameras

The board can be drawn orthographically — the view it has always had, where a square is the
same size wherever it sits — or in three-point perspective, which is what a real lens does
with a camera tilted the way this one is: the rows converge, the columns converge, and so do
the verticals. A checkbox in the game panel switches it, and the choice lives on the `View`
beside the yaw and the pitch, so it outlives a game and not the session.

`View.project` differs between the two by a single factor `k = d / (d - depth)`, which is 1 in
orthographic. Everything else follows from where that factor is allowed to look. Because it
sees the height as well as the ground position, the vertical axis gets a vanishing point of
its own — and that is what costs: under the old camera a chip's lid was exactly as wide as its
foot and exactly above it, so a piece could be an oval, a rectangle and an oval. It cannot be
now, and `chip`, the lid icons, the destination rings and the prisoner slot are all built in
the world and projected, on the pattern `obelisk` always used.

**One renderer serves both cameras**, rather than a fast path for the old one. Two renderers
of the same object drift, and nothing would catch it but the eye of whoever last looked at
both. What keeps the change honest instead is `tests/test_projection.py`, which pins the
orthographic arithmetic against the formula written in the design comment, and pins the
world-space icons against the pixel arithmetic they replaced — both to the last place.

The painter's order survives untouched: squares are still drawn far to near and each draws its
own stack. A leaning column could have broken that and does not, which is asserted over every
ordered pair of squares at every yaw rather than argued.

## The web layer

FastAPI, in `web/src/royals_web/`:

| File | What it does |
|---|---|
| `main.py` | HTTP routing, authorization, request limits, and the security headers. Decides *who may ask*. |
| `game.py` | Game state and move validation. Decides *what is legal*. |
| `seats.py` | Seat tokens: mint, hash, constant-time compare. |
| `ai_pool.py` | Process pool, concurrency cap, wall-clock deadline per search. |
| `store.py` | Bounded in-memory cache in front of the database; per-client rate limits. |
| `persist.py` | SQLite. One row per game, holding the move list rather than the board. |
| `limits.py` | ASGI middleware bounding a request body before it is read, which an HTTP-level check cannot do. |
| `static/` | The browser client — `index.html`, `app.js`, `board.js`, `style.css`, plus `engine.js` and `royals.wasm`. |

One thing in `main.py` is worth naming here rather than leaving in the source: the
Content-Security-Policy carries `'wasm-unsafe-eval'`, without which Chrome refuses to compile
`royals.wasm` at all. Despite the name it is the narrow token — it permits WebAssembly
compilation and nothing else, and `'unsafe-eval'` proper stays refused. It is the one
relaxation the browser engine cost.

The design rests on two rules. The first:

> **The board never travels from the client to the server.**

A move request carries a kind, an origin, a destination and a prisoner flag — four small
values. The server loads the position from its own store and independently regenerates
every legal move before accepting one. `AI.listAllMoves` is the same generator the computer
plays by, so a person and the machine are held to literally the same rules, and "is this
legal?" is a tuple membership test rather than a second, subtly different implementation of
the rulebook.

**The page's own copy of the engine does not weaken that, and the asymmetry is worth being
precise about.** `static/engine.js` drives `royals.wasm` to answer "which squares can this
piece reach" without a round trip — 100ms a pick-up on mobile data, for a question about a
position the page was already holding. But the module holds a *position*, not a *history*, so
it cannot apply the rule that a move may not return the game to a position it has already
stood in. What it returns is therefore a **superset**: every legal move, plus any that repeat.

That is not a rounding error — 43% of positions along the regression walks have at least one
move struck off by ko — so the page draws the local answer immediately and then reconciles
against the server's, which can only ever take squares away. The server is unchanged and
still re-derives everything on submission. The local copy exists to make the page quick, never
to make it right.

The second rule, which arrived with two players:

> **Every endpoint that changes a game asks who is asking.**

A game has two `Seat`s, either of which may be a person or the computer, and a seat is
claimed by holding its token. `_require_player` turns that token into a side, and the side
it returns is the side the action is taken as — no code path takes a side from a request
body, so "move for my opponent" is not a request that can be phrased rather than one that
is checked for. Knowing a game's id gets you a spectator's view and nothing else, which
matters because the id travels in a URL people paste into messages.

Playing a friend needs no account. Creating a game with `mode: "human"` mints a seat token
for the creator and a single-use invite token for the empty seat; the game sits in a
`waiting` phase until somebody claims it. Only hashes are stored, compared with
`secrets.compare_digest` — a plain SHA-256, because key-stretching exists to make
*guessable* secrets expensive and a 128-bit random token is not guessable.

The token travels in an `X-Royals-Seat` header rather than a cookie, which is what keeps
CSRF out of the design instead of defended against: a browser attaches cookies to
cross-site requests on its own and will not attach this.

Two things in here look like details and are not. `GET /join/{token}` serves the page and
**never claims the seat** — messengers fetch URLs to build link previews, and a claim on
GET would hand the game to a preview bot over exactly the channels invitations travel on.
And `client_key` reads the forwarded address only when `ROYALS_TRUSTED_PROXY` is set:
behind a proxy every request otherwise appears to come from one address and the whole site
shares a single rate-limit bucket, while trusting the header unconditionally lets anyone
spoof their way out of the limit.

The page is laid out by CSS grid areas rather than flex, because the four parts of a game
screen want a different *order* on a phone than on a desktop and not merely a different
wrap. `aside.side` used to hold the status, hint, controls and move list together; stacked
into a column that put whose turn it is underneath a full-width board, off the bottom of a
phone. They are four siblings now — `#turnbar`, `.board-wrap`, `#turn-controls`, `.side` —
placed by named areas, so the turn line sits beside the board on a desktop and above it on
a phone. Every id survived the split, so `app.js` was untouched by it.

`ROYALS_MAX_DEPTH` caps the search a public deployment will agree to run, and the top of the
ladder is what makes it worth setting: `dragon` is ten ply, measured at about eighteen
seconds a move on the machine this was developed on, all of it one pinned core available to
anyone who can click a menu. `allowed_difficulties()` is used by both the menu and the
validator so what is offered and what is accepted cannot drift apart. Unset means no
ceiling, so playing at home keeps every difficulty.

`ROYALS_AI_DEADLINE` is the other half of that and is not the same knob. It bounds how long
a search that has already started may run; it defaults to 60s, which is headroom over ten
ply rather than a target. Capping the *menu* is how a deployment declines a long search —
letting one start and then killing it at the deadline spends the CPU anyway and answers 504.

There is no push. The client polls `GET /api/games/{id}?since={version}`, which compares a
counter and returns forty bytes when nothing has happened — before `to_json`, which would
otherwise regenerate every legal move on every poll. The client pauses entirely while its
tab is hidden.

Two things the pool exists to prevent: the deepest search on the menu is the better part of
half a minute of pinned CPU that anybody can request by clicking, and a game holds every
position it has stood in, so
an unattended endpoint that mints games is a memory attack that needs no cleverness at all.
Hence the hard concurrency cap, the queue that refuses rather than grows, the deadline, the
game ceiling and the TTL.

Games are kept in SQLite, and **what is stored is the move list, not the position.** The
board is derived — replay the moves and you have it, along with `ko_boards`, `turn`,
`passes` and the phase, all rebuilt by the same `place` and `play_move` that built them
the first time. Storing the board instead would mean either losing the ko history, and
the ko rule is about the whole history, or writing a second way to serialise and restore
it, which is a second way to be wrong.

That also makes a row checkable: a move list either replays to itself or it does not, so
an edited or corrupted record fails to load rather than handing both players a plausible
wrong board. It has exactly one blind spot — a prefix of a legal game is a legal game, so
truncation replays perfectly into a stale position — and the stored `ply` is there to
close it, which is the only reason that column exists.

The `random_entry` column is the second thing a move list cannot speak for, and it is worth
saying why it is a column rather than something inferred. A random opening — the mode where
nobody picks their squares — is dealt by `fill_entering`, which is called from `new_game` and
from `claim_seat` and **never from `replay`**: the twelve placements it makes go into `moves`
as ordinary entry tokens, so the load path puts them back through `place` exactly like a game
somebody clicked out. Dealing again on load would replace the game that was played with one
that merely could have been. But a game between two people is stored before either of them
has entered anything, and an empty move list looks the same either way — so whether the
pieces are still to be dealt has to be written down. Note also that the fill is driven from
`game.py` rather than from `main._advance`: `_advance` only runs while the *computer* is to
move, and in a game between two people it correctly does nothing at all.

No `pickle`, anywhere. A board is a tuple and pickling it into a BLOB is the obvious
shortcut and is remote code execution; everything written is text and numbers, and the
only thing that turns them back into a game is a function that plays moves.

`store.py` in front of it is a cache, so eviction stopped being data loss and became a
memory policy. `ROYALS_DB` names the file; unset means in-memory, which is what keeps the
test suite isolated and is why `serve.py` sets it explicitly rather than letting a
database path appear wherever someone happened to run the server from.

## Running it under PyPy

The engine's whole no-dependencies discipline exists to keep this available, and the
server runs under PyPy too — the full suite passes on both interpreters and all three goldens
come out byte-identical. What PyPy does *not* get is the accelerator: `royals_accel` is an
abi3 CPython wheel, so a PyPy run is always the pure-Python path and `test_accel.py` skips.
That is the configuration it is there to keep honest. Installing it needs three lines rather
than the usual two:

```bash
pypy3 -m pip install -e ./engine
pypy3 -m pip install fastapi uvicorn          # plain, NOT uvicorn[standard]
pypy3 -m pip install --no-deps -e ./web
```

`uvicorn[standard]` is what the `--no-deps` is dodging. It pulls `httptools` and `uvloop`,
which are C accelerations with no PyPy wheels — and no use on PyPy anyway, since its JIT
is what makes the pure-Python h11 path fast. Everything else, `pydantic-core` included,
has a PyPy wheel.

**What it is worth, measured, and the shape of it matters more than the headline.** A
depth-5 search on one position:

| | first search in a fresh worker | once the worker is warm |
|---|---|---|
| CPython 3.12 | 0.60 s | 0.58 s |
| PyPy 3.11 | 0.94 s | 0.27 s |

PyPy is **slower cold and about 2.1× faster warm**, because the JIT has to see the search
run before it can compile it. End to end through the HTTP API, over a dozen consecutive
`expert` moves, it came out at roughly **1.6×** — that mixture is the number a deployment
actually gets, not the 3× an isolated warm benchmark reports.

The operational consequence is that **the pool's workers have to be long-lived**, which
they are: `ProcessPoolExecutor` reuses them and `ai_pool` sets no `maxtasksperchild`.
Anything that recycles a worker per request would spend the warmup every time and land on
the wrong side of that table.

### The entering variety setting

Every front end shows one 0–100 slider for how varied the computer's opening is, and it drives
two things at once. `setEntryNoise` tilts the entering scores with a Perlin field, which
reorders close decisions — that is the older half, and on its own it could not reach *random*,
because a field has a strongest square and a big enough tilt converges on it. `enterVaried` is
the other half: it ranks the legal squares (the computer's own choice first, then the rest in
`entryShortlist` order) and draws rank `r` with weight `intensity ** r`. That expression is
what makes both ends exact rather than approached — `0 ** 0` is 1 with every later weight zero,
so 0 is the fixed opening; every weight is 1 at intensity 1, so that is a uniform draw over
every legal square.

`chooseEntry` itself is untouched by all of this and is still the plain "best square I can
find", which is not an accident: `regress.py`'s `enteredBoard` calls it to build the positions
`golden_moves.txt` and `golden_search.txt` sweep from, and those boards are in
`tests/fixtures/port_fixtures.json` too. Changing what it returns would move the rules contract
and the port fixtures for the sake of a menu setting.

One thing to know before trusting it further: `ai.entryScore` computes in **floats**,
through the Perlin noise field. The evaluator proper is integers-only precisely because
CPython and PyPy once disagreed in the last place and changed the move played. The
entering goldens do match across both interpreters today, so this is a latent hazard
rather than a live bug — but it is a float path in the phase PyPy helps most.

## Tests

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the workflow. In short:

- **`tests/golden_moves.txt`** — the rules contract. Thirteen thousand lines. Every legal move from a spread
  of positions, the board it produces, and what the evaluator thinks it is worth. Must stay
  byte-identical.
- **`tests/golden_search.txt`** — node counts per move. Expected to churn.
- **`tests/test_engine_purity.py`** — enforces the no-dependencies, no-UI rule.
- **`tests/test_notation.py`** — round-trips for the text and JSON move forms, and for a
  whole game record: that a header changes nothing, that a note cannot smuggle a token into
  the move list, and that the file is the same string the database column holds.
- **`tests/test_record.py`** — the record walker against the server's replay. Two walks of a
  move list exist on purpose and must agree ply for ply; this is what says so.
- **`tests/test_desktop_record.py`** — the desktop's move recorder, driven through a real
  tkinter window and then replayed two ways. Needs a display, and skips without one.
- **`tests/test_desktop_lifecycle.py`** — what a game the player walked away from can still
  do to the next one. Every other test of the window plays one game in it; these need two,
  and a running event loop, so they block the engine on an Event to make "a search is in
  flight" a fact rather than a race. Needs a display too.
- **`tests/test_desktop_appearance.py`** — the appearance screen, and chiefly that the
  miniature draws the theme being edited rather than the one in use. Needs a display.
- **`tests/test_projection.py`** — the two cameras. Mostly display-free, because a `View` is
  eleven floats and some trigonometry, so the geometry half runs on a headless runner and
  only the board-drawing half skips. It is the first test the projection has ever had.
- **`tests/test_theme.py`** — the theme schema, and the one test that earns the file: the
  colour names in `theme.DEFAULT` and the constants in `royals_gui.py` are held against each
  other in both directions. Needs no display, so it runs where the three above skip.
- **`tests/conftest.py`** — one `Tk()` for the whole session, shared by all three display
  files. Not tidiness: on macOS a *second* root's `update()` blocks inside Tk once a game
  screen has been built on it, which made the suite hang or not depending on which file
  pytest reached first.
- **`tests/test_web_api.py`** — the REST surface, against FastAPI's `TestClient`.
- **`tests/test_break_rules.py`** — what a break may fall onto, and freeing.
- **`tests/test_flights.py`** — `moveFlights` against the executors.
- **`tests/test_entering.py`** — the random opening, the mode where nobody picks their
  squares: that every square it deals is one `enteringOptions` offered, and that an opening
  is a pure function of its seed, which is what lets the desktop and the server deal the
  same one. Deliberately not a golden — a recording of what one RNG did is the kind of file
  [PORTING.md](PORTING.md) tells a second implementation not to reproduce.
- **`tests/test_accel.py`** — the optional accelerator: that boards come back hashable, that
  `ROYALS_NO_ACCEL` is honoured, that clearing game state and capping the transposition table
  both reach the compiled side, and that both engines refuse malformed input the same way.

And outside pytest, because they check the other implementation:

- **`cargo test`** in `engine-rs/` — the unit tests plus three integration targets, including
  the tables checked against dumps taken from the Python. Note that it does *not* compile the
  tests in `src/wasm.rs`, which are behind `--features wasm`.
- **`engine-rs/src/bin/royals-golden.rs`** — emits `golden_moves.txt`; must match byte for byte.
- **`engine-rs/tests/wasm_parity.py`** — asks the shipped `static/royals.wasm` what the Python
  engine is asked, and reports how many questions that came to.

The web tests import `fastapi`, so the full suite needs the server installed
(`pip install -e ./web`); the engine's own tests need nothing but the standard library,
which is the point. CI runs them in their own CPython-only job for that reason — the
goldens matrix installs the engine alone, so `importorskip` would turn the entire server
into a silent pass there. **The totals each of these should report are in
[CLAUDE.md](../CLAUDE.md)**, written down once so they cannot drift apart; check yours against
those rather than against a number quoted here.

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
