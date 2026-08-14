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

# How far a push travelled, for the push-range variant: `Pd4d5>3`. Absent means one square,
# which is what every push meant before the variant existed and what every token written
# before it still means.
TRAVEL_SUFFIX = ">"

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
    """Engine move tuple -> 'Jd3f5'. `None` means a pass.

    A push under the push-range variant carries how far it travelled: `Pd4d5>3`. **Absent
    means one square**, so every token ever written before that rule existed still says what
    it always said, and a reader that has not been taught the suffix fails on it rather than
    quietly dropping it -- which would replay a three-square push as a one-square one.
    """
    if move is None:
        return PASS

    try:
        origin, kind, target, pris = move[:4]
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

    # The distance rides between the destination and the prisoner star, so the star stays
    # last and `text[:-1]` on the way back in goes on meaning what it meant.
    far = ""
    if kind == "push" and len(move) > 4 and move[4] and int(move[4]) > 1:
        far = TRAVEL_SUFFIX + str(int(move[4]))

    return head + index_to_alg(target) + far + (PRIS_SUFFIX if pris else "")


def decode_move(text):
    """'Jd3f5' -> engine move tuple, or a five-tuple where a push carries a distance.

    `'--'` gives back None.
    """
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

    travel = None
    if TRAVEL_SUFFIX in rest:
        if kind != "push":
            raise NotationError("only a push travels; %r cannot carry a distance" % (kind,))
        rest, _, far = rest.partition(TRAVEL_SUFFIX)
        if not far.isdigit():
            raise NotationError("%r is not a push distance" % (far,))
        travel = int(far)
        if travel < 2:
            # 1 is spelled by leaving the suffix off, and two spellings of one move would
            # mean a record that does not round-trip to itself.
            raise NotationError("a distance of %d is written by omitting the suffix" % (travel,))

    if len(rest) != 2:
        raise NotationError("%r does not name a destination square" % (rest,))

    if travel is None:
        return (origin, kind, alg_to_index(rest), pris)
    return (origin, kind, alg_to_index(rest), pris, travel)


# ---------------------------------------------------------------------------
# Moves, as JSON
# ---------------------------------------------------------------------------

def move_to_json(move):
    """Engine move tuple -> a plain dict, ready for a websocket frame."""
    if move is None:
        return {"kind": "pass"}

    origin, kind, target, pris = move[:4]
    if kind == "break":
        return {"kind": "break",
                "origin": square_to_alg(origin),
                "dir": DIR_LETTERS[target]}

    out = {"kind": kind,
           "origin": square_to_alg(origin),
           "target": index_to_alg(target),
           "pris": bool(pris)}
    # How far a push travelled, present only where it is more than one square -- so a
    # standard game's frame is the frame it has always been and no client has to learn a
    # field it will never see.
    if kind == "push" and len(move) > 4 and move[4] and int(move[4]) > 1:
        out["travel"] = int(move[4])
    return out


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

    target = alg_to_index(obj.get("target"))
    pris = bool(obj.get("pris", False))

    far = obj.get("travel")
    if far is None:
        return (origin, kind, target, pris)

    # Assume every field is hostile: a distance is a small whole number on a push and
    # nothing else. Absent means one square, so a client that has never heard of the
    # variant sends what it always sent and means what it always meant.
    if kind != "push":
        raise NotationError("only a push travels; %r cannot carry a distance" % (kind,))
    if not isinstance(far, int) or isinstance(far, bool) or far < 2 or far > 6:
        raise NotationError("%r is not a push distance (2 to 6, or absent for one)" % (far,))
    return (origin, kind, target, pris, far)


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


# ---------------------------------------------------------------------------
# Whole games, as text
# ---------------------------------------------------------------------------
# A game record is its move list and nothing else. Every front end already keeps one --
# the server in a database column, the desktop as it plays -- and every one of them is the
# same list of RAN tokens, so a file is that list with somewhere to put a date on it.
#
# These are string functions and do no file I/O, deliberately. The engine is imported by a
# web worker and compiled into a browser, neither of which has a filesystem to speak of;
# opening a file is each front end's business. That is the same line print() is on the
# wrong side of.

MAGIC = "# Royals 1"
COMMENT = "#"

# Long enough that a wrapped line still holds several plies, short enough to read in a
# terminal and to diff sensibly. Nothing parses the line breaks -- decode splits on
# whitespace -- so this is purely how it looks.
WRAP = 72

PLY_PASS, PLY_ENTER, PLY_MOVE = "pass", "enter", "move"


def decode_ply(token):
    """One token from a move list -> (kind, payload).

        "--"      ->  ("pass",  None)
        "@Rd4"    ->  ("enter", (piece, 1-based square))
        "Jd3f5"   ->  ("move",  move tuple)

    The three are told apart by their first character and by nothing else, which is why
    the prefixes had to be distinct in the first place. Every reader of a game record --
    replay, the review UI, the terminal viewer -- needs exactly this dispatch, and one
    that is written out once cannot be got subtly different in three places.
    """
    if not isinstance(token, str):
        raise NotationError("a ply is a string, got %r" % (type(token).__name__,))

    text = token.strip()
    if text == PASS:
        return PLY_PASS, None
    if text.startswith(ENTER_PREFIX):
        return PLY_ENTER, decode_entry(text)
    return PLY_MOVE, decode_move(text)


def encode_game(moves, notes=(), board=None, turn=None, rules=()):
    """A move list -> the text of a game record.

    `moves` is a list of RAN tokens -- exactly what `Game.moves` holds and what the
    database column stores -- and every one is decoded on the way past. That check is the
    point of encoding here rather than joining with spaces at the call site: a front end
    that has just learned to write its moves down gets told at save time that it wrote one
    wrong, instead of producing a file that fails to load later with nothing left to say
    which ply was at fault.

    `notes` are free-text lines for the header. They are dropped on read, so anything a
    reader must have has to be in the tokens -- which it is for an ordinary game: the
    entering placements are plies like any other, and a pass is written down rather than
    implied.

    **`board` and `turn` are the exception, and the reason they are not notes.** A game that
    began from a position set up by hand has no entering plies, so the move list alone no
    longer says which side played which ply -- `record.side_of_ply` derives that from the
    index and from a twelve-ply opening that this game never had. Those two facts go in real
    `turn` and `board` lines, in the same syntax `encode_position` writes, and a reader that
    has not been taught about them fails on the first one instead of quietly replaying the
    game from the entering board as somebody else's. They come as a pair or not at all.

    A game that did start with entering writes neither line and its file is byte for byte
    the file this function has always produced.

    `rules` names the optional rules the game was played under -- see RULES_KEY. A game under
    the standard rules passes none and writes no line.
    """
    if (board is None) != (turn is None):
        raise NotationError("a start position is a board and a side to move, or neither")

    for rule in rules:
        if rule not in KNOWN_RULES:
            raise NotationError("%r is not a rule this engine knows (one of %s)"
                                % (rule, ", ".join(KNOWN_RULES)))

    tokens = []
    for index, token in enumerate(moves):
        try:
            decode_ply(token)
        except NotationError as exc:
            raise NotationError("ply %d (%r) is not playable notation: %s"
                                % (index + 1, token, exc)) from exc
        tokens.append(token.strip())

    lines = [MAGIC]
    for note in notes:
        lines.extend(_comment(note))
    if rules:
        lines.append("%s %s" % (RULES_KEY, " ".join(rules)))
    if board is not None:
        if turn not in SIDE_TO_NAME:
            raise NotationError("side is 0 or 1, got %r" % (turn,))
        lines.append("%s %s" % (TURN_KEY, SIDE_TO_NAME[turn]))
        lines.append("%s %s" % (BOARD_KEY, encode_board(board)))
    lines.extend(_wrap(tokens))
    return "\n".join(lines) + "\n"


def _comment(note):
    """A note as comment lines, and never as anything else.

    splitlines rather than split("\\n"): it also breaks on \\r, \\x0b, \\x0c, \\x1c-\\x1e and
    U+2028/9. A note is often something a person typed, and one that smuggled a line break
    past this would land in the file as a line of tokens.
    """
    for line in str(note).splitlines() or [""]:
        yield ("%s %s" % (COMMENT, line)).rstrip()


def _wrap(tokens):
    line = []
    for token in tokens:
        if line and len(" ".join(line)) + 1 + len(token) > WRAP:
            yield " ".join(line)
            line = []
        line.append(token)
    if line:
        yield " ".join(line)


def decode_record(text):
    """Game record text -> (moves, start board or None, side or None, the rules it names).

    Comments are dropped and everything that is not a header line is split on whitespace,
    which means the move list is precisely what `moves.split()` gives in game.replay and
    what the database column holds. The file and the stored record are therefore the same
    string, and round-tripping is a property of the format rather than an agreement between
    two pieces of code.

    Every token is decoded before returning, so a truncated, corrupted or hand-edited file
    fails here -- with the offending ply named -- rather than replaying into a position
    nobody played to.

    **The two header keywords are why this is line-aware where it used to split the whole
    file at once.** `turn red` and `board d4:...` say where a game that did not begin with
    entering began; see encode_game. A line is a header line if its first word is one of
    them, and a move token can never be either, so nothing is ambiguous. Their order is not
    enforced, for the reason decode_position does not enforce its own: a hand-edited file
    should not fail for something that does not matter.
    """
    if not isinstance(text, str):
        raise NotationError("a game record is text, got %r" % (type(text).__name__,))

    tokens = []
    board = None
    turn = None
    rules = ()

    for line in text.splitlines():
        # A '#' can never occur inside a token, so anything from one to the end of the
        # line is a comment wherever it appears. Strictly more permissive than
        # whole-line comments, and unambiguous for the same reason.
        body = line.split(COMMENT, 1)[0]
        head = body.split(None, 1)
        if not head:
            continue

        key = head[0].lower()
        if key == TURN_KEY:
            if turn is not None:
                raise NotationError("two `turn` lines")
            if len(head) < 2:
                raise NotationError("`turn` needs a side: turn blue, or turn red")
            name = head[1].strip().lower()
            if name not in NAME_TO_SIDE:
                raise NotationError("%r is not a side -- blue or red" % (head[1].strip(),))
            turn = NAME_TO_SIDE[name]
            continue

        if key == BOARD_KEY:
            if board is not None:
                raise NotationError("two `board` lines -- a game begins from one position")
            if len(head) < 2:
                raise NotationError("`board` needs a position after it")
            board = decode_board(head[1].strip())
            continue

        if key == RULES_KEY:
            if rules:
                raise NotationError("two `rules` lines")
            named = head[1].split() if len(head) > 1 else []
            if not named:
                raise NotationError("`rules` needs at least one rule after it")
            for rule in named:
                if rule.lower() not in KNOWN_RULES:
                    # Loudly, and this is the point of the line existing: a game played under
                    # a rule this engine does not have is not a game it can replay, and
                    # guessing produces a convincing wrong answer.
                    raise NotationError(
                        "this record was played under %r, which this engine does not know "
                        "(it knows %s)" % (rule, ", ".join(KNOWN_RULES)))
            rules = tuple(r.lower() for r in named)
            continue

        tokens.extend(body.split())

    if (board is None) != (turn is None):
        raise NotationError(
            "a game that begins from a position needs both a `board` line and a `turn` "
            "line, and this one has only %s" % (TURN_KEY if turn is not None else BOARD_KEY,))

    for index, token in enumerate(tokens):
        try:
            decode_ply(token)
        except NotationError as exc:
            raise NotationError("ply %d (%r) is not playable notation: %s"
                                % (index + 1, token, exc)) from exc

    return tokens, board, turn, rules


def decode_game(text):
    """The text of an ordinary game record -> its move list.

    The narrow reader, kept because most callers only ever want the moves and because a
    caller that has *not* been taught about start positions must not be handed one silently.
    A record that carries a `board` line is refused here rather than having its board
    dropped: dropping it would replay the game from the entering board, which is a different
    game and a convincing one. Use decode_record for both kinds.
    """
    moves, board, _turn, rules = decode_record(text)
    if rules:
        raise NotationError("this record was played under %s, which decode_game cannot "
                            "represent -- read it with decode_record" % (", ".join(rules),))
    if board is not None:
        raise NotationError("this record begins from a position set up by hand, which "
                            "decode_game cannot represent -- read it with decode_record")
    return moves


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


# ---------------------------------------------------------------------------
# Boards, as text
# ---------------------------------------------------------------------------
# The packed form above is for storage; this one is for a person. A position somebody
# writes by hand -- an endgame study, a bug reduced to four pieces -- has to be readable
# and diffable, and 98 bytes of little-endian shorts is neither.
#
# **The square syntax here is not new.** It is exactly what `tests/regress.py`'s boardText
# emits into every line of golden_moves.txt, and there has been a decoder for it since the
# Rust port: engine-rs/tests/golden_search.rs parses it to check the search golden. What
# was missing was the Python half, so the format could be written here and read only over
# there. tests/test_position_setup.py pins the two encoders together across a sweep,
# because a drift would break golden_search.rs and say so in a place nobody would connect
# back to here.
#
# Eight fields per square, in Parse_Space order -- which is Build_Space's argument list, so
# decoding a square is Build_Space(*fields) and inherits its overfull check for nothing.

BOARD_EMPTY = "-empty-"
SQUARE_SEP = "|"
FIELD_SEP = ","
ALG_SEP = ":"

POSITION_MAGIC = "# Royals position 1"
TURN_KEY = "turn"

# A position file's board line is bare, because there is nothing else it could be. A game
# record's cannot be: its other bare lines are the move list. So the keyed form exists for
# encode_game, and decode_position accepts it too -- one spelling that reads the same in
# both files is worth more than the two characters it costs the format that could go
# without it.
BOARD_KEY = "board"

# Which optional rules a game was played under, as a `rules push-range` line. A game under the
# standard rules writes nothing, so its file is byte for byte the file it has always been.
#
# **It is a real line rather than a note for the same reason `turn` and `board` are.** The move
# list cannot say which rules produced it: replay a push-range game with the variant off and
# every ranged push is clamped to one square -- a plausible board, a different game, and
# nothing anywhere to say so. A reader that does not know a name fails on it instead.
RULES_KEY = "rules"
RULE_PUSH_RANGE = "push-range"
KNOWN_RULES = (RULE_PUSH_RANGE,)

# The engine's own names for the sides, and what a file is written with. The desktop calls
# them White and Black on its buttons, so both are accepted on the way in -- a person
# hand-editing a file should not have to know that the window renamed them.
SIDE_TO_NAME = {0: "blue", 1: "red"}
NAME_TO_SIDE = {"blue": 0, "red": 1, "white": 0, "black": 1}


def encode_board(board):
    """Board tuple -> `d3:0,1,0,0,0,0,0,0|d4:0,0,1,4,1,0,0,0`.

    Empty squares are left out rather than written as zeroes: a position is usually a
    handful of pieces, and 44 empties would bury them. An entirely empty board is the
    `-empty-` sentinel, because a zero-length line is not obviously a board at all.
    """
    validate_board(board)

    squares = []
    for index, s in enumerate(Hasher.Parse_Board(board)):
        fields = s[:Hasher.FIELDS]
        if not any(fields):
            continue
        squares.append(index_to_alg(index) + ALG_SEP
                       + FIELD_SEP.join(str(f) for f in fields))

    return SQUARE_SEP.join(squares) if squares else BOARD_EMPTY


def decode_board(text):
    """`d3:0,1,...` -> a board tuple, validated.

    Every failure is a NotationError naming the square at fault, because the input is a
    file somebody may well have typed. Build_Space's own ValueError for an overfull square
    is caught and reworded for the same reason -- "square overfull" with no square in it is
    not much help when the line holds a dozen of them.
    """
    if not isinstance(text, str):
        raise NotationError("a board is text, got %r" % (type(text).__name__,))

    body = text.strip()
    if not body:
        raise NotationError("a board line is empty")
    if body == BOARD_EMPTY:
        return Hasher.EMPTY_BOARD

    cells = list(Hasher.EMPTY_BOARD)
    seen = set()

    for chunk in body.split(SQUARE_SEP):
        piece = chunk.strip()
        if not piece:
            raise NotationError("empty square in %r -- two separators together?" % (body,))
        if ALG_SEP not in piece:
            raise NotationError("%r is not `square:fields`" % (piece,))

        alg, _, raw = piece.partition(ALG_SEP)
        square = alg_to_square(alg.strip())
        if square in seen:
            raise NotationError("%s appears twice" % (alg.strip().lower(),))
        seen.add(square)

        parts = raw.split(FIELD_SEP)
        if len(parts) != Hasher.FIELDS:
            raise NotationError("%s has %d fields, expected %d (%s)"
                                % (alg, len(parts), Hasher.FIELDS,
                                   "side,dragon,spy,pawns,royal,capSpy,capPawns,prisFlag"))

        fields = []
        for part in parts:
            value = part.strip()
            # int() takes "+3", " 3 " and unicode digits; a field is a plain small number
            # and anything else is a typo worth naming rather than quietly accepting.
            if not value.isdigit():
                raise NotationError("%s has field %r, which is not a whole number"
                                    % (alg, part))
            fields.append(int(value))

        try:
            cells[square - 1] = Hasher.Build_Space(*fields)
        except ValueError as exc:
            raise NotationError("%s is not a square this game can hold: %s" % (alg, exc)) from exc

    return validate_board(tuple(cells))


def encode_position(board, side, notes=()):
    """A board and whose turn it is -> the text of a position file.

    `notes` are free-text header lines and are dropped on read, exactly as encode_game's
    are. Everything a reader needs is on the two real lines.
    """
    if side not in SIDE_TO_NAME:
        raise NotationError("side is 0 or 1, got %r" % (side,))

    lines = [POSITION_MAGIC]
    for note in notes:
        lines.extend(_comment(note))
    lines.append("%s %s" % (TURN_KEY, SIDE_TO_NAME[side]))
    lines.append(encode_board(board))
    return "\n".join(lines) + "\n"


def decode_position(text):
    """The text of a position file -> (board, side to move).

    Order of the two lines is not enforced -- a `turn` line and a board line are told apart
    by their first word, and requiring them in a fixed order would only make a hand-edited
    file fail for a reason that does not matter.
    """
    if not isinstance(text, str):
        raise NotationError("a position is text, got %r" % (type(text).__name__,))

    turn = None
    board_line = None

    for line in text.splitlines():
        # Same comment rule as decode_game: a '#' can never occur inside a token, so it
        # opens a comment wherever it appears.
        body = line.split(COMMENT, 1)[0].strip()
        if not body:
            continue

        head = body.split(None, 1)
        if head[0].lower() == TURN_KEY:
            if turn is not None:
                raise NotationError("two `turn` lines")
            if len(head) < 2:
                raise NotationError("`turn` needs a side: turn blue, or turn red")
            name = head[1].strip().lower()
            if name not in NAME_TO_SIDE:
                raise NotationError("%r is not a side -- blue or red" % (head[1].strip(),))
            turn = NAME_TO_SIDE[name]
            continue

        if board_line is not None:
            raise NotationError("two board lines -- a position file holds one position")
        # `board d4:...` as well as a bare `d4:...`, so a line lifted out of a game record
        # reads here unchanged.
        body = head[1].strip() if head[0].lower() == BOARD_KEY and len(head) > 1 else body
        board_line = body

    if board_line is None:
        raise NotationError("no board in this file")
    if turn is None:
        raise NotationError("no `turn` line -- a position has to say who moves")

    return decode_board(board_line), turn
