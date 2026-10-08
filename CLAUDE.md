# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

Royals: a two-player abstract strategy game (7×7 wrapping board, stacks, prisoners) plus a
dependency-free engine, three front ends (tkinter, terminal, FastAPI+browser), and a golden
regression suite.

Read [docs/RULES.md](docs/RULES.md) before reasoning about game logic and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing structure. Both are current.

## Commands

`python3`, not `python` — there is no `python` on this machine.

**Pin the import path on every command.** The pip editable installs of `royals_engine` and
`royals_web` on this machine point at a *different, diverged* checkout
(`~/Documents/RoyalsClaude`); run against that engine, `golden_search.txt` fails at line 13.
`pypy3` has no engine installed at all. So:

```bash
cd tests && PYTHONPATH=../engine/src python3 regress.py check all     # the goldens
cd tests && PYTHONPATH=../engine/src pypy3 regress.py check all       # ...under PyPy too
PYTHONPATH=engine/src:web/src python3 -m pytest tests/ -q             # 136 unit tests
python3 -m py_compile engine/src/royals_engine/*.py apps/*/*.py tuning/*.py
```

Expected green state: `golden.txt` 13,496 lines identical, `golden_search.txt` 224 lines
identical, 136 tests passing (22 engine-purity, 51 notation, 22 web API, 27 weights,
14 tuning harness).

The web API tests call `pytest.importorskip("fastapi")`, so without `web/src` on the path
they skip silently and the run reports 114 passed, not 136. **Check the count, not just the
colour.** CI doesn't install the web package either, so the web layer is uncovered there.

### Tuning the evaluator

`tuning/` (stdlib-only, run it under `pypy3`, it puts its own checkout's engine on the path):

```bash
python3 tuning/bench.py [--set NAME=value]                 # nodes/s; CPython gates at 3%
pypy3 -m tuning.match sprt --a tuning/pool/champ-002.json --b cand.json --elo0 0 --elo1 10
pypy3 -m tuning.match gauntlet --challenger cand.json      # non-regression vs the pool
pypy3 -m tuning.match tournament --players a.json b.json c.json
pypy3 -m tuning.spsa --name run1 --iterations 20000        # tune; --resume to continue
pypy3 -m tuning.scale --from results.jsonl --terms NEW_TERM # probe size for a 0-default term
gh workflow run tune-match.yml --ref <branch> -f a=... -f b=... -f depth=4 -f shards=20 -f pairs_per_shard=250
gh workflow run tune-spsa.yml  --ref <branch> -f name=run3 -f shards=20     # re-dispatches itself per wave
```

Heavy tuning runs on GitHub Actions (free, ~10x the laptop; see ARCHITECTURE "Running on
GitHub Actions"); results land on the `tuning-results` branch. Locally the harness uses
half the cores by default so the machine stays usable.

Weights are `ai.WEIGHT_NAMES` / `getWeights` / `setWeights`, integers only. **A new
evaluation term defaults to 0 and must leave `golden.txt` byte-identical until a tournament
adopts it.** Adoption changes defaults, re-records the goldens, and only `eval` lines may
move. Check it by comparing everything else, not by reading the diff -- `git diff` will show
an unchanged move line as removed-and-added when the eval lines around it change:

```bash
diff <(git show HEAD:tests/golden.txt | grep -v '    eval') <(grep -v '    eval' tests/golden.txt)   # must print nothing
```

## The rule that governs everything

`tests/golden.txt` is the rules contract — every legal move from a spread of positions, the
board each produces, and its evaluation.

> **A change that leaves `golden.txt` byte-identical is provably behaviour-preserving. A
> change that moves it needs a stated reason.**

If `golden.txt` moves and you did not intend to change the rules, **you have a bug — do not
re-record it.** `python3 regress.py write all` exists but is almost never the right answer.

`golden_search.txt` (node counts) is expected to churn; `python3 regress.py write search`
re-records just that one, and that is routine.

## Invariants that will fail the build

Enforced by `tests/test_engine_purity.py`:

1. **`royals_engine` imports stdlib only.** No third-party imports, ever.
2. **No UI or I/O in the engine.** No `tkinter`, no `input()`, no `print()`.
3. **Python ≥ 3.10, not 3.12** — PyPy compatibility is deliberate and load-bearing.
4. **Evaluator scores stay integers** (`SCALE = 1000`). Floats already caused a real bug:
   CPython and PyPy disagreed in the last place on a square root, flipping an alpha-beta
   cutoff and changing the move played. Do not reintroduce them. `ai.setWeights` refuses
   non-integers for the same reason.
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
  see `web/src/royals_web/ai_pool.py`. Two *evaluators* in one game is the same hazard: a
  table entry is a score the weights in force when it was written produced, so
  `tuning/game.py` keeps a table set per side and swaps them.
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
