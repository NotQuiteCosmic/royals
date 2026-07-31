# Working on Royals

## Setting up

```bash
python3 -m pip install -e ./engine        # the engine, editable
python3 -m pip install pytest             # for the unit tests

python3 -m pip install -e ./web           # only if you're touching the server
```

The engine itself has **no dependencies and must keep none** — see below. `pytest` and the
web layer's FastAPI are development and server concerns, not engine ones. The one carve-out is
`royals_accel`, the compiled engine: `tests/test_engine_purity.py` whitelists that import and
nothing else, because it is the same rules built from `engine-rs/` rather than somebody else's
code in the path that validates a move.

If you are touching the Rust side, you need three more things — the same three CI installs:

```bash
rustup target add wasm32-unknown-unknown  # only for the browser build
python3 -m pip install maturin            # builds the royals-accel wheel
python3 -m pip install wasmtime           # runs engine-rs/tests/wasm_parity.py
```

`install-desktop.sh` at the repo root is for people who only want to play, not for working
on the game: it clones to `~/Royals` and runs the window from there via `PYTHONPATH`,
deliberately installing nothing. Keep using the editable installs above.

## Running things

Every command block below starts at the repo root. Anything that has to run somewhere else is
wrapped in a subshell, so the line after it still begins where you did — a bare `cd` at the top
of a block quietly breaks every line under it.

```bash
python3 apps/desktop/royals_gui.py                       # the tkinter window
(cd apps/terminal && python3 main_play.py)               # the terminal game
(cd web && python3 -m uvicorn royals_web.main:app --reload)   # the server, at :8000
python3 tools/replay.py <file>                           # step through a saved game
```

A saved game is a text file of moves — the desktop's SAVE button, the browser's "Save moves",
and `tools/replay.py` all read and write the same one. See "Game records" in
[CLAUDE.md](CLAUDE.md) before changing what any of them writes: a record that drops a ply
replays into a plausible wrong game rather than failing.

## Running the tests

```bash
(cd tests && python3 regress.py check all)   # all three goldens
python3 -m pytest tests/ -q                  # the unit tests
```

Everything should be green before you commit — and green is not enough on its own. **The counts
CI expects are written down in [CLAUDE.md](CLAUDE.md), in one place so they cannot disagree with
each other; check yours against those.** The reason to bother: the web API tests open with
`pytest.importorskip("fastapi")`, so without `pip install -e ./web` the whole file **skips
silently** and the run is green having tested none of the server. That is by design — the
engine's own tests must never need a third-party package — but it means the colour of a run
tells you less than its total does.

**That is still only half the check, because the engine exists twice.** The rules are
implemented in Python and again in Rust under `engine-rs/`, and both have to agree:

```bash
cargo test --manifest-path engine-rs/Cargo.toml
cargo run --release --manifest-path engine-rs/Cargo.toml --bin royals-golden \
  | diff - tests/golden_moves.txt                  # silent
ROYALS_NO_ACCEL=1 python3 -m pytest tests/ -q      # the pure-Python path
python3 engine-rs/tests/wasm_parity.py             # the browser's copy
```

If you change a rule, you are changing **both engines**, and `static/royals.wasm` is a checked-in
build artifact that must be rebuilt or the browser keeps playing the old rules while every test
stays green:

```bash
cargo build --release --features wasm --target wasm32-unknown-unknown \
  --manifest-path engine-rs/Cargo.toml
cp engine-rs/target/wasm32-unknown-unknown/release/royals_engine.wasm \
   web/src/royals_web/static/royals.wasm
```

`wasm_parity.py` is what catches a stale one, and it distinguishes "the two engines have
diverged" from "somebody forgot to rebuild". See [docs/PORTING.md](docs/PORTING.md).

---

## The one thing that matters: `golden_moves.txt`

`tests/golden_moves.txt` records, from a spread of reachable positions, **every legal move both
sides have, the board each one produces, and what the evaluator thinks the result is
worth.** It says nothing about *how* any of that is computed — only what the rules do.

That is why it survived the rewrite from the original variable-length bit encoding to the
packed integers used today, and it is the reason this codebase is safe to refactor.

> **A change to the engine that leaves `golden_moves.txt` byte-identical is provably
> behaviour-preserving. A change that moves it needs a reason.**

So:

| File | Churns? | What to do when it moves |
|---|---|---|
| `golden_moves.txt` | **No.** | Stop. Either you changed the rules on purpose — say so in the commit message — or you have a bug. |
| `golden_enter.txt` | Rarely. | Only the entering heuristic or the Perlin field can move it. It is a fingerprint of CPython's RNG as much as of the rules, which is why it is a separate file and why no port should try to reproduce it. |
| `golden_search.txt` | Yes, routinely. | Re-record it. It counts nodes per move, so it moves whenever the tree is walked differently, often for a perfectly good reason. |

```bash
(cd tests && python3 regress.py check all)      # verify
(cd tests && python3 regress.py write search)   # re-record ONLY the search baseline
(cd tests && python3 regress.py write all)      # re-records the contract too — be sure
```

Re-recording `golden_search.txt` is normal. Re-recording `golden_moves.txt` is not, and should
never be done just to get back to green.

All three are tracked in git deliberately, despite their size, as is
`tests/fixtures/port_fixtures.json` — the recorded RNG answers that let a second implementation
walk the same games without reimplementing CPython's Mersenne Twister. Do not add any of them
to `.gitignore`.

## Rules the engine has to keep

These are enforced by `tests/test_engine_purity.py`, so breaking one fails the build
rather than surprising someone later.

**1. No third-party imports in `royals_engine`.** Standard library only, with `royals_accel`
whitelisted because it is these same rules compiled. The modules run in a tkinter window, a
terminal and a web worker serving many games at once, and none of them may be assumed. It is
also a security property: no third-party code sits in the path that validates a move.

**2. No UI, no I/O.** No `tkinter`, no `input()`, no `print()`. A module that prints to
stdout or blocks on stdin has decided which front end it belongs to.

**3. Python ≥ 3.10, not 3.12.** PyPy tracks 3.10/3.11 and runs this search several times
faster than CPython, which is what the fallback has when there is no compiled wheel — a real
configuration, since the wheel is built for four platforms and CPython only. Don't reach for
syntax or stdlib newer than 3.10.

**4. Scores stay integers.** Alpha-beta compares exactly, and CPython and PyPy have already
been caught disagreeing in the last decimal place on a square root — which flipped a cutoff
and changed the move played. Don't reintroduce floats into the evaluator.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#scores-are-integers).

**5. Square arithmetic lives in `notation.py`.** The move tuple mixes a 1-based origin with
a 0-based target. That asymmetry is spelled out in exactly one place on purpose.

## If you have PyPy

CI runs the goldens under CPython 3.12 and PyPy 3.10 both, on purpose — the float bug above
passed under one and failed under the other. If PyPy is installed locally, it is worth
running the contract under it before pushing anything that touches the evaluator.

It needs its own copy of the engine — installing it into CPython does nothing for PyPy, and
the error if you skip this is a bare `ModuleNotFoundError` that looks like a broken checkout:

```bash
pypy3 -m pip install -e ./engine          # once, and separately from the CPython one
(cd tests && pypy3 regress.py check all)
```

PyPy never has the compiled wheel — `royals_accel` is abi3 CPython — so this is always the
pure-Python path. That is the point of running it: it is the configuration a machine with no
wheel is in, and the one the goldens have to come out identical from.

## Commit style

Say what moved and why. If `golden_moves.txt` changed, the commit message is the place to say
which rule changed and that it was intended.
