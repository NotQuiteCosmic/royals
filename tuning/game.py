"""One game between two weight sets.

The engine keeps its search state -- the transposition table and its retired generation,
the killer and history tables -- as module globals that persist from one move to the next,
on purpose: the position after a move is one the previous search already looked at. That is
fine when both sides are the same program. Here they usually are not, and a table entry is
a score the weights in force WHEN IT WAS WRITTEN produced. Let red read blue's entries and
red is partly thinking with blue's evaluator.

So each side owns a SearchState: its weights and its four tables. Before a side's search
its state is put in force and after it the tables are read back -- read back, not assumed,
because chooseMove REBINDS those names (it empties killers, rebuilds history and rotates
the table generation) rather than mutating them in place. The ko history is shared: it is a
fact about the game, not about either player.

When both sides have the same weights there is nothing to protect and the swap is skipped,
so the game is exactly what the engine plays on its own. That is what lets a default-vs-
default game be checked ply for ply against golden_search.txt.
"""

import sys
import time

from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import ai as AI
from royals_engine import notation as N

from tuning import weights as W

# What web/ai_pool.py uses per worker. The desktop default of 300k entries is a few hundred
# MB per generation, and a tournament runs sixteen tables at once.
TABLE_LIMIT = 50_000

PLY_CAP = 300

BLUE, RED = 0, 1


class SearchState:
    def __init__(self, weights):
        self.weights = W.complete(weights)
        self.id = W.weight_id(self.weights)
        self.table = {}
        self.tableOld = {}
        self.history = {}
        self.killers = {}

    def enter(self):
        AI.setWeights(self.weights, clear=False)
        AI.table = self.table
        AI.tableOld = self.tableOld
        AI.history = self.history
        AI.killers = self.killers

    def leave(self):
        self.table = AI.table
        self.tableOld = AI.tableOld
        self.history = AI.history
        self.killers = AI.killers


def play_game(opening, weights_blue, weights_red, depth, ply_cap=PLY_CAP, first=None,
              table_limit=TABLE_LIMIT, keep_scores=True):
    """Play one game out. Returns a plain dict, one JSON line's worth, with everything needed
    to replay it: the opening's tokens, every move in RAN, and which weights played which
    side. result_blue is 1, 0.5 or 0 from blue's side."""
    states = [SearchState(weights_blue), SearchState(weights_red)]
    same = states[BLUE].id == states[RED].id
    if same: states[RED] = states[BLUE]

    AI.TABLE_LIMIT = table_limit
    Engine.koReset()
    AI.newGame()
    board = opening.board
    Engine.koRecord(board)

    contr = opening.to_move if first is None else first
    moves = []
    scores = []
    passes = 0
    nodes = 0
    termination = "ply_cap"
    result = 0.5
    started = time.monotonic()

    # with one shared state there is no swap to do; its weights still have to be in force
    if same: states[BLUE].enter()

    for ply in range(ply_cap):
        state = states[contr]
        if not same: state.enter()
        after, move, score = AI.takeTurn(board, contr, depth)
        if not same: state.leave()
        nodes += AI.calcCount

        moves.append(N.encode_move(move))
        if keep_scores: scores.append(score)

        if move is None:
            passes += 1
            if passes > 1:
                termination = "double_pass"
                break
            contr = 1 - contr
            continue

        passes = 0
        board = after
        Engine.koRecord(board)
        ended, winner = Hasher.Check_For_Winner(board)
        if ended:
            termination = "gather"
            result = 1.0 if winner[BLUE] else 0.0
            break
        contr = 1 - contr

    # leave the engine the way a fresh process would be
    AI.setWeights(AI.DEFAULT_WEIGHTS)
    Engine.koReset()

    return {
        "seed": opening.seed, "noise": opening.noise, "random_plies": opening.random_plies,
        "rng_seed": opening.rng_seed, "entries": opening.entries, "opening_moves": opening.moves,
        "first": opening.to_move if first is None else first,
        "depth": depth, "table_limit": table_limit,
        "blue_id": states[BLUE].id, "red_id": states[RED].id,
        "moves": moves, "scores": scores if keep_scores else None,
        "result_blue": result, "termination": termination, "plies": len(moves),
        "nodes": nodes, "seconds": round(time.monotonic() - started, 3),
        "interpreter": sys.implementation.name,
    }


def replay_game(record):
    """The final board of a recorded game, from its tokens alone."""
    board, contr = _replay_opening(record)
    for token in record["moves"]:
        move = N.decode_move(token)
        if move is not None:
            board = AI.performOneStep(board, contr, move)
        contr = 1 - contr
    return board


def _replay_opening(record):
    from tuning import openings as O
    board, to_move = O.replay(record["entries"], record["opening_moves"])
    return board, record["first"]
