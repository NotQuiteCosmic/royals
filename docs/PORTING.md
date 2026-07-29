# Porting the engine to another language

This is the specification a second implementation of the Royals engine is written against.
It exists because [`tests/golden_moves.txt`](../tests/golden_moves.txt) is a
**cross-language conformance oracle** — 13,051 lines stating what every legal move does to
the board and what every resulting position is worth, in terms of parsed fields and square
numbers and never in terms of how a board is stored. Any implementation that plays the same
game produces that file byte for byte.

> **The bar: a port is correct when its emitter's output is byte-identical to
> `golden_moves.txt`. Not "equivalent", not "passes the tests". Identical.**

If you are reading this to write the Rust engine, you should need nothing else. Read
[RULES.md](RULES.md) for why the rules are what they are; read this for what to build.

---

## 1. What to port, and what not to

| Port | Don't port |
|---|---|
| `hasher.py` — board encoding, tables, win check | `perlin.py` — **see below** |
| `engine.py` — rays, move generation, executors | the entering search in `ai.py` (`chooseEntry`, `enterSearch`, `entryScore`, `enteringAnchor`) |
| `ai.py` — evaluator, move ordering, alpha-beta | `notation.py`, `moveFlights`, `describeMove`, `scoreText` |

**`perlin.py` and the entering search stay in Python, permanently.** They use floats and
seed off `random.Random(seed).shuffle` — CPython's Mersenne Twister *and* CPython's specific
shuffle algorithm. Reproducing both bit-for-bit in another language is exacting work for a
path that runs about a dozen times per game and contributes nothing to search speed. All of
the risk, none of the reward. `golden_enter.txt` is split out from `golden_moves.txt`
precisely so that this decision doesn't look like a failure.

Everything in the "Port" column is integer-only and free of any language's RNG.

---

## 2. The board

A board is **49 squares in reading order**, each packing one square into 13 bits. In Rust:
`[u16; 49]`, `Copy`, 98 bytes — no allocation, no reference counting.

| Bits | Field |
|---|---|
| 0 | occupied |
| 1 | controlling side (0 blue, 1 red) |
| 2 | dragon |
| 3 | spy |
| 4–6 | pawns, 0–4 |
| 7 | royal |
| 8 | any prisoners |
| 9 | captured spy |
| 10–12 | captured pawns, 0–4 |

**One arrangement of pieces has exactly one encoding.** `Build_Space` zeroes everything past
a dragon, and everything past the prisoner flag when there are no prisoners. This is
load-bearing, not tidiness: it is what lets a board stand as its own key in the ko set and
the transposition table. A port that leaves a stale bit set in an unreachable field produces
a board that compares unequal to the identical position reached another way, and the
transposition table silently stops hitting.

`Build_Space` also **raises** on an overfull square (`spy > 1`, `pawns > 4`, `royal > 1`,
`capSpy > 1`, `capPawns > 4`). Keep that. Shifting an oversized value would bleed it into the
next field and the board would go wrong somewhere else entirely, far from the cause.

### Derived scalars

A parsed square carries eight fields followed by six derived scalars — `occupied`, `weight`,
`strength`, `captors`, `prisCount`, `myPieces`. These are not new facts; each is something
the eight fields already say. They exist because move generation asks for them millions of
times per search and a table lookup beats a re-derivation.

Precompute all 8,192 codes at startup (`UNPACK`). Do not ship it as data — build it, then
check it. `fixtures/port_fixtures.json` carries `unpack_sha256`, the SHA-256 of
`",".join(",".join(str(v) for v in row) for row in UNPACK)`. Match that digest before
writing another line of code; almost every encoding mistake shows up here, cheaply.

Three weight rules, all of which treat a dragon as 3:

- **weight** — everything on the square, prisoners included
- **strength** — carried prisoners count *against* the stack: `spy + pawns + royal − capSpy − capPawns`
- **captors** — just the pieces standing, ignoring anyone they hold

`myPieces` counts a dragon as **one** piece, not three. It is a head count, and it is what
the evaluator means by the size of a stack.

---

## 3. The move tuple

```
(origin, kind, target, movingPris)
```

| Field | Meaning |
|---|---|
| `origin` | **1-based** square it leaves from |
| `kind` | `"jump"`, `"push"`, `"free"` or `"break"` |
| `target` | **0-based** square, for jump/push/free — **a direction index into `pushDirs`, for break** |
| `movingPris` | whether the prisoners on the origin come along |

**The 1-based origin against the 0-based target is the single most dangerous thing in this
codebase.** `performOneStep` does `target + 1` for the first three kinds and passes `target`
through untouched for a break. Every off-by-one this engine can produce lives in that
asymmetry. Keep the asymmetry rather than "fixing" it — the goldens encode it, and a port
that normalizes to one base will produce a file that differs everywhere at once.

Square arithmetic belongs in one module. No other module does arithmetic on a square.

`IndexToAlg(index)` = `"abcdefg"[index % 7] + str(index / 7 + 1)`, with `index` 0-based.

---

## 4. Integer-only, and exactly which integers

**Scores are whole numbers of thousandths of a point. `SCALE = 1000`. No float enters the
search or the evaluator, ever.**

This is not stylistic. Scores were floats once, and alpha-beta compares its numbers exactly
(`beta <= alpha`, `score > bestScore`), so a difference in the last place — which is all it
takes for two interpreters to disagree about a square root — flips a cutoff and changes the
move played. Measured over 3,064 positions, CPython and PyPy differed on 400 of them by up
to 1.5e-14 relative: never enough to show at four decimal places, occasionally enough to
pick a different move. The suite passed under one interpreter and failed under the other on
exactly that.

Concretely:

- **`math.isqrt` is an exact integer square root** (floor). Rust: `u64::isqrt`. Verify the
  truncation agrees on a fixture sweep rather than assuming.
- **`spreadPenalty`** computes `isqrt(9 * SCALE * SCALE * q) / (2 * n)`, where
  `q = max(n*sx2 − sx*sx, n*sy2 − sy*sy)`. Both operands are non-negative here, so Rust's
  `/` matches Python's `//`. Assert that; don't assume it.
- **`checkBreak`'s `(i / 7) + 1`** is integer division — it was a float once, after the
  Python-2 port, and that was a real bug.
- **`INFINITY`** is `WIN_SCORE * 1000`, a plain integer, not a floating infinity. The search
  opens its window with it so that no float enters even at the root.

The one exception: `chooseEntry` uses `math.inf` and float weights — and it is not being
ported. See §1.

---

## 5. Move generation order

**The order moves are generated is part of the contract.** `golden_moves.txt` records moves
sorted by `moveText`, so generation order does not show there directly — but the fixture
walks pick moves *by index* into `listAllMoves`' output, so a different order sends the walk
down a different game on the very next line.

Reproduce exactly:

1. **`makePossList`** walks squares **1 through 49 ascending**. A square the other side holds
   is included only if it holds our captured spy — that spy can break out, and that is the
   only move it has.
2. **`listAllMoves`**, per origin, tries `movingPris` in the order **`false` then `true`**,
   and only appends `true` when the square actually holds prisoners.
3. **`listMoves`** appends, in this order: **all jumps, all pushes, all breaks, all frees.**
   - breaks are appended **only when `movingPris` is false** — a break scatters the whole
     square, so there is no carrying-prisoners variant
   - frees come from `moveArray[5]`, not `[3]` or `[4]`

A square can appear in both `possPushes` and `possFrees`. That is deliberate: a push into
allies being held is two different moves onto one square — shove the whole thing along, or
free them and stand where they stood. They cost differently, so they are measured separately.

### `checkMoves` returns a six-list

Indices `[possJumps, possPushes, possBreaks, possMoves, alphBreaks, possFrees]`. The AI reads
**0, 1, 2 and 5**. Internals may be a struct; preserve the shape at the boundary.

It returns an **empty list** — not a six-list of empties — when there are no moves at all.

---

## 6. Executors: the three that bite

### `exePush` — build all payloads, then commit ascending

The shuffle is two passes on purpose. Grab every square the push needs *before* touching
anything, compute every square's new contents, then write them in **ascending offset order**.

The reason is wrapping: a push long enough to lap the board has two offsets landing on one
square, and **the later one must win**. Port the algorithm, not a cleaner-looking rewrite.

A lone spy's shove shatters what it hits — `getLegalPushLength` only ever grants a lone spy a
range of 1, so the displaced stack is two squares out and scatters onward from there.

### `checkBreak` — measure against the falling piece, not the breaker

Prisoners drop first. So the early steps of a scatter are dropping the *other* side's people
and the later steps our own:

```
if i < prisoners:  dropping = 1 - control
else:              dropping = control
```

Getting this wrong the obvious way — measuring against the breaker — lets a freed captive
land on a stack of its captor's and take the whole thing prisoner on the way past.

Two further rules, and they are not the same rule twice:

- an enemy square blocks if its **weight** exceeds `(i / 7) + 1` (the tolerance grows by one
  each full lap, because by then the walk has dropped a piece the board it is reading
  doesn't show)
- an enemy square blocks if it holds **any prisoners**, whatever it weighs

A lone jailer weighs 2, so weight stops it on the first lap — but by the second the tolerance
is 2 as well and it would slip straight through. The prisoner test is what keeps a break from
ever freeing anybody.

Every 7th step lands back on the origin, which is being emptied anyway, so it has nothing to
block against. A range below 2 is no move at all.

### `exeMove` — the `spyBreak` gap

Moving your own spy out of an enemy square that holds it prisoner **has no handling**, on
either side of the port. Port the gap as-is. Do not fix it here: `golden_moves.txt` would
move and you would not know which change did it.

---

## 7. Search

### Transposition table

Keyed on **`(board, sideToMove, stamp)`** — the board itself, a tuple of 49 codes, **not a
hash of it**. There are no collisions to guard against, which is what makes the staged
candidate optimization safe (below). If you switch to Zobrist for speed you **must** add move
legality verification before playing a stored move.

- Root entries are stamped with `koGeneration`; everything below the root with **`-1`**.
  Root answers have ko-breaking moves struck off them and go stale when a move is played;
  deeper nodes never consult the ko history at all and stay good for the rest of the game.
- Entries are filed **from the side-to-move's point of view** and negated on probe. The bound
  flags swap with the sign: `LOWER` ↔ `UPPER`. Get this wrong and the search still works and
  plays subtly worse — the worst possible failure mode.
- Two generations (`table` / `tableOld`). When the live one fills it is retired wholesale and
  a fresh one starts; lookups try both, and an entry found in the old one is written forward.
- Win scores are **not filed** — they are scaled by depth remaining, so the same win filed at
  one depth reads as a different score at another.

### Staged candidates

Most interior nodes cut off (85%, measured), and 78% of those cut off on the first move
tried. Where there is a stored move, that first move *is* the stored move. So generating the
other forty, scoring them and sorting them is work done to be thrown away.

Try the stored move before generating anything; what follows is the same list in the same
order with that one move lifted out. Same nodes, same cutoffs, same answer.

### `orderMoves` — stable sort, on the score alone

Sort **stably**, on the pair's first element only. Rust's `sort_by_key` is stable and matches
Python's `list.sort`. Two rules:

- **Never sort on the move itself.** A move is a tuple; sorting the pairs whole would fall
  through to comparing moves and quietly reorder the ties.
- Equal-scoring moves must keep board order, which is what makes the search repeatable.

`history` is halved each move and entries that decay to zero are dropped. `killers` start
each move empty — a killer is a move that cut off at a given depth with no record of where,
which is worth keeping across the deepening passes of one search and misleading across a move.

### Iterative deepening

Depth 1, then 2, and so on, handing each pass's best move to the next to try first. The
window opens at `±INFINITY`. Node counts land in `golden_search.txt`.

---

## 8. The emitter

Build a binary that reproduces the MOVES sweep. Formats, exactly:

**Board** — for each of 49 squares, skip if all eight semantic fields are zero, else
`IndexToAlg(i) + ":" + fields joined by ","`. Squares joined by `"|"`. Empty board is
`-empty-`. **Only the eight semantic fields** — side, dragon, spy, pawns, royal, capSpy,
capPawns, prisFlag. The derived scalars are answers those fields already contain; printing
them would put the same fact in the file twice and make an optimization look like a change.

**Move** — `None` is `none`. Otherwise `IndexToAlg(origin - 1) + " " + what`, plus
`" +pris"` when carrying, where `what` is:

- break: `"break " + str(tuple(pushDirs[target]))` — Python tuple formatting, e.g.
  `break (0, 1)`, with the space after the comma
- otherwise: `kind + " " + IndexToAlg(target)`

**Sweep structure** — `### MOVES`, then for each seed in `(3, 11, 57)`, for turn 0..39, for
side in `(0, 1)`:

```
s%d t%d side %d moves %d
  <moveText> => <boardText>
    eval %d %d
```

with moves **sorted by `moveText`**, then after both sides:

```
  winner %s %s
```

— Python's `str()` of a bool and of a two-element list, so `winner False [0, 0]`. Break out
of the turn loop if the game ended.

`eval` is `fullCheck(child, 0)` then `fullCheck(child, 1)`, printed as exact integers. A
rounded print is what let a cross-interpreter difference hide here in the first place.

### Fixtures

`tests/fixtures/port_fixtures.json`, regenerated by `python3 regress.py fixtures`:

| Key | For |
|---|---|
| `unpack_sha256`, `unpack_rows`, `unpack_width` | check your `UNPACK` before anything else |
| `jumpray`, `pushray`, `breakray`, `pushfrom` | ray geometry, indexed from square 1 |
| `jumpreach`, `jumpdist` | evaluator tables. `jumpreach` is **pure geometry** — the count of non-empty jump strands from a square — so it needs nothing from move generation |
| `entering_board` | the board entering starts from |
| `walks` | per seed: `start` (the entered board), `choices` (move indices to play), `final` |

The walks are what make the emitter possible without reproducing CPython's RNG. Start from
`start`, and at each turn play `listAllMoves(board, turn % 2)[choices[turn]]`. If your move
generation order differs at all, the index selects a different move and the walk diverges
immediately — which is the point.

`regress.py fixtures` re-checks `golden_moves.txt` after writing and refuses if it moved, so
a fixture dump can never certify a walk the recorded file doesn't take.

---

## 9. The accelerator contract

The compiled engine is **optional**. `royals_engine` keeps its exact Python API and falls
back to the pure-Python modules when no extension is present — that is what keeps
`install-desktop.sh`'s "no pip required" promise true, keeps `test_engine_purity.py`
meaningful, and keeps the PyPy cross-check alive.

**`ROYALS_NO_ACCEL=1` forces the pure-Python path** even when an extension is installed.
Everything that dispatches must honour it. CI runs the whole suite both ways and requires
identical goldens from both; that differential check is the reason two implementations are
worth their cost.

The search crosses the FFI boundary **once per move, not once per node**, so marshalling
cost is irrelevant where it matters. A board is 49 small integers.

---

## 10. Checklist

```bash
cd tests && python3 regress.py check all        # 445 / 13,051 / 245 lines
python3 -m pytest tests/ -q                     # must report 165, not 101
python3 regress.py fixtures                     # regenerate + self-check
```

- [ ] `UNPACK` digest matches `unpack_sha256`
- [ ] Ray tables match the fixture element for element
- [ ] Emitter output is byte-identical to `golden_moves.txt`
- [ ] Integer-only throughout; `isqrt` truncation verified against Python
- [ ] `orderMoves` sorts stably on the score alone
- [ ] Generation order matches §5 exactly
- [ ] TT sign and bound-flag negation verified both directions
- [ ] `spyBreak` gap ported as-is, not fixed
- [ ] `ROYALS_NO_ACCEL=1` produces identical goldens

**If `golden_moves.txt` moves and you did not intend to change the rules, the port has a
bug. Do not re-record it.**
