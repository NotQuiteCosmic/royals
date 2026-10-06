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
PYTHONPATH=../engine/src python3 regress.py check all             # both goldens
PYTHONPATH=../engine/src:../web/src python3 -m pytest ../tests -q # the unit tests
```

**Pin the path.** If an editable install of `royals_engine` from *another* checkout is on
your machine, `import royals_engine` finds that one, and the goldens test code you are not
looking at. `PYTHONPATH=engine/src` (and `web/src` for the web tests) makes the checkout
you are in the one that runs. `tuning/` does this for itself.

Everything should be green before you commit. As of the last run: **136 unit tests pass,
`golden.txt` 13,496 lines identical, `golden_search.txt` 245 lines identical.**

The 136 break down as 22 engine-purity, 51 notation, 22 web API, 27 evaluator weights and
14 tuning harness. The web API tests call `pytest.importorskip("fastapi")`, so without the
web package on the path they **skip silently** and you'll see 114 passed rather than 136.
That is by design — the engine's tests must never need a third-party package — but it does
mean a green run is not proof the server is green. Check the count.

CI does not install the web package, so those 22 skip there too: **the web layer is not
covered by CI.** Run it locally before trusting a change to `web/`.

---

## The one thing that matters: `golden.txt`

`tests/golden.txt` records, from a spread of reachable positions, **every legal move both
sides have, the board each one produces, and what the evaluator thinks the result is
worth.** It says nothing about *how* any of that is computed — only what the rules do.

That is why it survived the rewrite from the original variable-length bit encoding to the
packed integers used today, and it is the reason this codebase is safe to refactor.

> **A change to the engine that leaves `golden.txt` byte-identical is provably
> behaviour-preserving. A change that moves it needs a reason.**

So:

| File | Churns? | What to do when it moves |
|---|---|---|
| `golden.txt` | **No.** | Stop. Either you changed the rules on purpose — say so in the commit message — or you have a bug. |
| `golden_search.txt` | Yes, routinely. | Re-record it. It counts nodes per move, so it moves whenever the tree is walked differently, often for a perfectly good reason. |

```bash
cd tests
python3 regress.py check all      # verify
python3 regress.py write search   # re-record ONLY the search baseline
python3 regress.py write all      # re-records the contract too — be sure
```

Re-recording `golden_search.txt` is normal. Re-recording `golden.txt` is not, and should
never be done just to get back to green.

### The one exception: changing evaluation weights

`golden.txt` records what the evaluator thinks each position is worth, so a deliberate
change to the evaluator's weights moves its `eval` lines and nothing else. That is the one
legitimate way the file moves without a rules change, and it comes with conditions:

1. The change was decided by games, not by taste: a candidate that passed the gain SPRT and
   the gauntlet in `tuning/` (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#tuning-the-evaluator)),
   with the Elo figures written into the comment above the weight in `ai.py`.
2. Only `eval` lines differ. Check it by comparing everything else, not by reading the
   diff -- `git diff` shows an unchanged move line as removed-and-added when the eval lines
   around it change:
   ```bash
   diff <(git show HEAD:tests/golden.txt | grep -v '    eval') <(grep -v '    eval' tests/golden.txt)   # must print nothing
   ```
3. The commit message names every weight that changed, old and new, and says the rules
   are unchanged.

A **new** evaluation term is added at a default of 0 and must leave `golden.txt`
byte-identical; it earns a non-zero default the same way.

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
cd tests && PYTHONPATH=../engine/src pypy3 regress.py check all
```

PyPy is also where tuning runs belong: `pypy3 -m tuning.match …` and `pypy3 -m tuning.spsa …`
play roughly twice as many games per hour as CPython once the JIT is warm.

## Commit style

Say what moved and why. If `golden.txt` changed, the commit message is the place to say
which rule changed and that it was intended.
