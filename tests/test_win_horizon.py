"""The delayed win at the search horizon, and the two rule facts that decide it.

`test_delayed_win.py` holds the rule itself -- gathering does not end the game, surviving a
reply does. This file holds the place the rule leaks: the **leaf of the search**, where there
is no ply left to find the reply in.

The rule lives in `minimax`, which is terminal only when the side to move is the side that
gathered. That is right everywhere except at depth 0, where the node has no children to find
the refutation in and falls through to `evaluateSides` -- which answers `WIN_SCORE` flat for
six on a square, because a static function of the board cannot express a rule defined by
whose turn it is. So at depth N the search sees a gather made on the last ply, calls it won,
and never looks at the lone spy standing beside it. `minimax` now extends by exactly one ply
when the side that just moved has an unresolved gather, which is what "must survive a reply"
asks for and no more.

**No golden file can see any of this.** The largest `|eval|` in `golden_moves.txt`'s 13,051
lines is 128,642 against a `WIN_SCORE` of 1,000,000 -- there is not one won position in the
contract, because the move sweep's walks never assemble six on a square and
`golden_search.txt`'s games are cut off at thirty plies while a win takes sixty-eight. The
extension went in and all three goldens stayed byte-identical, which reads as "behaviour
preserved" and actually means "never exercised". Same shape as the carrying rules before
`test_carry_rules.py`.

Two rule facts here correct what the prose said before it was checked, and both are load
bearing for the strategy rather than trivia:

  - **Six may not land on six.** A finished stack holds the royal and nothing may land on a
    royal at any weight, so matching the stack is not an answer and never was. A lone spy's
    shattering push is the *only* refutation that exists -- asserted here by enumerating every
    move the opponent has, not by naming the one we expect.
  - **The shatter needs the square behind the stack empty.** A spy has one point of strength
    and spends it on the square it shoves, leaving nothing for the next one. Blocking that
    square -- with either side's piece -- makes a gathered six untouchable.
"""

import pytest

from royals_engine import ai as AI
from royals_engine import engine as Engine
from royals_engine import hasher as Hasher


# Hand-built boards, as in test_break_rules.py and test_carry_rules.py, and for the same
# reason: what is on the board is exactly what the test put there.
def board_of(**squares):
    """A board from `alg=(side, dragon, spy, pawns, royal, capSpy, capPawns, prisFlag)`."""
    board = list(Hasher.EMPTY_BOARD)
    for alg, spec in squares.items():
        board[Hasher.AlgebraToSquare(alg) - 1] = Hasher.Build_Space(*spec)
    return tuple(board)


def occupied(board):
    """`{alg: weight}` for every square holding anything."""
    return {Hasher.IndexToAlg(i): Hasher.UNPACK[c][Hasher.WEIGHT]
            for i, c in enumerate(board) if c}


def square(alg):
    return Hasher.AlgebraToSquare(alg)


SIX = (0, 0, 1, 4, 1)          # blue's whole army on one square: spy, four pawns, royal
RED_SPY = (1, 0, 1, 0, 0)      # the only piece that can do anything about it


# ---------------------------------------------------------------------------
# Why the leaf needs help
# ---------------------------------------------------------------------------

def test_the_evaluator_calls_a_completed_stack_won_flat():
    # The behaviour the extension exists to compensate for, asserted rather than assumed. This
    # is not a bug in evaluateSides and should not be "fixed" there: it is a static function of
    # the board with no turn to consult, and the win is defined by whose turn it is. The search
    # is the only place that knows, which is why the correction lives there.
    board = board_of(d4=SIX, a1=(1, 0, 0, 0, 1))
    assert AI.evaluateSides(board)[0] == AI.WIN_SCORE
    assert AI.fullCheck(board, 0) >= AI.WIN_SCORE
    assert AI.fullCheck(board, 1) <= -AI.WIN_SCORE


# ---------------------------------------------------------------------------
# The one answer there is
# ---------------------------------------------------------------------------

def test_the_lone_spy_push_is_the_only_answer():
    # Enumerating every move rather than naming the expected one: the claim is that exactly one
    # reply exists on the whole board, and only a sweep can say that. A full red army is given
    # every chance -- pawns, royal, dragon, and the spy.
    board = board_of(d4=SIX, e4=RED_SPY,
                     d6=(1, 0, 0, 1, 0), d2=(1, 0, 0, 1, 0), f6=(1, 0, 0, 1, 0),
                     f2=(1, 0, 0, 1, 0), a1=(1, 0, 0, 0, 1), g7=(1, 1, 0, 0, 0))
    assert Hasher.Check_For_Winner(board)[1][0] == 1

    answers = [move for move in AI.listAllMoves(board, 1)
               if not Hasher.Check_For_Winner(AI.performOneStep(board, 1, move))[1][0]]

    assert len(answers) == 1, "expected one refutation, got %r" % (
        [AI.describeMove(m) for m in answers],)
    origin, kind, target, _pris = answers[0]
    assert kind == "push"
    assert origin == square("e4") and target == square("d4") - 1

    # and what it does: the six comes apart one to a square along the rank it was shoved down
    after = AI.performOneStep(board, 1, answers[0])
    assert not Hasher.Check_For_Winner(after)[0]
    for alg in ("a4", "b4", "c4", "d4", "e4", "f4", "g4"):
        assert occupied(after)[alg] == 1


def test_six_may_not_land_on_six():
    # The refutation the prose used to name and the rules have never allowed. A finished stack
    # holds the royal, and "nothing may land on a square holding a royal" is tested before any
    # weight comparison -- so a matching six-stack is refused like everything else.
    board = board_of(d4=SIX, e5=(1, 0, 1, 4, 1))
    landings = [m for m in AI.listAllMoves(board, 1)
                if m[0] == square("e5") and m[2] == square("d4") - 1]
    assert landings == []


def test_blocking_the_square_behind_makes_the_stack_safe():
    # The spy spends its one point of strength on the square it shoves and has nothing left for
    # the next one, so the shatter needs the square BEYOND the stack empty. This is the concrete
    # defence of a gathering square, and the reason choosing one is not only about mobility.
    exposed = board_of(d4=SIX, e4=RED_SPY, a1=(1, 0, 0, 0, 1))
    assert any(not Hasher.Check_For_Winner(AI.performOneStep(exposed, 1, m))[1][0]
               for m in AI.listAllMoves(exposed, 1)), "an open line behind d4 should be fatal"

    # c4 is where the push would shove the stack. Either side's piece will do -- the spy is
    # paying for weight, not for ownership.
    for blocker in ((1, 0, 0, 1, 0), (0, 0, 0, 1, 0)):
        blocked = board_of(d4=SIX, e4=RED_SPY, c4=blocker, a1=(1, 0, 0, 0, 1))
        assert all(Hasher.Check_For_Winner(AI.performOneStep(blocked, 1, m))[1][0]
                   for m in AI.listAllMoves(blocked, 1)), "no red move should un-win blue"


# ---------------------------------------------------------------------------
# The horizon itself
# ---------------------------------------------------------------------------

# Blue's spy and four pawns stand on d4; the royal is on e5, one diagonal step away, and
# jumping it in completes the six. Red's lone spy waits on e4 with c4 empty behind it, so the
# gather is refuted the instant it is made.
#
# Before the extension a depth-1 search played this and scored it 1,002,500.
DOOMED = board_of(d4=(0, 0, 1, 4, 0), e5=(0, 0, 0, 0, 1), e4=RED_SPY, a1=(1, 0, 0, 0, 1))
GATHER = (square("e5"), "jump", square("d4") - 1, False)


@pytest.mark.parametrize("depth", [1, 2, 3, 4])
def test_the_search_refuses_a_gather_it_cannot_hold(depth):
    Engine.koReset(); AI.newGame(); Engine.koRecord(DOOMED)
    score, move = AI.chooseMove(DOOMED, 0, depth)

    assert move != GATHER, "walked into the shatter at depth %d" % depth
    assert score < AI.WIN_SCORE, "scored a refuted gather as a win at depth %d" % depth


@pytest.mark.parametrize("depth", [1, 2, 3, 4])
def test_the_search_still_takes_a_gather_that_holds(depth):
    # The control, and the half a too-eager fix would break: put the enemy spy out of reach and
    # the identical gather is correct at every depth. An extension that refused every gather
    # would pass the test above and fail this one.
    safe = board_of(d4=(0, 0, 1, 4, 0), e5=(0, 0, 0, 0, 1),
                    g7=RED_SPY, a1=(1, 0, 0, 0, 1))
    Engine.koReset(); AI.newGame(); Engine.koRecord(safe)
    score, move = AI.chooseMove(safe, 0, depth)

    assert move == GATHER, "missed the win at depth %d, played %s" % (depth, AI.describeMove(move))
    assert score >= AI.WIN_SCORE


def test_the_extension_resolves_rather_than_recurses():
    # The extension turns a leaf into a one-ply node whose children could in principle present
    # another unresolved gather. `gatherExt` bounds that, and the consequence is what is worth
    # asserting: the search returns, and does not visit an absurd number of nodes doing it. A
    # regression here would hang rather than fail, so the guard is cheap at the price.
    Engine.koReset(); AI.newGame(); Engine.koRecord(DOOMED)
    AI.chooseMove(DOOMED, 0, 4)
    assert AI.calcCount < 500_000, "the extension is not terminating tidily: %d nodes" % AI.calcCount
