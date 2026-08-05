# Royals

A two-player abstract strategy game, and the engine that plays it.

Seven-by-seven, wrapping at the edges. Each side has a royal, four pawns, a spy and a
dragon. Squares are stacks, stacks take prisoners, and you win by gathering your spy,
your four pawns and your royal onto a single square. No dice, no hidden cards — both
players can see everything, and every position has a best move whether or not anyone
finds it.

![The board](board-preview.png)

**[Read the rules →](docs/RULES.md)**  ·  **[How it's built →](docs/ARCHITECTURE.md)**  ·
**[Working on it →](CONTRIBUTING.md)**

## Layout

```
engine/src/royals_engine/   the game itself — no UI, no dependencies
  hasher.py                 board representation: 49 ints, 13 bits each
  engine.py                 move generation, validation, execution, ko
  ai.py                     alpha-beta search
  notation.py               moves and boards as text and JSON — the only module
                            allowed to know how a move is packed
  perlin.py                 noise, used only to vary the computer's opening
  compat.py                 two helpers that outlived the legacy RoyalsLib
  _accel.py                 finds the compiled engine, or doesn't, and says which

apps/desktop/royals_gui.py  tkinter window, with a hand-rolled 3D board
  theme.py                  the palette as data: colours, line weights, a board gradient
  themes/                   the schemes that ship with it; your own live in ~/.royals/themes
apps/terminal/main_play.py  the same game in a terminal
  display.py                ANSI board rendering (was in Hasher)
  prompt.py                 the "which square?" prompt (was Engine.getOrigin)
  royals_lib.py             frozen Python-2-era original, kept for the terminal driver

web/src/royals_web/         FastAPI server — REST API and a browser client
  main.py                   routing and request limits: who may ask
  game.py                   game state and move validation: what is legal
  seats.py                  the two seats and the tokens that prove one is yours
  ai_pool.py                process pool, concurrency cap, per-search deadline
  persist.py                SQLite: a game is stored as its move list, not its board
  store.py                  bounded, TTL'd cache in front of persist.py
  limits.py                 the ASGI body-size cap, before a request is read
  static/                   the browser client — index.html, app.js, board.js, style.css
    engine.js               drives royals.wasm so highlighting is instant
    royals.wasm             the engine again, compiled from engine-rs/ — see below

engine-rs/                  the same rules in Rust — the optional compiled engine
  src/lib.rs                the module map, and the one move type all of it agrees on
  src/board.rs              the 13-bit square encoding and the geometry
  src/tables.rs             the rays and the unpacked squares, built at startup
  src/movegen.rs            move generation
  src/exec.rs               the executors
  src/eval.rs               integer-only evaluation
  src/search.rs             alpha-beta, the transposition table
  src/py.rs                 the PyO3 bindings, built into the royals_accel wheel
  src/wasm.rs               the browser build
  src/bin/royals-golden.rs  emits golden_moves.txt, which is how the port is proved
  tests/wasm_parity.py      asks the shipped .wasm what the Python engine is asked

tests/regress.py            the regression harness
tests/golden_moves.txt      the rules contract — see below
tests/golden_enter.txt      the entering phase, which is CPython's RNG and stays there
tests/golden_search.txt     node counts per move; expected to churn
docs/                       the rulebook, the architecture notes, the port spec, hosting
Backup/                     frozen Python-2-era originals, kept as the historical record
```

## Installing it

To just play the game on a Mac or a Linux box, there is an installer. Download it, read it —
it is short, and you should never run a script off the internet you haven't looked at — then
run it:

```bash
curl -fsSLO https://raw.githubusercontent.com/NotQuiteCosmic/royals/master/install-desktop.sh
less install-desktop.sh
sh install-desktop.sh
```

Or, if you'd rather not:

```bash
curl -fsSL https://raw.githubusercontent.com/NotQuiteCosmic/royals/master/install-desktop.sh | sh
```

It clones the game to `~/Royals`, writes a launcher next to it, and on macOS builds
`~/Applications/Royals.app` so it's double-clickable. Then it opens the window.

It **installs nothing it needs** — no virtualenv, nothing added to your system Python, nothing
outside those two paths in your home directory. It needs no sudo and refuses to run with it.
`royals_engine` imports the standard library and nothing else, so the launcher just puts
`engine/src` on `PYTHONPATH`. Pass `--dry-run` to see exactly what it would do without it
doing anything.

There is one exception, and it is best-effort: the installer makes a single attempt at
`pip install --user royals-accel`, the optional compiled engine described below. Every way that
can fail — no wheel for your machine, no network, no pip — ends in carrying on without it, and
`--no-accel` skips the attempt entirely. The game plays the same either way; it just thinks
quicker. `sh ~/Royals/uninstall.sh` removes it again along with everything else.

It won't overwrite things it didn't create: if `~/Royals` is already something else, or if
an `Royals.app` is there that this installer didn't build, it stops and says so rather than
clearing the way.

### Updating it

Run the installer again:

```bash
sh ~/Royals/install-desktop.sh --no-launch
```

It fetches, fast-forwards `~/Royals`, and rebuilds the app from it. `--no-launch` makes that a
quiet update rather than one that opens a window; drop it if you want to play straight away.

It is deliberately timid about your copy. A checkout with local edits is left exactly as it is,
and so is one whose history has diverged from GitHub — it never rebases, resets or discards, it
just says so and carries on. If it can't reach GitHub it keeps what is already there.

The one thing it does not update is the compiled engine: the `pip install` it runs has no
`--upgrade`, so an accelerator you already have stays the version you already have. To move
that one along:

```bash
python3 -m pip install --user --upgrade --only-binary :all: royals-accel
```

**If you are working on the game, rebuild the app instead.** `Royals.app` holds its own copy of
the source — a sandboxed app is only reliably allowed to read itself — so from your checkout:

```bash
sh tools/build-royals-app.sh
```

The app does try to re-copy from the checkout it was built from every time it starts, but macOS
usually refuses to let it read anything under `~/Documents` and the fallback is silent, so it
can go on running an old copy indefinitely. `~/Library/Logs/Royals.log` says which happened.
Rebuilding by hand is the reliable way.

Both commands write `~/Applications/Royals.app`, and the installer knows it. Every bundle
records the checkout it was built from, so an installer run that finds an app built somewhere
else leaves it alone and says where to rebuild it — it will not quietly swap your own build for
the public one. Pass a path if you want both at once:
`sh tools/build-royals-app.sh ~/Applications/Royals-dev.app`.

That check lives in the installer, so it protects you from the version of the installer that
has it. `~/Royals` only ever fast-forwards GitHub's `master`, so a copy older than this
paragraph will still replace your build — run `sh install-desktop.sh` from the checkout that
has this text in it, or update `~/Royals` first.

To remove it, `sh ~/Royals/uninstall.sh`, or delete `~/Royals` and `~/Applications/Royals.app`
by hand. That's all there is.

The installer covers the desktop window only. For the terminal and browser front ends, and
for working on any of it, carry on below.

## Running it

Every command block here starts at the repo root; anything that has to run elsewhere says so
in a subshell, so the line after it still begins where you did.

```bash
python3 -m pip install -e ./engine        # once

python3 apps/desktop/royals_gui.py                    # the window
(cd apps/terminal && python3 main_play.py)            # the terminal
(cd tests && python3 regress.py check all)            # the rules contract
python3 -m pytest tests/ -q                           # the unit tests
```

And the server, which needs its own install because it has dependencies the engine
deliberately doesn't:

```bash
python3 -m pip install -e ./web
python3 serve.py                    # starts it and opens your browser
```

`serve.py` is a thin wrapper around uvicorn that handles the two things which otherwise
look like the app being broken: **uvicorn never opens a browser** — it only listens and
prints a URL — and if the port is taken it exits with `[Errno 48] Address already in
use`, which reads like a crash rather than a second copy declining to start. The wrapper
opens the page for you and steps to the next free port instead of failing.

To run uvicorn directly, that is still fine — just visit the URL yourself:

```bash
cd web && python3 -m uvicorn royals_web.main:app --reload    # then open http://127.0.0.1:8000/
```

If a stale server is holding the port:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN     # find it
```

## golden_moves.txt is the contract

`tests/golden_moves.txt` records, from a spread of reachable positions, **every legal move
both sides have, the board each one produces, and what the evaluator thinks the result
is worth.** Thirteen thousand lines of it. It says nothing about how any of that is computed — only
what the rules do — which is why it survived the rewrite from the original
variable-length bit encoding to the packed integers used today.

So the rule is simple:

> **A change to the engine that leaves `golden_moves.txt` byte-identical is provably
> behaviour-preserving. A change that moves it needs a reason.**

That single property is what makes this codebase safe to refactor, and it is why the
engine was carved into an importable package without anyone having to re-read a
thousand lines of move generation and hope.

`golden_search.txt` is different and is *expected* to churn: it records node counts per
move, so it moves whenever the tree is walked differently — often for a perfectly good
reason. Re-recording it is normal. Re-recording `golden_moves.txt` is not.

```bash
(cd tests && python3 regress.py check all)      # verify
(cd tests && python3 regress.py write search)   # re-record just the search baseline
```

## Why the engine has no dependencies

`royals-engine` imports nothing but the standard library, and
`tests/test_engine_purity.py` fails the build if that stops being true — or if anything
in the engine imports tkinter, calls `input()`, or prints.

This is not tidiness. The same modules need to run in a tkinter window, in a terminal, and in
a web worker serving many games at once. A module that prints to stdout or blocks on stdin has
decided which of those it is. It is also a security property: no third-party code sits in the
path that validates a move. (The browser is the one front end these modules never reach — it
runs its own copy of the rules, compiled from the Rust to WebAssembly. What keeps that honest
is the server, which validates every move with this engine.)

The engine targets **Python ≥ 3.10**, not 3.12, so it keeps running under PyPy — a good deal
faster than CPython on this search, and the interpreter to reach for when there is no compiled
wheel. Scores are computed in integers rather than floats for a related reason: CPython and
PyPy once disagreed in the last decimal place on a square root, which flipped an alpha-beta
cutoff and changed the move played. CI runs the goldens under both, and integers are also what
lets a second and third implementation reproduce them at all.

## It is faster if you have the compiled engine, and identical if you don't

The same rules are also implemented in Rust, under `engine-rs/`. It plays exactly the same
game about **thirty-six times faster** — enough for the computer player to think three plies
deeper in the same time — and it reaches you two ways:

- **`royals-accel`**, an optional wheel. `install-desktop.sh` tries for one and shrugs if there
  isn't a build for your machine. Nothing else changes; the game just thinks quicker.
- **`static/royals.wasm`**, about 50 KB, which the browser loads so that picking a piece up is
  instant instead of a round trip to the server.

**Not having either is a supported configuration, not a degraded one.** With no wheel and no
wasm the Python engine answers, and it answers the same. That is checked rather than hoped for.
`tests/golden_moves.txt` records what every legal move does from a spread of positions; the
Python engine and the wheel each have to reproduce it byte for byte on every commit, and the
browser's copy is held to the same rules move by move, by a script that asks it what it asks
the Python engine. `ROYALS_NO_ACCEL=1` forces the Python path if you want to see for yourself.

## The server never trusts the board

The web layer rests on one rule: **the board never travels from the client to the
server.** A move request carries a kind, an origin, a destination and a prisoner flag —
four small values — and the server loads the position from its own store and
independently regenerates every legal move before accepting one.

`AI.listAllMoves` is the same generator the computer plays by, so a person and the machine
are held to literally the same rules, and "is this legal?" is a tuple membership test rather
than a second, subtly different implementation of the rulebook.

The browser does now carry a copy of the rules, and it changes nothing about that. What the
page computes locally is which squares to light up, and it is deliberately allowed to be
generous: it holds the position in front of it, not the history behind it, so it cannot apply
the rule that a move may not repeat an earlier position. The server can only ever take squares
away from that answer, and it re-derives every legal move on submission whether or not the page
asked first.

## Status

| Part | State |
|---|---|
| Engine (Python) | Working. All three goldens green under CPython and PyPy. |
| Engine (Rust) | Working, and optional. Ships as the `royals-accel` wheel and as the browser's wasm; CI holds both to the same goldens. |
| Desktop (tkinter) | Working. |
| Terminal | Working. |
| Web API + client | Working. Games are kept in SQLite as their move list, so they survive a restart. |
| Tests | Green. The counts CI expects are in [CLAUDE.md](CLAUDE.md) — one place, so they can't disagree. |
| Accounts | Not started, and may never be. A game is shared as a link and a seat is proved by a token, which is what `seats.py` does instead. |
