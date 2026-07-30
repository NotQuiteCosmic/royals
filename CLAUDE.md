# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

Royals: a two-player abstract strategy game (7×7 wrapping board, stacks, prisoners) plus a
dependency-free engine, three front ends (tkinter, terminal, FastAPI+browser), and a golden
regression suite.

**The engine is implemented twice.** `engine/src/royals_engine/` (Python, stdlib only) is the
reference implementation and the fallback; `engine-rs/` is a Rust port, about thirty-six times
faster, that ships as an optional wheel (`royals_accel`) and as a wasm module the browser loads.
Neither replaces the Python — `tests/golden_moves.txt` is a contract both answer to on every
commit, which is the only reason two implementations are worth having.

Read [docs/RULES.md](docs/RULES.md) before reasoning about game logic,
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing structure, and
[docs/PORTING.md](docs/PORTING.md) before touching either engine's rules. All three are
current.

## Commands

`python3`, not `python` — there is no `python` on this machine.

**Every block in this file starts at the repo root.** Where a command has to run somewhere else
it is wrapped in a subshell, so the next line still begins where the last one did — a bare `cd`
at the top of a block silently breaks every line under it.

```bash
(cd tests && python3 regress.py check all)    # the goldens
python3 -m pytest tests/ -q                   # the unit tests
python3 -m py_compile engine/src/royals_engine/*.py apps/*/*.py    # quick syntax check
```

**The engine exists twice, so those three commands are not the whole check.** The full sweep,
which is what CI runs:

```bash
cargo test --manifest-path engine-rs/Cargo.toml
cargo run --release --manifest-path engine-rs/Cargo.toml --bin royals-golden \
  | diff - tests/golden_moves.txt                 # must be silent
ROYALS_NO_ACCEL=1 python3 -m pytest tests/ -q     # the pure-Python path
(cd tests && ROYALS_NO_ACCEL=1 python3 regress.py check all)   # must match the compiled run
python3 engine-rs/tests/wasm_parity.py            # the browser's copy
```

Expected green state: `golden_enter.txt` 445 lines, `golden_moves.txt` 13,051 lines,
`golden_search.txt` 245 lines identical — **from both implementations** — and 195 tests passing
(23 accel, 24 break rules, 25 engine-purity, 4 flights, 51 notation, 68 web API). With
`ROYALS_NO_ACCEL=1` it is 192 passed and 3 skipped; the skips are the tests that need the
wheel, and skipping is correct — not having it is a supported configuration.

`cargo test` is 33, and `royals-golden` must emit `golden_moves.txt` byte for byte. A
Python-only run proves almost nothing about what a browser or a wheel-equipped machine will do.
Note what that 33 leaves out: the seven tests in `engine-rs/src/wasm.rs` are behind
`--features wasm` and a bare `cargo test` never compiles them, including the one asserting the
move-kind order `static/engine.js` decodes with. `wasm_parity.py` is what actually guards the
browser.

This file is the one place those figures are written down. Everything else points here, because
a count quoted in five files is a count that will be right in one of them — see the end of this
file for how to re-measure them.

All three goldens and every test also pass under PyPy, and that is worth keeping true — see
"Running it under PyPy" in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The trap there is
sqlite3: CPython finalises a cursor by refcount, PyPy does not, so an `execute` whose
cursor is left open makes the next `commit` fail. Everything in `persist.py` goes through
`_run`/`_query`, which close theirs. Don't add a bare `self._conn.execute`.

The web API tests call `pytest.importorskip("fastapi")`, so without `pip install -e ./web`
they skip silently and the run reports 127 passed, not 195. **Check the count, not just
the colour.** CI runs them in a separate CPython-only job that installs the web package,
because the goldens matrix installs the engine alone. Both packages are already installed
editable in this environment.

## The rule that governs everything

`tests/golden_moves.txt` is the rules contract — every legal move from a spread of positions, the
board each produces, and its evaluation.

> **A change that leaves `golden_moves.txt` byte-identical is provably behaviour-preserving. A
> change that moves it needs a stated reason.**

If `golden_moves.txt` moves and you did not intend to change the rules, **you have a bug — do not
re-record it.** `python3 regress.py write all` exists but is almost never the right answer.

**It now binds three things, not one** — though not all three the same way. The Python engine
and the compiled wheel each *emit* the file and must match it byte for byte. The browser's wasm
is held to the same rules **move by move** instead: `engine-rs/tests/wasm_parity.py` asks the
shipped module what it asks the Python engine and requires the same answers. That is deliberate
— diffing the artifact against a fresh build would fail the day the runner's rustc moves, for a
module that plays exactly the same game, and a check nobody can act on is a check that gets
switched off. `ci.yml`'s `browser:` job says so at length.

So a rules change is a change to `engine/src/royals_engine/` *and* to `engine-rs/`, and the
browser artifact has to be rebuilt — and only then is re-recording even a question. If the two
engines disagree, exactly one of them is wrong and the goldens will not say which;
`docs/PORTING.md` is the spec that settles it.

Re-recording to make a red build green is the single most expensive mistake available here. It
converts the one property that makes two implementations worth maintaining into a file that
merely describes whatever the code currently does.

`golden_search.txt` (node counts) is expected to churn; `python3 regress.py write search`
re-records just that one, and that is routine.

`golden_enter.txt` is the third baseline and the odd one out. It records the entering phase,
which runs on Perlin noise over floats seeded through `random.Random(seed).shuffle` — so it is
a fingerprint of CPython's Mersenne Twister as much as of the entering heuristic. That is why
it is a separate file from `golden_moves.txt`: the moves half is a portable statement about the
rules that a second implementation of the engine must reproduce byte for byte, and the entering
half is one interpreter's RNG, which no port should try to reproduce. They shared a file until
the Rust port made the distinction matter. `regress.py check base` still means both.

See [docs/PORTING.md](docs/PORTING.md) before writing any second implementation of the engine.

## Invariants that will fail the build

Enforced by `tests/test_engine_purity.py`:

1. **`royals_engine` imports stdlib only.** No third-party imports, ever.
2. **No UI or I/O in the engine.** No `tkinter`, no `input()`, no `print()`.
3. **Python ≥ 3.10, not 3.12** — PyPy compatibility is deliberate and load-bearing.
4. **Evaluator scores stay integers** (`SCALE = 1000`). Floats already caused a real bug:
   CPython and PyPy disagreed in the last place on a square root, flipping an alpha-beta
   cutoff and changing the move played. Do not reintroduce them.
5. **Square arithmetic belongs to `notation.py`.** The move tuple is
   `(origin, kind, target, movingPris)` with a **1-based origin and a 0-based target** —
   except for breaks, where `target` is a direction index. No other module does arithmetic
   on a square.

## Things that look like bugs but aren't

- **CamelCase function names** (`Build_Space`, `Check_For_Winner`, `checkMoves`). Kept from
  the Python-2 original so call sites read unchanged across the port. Don't rename them.
- **Module-level globals** — `Engine.koTrack`, `Engine.koGeneration`, and the AI's
  transposition/killer/history tables. Fine for one game per process; a correctness hazard
  for two. Anything serving multiple games must load one game's state, run, and drop it —
  see `web/src/royals_web/ai_pool.py`.
- **Boards are tuples, not lists.** Hashable and immutable on purpose: that is what lets a
  board be its own key in the ko set and the transposition table, and it's why every
  executor returns a new board instead of mutating.
- **`Backup/`** is frozen Python-2-era source, not imported by anything. Engine comments
  refer to "the backup" when explaining why a rule is as it is. Leave it alone.
- **`apps/terminal/royals_lib.py`** is legacy but still live — the terminal driver imports it.

## Style

The codebase comments *why*, at length, especially where something is counter-intuitive or
was arrived at the hard way. Match that. A comment explaining a performance decision or a
rule's edge case is in keeping here; a comment restating what the line does is not.

Numbers in prose follow one rule: **exact where the reader is meant to check it, rounded into
words where it is only conveying scale.** "195 tests, and 127 means you forgot the web package"
is a check and has to be exact. "thirteen thousand lines", "about thirty-six times faster" are
rhetoric, and a rounded word is still true two commits later where a digit is not. Every count
in this file is the first kind, which is why they live here and nowhere else.

## Re-measuring this file

Every figure above comes from something that already runs. From the repo root:

```bash
python3 -m pytest tests/ -q                      # the total, and 195 vs 127 above
ROYALS_NO_ACCEL=1 python3 -m pytest tests/ -q    # the 192 passed / 3 skipped split
(cd tests && python3 regress.py check all)       # the three golden line counts
cargo test --manifest-path engine-rs/Cargo.toml  # the 33
python3 engine-rs/tests/wasm_parity.py           # prints its own question count
```

For the per-file breakdown, `python3 -m pytest tests/<file> --collect-only -q` — counting
`def test_` undercounts badly, because several files parametrise (`test_accel.py` reads as 11
and collects 23).
