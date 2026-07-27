# Regression harness for the board representation.
#
# Everything it records is stated in terms of parsed fields and square numbers, never in
# terms of how a board is stored -- so the same golden file has to come back out of any
# representation that plays the same game. That is the whole point: it was written against
# the variable-length BitArray encoding and is what the packed-int rewrite was checked
# against.
#
#   python3 regress.py check [base|search|all]  -- replay and diff against the goldens
#   python3 regress.py write [base|search|all]  -- record them from the current code
#   python3 regress.py                          -- check all
#
# Three sweeps, cheapest first:
#   entering  -- the whole entering order, several noise seeds, every placement recorded
#   moves     -- from a spread of reachable positions, EVERY legal move applied and the
#                resulting board recorded. This is the one that covers exePush/exeBreak
#                corners a played game would take thousands of turns to stumble into.
#   games     -- full AI-vs-AI games, recording each move, its score and the node count
#
# They go into two files, because they answer two different questions and only one of
# them is a contract.
#
#   golden.txt        -- entering and moves. A statement about the representation, move
#                        generation and the evaluator, with no search internals anywhere
#                        in it: what the rules say a move does, and what a position is
#                        worth. Making the search faster must leave this byte-identical,
#                        which is what makes it worth having.
#   golden_search.txt -- games, which record each move's node count. Those move whenever
#                        the tree is walked differently -- often for a perfectly good
#                        reason -- so re-recording this file is a normal thing to do and
#                        shouldn't drag the contract above along with it.
#
# Keeping them in one file meant a search change appeared to break move generation. It
# also meant the only way to get back to green was to rewrite the whole thing, which is
# how the games section came to disagree with the code while everything above it, the
# part actually worth checking, still matched line for line.

import sys
import random

from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import ai as AI


def boardText(board):
    # a board as 49 parsed squares, empties collapsed so the line stays readable.
    # Only the semantic fields are recorded -- side through prisFlag, everything an
    # arrangement of pieces actually is. A parsed square carries derived scalars after
    # those, but they are answers the fields already contain, so printing them would put
    # the same fact in the file twice and make an optimisation look like a change.
    out = []
    for i, s in enumerate(Hasher.Parse_Board(board)):
        fields = s[:Hasher.FIELDS]
        if not any(fields): continue
        out.append(Hasher.IndexToAlg(i) + ":" + ",".join(str(f) for f in fields))
    return "|".join(out) if out else "-empty-"


def moveText(move):
    if move is None: return "none"
    origin, kind, target, pris = move
    # a break's direction is carried as an index into Engine.pushDirs; spelled back out as
    # the pair, so what this records is the move and not how the move happens to be stored
    if kind == "break": what = "break " + str(tuple(Engine.pushDirs[target]))
    else: what = kind + " " + Hasher.IndexToAlg(target)
    return Hasher.IndexToAlg(origin - 1) + " " + what + (" +pris" if pris else "")


def enteredBoard(seed, intensity = 0.5):
    AI.setEntryNoise(intensity, seed)
    board = Hasher.Entering_Board()
    for contr, piece in Engine.enteringSequence():
        isSpy = (piece == Hasher.SPY)
        if not Engine.enteringOptions(board, contr, isSpy): continue
        board = Engine.dropPiece(board, AI.chooseEntry(board, contr, piece, isSpy), contr, piece)
    return board


def sweepEntering(emit):
    emit("### ENTERING")
    for seed in (1, 7, 99, 4242):
        for intensity in (0.0, 0.5, 1.0):
            AI.setEntryNoise(intensity, seed)
            board = Hasher.Entering_Board()
            emit("seed %d int %.1f start %s" % (seed, intensity, boardText(board)))

            for contr, piece in Engine.enteringSequence():
                isSpy = (piece == Hasher.SPY)
                options = Engine.enteringOptions(board, contr, isSpy)
                emit("  options %d %s" % (len(options), ",".join(str(o) for o in options)))
                if not options:
                    emit("  skipped")
                    continue
                square = AI.chooseEntry(board, contr, piece, isSpy)
                emit("  side %d piece %d -> %s" % (contr, piece, Hasher.IndexToAlg(square - 1)))
                board = Engine.dropPiece(board, square, contr, piece)
                emit("  " + boardText(board))


def sweepMoves(emit):
    # Walk a game with random (but seeded) move choices, and at every position apply every
    # legal move both sides have and record where it lands. Random play reaches messy
    # positions -- captures, prisoners, spies inside enemy stacks -- far faster than the AI,
    # which is steering for a tidy gather.
    emit("### MOVES")
    for seed in (3, 11, 57):
        rng = random.Random(seed)
        board = enteredBoard(seed)
        Engine.koReset()
        AI.newGame()
        Engine.koRecord(board)

        for turn in range(0, 40):
            contr = turn % 2

            for side in (0, 1):
                moves = AI.listAllMoves(board, side)
                emit("s%d t%d side %d moves %d" % (seed, turn, side, len(moves)))
                for move in sorted(moves, key = moveText):
                    child = AI.performOneStep(board, side, move)
                    emit("  " + moveText(move) + " => " + boardText(child))
                    # the evaluator reads the same fields, so a representation slip that
                    # somehow survived boardText would still show up here
                    # whole numbers of thousandths, printed exactly -- a rounded print was
                    # what let a cross-interpreter difference hide here in the first place
                    emit("    eval %d %d" % (AI.fullCheck(child, 0), AI.fullCheck(child, 1)))

            gameEnd, winner = Hasher.Check_For_Winner(board)
            emit("  winner %s %s" % (gameEnd, winner))
            if gameEnd: break

            moves = AI.listAllMoves(board, contr)
            if not moves: break
            board = AI.performOneStep(board, contr, moves[rng.randrange(len(moves))])
            Engine.koRecord(board)


def sweepGames(emit):
    emit("### GAMES")
    for seed in (5, 23):
        for depth in (2, 3):
            board = enteredBoard(seed)
            # a game at a time, so what one game's search learned doesn't carry into the
            # next one's numbers -- the table is meant to persist within a game, not beyond
            Engine.koReset()
            AI.newGame()
            Engine.koRecord(board)
            emit("seed %d depth %d %s" % (seed, depth, boardText(board)))

            passes = 0
            for turn in range(0, 30):
                contr = turn % 2
                board, move, score = AI.takeTurn(board, contr, depth)
                emit("  t%d side %d %s score %d nodes %d"
                     % (turn, contr, moveText(move), score, AI.calcCount))
                emit("  " + boardText(board))

                if move is None:
                    passes += 1
                    if passes > 1: break
                    continue
                passes = 0

                Engine.koRecord(board)
                gameEnd, winner = Hasher.Check_For_Winner(board)
                if gameEnd:
                    emit("  finished %s" % (winner,))
                    break


SWEEPS = {"entering": sweepEntering, "moves": sweepMoves, "games": sweepGames}

# name -> (file, sweeps), in the order they run
BASELINES = [
    ("base", ("golden.txt", ("entering", "moves"))),
    ("search", ("golden_search.txt", ("games",))),
]


def run(sweeps = ("entering", "moves", "games")):
    lines = []
    for name in sweeps: SWEEPS[name](lines.append)
    return lines


# Which baselines a command-line word asks for. Naming a sweep works too, so a single
# expensive sweep can be re-recorded on its own.
def selected(word):
    if word in ("all", "", None): return [name for name, spec in BASELINES]
    if word in dict(BASELINES): return [word]
    for name, spec in BASELINES:
        if word in spec[1]: return [name]
    print("don't know what '%s' is -- try base, search, or all" % word)
    sys.exit(2)


def check(name):
    path, sweeps = dict(BASELINES)[name]
    lines = run(sweeps)

    try:
        with open(path) as f: want = f.read().splitlines()
    except IOError:
        print("%-7s NO BASELINE -- %s missing, run: python3 regress.py write %s"
              % (name, path, name))
        return False

    if want == lines:
        print("%-7s OK -- %d lines identical (%s)" % (name, len(lines), path))
        return True

    print("%-7s MISMATCH: %s has %d lines, got %d" % (name, path, len(want), len(lines)))
    shown = 0
    for i in range(max(len(want), len(lines))):
        a = want[i] if i < len(want) else "<missing>"
        b = lines[i] if i < len(lines) else "<missing>"
        if a != b:
            print("  line %d\n    want %s\n    got  %s" % (i + 1, a, b))
            shown += 1
            if shown >= 12: break
    return False


def write(name):
    path, sweeps = dict(BASELINES)[name]
    lines = run(sweeps)
    with open(path, "w") as f: f.write("\n".join(lines) + "\n")
    print("%-7s wrote %s, %d lines" % (name, path, len(lines)))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    which = selected(sys.argv[2] if len(sys.argv) > 2 else "all")

    if mode == "write":
        for name in which: write(name)
    else:
        if not all([check(name) for name in which]): sys.exit(1)
