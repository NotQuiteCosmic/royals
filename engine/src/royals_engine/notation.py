"""Reading and writing moves and boards as text and bytes.

Everything that crosses a boundary -- a websocket frame, a database column, a game
record someone pastes into a forum -- goes through this module, and nothing outside it
is allowed to know how a move is packed. That matters more than it sounds, because the
engine's move tuple mixes two numbering schemes:

    move = (origin, kind, target, movingPris)

    origin      1-BASED square
    target      0-BASED square, for "jump", "push" and "free"
    target      an index into Engine.pushDirs, for "break"
    movingPris  whether the mover drags its prisoners along

That `+1` is real -- ai.performOneStep does `move[MOVE_TARGET] + 1` for the first three
kinds and passes target through untouched for a break. Every off-by-one this codebase
can produce lives in that asymmetry, so it is spelled out once, here, and the rule is
that no other module performs arithmetic on a square.

Two text forms:

    RAN, one token per ply, for archives and move lists
        Jd3f5     jump            Bd3r    break, direction spelled as a letter
        Pd3d4     push            @Rd3    enter a royal on d3
        Fd3d4     free            --      pass
        Jd3f5*    ... carrying prisoners

    JSON, for the wire, because a client should never have to write a parser
        {"kind": "jump", "origin": "d3", "target": "f5", "pris": false}

Direction letters are DERIVED from Engine.HEADINGS rather than written down, so that if
the direction table is ever reordered the notation follows it instead of silently
disagreeing with it.
"""

import struct

from royals_engine import hasher as Hasher
from royals_engine import engine as Engine


# ---------------------------------------------------------------------------
# Alphabets
# ---------------------------------------------------------------------------

# ("d", "u", "l", "r") -- indexed identically to Engine.pushDirs, by construction.
DIR_LETTERS = tuple(h[0] for h in Engine.HEADINGS)
assert len(set(DIR_LETTERS)) == len(DIR_LETTERS), "heading initials are not unique"

LETTER_TO_DIR = {letter: i for i, letter in enumerate(DIR_LETTERS)}

PIECE_TO_LETTER = {Hasher.ROYAL: "R", Hasher.PAWNS: "P", Hasher.SPY: "S"}
LETTER_TO_PIECE = {v: k for k, v in PIECE_TO_LETTER.items()}

KIND_TO_LETTER = {"jump": "J", "push": "P", "free": "F", "break": "B"}
LETTER_TO_KIND = {v: k for k, v in KIND_TO_LETTER.items()}

PASS = "--"
PRIS_SUFFIX = "*"
ENTER_PREFIX = "@"

# A square code occupies 13 bits, and Hasher.UNPACK is exactly 8192 entries long.
SQUARE_CODE_LIMIT = 8192
BOARD_SQUARES = 49


class NotationError(ValueError):
    """Malformed notation. Raised rather than guessed at -- this parses untrusted input."""


# ---------------------------------------------------------------------------
# Squares
# ---------------------------------------------------------------------------

def square_to_alg(square):
    """1-based square number -> 'd3'."""
    if not 1 <= square <= BOARD_SQUARES:
        raise NotationError("square %r is off the board (1..49)" % (square,))
    return Hasher.IndexToAlg(square - 1)


def alg_to_square(text):
    """'d3' -> 1-based square number."""
    if not isinstance(text, str):
        raise NotationError("square must be a string, got %r" % (type(text).__name__,))
    square = Hasher.AlgebraToSquare(text.lower())
    # AlgebraToSquare answers 0 for anything that isn't a square, by design -- it was
    # written to be handed whatever a player typed.
    if not square:
        raise NotationError("%r is not a square" % (text,))
    return square


def index_to_alg(index):
    """0-based square index -> 'd3'. For move targets, which are 0-based."""
    return square_to_alg(index + 1)


def alg_to_index(text):
    """'d3' -> 0-based square index. For move targets, which are 0-based."""
    return alg_to_square(text) - 1


# ---------------------------------------------------------------------------
# Moves, as RAN text
# ---------------------------------------------------------------------------

def encode_move(move):
    """Engine move tuple -> 'Jd3f5'. `None` means a pass."""
    if move is None:
        return PASS

    try:
        origin, kind, target, pris = move
    except (TypeError, ValueError):
        raise NotationError("not a move tuple: %r" % (move,))

    if kind not in KIND_TO_LETTER:
        raise NotationError("unknown move kind %r" % (kind,))

    head = KIND_TO_LETTER[kind] + square_to_alg(origin)

    if kind == "break":
        if not 0 <= target < len(DIR_LETTERS):
            raise NotationError("break direction %r is not a heading" % (target,))
        # A break scatters the whole square, so it has no carrying-prisoners variant.
        return head + DIR_LETTERS[target]

    return head + index_to_alg(target) + (PRIS_SUFFIX if pris else "")


def decode_move(text):
    """'Jd3f5' -> engine move tuple. `'--'` gives back None."""
    if not isinstance(text, str):
        raise NotationError("notation must be a string, got %r" % (type(text).__name__,))

    text = text.strip()
    if text == PASS:
        return None
    if text.startswith(ENTER_PREFIX):
        raise NotationError("%r is an entering placement -- use decode_entry" % (text,))

    pris = text.endswith(PRIS_SUFFIX)
    if pris:
        text = text[:-1]

    if len(text) < 4:
        raise NotationError("%r is too short to be a move" % (text,))

    kind = LETTER_TO_KIND.get(text[0])
    if kind is None:
        raise NotationError("%r does not start with a move kind (one of %s)"
                            % (text, "".join(sorted(LETTER_TO_KIND))))

    origin = alg_to_square(text[1:3])
    rest = text[3:]

    if kind == "break":
        if pris:
            raise NotationError("a break scatters the whole square; it cannot carry prisoners")
        if rest not in LETTER_TO_DIR:
            raise NotationError("%r is not a direction (one of %s)"
                                % (rest, "".join(DIR_LETTERS)))
        return (origin, kind, LETTER_TO_DIR[rest], False)

    if len(rest) != 2:
        raise NotationError("%r does not name a destination square" % (rest,))

    return (origin, kind, alg_to_index(rest), pris)


# ---------------------------------------------------------------------------
# Moves, as JSON
# ---------------------------------------------------------------------------

def move_to_json(move):
    """Engine move tuple -> a plain dict, ready for a websocket frame."""
    if move is None:
        return {"kind": "pass"}

    origin, kind, target, pris = move
    if kind == "break":
        return {"kind": "break",
                "origin": square_to_alg(origin),
                "dir": DIR_LETTERS[target]}

    return {"kind": kind,
            "origin": square_to_alg(origin),
            "target": index_to_alg(target),
            "pris": bool(pris)}


def move_from_json(obj):
    """A dict off the wire -> engine move tuple. Assume every field is hostile."""
    if not isinstance(obj, dict):
        raise NotationError("a move must be an object, got %r" % (type(obj).__name__,))

    kind = obj.get("kind")
    if kind == "pass":
        return None
    if kind not in KIND_TO_LETTER:
        raise NotationError("unknown move kind %r" % (kind,))

    origin = alg_to_square(obj.get("origin"))

    if kind == "break":
        letter = obj.get("dir")
        if letter not in LETTER_TO_DIR:
            raise NotationError("%r is not a direction" % (letter,))
        return (origin, kind, LETTER_TO_DIR[letter], False)

    return (origin, kind, alg_to_index(obj.get("target")), bool(obj.get("pris", False)))


# ---------------------------------------------------------------------------
# Entering placements
# ---------------------------------------------------------------------------
# The entering phase is twelve placements, not moves, but they belong in the same move
# list: replay stays uniform, and the opening gets a review UI for free.

def encode_entry(piece, square):
    """(Hasher.ROYAL, 25) -> '@Rd4'."""
    if piece not in PIECE_TO_LETTER:
        raise NotationError("%r is not an enterable piece" % (piece,))
    return ENTER_PREFIX + PIECE_TO_LETTER[piece] + square_to_alg(square)


def decode_entry(text):
    """'@Rd4' -> (piece, 1-based square)."""
    if not isinstance(text, str):
        raise NotationError("notation must be a string, got %r" % (type(text).__name__,))

    text = text.strip()
    if len(text) != 4 or text[0] != ENTER_PREFIX:
        raise NotationError("%r is not an entering placement" % (text,))

    piece = LETTER_TO_PIECE.get(text[1])
    if piece is None:
        raise NotationError("%r is not a piece (one of %s)"
                            % (text[1], "".join(sorted(LETTER_TO_PIECE))))

    return piece, alg_to_square(text[2:])


def entry_to_json(piece, square):
    return {"kind": "enter", "piece": PIECE_TO_LETTER[piece], "square": square_to_alg(square)}


def entry_from_json(obj):
    if not isinstance(obj, dict) or obj.get("kind") != "enter":
        raise NotationError("not an entering placement: %r" % (obj,))
    piece = LETTER_TO_PIECE.get(obj.get("piece"))
    if piece is None:
        raise NotationError("%r is not a piece" % (obj.get("piece"),))
    return piece, alg_to_square(obj.get("square"))


# ---------------------------------------------------------------------------
# Boards
# ---------------------------------------------------------------------------
# struct, never pickle. A board is 49 unsigned shorts -- 98 bytes -- and unpacking one
# cannot execute anything. pickle.loads on bytes from a database or a cache is remote
# code execution, and a board being "just a tuple" is exactly the shortcut that invites
# it. tests/test_engine_purity.py bans importing pickle from the engine at all.

BOARD_STRUCT = struct.Struct("<%dH" % BOARD_SQUARES)
assert BOARD_STRUCT.size == 98


def pack_board(board):
    """Board tuple -> 98 bytes."""
    validate_board(board)
    return BOARD_STRUCT.pack(*board)


def unpack_board(blob):
    """98 bytes -> board tuple, validated.

    "<49H" cannot produce a negative value, and that is load-bearing: Hasher.UNPACK has
    8192 entries, so a code of 8192 or more raises IndexError, but a NEGATIVE code would
    quietly index from the end of the table and hand back a real-looking square. Codes
    arriving as JSON integers have no such guarantee, which is why validate_board checks
    the range explicitly rather than trusting the reader.
    """
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise NotationError("a packed board must be bytes, got %r" % (type(blob).__name__,))
    if len(blob) != BOARD_STRUCT.size:
        raise NotationError("a packed board is %d bytes, got %d"
                            % (BOARD_STRUCT.size, len(blob)))

    board = BOARD_STRUCT.unpack(bytes(blob))
    validate_board(board)
    return board


def validate_board(board):
    """Reject anything that is not a board this engine could have produced.

    Run on everything read back from storage, not only on what arrives from a client --
    it costs microseconds and catches corruption and tampering with the same check.
    """
    if not isinstance(board, tuple):
        raise NotationError("a board is a tuple, got %r" % (type(board).__name__,))
    if len(board) != BOARD_SQUARES:
        raise NotationError("a board has %d squares, got %d" % (BOARD_SQUARES, len(board)))

    for i, code in enumerate(board):
        if not isinstance(code, int) or isinstance(code, bool):
            raise NotationError("square %s holds %r, which is not an integer"
                                % (index_to_alg(i), code))
        if not 0 <= code < SQUARE_CODE_LIMIT:
            raise NotationError("square %s has code %d, outside 0..%d"
                                % (index_to_alg(i), code, SQUARE_CODE_LIMIT - 1))

    _check_piece_counts(board)
    return board


# What each side owns, once and only once. The dragon starts on the board and can never
# be captured or stacked, so it is exactly one; the rest are capped rather than fixed
# because during the entering phase they have not all arrived yet.
PIECE_LIMITS = {"spy": 1, "pawns": 4, "royal": 1, "dragon": 1}


def _check_piece_counts(board):
    counts = [dict.fromkeys(PIECE_LIMITS, 0) for _ in (0, 1)]

    for code in board:
        if not code:
            continue
        s = Hasher.Parse_Space(code)
        side = s[Hasher.SIDE]

        counts[side]["dragon"] += s[Hasher.DRAGON]
        counts[side]["spy"] += s[Hasher.SPY]
        counts[side]["pawns"] += s[Hasher.PAWNS]
        counts[side]["royal"] += s[Hasher.ROYAL]

        # Prisoners on a square belong to the side that does NOT hold it.
        other = 1 - side
        counts[other]["spy"] += s[Hasher.CAPSPY]
        counts[other]["pawns"] += s[Hasher.CAPPAWNS]

    for side in (0, 1):
        for piece, limit in PIECE_LIMITS.items():
            got = counts[side][piece]
            if got > limit:
                raise NotationError(
                    "%s has %d %s on the board, which is %d too many"
                    % ("blue" if side == 0 else "red", got, piece, got - limit))

    return counts
