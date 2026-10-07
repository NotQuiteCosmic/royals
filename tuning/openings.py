"""Where a game starts from.

The search is deterministic, so two games from the same position with the same weights are
the same game: all the variety a tuning run gets, it gets from here. An opening is the
entering phase played by the engine's own entering heuristic under Perlin noise at a given
seed -- regress.enteredBoard, with the placements written down -- optionally followed by a
few random legal plies, which reach shapes the heuristic never would.

Entering costs about a second per opening, far too much to pay at play time against a
two-second game. So a book is built once, in parallel, and each opening is stored as its
RAN tokens: the twelve placements (a `--` where a side had nowhere to go) and the random
plies. Replaying tokens through dropPiece and performOneStep takes microseconds, and the
book stays readable and diffable.
"""

import json
import random
from concurrent.futures import ProcessPoolExecutor

from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import ai as AI
from royals_engine import notation as N

ENTER_STEPS = Engine.enteringSequence()

# Blue enters first at every stage, so red places the last spy and then, by the rules, takes
# the first move. (regress.py's games sweep starts with blue instead; play_game takes a
# `first` override for reproducing it.)
FIRST_TO_MOVE = 1


class Opening:
    __slots__ = ("seed", "noise", "random_plies", "rng_seed", "entries", "moves", "board", "to_move")

    def __init__(self, seed, noise, random_plies, rng_seed, entries, moves, board, to_move):
        self.seed = seed
        self.noise = noise
        self.random_plies = random_plies
        self.rng_seed = rng_seed
        self.entries = entries
        self.moves = moves
        self.board = board
        self.to_move = to_move

    def to_row(self):
        return {"seed": self.seed, "noise": self.noise, "random_plies": self.random_plies,
                "rng_seed": self.rng_seed, "entries": self.entries, "moves": self.moves}

    @classmethod
    def from_row(cls, row):
        board, to_move = replay(row["entries"], row["moves"])
        return cls(row["seed"], row["noise"], row["random_plies"], row.get("rng_seed"),
                   row["entries"], row["moves"], board, to_move)

    def __repr__(self):
        return "Opening(seed=%r, noise=%r, random_plies=%r)" % (self.seed, self.noise, self.random_plies)


def enter(seed, noise):
    """The entering phase under the engine's heuristic. Returns (board, entry tokens)."""
    AI.setEntryNoise(noise, seed)
    board = Hasher.Entering_Board()
    entries = []
    for contr, piece in ENTER_STEPS:
        isSpy = (piece == Hasher.SPY)
        if not Engine.enteringOptions(board, contr, isSpy):
            entries.append(N.PASS)
            continue
        square = AI.chooseEntry(board, contr, piece, isSpy)
        board = Engine.dropPiece(board, square, contr, piece)
        entries.append(N.encode_entry(piece, square))
    return board, entries


def random_moves(board, plies, rng_seed, to_move=FIRST_TO_MOVE):
    """`plies` random legal moves, alternating sides from `to_move`. Returns (board, tokens,
    to_move after), or None if a side ran out of moves -- such an opening is dropped rather
    than padded with a pass, since a pass this early says the position is degenerate.

    Moves that win on the spot or repeat a position are not offered: the point is a
    playable position, not a decided one."""
    rng = random.Random(rng_seed)
    seen = {board}
    tokens = []
    contr = to_move
    for ply in range(plies):
        options = []
        for move in AI.listAllMoves(board, contr):
            child = AI.performOneStep(board, contr, move)
            if child in seen: continue
            if Hasher.Check_For_Winner(child)[0]: continue
            options.append((move, child))
        if not options: return None
        move, board = options[rng.randrange(len(options))]
        seen.add(board)
        tokens.append(N.encode_move(move))
        contr = 1 - contr
    return board, tokens, contr


def make_opening(seed, noise=1.0, random_plies=0, rng_seed=None):
    """Build one opening from scratch. Returns None if the random plies could not be
    completed. An odd number of random plies leaves blue to move, which the harness copes
    with (to_move is recorded) but the book builder refuses, so that every opening in a
    book has the same side to move."""
    board, entries = enter(seed, noise)
    moves = []
    to_move = FIRST_TO_MOVE
    if random_plies:
        if rng_seed is None: rng_seed = seed
        got = random_moves(board, random_plies, rng_seed)
        if got is None: return None
        board, moves, to_move = got
    return Opening(seed, noise, random_plies, rng_seed, entries, moves, board, to_move)


def replay(entries, moves):
    """Tokens back to a board. Returns (board, side to move)."""
    board = Hasher.Entering_Board()
    if len(entries) != len(ENTER_STEPS):
        raise ValueError("an opening has %d placements, got %d" % (len(ENTER_STEPS), len(entries)))
    for (contr, piece), token in zip(ENTER_STEPS, entries):
        if token == N.PASS: continue
        got_piece, square = N.decode_entry(token)
        if got_piece != piece:
            raise ValueError("placement %r out of order: expected piece %d" % (token, piece))
        board = Engine.dropPiece(board, square, contr, piece)

    contr = FIRST_TO_MOVE
    for token in moves:
        move = N.decode_move(token)
        if move is not None:
            board = AI.performOneStep(board, contr, move)
        contr = 1 - contr
    return board, contr


# ---------------------------------------------------------------------------
# The book
# ---------------------------------------------------------------------------

def _build_one(args):
    seed, noise, random_plies = args
    opening = make_opening(seed, noise, random_plies)
    if opening is None: return None
    return opening.to_row(), opening.board


def build_book(count, path, noise=1.0, random_plies=2, start_seed=1, workers=None, shard=None):
    """Write `count` distinct openings to `path` as JSONL. Seeds are tried in order from
    start_seed; two seeds that reach the same board keep only the first, so the book is a
    set of positions, not of seeds. Returns how many seeds were tried.

    `shard` = (i, N) takes only every Nth seed, offset i, so N jobs can build one book
    between them with no coordination; merge_books then dedupes across their outputs."""
    if random_plies % 2:
        raise ValueError("random_plies must be even so red is to move in every opening")
    shard_i, shard_n = shard or (0, 1)

    seen = set()
    written = 0
    tried = 0
    with ProcessPoolExecutor(max_workers=workers) as pool, open(path, "w") as out:
        seed = start_seed
        while written < count:
            batch = [(s, noise, random_plies) for s in range(seed, seed + 64 * shard_n)
                     if (s - start_seed) % shard_n == shard_i]
            seed += 64 * shard_n
            tried += len(batch)
            for got in pool.map(_build_one, batch):
                if got is None: continue
                row, board = got
                if board in seen: continue
                seen.add(board)
                out.write(json.dumps(row, sort_keys=True) + "\n")
                written += 1
                if written >= count: break
    return tried


def merge_books(paths, out):
    """One book from several shards' books: every row whose position has not been seen,
    in file order. Returns how many were written."""
    seen = set()
    written = 0
    with open(out, "w") as f:
        for path in paths:
            with open(path) as src:
                for line in src:
                    line = line.strip()
                    if not line: continue
                    row = json.loads(line)
                    board, _ = replay(row["entries"], row["moves"])
                    if board in seen: continue
                    seen.add(board)
                    f.write(json.dumps(row, sort_keys=True) + "\n")
                    written += 1
    return written


def load_book(path, limit=None):
    openings = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            openings.append(Opening.from_row(json.loads(line)))
            if limit is not None and len(openings) >= limit: break
    return openings


if __name__ == "__main__":
    import argparse
    import sys
    import time

    parser = argparse.ArgumentParser(description="build an opening book")
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--out", default="tuning/book.jsonl")
    parser.add_argument("--noise", type=float, default=1.0)
    parser.add_argument("--random-plies", type=int, default=2)
    parser.add_argument("--start-seed", type=int, default=1)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--shard", default=None, help="i/N: build only every Nth seed, offset i")
    parser.add_argument("--merge", nargs="*", default=None, help="instead: merge these shard books into --out")
    args = parser.parse_args()

    if args.merge is not None:
        n = merge_books(args.merge, args.out)
        sys.stdout.write("%d openings merged from %d files -> %s\n" % (n, len(args.merge), args.out))
        sys.exit(0)

    shard = None
    if args.shard:
        i, n = (int(x) for x in args.shard.split("/"))
        shard = (i, n)
    started = time.time()
    tried = build_book(args.count, args.out, args.noise, args.random_plies, args.start_seed, args.workers, shard)
    sys.stdout.write("%d openings from %d seeds in %.0fs -> %s\n"
                     % (args.count, tried, time.time() - started, args.out))
