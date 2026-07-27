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

## The web layer

FastAPI, in `web/src/royals_web/`:

| File | What it does |
|---|---|
| `main.py` | HTTP routing, authorization, request limits. Decides *who may ask*. |
| `game.py` | Game state and move validation. Decides *what is legal*. |
| `seats.py` | Seat tokens: mint, hash, constant-time compare. |
| `ai_pool.py` | Process pool, concurrency cap, wall-clock deadline per search. |
| `store.py` | Bounded in-memory cache in front of the database; per-client rate limits. |
| `persist.py` | SQLite. One row per game, holding the move list rather than the board. |
| `static/` | The browser client — `index.html`, `app.js`, `board.js`, `style.css`. |

The design rests on two rules. The first:

> **The board never travels from the client to the server.**

A move request carries a kind, an origin, a destination and a prisoner flag — four small
values. The server loads the position from its own store and independently regenerates
every legal move before accepting one. `AI.listAllMoves` is the same generator the computer
plays by, so a person and the machine are held to literally the same rules, and "is this
legal?" is a tuple membership test rather than a second, subtly different implementation of
the rulebook.

The second, which arrived with two players:

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

There is no push. The client polls `GET /api/games/{id}?since={version}`, which compares a
counter and returns forty bytes when nothing has happened — before `to_json`, which would
otherwise regenerate every legal move on every poll. The client pauses entirely while its
tab is hidden.

Two things the pool exists to prevent: a depth-6 search is seconds of pinned CPU that
anybody can request by clicking a menu, and a game holds every position it has stood in, so
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

No `pickle`, anywhere. A board is a tuple and pickling it into a BLOB is the obvious
shortcut and is remote code execution; everything written is text and numbers, and the
only thing that turns them back into a game is a function that plays moves.

`store.py` in front of it is a cache, so eviction stopped being data loss and became a
memory policy. `ROYALS_DB` names the file; unset means in-memory, which is what keeps the
test suite isolated and is why `serve.py` and the Dockerfile set it explicitly rather than
letting a database path appear wherever someone happened to run the server from.

## Tests

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the workflow. In short:

- **`tests/golden.txt`** — the rules contract. 13,496 lines. Every legal move from a spread
  of positions, the board it produces, and what the evaluator thinks it is worth. Must stay
  byte-identical.
- **`tests/golden_search.txt`** — node counts per move. Expected to churn.
- **`tests/test_engine_purity.py`** — enforces the no-dependencies, no-UI rule. (22)
- **`tests/test_notation.py`** — round-trips for the text and JSON move forms. (51)
- **`tests/test_web_api.py`** — the REST surface, against FastAPI's `TestClient`. (46)

119 in total. The web tests import `fastapi`, so the full suite needs the server installed
(`pip install -e ./web`); the engine's own tests need nothing but the standard library,
which is the point. CI runs them in their own CPython-only job for that reason — the
goldens matrix installs the engine alone, so `importorskip` would turn the entire server
into a silent pass there.

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
