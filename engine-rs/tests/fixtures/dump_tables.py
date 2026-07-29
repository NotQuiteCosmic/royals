# Dumps the Python engine's startup tables so the Rust build can be checked against them
# element for element. Run from anywhere with royals_engine importable:
#
#     python3 engine-rs/tests/fixtures/dump_tables.py
#
# Everything here is whitespace-separated integers rather than JSON, deliberately: the Rust
# side parses these in a test with nothing but the standard library, so `cargo test` needs no
# registry access and the fixtures stay diffable when a table moves.
import os

from royals_engine import ai as AI
from royals_engine import engine as Engine
from royals_engine import hasher as Hasher

HERE = os.path.dirname(os.path.abspath(__file__))


def write(name, lines):
    with open(os.path.join(HERE, name), "w") as f:
        for line in lines:
            f.write(line + "\n")
    print("wrote", name, len(lines), "lines")


# Every code unpacked: the eight fields proper, then the six derived scalars, in the order
# hasher.py lays them out. See UNPACK there -- the Rust Square struct mirrors it field for
# field, so a mismatch here is a mismatch in the encoding itself.
write("unpack.txt", [" ".join(str(int(v)) for v in row) for row in Hasher.UNPACK])

# The three rays, one line per (1-based square, direction index): square, direction, length,
# then that many 0-based board indices.
for name, table in (("jumpray", Engine.JUMPRAY),
                    ("pushray", Engine.PUSHRAY),
                    ("breakray", Engine.BREAKRAY)):
    rows = []
    for square in range(1, 50):
        for d, strand in enumerate(table[square]):
            rows.append(" ".join(str(v) for v in (square, d, len(strand)) + tuple(strand)))
    write(name + ".txt", rows)

# 50x50, indexed [origin][destination] with both 1-based and row/column 0 unused. -1 is None.
write("pushfrom.txt", [" ".join("-1" if v is None else str(v) for v in row)
                       for row in Engine.PUSHFROM])

# Jump mobility and jump distance, both measured off checkMoves in Python. The Rust side
# derives them from JUMPRAY instead -- these fixtures are what proves the two agree.
write("jumpreach.txt", ["%d %d" % (square, AI.JUMPREACH[square]) for square in range(1, 50)])
write("jumpdist.txt", [" ".join(str(v) for v in [start] + list(AI.JUMPDIST[start]))
                       for start in range(1, 50)])

write("wincodes.txt", [str(code) for code in sorted(Hasher.WIN_CODES)])
write("entering_board.txt", [" ".join(str(c) for c in Hasher.Entering_Board())])
