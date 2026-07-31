# The rules of Royals

Two players, blue and red. No dice, no hidden information — both sides see everything,
and every position has a best move whether or not anyone finds it.

This document is the human-readable rulebook. The machine-readable one is
[tests/golden_moves.txt](../tests/golden_moves.txt), which records every legal move from a spread of
reachable positions and the board each one produces. Where this page and `golden_moves.txt`
disagree, `golden_moves.txt` is right and this page is a bug.

---

## The board

Seven by seven, forty-nine squares, lettered `a`–`g` across and numbered `1`–`7` down.
Square `d4` is the centre.

**The board wraps.** Walk off the right edge and you arrive at the left; walk off the
bottom and you arrive at the top. It is a torus, not a plane. There are no corners and no
edge squares — every square has the same eight neighbours as every other.

The one exception is the entering phase, which does *not* wrap. See below.

A square is not a slot for one piece. **A square is a stack**, and it can hold up to six
of your pieces at once, plus any enemy pieces they are holding prisoner.

## The pieces

Each side has seven pieces:

| Piece | How many | Weight | Notes |
|---|---|---|---|
| Royal | 1 | 1 | Nothing may jump onto it. |
| Pawn | 4 | 1 each | The bulk of the army. |
| Spy | 1 | 1 | Slips *into* squares rather than onto them. |
| Dragon | 1 | 3 | Flies. Stands alone, never stacks. |

**Weight** is what makes a stack move further, and it is counted three different ways
depending on the question being asked:

- **captors** — the pieces standing on the square, ignoring any prisoners they hold.
- **weight** — everything on the square, prisoners included.
- **strength** — the standing pieces *minus* the prisoners they are carrying. Dragging
  captives slows you down.

A dragon is worth 3 by all three counts.

## Setting up: the entering phase

The two dragons start on the board: **blue's on `d3`, red's on `d5`.** Nothing else is
placed yet.

The remaining pieces then enter one at a time, players alternating, in this order:

1. Both royals
2. Both first pawns, both second pawns, both third pawns, both fourth pawns
3. Both spies

**Blue enters first at every stage**, which is why red takes the first move once the
board is full.

Where you may enter a piece:

- A **royal or a pawn** may not be entered on a square touching anything you already
  control — your own dragon included. Squares the *enemy* controls are no obstacle; only
  your own pieces crowd you out.
- A **spy** ignores that rule entirely and needs only an empty square.

Entering does not wrap around the edges. A square on the `a` file is not adjacent to one
on the `g` file for the purposes of placement, even though it is for every move that
follows.

*Not a rule, but worth knowing where to find it:* both front ends offer an opening where
nobody chooses. Pick it in the menu and all twelve placements are made for you, each on a
square drawn from the ones the rules above allow — the same list you would have been
clicking from. Everything on this page still holds; the only thing that changes is who
picks. The game then opens with the board already full, red to move as always.

## Winning

> **You win by gathering your spy, all four of your pawns and your royal onto a single
> square.**

Six pieces, one square. The dragon is not part of it and cannot help you carry it — a
dragon's square holds nothing but the dragon.

The assembly order is forced by the movement rules and it is worth understanding early: a
spy can never jump *onto* anything, and nothing can ever jump *onto* a royal. So the spy
cannot come last and the royal cannot come first. **The pawns gather onto the spy, and
the royal arrives last.**

## Moving

On your turn you move one square's worth of pieces. There are four kinds of move, plus
passing.

### Jump

A stack travels **diagonally**, up to as many squares as it weighs, wrapping at the edges.
A one-piece stack moves one square; a full six-piece stack moves six.

Because jumps run along diagonals for exactly as many squares as the stack weighs, **a
jump never changes the colour of the square you are on.** Half the board is permanently
out of reach of any given stack until its weight changes.

What you may land on:

- **Your own pieces** — legal whatever is standing there and whatever it weighs, unless you
  are carrying prisoners. The two stacks merge. This is how you assemble a winning stack.
- **An empty square** — always legal.
- **An enemy square** — legal only if it weighs no more than you do. Their spy and pawns
  become **your prisoners**.

And the exceptions, which is where most of the game's character lives:

- A **spy** may not land on anything at all. Any occupied square stops it.
- Nothing may land on a square holding a **royal** or a **dragon**.
- Nothing may land on a square holding **your own pieces as prisoners** — you cannot jump
  onto your own jailers. Push them free instead.
- A **dragon** flies: occupied squares do not block its path, it simply cannot end on one.
- If you are **carrying prisoners**, *any* occupied square stops you, either side's — you
  may not land on it and you may not pass over it. Your own pieces are no exception, and
  this is the one place where they are not. Leave the prisoners behind and the square is
  open again.

### Push

A stack shoves the **orthogonally** adjacent square — up, down, left or right — and
everything in a line behind it moves along. How far the line travels depends on your
strength against what is in the way.

A lone spy is a special case: its shove **shatters** what it hits. The spy has one point
of strength, spends it on the adjacent square, and the stack it displaced scatters from
where it lands. This is the same instinct as the spy's jump rule — it does not join
squares, it disrupts them.

### Free

A push into an enemy square that is holding **your** pieces prisoner is offered as two
separate moves, because they are genuinely different and cost differently:

- **Push** — shove the whole square along, prisoners still in it.
- **Free** — step onto the square, your captives stand back up and join your stack, and
  the jailers get shoved on alone.

Not everything may free. Freeing means standing on the square with the pieces you just
released, so anything that cannot legally land on top of other pieces cannot free either:

- A **spy** cannot free.
- A **dragon** cannot free — its square has no room for company.
- A stack **carrying its own prisoners** cannot free, royal or no royal.

### Break

A stack **scatters**, dropping one piece per square along a direction, starting on the
square it is standing on. It drops from the bottom up: prisoners first, then your spy, then
your pawns, and your royal last. A break can never reach further than the number of pieces
it has to drop, and like every other move it wraps around the edge of the board.

**Only a stack holding a spy may break** — yours standing in it, or the enemy's held prisoner
in it. That second case is how a captured spy gets out: the square is its captor's, but
breaking it is *your* move.

The scatter keeps going until it meets an obstacle. Four things are one:

- a **royal**, either side's
- a **dragon**, either side's
- an **enemy** square holding prisoners
- an **enemy** square with more than one piece on it

Everything else it falls straight through. A square of your own is never an obstacle however
much is standing on it — the falling piece simply joins the stack. A lone enemy is not an
obstacle either: the piece lands on it and takes it prisoner.

"Enemy" here means enemy to the piece that is falling, which is not always your side —
prisoners drop first and stand back up as their own. And a break never sets anyone free but
the prisoners in the stack doing the breaking; that is what keeps it off enemy jailers.

**When the scatter is stopped, everything still falling lands together** on the last square
it reached.

A break is how a stack that has become too heavy, or too committed, takes itself apart.

### Pass

You may pass. In notation it is written `--`.

## The ko rule

> **A move may not return the board to any position the game has already stood in.**

Not a window of the last few turns — the *entire* history, from the position the entering
phase left behind onwards. Every move must leave the game somewhere it has never been.

This is stricter than the repetition rules of most abstract games, and it is what stops
two stacks from shuffling back and forth forever.

## Prisoners

When you jump onto an enemy square, its spy and pawns become your prisoners. They sit on
your square, held.

- Prisoners belong to whichever side does **not** control the square they sit on.
- You may **leave them behind** when you move on — they stand back up as a stack of their
  own side, on the square you vacated.
- Or you may **drag them along**, which costs you twice over: your strength drops by their
  number, and every occupied square now stops you — you cannot land on one or pass over
  one, your own included.
- So **a stack carrying prisoners cannot change size.** That is a restriction on what it
  may do, not on what may be done to it: an allied stack may still jump *onto* it, and the
  two merge with the prisoners still held. Size changes are available from one side of the
  move only.
- A royal is never taken prisoner.

## Reading and writing moves

Moves are recorded in RAN, one token per ply:

```
Jd3f5     jump from d3 to f5
Pd3d4     push from d3 toward d4
Fd3d4     free: push into d4 and release your pieces held there
Bd3r      break out of d3, scattering rightward
@Rd3      enter a royal on d3
Jd3f5*    ... the trailing star means prisoners came along
--        pass
```

Direction letters are `d`own, `u`p, `l`eft, `r`ight.

Parsing and printing both live in
[engine/src/royals_engine/notation.py](../engine/src/royals_engine/notation.py), which is
the only module permitted to know how a move is packed.

---

## Where the rules actually live

This page is prose and prose drifts. The authoritative statements are all in code:

| Rule | Where |
|---|---|
| What a square can hold | `hasher.py` — the 13-bit encoding, at the top |
| The three weights | `hasher.py` — `spaceWeight`, `spaceStrength`, `spaceCaptors` |
| Jump legality | `engine.py` — the jump loop in `checkMoves` |
| Push range | `engine.py` — `getLegalPushLength` |
| Who may free | `engine.py` — `canFreePrisoners` |
| Break range | `engine.py` — `checkBreak` |
| Entering | `engine.py` — the `Entering` section |
| Ko | `engine.py` — the `KO` section |
| Winning | `hasher.py` — `WIN_CODES`, `Check_For_Winner` |
