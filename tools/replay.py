#!/usr/bin/env python3
"""Walk through a saved Royals game in a terminal.

    python3 tools/replay.py game.txt              step through it
    python3 tools/replay.py game.txt --ply 24     one position, and stop
    python3 tools/replay.py game.txt --all        every position, in order
    python3 tools/replay.py game.txt --moves      just the move list

The file is one a browser downloaded or the desktop window saved -- they are the same
format, which is the point of there being a format at all.

This is a dozen lines of its own on top of two things that already existed: the record
walker (`royals_engine.record`) and the board printer the terminal game uses
(`apps/terminal/display.py`). It decides nothing about the rules, and could not: a record
holds moves that were found legal when they were played, and reviewing one applies them
rather than judging them. There is no ko set here, and no move generation -- see the note
at the top of royals_engine/record.py.

Stepping reads a line rather than a raw keypress. A raw keypress means termios, a saved
terminal state, and a restore in a finally block that has to survive every way out; for
"press Enter to see the next move" that is a lot of machinery to get subtly wrong in a
reader whose whole job is to be simple.
"""

import argparse
import pathlib
import sys

# display.py imports royals_lib, which sits beside it.
TERMINAL = pathlib.Path(__file__).resolve().parent.parent / "apps" / "terminal"
sys.path.insert(0, str(TERMINAL))

from royals_engine import hasher as Hasher
from royals_engine import notation as N
from royals_engine import record as Record

import display as Display


SIDE_NAMES = ("Blue", "Red")
PIECE_NAMES = {Hasher.ROYAL: "royal", Hasher.PAWNS: "pawn", Hasher.SPY: "spy"}


def describe(spot, last):
    """The header over one position: which move it is, and what got it there.

    The move number leads and the ply is in brackets after it, because they are different
    numbers and only one of them is what a person would say. A record counts the twelve
    placements as plies 1 to 12, so a game's seventh move is its nineteenth ply.
    """
    if spot.ply == 0:
        return "Start of the game — nothing entered yet.  (ply 0 of %d)" % last

    if spot.token == N.PASS:
        what = "nothing to play — passed"
    elif spot.token.startswith(N.ENTER_PREFIX):
        piece, square = N.decode_entry(spot.token)
        what = "enters a %s on %s" % (PIECE_NAMES[piece], N.square_to_alg(square).upper())
    else:
        what = spot.token

    where = ("Entering %d of %d" % (spot.turn, len(Record.ENTER_STEPS))
             if spot.phase == Record.PHASE_ENTERING else "Move %d" % spot.turn)

    return "%s   %s: %s   (ply %d of %d)" % (where, SIDE_NAMES[spot.side], what,
                                             spot.ply, last)


def show(spot, last):
    print()
    print(describe(spot, last))
    Display.DisplayHashBoard(spot.board, spot.squares)


def step_through(spots):
    """Enter for the next position, `b` for the one before, a number to jump, `q` to stop."""
    last = len(spots) - 1
    at = 0

    while True:
        show(spots[at], last)
        if at == last:
            print("(end of the game)")

        try:
            answer = input("[Enter] next   b back   <n> jump   q quit  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if answer in ("q", "quit"):
            return
        if answer in ("b", "back"):
            at = max(0, at - 1)
        elif answer.isdigit():
            at = max(0, min(last, int(answer)))
        elif answer:
            print("Not one of those.")
        elif at < last:
            at += 1
        else:
            return


def main(argv=None):
    parser = argparse.ArgumentParser(description="Walk through a saved Royals game.")
    parser.add_argument("file", help="a game record, as saved by the desktop or the browser")
    parser.add_argument("--ply", type=int, help="show one position and stop")
    parser.add_argument("--all", action="store_true", help="print every position in order")
    parser.add_argument("--moves", action="store_true", help="print the move list and stop")
    args = parser.parse_args(argv)

    try:
        text = pathlib.Path(args.file).read_text(encoding="utf-8")
        moves, spots = Record.read(text)
    except (OSError, UnicodeDecodeError) as error:
        # Named rather than traced back: the reader of a file that will not open wants to
        # know which file and why, not where in this script the open() was.
        print("Could not read %s: %s" % (args.file, error), file=sys.stderr)
        return 2
    except (N.NotationError, Record.RecordError) as error:
        print("%s is not a playable game record.\n  %s" % (args.file, error), file=sys.stderr)
        return 2

    if not moves:
        print("There are no moves in %s." % args.file, file=sys.stderr)
        return 2

    last = len(spots) - 1

    if args.moves:
        print(N.encode_game(moves))
        return 0

    if args.ply is not None:
        if not 0 <= args.ply <= last:
            print("This game has %d plies, so there is no ply %d." % (last, args.ply),
                  file=sys.stderr)
            return 2
        show(spots[args.ply], last)
        return 0

    if args.all:
        for spot in spots:
            show(spot, last)
        return 0

    print("%s — %d plies. Enter steps forward." % (args.file, last))
    step_through(spots)
    return 0


if __name__ == "__main__":
    sys.exit(main())
