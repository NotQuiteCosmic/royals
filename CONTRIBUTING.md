# Working on Royals

## Setting up

```bash
python3 -m pip install -e ./engine        # the engine, editable
python3 -m pip install pytest             # for the unit tests

python3 -m pip install -e ./web           # only if you're touching the server
```

The engine itself has **no dependencies and must keep none** — see below. `pytest` and the
web layer's FastAPI are development and server concerns, not engine ones.

`install-desktop.sh` at the repo root is for people who only want to play, not for working
on the game: it clones to `~/Royals` and runs the window from there via `PYTHONPATH`,
deliberately installing nothing. Keep using the editable installs above.

## Running things

```bash
python3 apps/desktop/royals_gui.py                 # the tkinter window
cd apps/terminal && python3 main_play.py           # the terminal game
cd web && python3 -m uvicorn royals_web.main:app --reload   # the server, at :8000
```

## Running the tests

```bash
cd tests
python3 regress.py check all      # both goldens
python3 -m pytest ../tests -q     # the unit tests
```

Everything should be green before you commit. As of the last run: **95 unit tests pass,
`golden_enter.txt` 445, `golden_moves.txt` 13,051, `golden_search.txt` 245 lines identical.**

The 95 break down as 22 engine-purity, 51 notation and 22 web API. The web API tests call
`pytest.importorskip("fastapi")`, so without `pip install -e ./web` they **skip silently**
and you'll see 73 passed rather than 95. That is by design — the engine's tests must never
need a third-party package — but it does mean a green run is not proof the server is green.
Check the count.

CI does not install the web package, so those 22 skip there too: **the web layer is not
covered by CI.** Run it locally before trusting a change to `web/`.

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
| `golden_search.txt` | Yes, routinely. | Re-record it. It counts nodes per move, so it moves whenever the tree is walked differently, often for a perfectly good reason. |

```bash
cd tests
python3 regress.py check all      # verify
python3 regress.py write search   # re-record ONLY the search baseline
python3 regress.py write all      # re-records the contract too — be sure
```

Re-recording `golden_search.txt` is normal. Re-recording `golden_moves.txt` is not, and should
never be done just to get back to green.

Both files are tracked in git deliberately, despite their size. Do not add them to
`.gitignore`.

## Rules the engine has to keep

These are enforced by `tests/test_engine_purity.py`, so breaking one fails the build
rather than surprising someone later.

**1. No third-party imports in `royals_engine`.** Standard library only. The same modules
run in a tkinter window, a terminal, a web worker and eventually a browser under Pyodide.
It is also a security property: no third-party code sits in the path that validates a move.

**2. No UI, no I/O.** No `tkinter`, no `input()`, no `print()`. A module that prints to
stdout or blocks on stdin has decided which front end it belongs to.

**3. Python ≥ 3.10, not 3.12.** PyPy tracks 3.10/3.11 and runs this search several times
faster than CPython, which is worth keeping available for the server's AI worker. Don't
reach for syntax or stdlib newer than 3.10.

**4. Scores stay integers.** Alpha-beta compares exactly, and CPython and PyPy have already
been caught disagreeing in the last decimal place on a square root — which flipped a cutoff
and changed the move played. Don't reintroduce floats into the evaluator.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#scores-are-integers).

**5. Square arithmetic lives in `notation.py`.** The move tuple mixes a 1-based origin with
a 0-based target. That asymmetry is spelled out in exactly one place on purpose.

## If you have PyPy

CI runs the goldens under CPython 3.12 and PyPy 3.10 both, on purpose — the float bug above
passed under one and failed under the other. If PyPy is installed locally, it is worth
running the contract under it before pushing anything that touches the evaluator:

```bash
cd tests && pypy3 regress.py check all
```

## Commit style

Say what moved and why. If `golden_moves.txt` changed, the commit message is the place to say
which rule changed and that it was intended.
