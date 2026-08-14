"""Is the browser playing by the same rules as everything else?

    python3 engine-rs/tests/wasm_parity.py [freshly-built.wasm]

`web/src/royals_web/static/royals.wasm` is a build artifact that is checked in, which makes
it the one piece of this engine that can go stale without saying so: edit `movegen.rs`, forget
to rebuild, and the page keeps highlighting yesterday's rules while every test in the repo
stays green. Nothing else here has that failure mode -- the Python engine is the source, and
the accelerator is rebuilt by CI on every commit.

So the shipped module is asked the same questions the Python engine is asked, and has to give
the same answers. Every position along the port fixtures' walks, every square, both sides,
carrying prisoners and not -- a few seconds' work, and it prints how many questions that came
to rather than quoting a figure here that would rot the moment the walks change length.

Pass a freshly built module as an argument and it is checked too. That separates the two ways
this can fail, and they want different fixes:

  * the fresh module disagrees with Python  -- the Rust and the Python rules have diverged,
    which is the golden files' business and a real bug
  * only the shipped module disagrees       -- someone changed the engine and did not rebuild
    the artifact. Rebuild it and commit it.

**Minus the ko filter**, which the module does not implement and does not claim to: it holds
a position, not a history. See engine-rs/src/wasm.rs.
"""

import json
import os
import sys

from wasmtime import Engine, Instance, Module, Store

from royals_engine import ai as AI
from royals_engine import hasher as Hasher
# `Engine` above is wasmtime's, so the rules engine comes in under its own name. Only
# setPushRange is wanted from it -- telling the Python side which game it is comparing.
from royals_engine import engine as Rules

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

SHIPPED = os.path.join(ROOT, "web", "src", "royals_web", "static", "royals.wasm")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "port_fixtures.json")

SQUARES = 49
FIELDS = 8

# engine.js indexes this with the kind byte; wasm.rs asserts the same order in a Rust test.
KINDS = ("jump", "push", "break", "free")

# What royals_self_test() answers: blue's moves on the board entering starts from.
SELF_TEST = 10


class Browser:
    """One compiled module, driven the way engine.js drives it."""

    def __init__(self, path):
        self.path = path
        engine = Engine()
        self.store = Store(engine)
        self.instance = Instance(self.store, Module.from_file(engine, path), [])
        self.exports = self.instance.exports(self.store)

        missing = [name for name in
                   ("memory", "royals_board_ptr", "royals_out_ptr", "royals_load",
                    "royals_moves_from", "royals_has_moves", "royals_origins",
                    "royals_self_test", "royals_move_stride", "royals_set_push_range")
                   if name not in self.exports]
        if missing:
            raise SystemExit("%s exports none of %s" % (path, ", ".join(missing)))

        self.memory = self.exports["memory"]

    def call(self, name, *args):
        return self.exports[name](self.store, *args)

    def load(self, board):
        """Writes a position in as parsed fields, exactly as the page does."""
        payload = bytearray(SQUARES * FIELDS)
        for square, code in enumerate(board):
            s = Hasher.UNPACK[code]
            payload[square * FIELDS:(square + 1) * FIELDS] = bytes((
                s[Hasher.SIDE], s[Hasher.DRAGON], s[Hasher.SPY], s[Hasher.PAWNS],
                s[Hasher.ROYAL], s[Hasher.CAPSPY], s[Hasher.CAPPAWNS], s[Hasher.PRISFLAG]))

        self.memory.write(self.store, bytes(payload), self.call("royals_board_ptr"))
        return self.call("royals_load") == 1

    def moves_from(self, contr, origin, pris):
        count = self.call("royals_moves_from", contr, origin, 1 if pris else 0)
        at = self.call("royals_out_ptr")
        stride = self.call("royals_move_stride")
        raw = self.memory.read(self.store, at, at + count * stride)
        # The fourth byte is what the variant needs and what this test was blind to before
        # it existed: under push-range the pushes out of a square share one target and differ
        # only in how far they carry, so comparing the first three would have called six
        # different moves identical.
        return [(KINDS[raw[i * stride]], raw[i * stride + 1], raw[i * stride + 2],
                 raw[i * stride + 3])
                for i in range(count)]

    def set_push_range(self, on):
        self.call("royals_set_push_range", 1 if on else 0)

    def origins(self, contr):
        count = self.call("royals_origins", contr)
        at = self.call("royals_out_ptr")
        return list(self.memory.read(self.store, at, at + count))


def positions(key="walks"):
    """Every position the fixture walks pass through -- entered boards, then real games.

    `key` picks which set. The variant needs its own because a walk is a list of *indices*
    into listAllMoves' output and the variant's output is longer, so index 7 is a different
    move -- one set of indices cannot serve both rule sets. tests/regress.py records the two
    separately for the same reason.
    """
    with open(FIXTURES) as f:
        walks = json.load(f)[key]

    out = []
    for walk in walks:
        board = tuple(walk["start"])
        out.append(board)
        for turn, pick in enumerate(walk["choices"]):
            moves = AI.listAllMoves(board, turn % 2)
            board = AI.performOneStep(board, turn % 2, moves[pick])
            out.append(board)

    return out


def expected(board, contr, origin, pris):
    """What game.py's moves_from answers, minus the ko filter the module cannot apply."""
    return [(m[AI.MOVE_KIND], m[AI.MOVE_ORIGIN], m[AI.MOVE_TARGET],
             m[AI.MOVE_TRAVEL] if len(m) > AI.MOVE_TRAVEL else 1)
            for m in AI.listAllMoves(board, contr)
            if m[AI.MOVE_ORIGIN] == origin and bool(m[AI.MOVE_PRIS]) == pris]


def check(module, boards, push_range=False):
    """Returns a list of complaints, first few only -- they come in floods once they start.

    `push_range` runs the whole sweep again under the optional rule. Both sides are told:
    the module through its own export, and the Python engine through Engine.setPushRange --
    and if either were missed the comparison would be of two different games and would fail
    loudly, which is the point. Put back in a `finally` by the caller.
    """
    faults = []

    module.set_push_range(push_range)

    if module.call("royals_self_test") != SELF_TEST and not push_range:
        faults.append("royals_self_test answered %d, not %d"
                      % (module.call("royals_self_test"), SELF_TEST))

    asked = 0
    for n, board in enumerate(boards):
        if not module.load(board):
            faults.append("position %d was refused by royals_load" % n)
            continue

        for contr in (0, 1):
            # every square, not just the occupied ones: a module that answers a question it
            # should have no answer to is exactly as wrong as one that misses a move
            for origin in range(1, 50):
                for pris in (False, True):
                    asked += 1
                    got = module.moves_from(contr, origin, pris)
                    want = expected(board, contr, origin, pris)
                    if got != want:
                        faults.append(
                            "position %d, side %d, %s%s:\n    wasm   %s\n    python %s"
                            % (n, contr, Hasher.IndexToAlg(origin - 1),
                               " carrying" if pris else "", got, want))
                    if len(faults) >= 8:
                        return faults, asked

            want_origins = sorted({m[AI.MOVE_ORIGIN] for m in AI.listAllMoves(board, contr)})
            if module.origins(contr) != want_origins:
                faults.append("position %d, side %d: origins %s, python %s"
                              % (n, contr, module.origins(contr), want_origins))

    return faults, asked


def sweep(module, label):
    """Both rule sets, and the total asked across them."""
    was = Rules.PUSH_RANGE
    total = 0
    try:
        for push_range, key in ((False, "walks"), (True, "push_walks")):
            Rules.setPushRange(push_range)
            faults, asked = check(module, positions(key), push_range)
            total += asked
            if faults:
                return faults, total, ("the push-range variant" if push_range
                                       else "the standard rules")
    finally:
        Rules.setPushRange(was)
    return [], total, None


def main():
    fresh = sys.argv[1] if len(sys.argv) > 1 else None

    if fresh:
        faults, asked, under = sweep(Browser(fresh), "fresh")
        if faults:
            print("FAIL -- the freshly built module disagrees with the Python engine, under "
                  "%s." % (under,))
            print("       The Rust and the Python rules have diverged; the goldens are where")
            print("       to look, not this file.\n")
            print("\n".join(faults))
            return 1
        print("fresh   OK -- %s agrees with the Python engine on %d questions, both rule sets"
              % (os.path.relpath(fresh, ROOT), asked))

    faults, asked, under = sweep(Browser(SHIPPED), "shipped")
    if faults:
        print("FAIL -- static/royals.wasm disagrees with the Python engine, under %s."
              % (under,))
        if fresh:
            print("       A fresh build of the same source agrees, so the checked-in module")
            print("       is STALE. Rebuild it and commit the result:\n")
        else:
            print("       Rebuild it and commit the result:\n")
        print("         cd engine-rs")
        print("         cargo build --release --features wasm --target wasm32-unknown-unknown")
        print("         cp target/wasm32-unknown-unknown/release/royals_engine.wasm \\")
        print("            ../web/src/royals_web/static/royals.wasm\n")
        print("\n".join(faults))
        return 1

    print("shipped OK -- static/royals.wasm agrees with the Python engine on %d questions, "
          "both rule sets" % (asked,))
    return 0


if __name__ == "__main__":
    sys.exit(main())
