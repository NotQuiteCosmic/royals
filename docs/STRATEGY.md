# How to play Royals well

[RULES.md](RULES.md) tells you what is legal. This tells you what is good.

Everything below was checked against the engine rather than reasoned about from the
rulebook, and the measured claims say how they were measured. Where a number appears, it
came from running something — the appendix says what.

---

## The shape of the game

The entering rules forbid you from placing a royal or a pawn next to anything you already
control. The winning condition requires all six of those pieces on one square. **So the
opening forces your army apart and the win demands you put it back together**, and the
whole game is the argument between those two facts.

This is not a game about killing things. In sixty self-play games, every single one ended
by gathering, and only 5.1% of moves took a prisoner. Attrition is a side effect. If you
find yourself hunting pieces, you are probably losing.

A game runs about fifty plies after the entering phase — median 50, mean 57, and a long tail
that reached 194 once in sixty games. You will have roughly 24 legal moves to choose from on
a typical turn, so the tree is small enough to read a little way down by hand.

---

## Part 1: the colour law

This is the one thing to learn first. It explains more of the game than everything else
combined.

Colour a1 dark, b1 light, and so on, like a chessboard. There are 25 dark squares and 24
light ones. Then:

> **A jump never changes the colour of the square you are on.
> A push always does.
> A break scatters you across both.**

The first of those is exact. I asked the engine for every jump from every square at every
stack weight — 1,988 destinations — and not one changed colour. It is not a tendency, it is
a law, and it holds even for the jumps that wrap around the edge of the board.

The reason is arithmetic. A diagonal step changes your file and your rank by one each, so
the two changes cancel and the colour is preserved. A wrapping jump changes both
coordinates by six, which cancels the same way.

A push moves you one square orthogonally, which changes the colour every time. And it
*is* every time: you cannot begin a push by shoving something off the edge of the board, so
the first step of a push is always an ordinary interior step.

### What this costs you

Your six pieces have to end on one square. That square has one colour. **So every piece
that is on the wrong colour has to spend at least one push to get right**, and pushes are
not free — see Part 3.

Measured over 1,200 games from randomised openings, counting how many of a side's six
gatherable pieces sat on the minority colour once the entering phase ended:

| Pieces on the wrong colour | Win rate | Median game length |
|---|---|---|
| 0 | **61.7%** | 48 plies |
| 1 | 51.8% | 53 |
| 2 | 50.9% | 55 |
| 3 | 46.4% | 59 |

Monotone in both columns. Perfect colour discipline is worth about fifteen percentage
points against the worst case, and it finishes eleven plies sooner.

Note that this is not overwhelming, and the reason is that your opponent has the same
problem. It is an edge, not a lock.

---

## Part 2: the assembly order is forced

Two rules interlock:

- A spy **may not land on anything at all**. Any occupied square stops it.
- **Nothing may land on a royal.**

Put those together and the order is decided for you:

> **The spy cannot come last. The royal cannot come first.
> The pawns gather onto the spy, and the royal arrives last.**

I checked all four halves of that against the engine and they hold exactly. A pawn can jump
onto your spy; your spy cannot jump onto that pawn. A pawn cannot jump onto your royal;
your royal can jump onto that pawn.

The practical consequence is large: **your spy's square is your gathering square**, and it
is chosen at entering step 11, not in the middlegame. Everything after that is transport.

The spy can still move — it jumps to empty squares freely — so the gathering square is not
irrevocable. But moving it means moving the destination everything else is already
travelling towards, and that is usually a tempo you cannot spare.

---

## Part 3: beginner

**1. Decide your gathering square during the entering phase.** You get a great deal of
choice early and very little late: the first royal has 39 legal squares, the last pawn
averages 15, and the spy — which arrives at step 11, after all the pawns — gets about 37.
That ordering is a gift. You place your pawns first and then choose a spy square that suits
them.

**2. Put everything on one colour.** See Part 1. When you place a pawn, look at the colour
of the squares your other pieces are on. This is the single cheapest improvement available
to a new player.

**3. Never park your royal with your spy.** The engine penalises this by five points times
the square of your group count, which at three groups is a forty-five point swing — larger
than the value of any stack you could build. Two arrangements of the same six pieces:

```
spy + royal together, three groups    -32.72
spy + royal apart,    three groups    +12.28
```

The reason behind the penalty is real and not an artefact. The royal must arrive **last**.
If it is already sitting on the spy, then nothing else can ever join them — pawns cannot
land on a royal — and you have to break the stack up and start again.

**4. Gather onto the spy, royal last.** Bring pawns in. Keep the royal one jump away,
somewhere it can reach the gathering square, and bring it home only when the other five are
assembled.

**5. Ignore captures.** They are 5.1% of moves and they are usually a distraction. Taking a
prisoner is worth 0.8 of a point; merging two three-stacks into a six is worth the game.

**6. When you must split, split lopsided.** A stack is worth the square of its size, so:

```
5 + 1    28.25
4 + 2    22.25
3 + 3    20.25
2+2+2    12.28
six singletons, clustered      1.65
six singletons, scattered     -2.84
```

Two threes is the *worst* of the even splits and 5+1 is the best of everything short of
winning. If you have to leave a piece behind, leave one piece behind.

---

## Part 4: intermediate

### Weight is speed

A stack jumps up to as many squares as it weighs. A lone pawn moves one square; a
five-stack moves up to five. So **merging does not only score better, it accelerates**. The
first merge in a game is worth more than its score suggests, because everything afterwards
happens faster.

Average distinct jump destinations, by weight:

| Weight | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Destinations | 3.0 | 5.2 | 6.8 | 7.3 | 7.4 |

Note the flattening. Going from 1 to 3 nearly doubles your options; going from 4 to 5 buys
almost nothing. The mobility argument for stacking is strongest early.

### The push economy

Pushes are how you change colour, and they are scarce. **A push must shove something** —
you cannot push into an empty square — so a push needs a neighbour, and it needs you to be
strong enough to move the whole line.

Measured across midgame positions, only **33.5%** of a side's groups had any legal push at
all on a given turn. Two thirds of the time, a group is stuck on its colour that turn.

The cost rule is unforgiving: your strength must cover the **total weight of every square in
the line**, and if it falls short there is no push at all rather than a shorter one. A lone
pawn can shove one lone pawn and nothing heavier.

```
my strength 1 vs a 1-weight square    push length 1
my strength 1 vs a 2-weight square    refused
my strength 3 vs three lone pawns     push length 3   (the whole line moves one square)
```

Two consequences worth internalising:

- **Plan your colour changes early**, while there is still something adjacent to shove.
- **Your opponent's pieces are your staircase.** Adjacency to an enemy is what makes a push
  possible. A stack alone in an empty quarter of the board is stuck on its colour
  indefinitely.

### Carrying prisoners is usually a trap

Dragging captives costs you twice: your strength drops by their number, and *every* occupied
square now stops you — you cannot land on one or pass over one, **including your own**. A
stack carrying prisoners therefore cannot merge with anything, which means it cannot
progress toward the win at all.

It is 5.5% of moves in self-play, and it is nearly always either a short escort or an
accident. If you are carrying prisoners, you have taken yourself out of the game until you
put them down.

Leaving them behind is a real option and costs nothing: they stand back up on the square
you vacate.

### The dragon

Weight 3, strength 3, flies over occupied squares, cannot stack, cannot be captured, cannot
be landed on, and **cannot help you win**. It is not part of the six.

What it is good for:

- **Mobility.** Twelve jump destinations from d4 — the most mobile piece on the board,
  because it ignores everything in the way.
- **Pushing.** Strength 3 shoves a three-stack. That is a serious push, and it is often your
  cheapest way to change a colour.
- **Blocking.** A dragon is one of the four things that stops a break, either side's. In the
  test below, an enemy dragon two squares along cut a five-piece scatter from range 5 to
  range 2.
- **Denial.** Nothing can land on it, so it fences off a square permanently.

What it is not good for: anything resembling attack. It cannot take prisoners by landing,
since it cannot land on anyone.

### Ko

A move may not return the board to **any** position the game has already stood in — the
whole history, not a window. In practice this means shuffling is not a plan. If you and your
opponent are both moving a stack back and forth, one of you runs out of legal repetitions
and has to commit first.

It also occasionally bites without warning in a locked position, taking away the move you
were relying on. When a position is tight, prefer moves that leave you more than one way
forward.

---

## Part 5: advanced

### Entering is a parity plan, not a placement contest

You have twelve steps, and the choice shrinks as you go:

```
step  1  blue royal   39 legal squares
step  3  blue pawn    32
step  5  blue pawn    26
step  7  blue pawn    20
step  9  blue pawn    15        <- the tightest point
step 11  blue spy     37        <- and then it opens up again
```

The pawns get progressively harder to place because they may not touch each other or your
dragon. The spy ignores that rule entirely, which is why it is easy again at step 11.

So the plan writes itself: **place the four pawns as a loose constellation on one colour,
accepting that they cannot be adjacent, and then use the spy's freedom to pick the square
they can all converge on.** The royal goes somewhere on the same colour with a clear run in.

Do not try to place pawns close together. The rules forbid adjacency, and the distance you
save is a single jump — whereas being on the wrong colour costs a push, which is scarcer.

### The long diagonals

Only thirteen squares can wrap when jumping, and only along their own diagonal:

```
a1 b2 c3 d4 e5 f6 g7        and        a7 b6 c5 d4 e3 f2 g1
```

d4 is on both. From anywhere else, a jump that would leave the board simply stops.

This is why RULES.md's "there are no corners and no edge squares" is true of pushes and
breaks but **misleading about jumps**. A lone pawn on d1 has two jump destinations; a lone
pawn on c3 has four. The rim is real for jumping.

The evaluator agrees, mildly: it pays a quarter point per piece per available jump, so three
pawns on d4 score 10.50 against 9.00 on a1. That is a nudge, not a reason to turn down a
merge. The real value of the long diagonals is that a stack on one of them can cross the
board in a single jump, which matters enormously when you are trying to bring a straggler
home.

### The break, used deliberately

Breaks are 3.3% of moves and most players never use one on purpose. Three things they do
that nothing else can:

**1. They change stack shape at will.** A break drops one piece per square, bottom-up:
prisoners first, then your spy, then pawns, and **your royal last**. A full stack of six on
d4 breaking rightward lands spy on d4, pawns on e4, f4, g4 and a4 (wrapping), and the royal
on b4. That is a controlled disassembly with the royal thrown furthest.

**2. They cross colours.** The pieces land on alternating squares, so a break is the one
move that puts you on both colours at once. Sometimes that is exactly what you want; usually
it is the cost.

**3. They rescue a captured spy.** This is the one every player should know. If the enemy
has captured your spy, **the break is your move even though the square is theirs**:

```
before   d4: red 2 pawns, holding blue spy + blue pawn
after    d4: blue spy   e4: blue pawn   f4: red pawn   g4: red pawn
```

Your prisoners drop first, stand back up as your own, and **take the square** — while the
captors are scattered outward. You do not merely escape, you take the ground and break up
their stack in the same move.

Remember what stops a break: a royal of either side, a dragon of either side, an enemy
square holding prisoners, or an enemy square with more than one piece on it. A **lone**
enemy is not an obstacle — the falling piece lands on it and takes it prisoner. And when a
break is stopped, **everything still falling lands together** on the last square reached,
which can be an accidental merge or an accidental pile-up depending on whether you saw it
coming.

### Tempo and the first move

Red moves first, and it is worth something:

| Search depth | Games | Red wins |
|---|---|---|
| 3 | 199 | 56.3% |
| 5 | 80 | 65.0% |

The depth-5 sample is small enough that its exact figure should be taken loosely, but both
point the same way, and the effect appears to grow with skill — which is what you would
expect in a race. **Royals is fundamentally a race**, and in a race the player who is ahead
on tempo should simplify and hurry, while the player behind should look for a push or a
capture that costs the leader a move.

If you are blue, you are a move behind from the start. Spend the entering phase buying it
back with better colour discipline.

### Denial

Because the win is a gathering, you can lose by being disassembled at the wrong moment. The
sharpest defensive moves in Royals are the ones that force a stack apart or make its target
square unusable:

- A **lone spy's push shatters** what it hits. The spy spends its one point of strength and
  the stack it displaced scatters from where it lands. This is the cheapest disruption in
  the game — one piece, and a five-stack becomes five singletons.
- **Capturing one pawn** is worth far more than 0.8 points if it is the pawn that was about
  to complete a six. A group of yours held prisoner scores *negative* the square of its
  size, so a captured three-stack is an eighteen-point swing.
- **Standing your dragon** on or beside the square they are gathering on. Nothing can land
  on it.

Use these when you are behind. Used when you are ahead, they cost you the tempo that was
winning the race.

### Reading the engine

If you play against the computer and want to learn from it, the score in the game log is in
points where a stack of *n* is worth *n²*. Rough calibration:

| Score | Means |
|---|---|
| under 5 | scattered, early |
| 12–20 | consolidating, two or three groups |
| 25–30 | one big stack and a straggler — close |
| 1000 | won |
| negative | something of yours is captured, or your royal is on your spy |

A swing of more than about 20 points in one move almost always means a merge happened, a
capture happened, or somebody put their royal somewhere foolish.

---

## Appendix: where these numbers came from

Everything measured here is reproducible from the repo root. The self-play harnesses were
throwaway scripts driving `royals_engine.ai.takeTurn`; the structural facts come from asking
the engine directly.

| Claim | How |
|---|---|
| No jump changes colour | Every jump from all 49 squares at weights 1–6: 1,988 destinations, 0 exceptions |
| Jumps wrap only from 13 squares | `engine.buildJumpRays` — `canWrap` requires the origin to be on the matching long diagonal |
| A push must shove something | `getLegalPushLength` returns 0 on the first empty square |
| Push cost and refusal | `getLegalPushLength`, strength against cumulative weight |
| Colour discipline vs win rate | 1,200 games, depth 3, randomised entering |
| Game length, move mix, captures | 60 games, depth 5: 78.7% jumps, 17.4% pushes, 3.3% breaks, 0.6% frees |
| Push availability 33.5% | 9,799 group-turns across 40 randomised games |
| First-move edge | 199 games at depth 3, 80 at depth 5 |
| Stack and penalty values | `ai.evaluateSides` — `n²`, `PRISONER_WEIGHT` 0.8, `GROUP_PENALTY` 1.5, royal-and-spy `−5 × groups²` |
| Entering option counts | 300 randomised entering phases |
| Search depth matters | Depth 5 beat depth 2 24–5 over 30 games |

The engine's evaluator is a heuristic, not an oracle — it is what the computer believes, and
`ai.py` is candid about which of its terms survived tuning and which did not. The
*structural* claims (colour, wrapping, assembly order, push legality) are not heuristics;
they are the rules, and they will not change without `tests/golden_moves.txt` moving.
