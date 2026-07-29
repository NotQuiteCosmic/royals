# Regression harness for the board representation.
#
# Everything it records is stated in terms of parsed fields and square numbers, never in
# terms of how a board is stored -- so the same golden file has to come back out of any
# representation that plays the same game. That is the whole point: it was written against
# the variable-length BitArray encoding and is what the packed-int rewrite was checked
# against.
#
#   python3 regress.py check [enter|moves|search|base|all]  -- diff against the goldens
#   python3 regress.py write [enter|moves|search|base|all]  -- record from current code
#   python3 regress.py                                      -- check all
#
# `base` is kept as an alias for enter+moves, which is what it named when the two lived
# in one file. Naming a sweep works too, so a single expensive sweep can be re-recorded
# on its own.
#
# Three sweeps, cheapest first:
#   entering  -- the whole entering order, several noise seeds, every placement recorded
#   moves     -- from a spread of reachable positions, EVERY legal move applied and the
#                resulting board recorded. This is the one that covers exePush/exeBreak
#                corners a played game would take thousands of turns to stumble into.
#   games     -- full AI-vs-AI games, recording each move, its score and the node count
#
# They go into three files, because they answer three different questions and are allowed
# to move for three different reasons.
#
#   golden_enter.txt  -- entering. Depends on Perlin noise and on random.Random(seed)
#                        .shuffle, so it is bound to CPython's Mersenne Twister and to
#                        float arithmetic. That makes it the one sweep a port to another
#                        language cannot reproduce without reimplementing MT19937 exactly
#                        -- and entering is called a dozen times a game and contributes
#                        nothing to search speed, so no port should try.
#   golden_moves.txt  -- moves. A statement about the representation, move generation and
#                        the evaluator, with no search internals anywhere in it: what the
#                        rules say a move does, and what a position is worth. Integer-only
#                        and free of any language's RNG, so a second implementation of the
#                        rules must reproduce this file byte for byte. That is what makes
#                        it a cross-language conformance oracle and not just a snapshot.
#   golden_search.txt -- games, which record each move's node count. Those move whenever
#                        the tree is walked differently -- often for a perfectly good
#                        reason -- so re-recording this file is a normal thing to do and
#                        shouldn't drag the contracts above along with it.
#
# Keeping games in with the rest meant a search change appeared to break move generation.
# It also meant the only way to get back to green was to rewrite the whole thing, which is
# how the games section came to disagree with the code while everything above it, the
# part actually worth checking, still matched line for line.
#
# Splitting entering from moves is the same argument one level down. They used to share
# golden.txt, so a single file had to stay byte-identical for two unrelated reasons: one
# half is a portable statement about the rules, the other half is a fingerprint of one
# interpreter's RNG. A port that reproduces the rules exactly and leaves entering in
# Python is correct -- and against the combined file it looked like a failure.
#
# The split is byte-exact by construction: run() concatenates each sweep's lines in order,
# so golden_enter.txt followed by golden_moves.txt is the old golden.txt exactly. If you
# ever need to prove that again:
#
#     cat golden_enter.txt golden_moves.txt | diff - <(git show HEAD:tests/golden.txt)

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


####### Fixtures for a second implementation #######
# golden_moves.txt is the cross-language oracle, but a port cannot reproduce it from
# nothing, and the reason is easy to miss: sweepMoves is not self-contained.
#
#   - it starts each seed from enteredBoard(seed), which runs the entering search --
#     Perlin noise over floats, seeded through random.Random(seed).shuffle.
#   - it advances the game with moves[rng.randrange(len(moves))], which is CPython's
#     Mersenne Twister again.
#
# Both are CPython-specific. Reimplementing MT19937 and CPython's shuffle in another
# language to reproduce a *test harness* would be a lot of exacting work to gain nothing
# about the rules, which is the only thing the file is meant to check.
#
# So the RNG's answers are recorded here instead of re-derived. A port reads this file,
# starts from the same boards, and plays the same move *indices* -- and that is a stricter
# test than it looks. The index selects from listAllMoves' output, so if the port's move
# generation differs by so much as an ordering, index 7 is a different move, the walk
# diverges on the next line, and the diff points straight at it.
#
# What the port still has to do for itself is everything that matters: generate the moves,
# order them the same way, apply them, and score the result.
FIXTURE_PATH = "fixtures/port_fixtures.json"

MOVE_SEEDS = (3, 11, 57)


def walkChoices(seed):
    """The move indices sweepMoves plays for one seed, and the board it starts from.

    Mirrors sweepMoves' walk exactly and emits nothing. Deliberately a separate function
    rather than a refactor of sweepMoves: golden_moves.txt must not move, and the surest
    way to guarantee that is not to touch the code that writes it. The self-check in
    dumpFixtures is what keeps the two from drifting apart.
    """
    rng = random.Random(seed)
    board = enteredBoard(seed)
    start = board

    Engine.koReset()
    AI.newGame()
    Engine.koRecord(board)

    choices = []
    for turn in range(0, 40):
        contr = turn % 2

        gameEnd, winner = Hasher.Check_For_Winner(board)
        if gameEnd: break

        moves = AI.listAllMoves(board, contr)
        if not moves: break

        pick = rng.randrange(len(moves))
        choices.append(pick)
        board = AI.performOneStep(board, contr, moves[pick])
        Engine.koRecord(board)

    return start, choices, board


def dumpFixtures():
    import hashlib
    import json
    import os

    walks = []
    for seed in MOVE_SEEDS:
        start, choices, final = walkChoices(seed)
        walks.append({"seed": seed, "start": list(start),
                      "choices": choices, "final": list(final)})

    # UNPACK is 8192 rows of 14, which is half a megabyte of JSON to restate facts the
    # field rules already give. A port builds its own and checks the digest.
    flat = ",".join(",".join(str(v) for v in row) for row in Hasher.UNPACK)
    unpackDigest = hashlib.sha256(flat.encode()).hexdigest()

    fixtures = {
        "note": "Generated by regress.py fixtures. See docs/PORTING.md.",
        "unpack_sha256": unpackDigest,
        "unpack_rows": len(Hasher.UNPACK),
        "unpack_width": Hasher.WIDTH,
        "jumpray": [[list(strand) for strand in Engine.JUMPRAY[sq]] for sq in range(1, 50)],
        "pushray": [[list(strand) for strand in Engine.PUSHRAY[sq]] for sq in range(1, 50)],
        "breakray": [[list(strand) for strand in Engine.BREAKRAY[sq]] for sq in range(1, 50)],
        "pushfrom": [[Engine.PUSHFROM[o][d] for d in range(0, 50)] for o in range(0, 50)],
        "jumpreach": [AI.JUMPREACH[sq] for sq in range(1, 50)],
        "jumpdist": [list(AI.JUMPDIST[sq]) for sq in range(1, 50)],
        "entering_board": list(Hasher.Entering_Board()),
        "walks": walks,
    }

    os.makedirs(os.path.dirname(FIXTURE_PATH), exist_ok=True)
    with open(FIXTURE_PATH, "w") as f:
        json.dump(fixtures, f, indent=1, sort_keys=True)
        f.write("\n")

    print("fixtures wrote %s -- %d walks, unpack sha256 %s..."
          % (FIXTURE_PATH, len(walks), unpackDigest[:16]))

    # The whole value of this file is that it agrees with the sweep. Re-run the sweep and
    # confirm the goldens still match, so a fixture dump can never quietly certify a walk
    # the recorded file doesn't take.
    if not check("moves"):
        print("fixtures REFUSED -- golden_moves.txt does not match; fixtures may be stale")
        sys.exit(1)


SWEEPS = {"entering": sweepEntering, "moves": sweepMoves, "games": sweepGames}

# name -> (file, sweeps), in the order they run
BASELINES = [
    ("enter", ("golden_enter.txt", ("entering",))),
    ("moves", ("golden_moves.txt", ("moves",))),
    ("search", ("golden_search.txt", ("games",))),
]

# Words that stand for more than one baseline. "base" is what enter+moves were called
# while they shared a file; every existing invocation and every line of documentation
# says `check base`, so it keeps working rather than becoming a silent no-op.
ALIASES = {"base": ["enter", "moves"]}


def run(sweeps = ("entering", "moves", "games")):
    lines = []
    for name in sweeps: SWEEPS[name](lines.append)
    return lines


# Which baselines a command-line word asks for. Naming a sweep works too, so a single
# expensive sweep can be re-recorded on its own.
def selected(word):
    if word in ("all", "", None): return [name for name, spec in BASELINES]
    if word in ALIASES: return list(ALIASES[word])
    # a baseline's own name wins over a sweep's, which matters for "moves": it is both
    if word in dict(BASELINES): return [word]
    for name, spec in BASELINES:
        if word in spec[1]: return [name]
    print("don't know what '%s' is -- try %s, base, or all"
          % (word, ", ".join(name for name, spec in BASELINES)))
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

    if mode == "fixtures":
        dumpFixtures()
        sys.exit(0)

    which = selected(sys.argv[2] if len(sys.argv) > 2 else "all")

    if mode == "write":
        for name in which: write(name)
    else:
        if not all([check(name) for name in which]): sys.exit(1)
