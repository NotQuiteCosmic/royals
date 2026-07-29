//! The board layer: how a square is encoded, how the geometry wraps, and the two questions
//! that need neither move generation nor search to answer. No game rules live here.
//!
//! A board is 49 squares in reading order, each 13 bits, exactly as `hasher.py` packs them:
//!
//! ```text
//!   bit  0     occupied
//!   bit  1     controlling side (0 blue, 1 red)
//!   bit  2     dragon
//!   bit  3     spy
//!   bits 4-6   pawns, 0 to 4
//!   bit  7     royal
//!   bit  8     any prisoners
//!   bit  9     captured spy
//!   bits 10-12 captured pawns, 0 to 4
//! ```
//!
//! [`build_space`] zeroes everything past a dragon and everything past the prisoner flag, so
//! one arrangement of pieces has exactly one encoding. That is not tidiness: it is what lets
//! a board stand as its own name in the ko history and in the transposition table, and the
//! search leans on it in both places.
//!
//! The board is `[u16; 49]` and `Copy` -- 98 bytes of memcpy, no allocation. The Python side
//! uses tuples for the same reason (hashable, and no caller can edit one from under
//! another); here immutability is the type system's job and every executor still returns a
//! new board rather than mutating one.

/// Squares on a board. Seven by seven, and the edges wrap.
pub const BOARD_SQUARES: usize = 49;

/// A packed board: 49 square codes, 0-based in reading order.
pub type Board = [u16; BOARD_SQUARES];

pub const EMPTY_BOARD: Board = [0; BOARD_SQUARES];

/// Every code a square can hold, so tables over them can be built by index.
pub const CODE_RANGE: usize = 1 << 13;

// Where each field starts, and how wide it is where that isn't one bit.
pub const BIT_OCCUPIED: u16 = 1;
pub const BIT_SIDE: u16 = 2;
pub const BIT_DRAGON: u16 = 4;
pub const BIT_SPY: u16 = 8;
pub const SHIFT_PAWNS: u32 = 4;
pub const BIT_ROYAL: u16 = 128;
pub const BIT_PRIS: u16 = 256;
pub const BIT_CAPSPY: u16 = 512;
pub const SHIFT_CAPPAWNS: u32 = 10;

/// The four jump directions, and the four push directions. A direction is carried as an
/// index into one of these wherever it is being used rather than displayed.
pub const JUMP_DIRS: [[i32; 2]; 4] = [[1, 1], [1, -1], [-1, 1], [-1, -1]];
pub const PUSH_DIRS: [[i32; 2]; 4] = [[0, 1], [0, -1], [-1, 0], [1, 0]];

/// What each push direction is called, indexed the same way. `direction[1]` moves along the
/// ranks and rank 1 prints at the top, so +1 on that axis reads as "down".
pub const HEADINGS: [&str; 4] = ["down", "up", "left", "right"];

/// The most a jump can travel: a stack is at most the spy, four pawns and the royal.
pub const MAX_STACK: usize = 6;

/// How far a push can reach before the runaway backstop gives up. The board wraps, so the
/// line itself is endless -- this is the point past which something has gone wrong rather
/// than a limit of the geometry.
pub const PUSH_REACH: usize = 21;

/// The most pieces a break can scatter: a full stack of six plus the five prisoners it could
/// be holding. Weight counts prisoners here, unlike everywhere jumps are concerned.
pub const MAX_SCATTER: usize = 11;

/// A square, unpacked into plain numbers so move code never has to touch bits.
///
/// The first eight fields are the encoding; the six after them are derived -- not new facts,
/// just answers the eight already give, precomputed for all 8,192 codes in
/// [`crate::tables::UNPACK`]. Move generation reads a square's weight and occupancy several
/// times per step of every strand it walks, and in Python those were over a million calls
/// per depth-6 search; here they are field reads on a value that was fetched anyway.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub struct Square {
    /// 0 blue, 1 red.
    pub side: u8,
    pub dragon: u8,
    pub spy: u8,
    pub pawns: u8,
    pub royal: u8,
    pub cap_spy: u8,
    pub cap_pawns: u8,
    /// Carried separately from the captured counts because a square can be marked as holding
    /// prisoners while holding nobody -- a side captured with no pieces left. Dropping it
    /// would change what "has prisoners" answers and break the [`build_space`] round trip.
    pub pris_flag: u8,

    /// Anything standing or held here at all.
    pub occupied: bool,
    /// Everything on the square, prisoners included.
    pub weight: u8,
    /// Carried prisoners count *against* the stack, so this goes negative for a stack holding
    /// more than it has.
    pub strength: i8,
    /// Just the pieces standing, ignoring anyone they are holding.
    pub captors: u8,
    pub pris_count: u8,
    /// A head count of the stack -- what the evaluator means by its size. The dragon counts
    /// as one here, not three.
    pub my_pieces: u8,
}

/// Unpacks a square code into [`Square`], derived scalars and all. Inverse of
/// [`build_space`] over the fields proper. Every field is 0 on an empty square.
///
/// This is Python's `unpackCode` and `unpackRow` in one: they are separate there only
/// because the second is what fills the table.
pub fn unpack_code(code: u16) -> Square {
    let mut s = Square::default();

    if code & BIT_OCCUPIED == 0 {
        return s;
    }

    s.side = ((code & BIT_SIDE) >> 1) as u8;

    if code & BIT_DRAGON != 0 {
        // a dragon carries nothing else
        s.dragon = 1;
    } else {
        s.spy = ((code & BIT_SPY) >> 3) as u8;
        s.pawns = ((code >> SHIFT_PAWNS) & 7) as u8;
        s.royal = ((code & BIT_ROYAL) >> 7) as u8;

        // no prisoners, so nothing past the flag is set
        if code & BIT_PRIS != 0 {
            s.cap_spy = ((code & BIT_CAPSPY) >> 9) as u8;
            s.cap_pawns = ((code >> SHIFT_CAPPAWNS) & 7) as u8;
            s.pris_flag = 1;
        }
    }

    s.occupied = (s.dragon | s.spy | s.pawns | s.royal | s.cap_spy | s.cap_pawns) != 0;
    s.weight = space_weight(&s);
    s.strength = space_strength(&s);
    s.captors = space_captors(&s);
    s.pris_count = s.cap_spy + s.cap_pawns;
    s.my_pieces = s.dragon + s.spy + s.pawns + s.royal;
    s
}

/// A dragon is worth 3 whichever way you count it, which is why it short-circuits all three.
pub fn space_weight(s: &Square) -> u8 {
    if s.dragon != 0 {
        return 3;
    }
    s.spy + s.pawns + s.royal + s.cap_spy + s.cap_pawns
}

pub fn space_strength(s: &Square) -> i8 {
    if s.dragon != 0 {
        return 3;
    }
    (s.spy + s.pawns + s.royal) as i8 - (s.cap_spy + s.cap_pawns) as i8
}

pub fn space_captors(s: &Square) -> u8 {
    if s.dragon != 0 {
        return 3;
    }
    s.spy + s.pawns + s.royal
}

/// What [`build_space`] refuses to encode. A side has one spy, one royal and four pawns, so
/// nothing should ever run over its field. Shifting an oversized value in would bleed it
/// quietly into the next field and the board would go wrong somewhere else entirely; the
/// Python side raises `ValueError` here for exactly that reason.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct SquareOverfull {
    pub spy: u8,
    pub pawns: u8,
    pub royal: u8,
    pub cap_spy: u8,
    pub cap_pawns: u8,
}

impl std::fmt::Display for SquareOverfull {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "square overfull: spy {} pawns {} royal {} capSpy {} capPawns {}",
            self.spy, self.pawns, self.royal, self.cap_spy, self.cap_pawns
        )
    }
}

impl std::error::Error for SquareOverfull {}

/// Packs the numbers back into a square code. Zeroes everything past a dragon, and
/// everything past the prisoner flag when there are no prisoners, so one arrangement of
/// pieces has exactly one code -- see the module comment for why that matters.
pub fn build_space(
    side: u8,
    dragon: u8,
    spy: u8,
    pawns: u8,
    royal: u8,
    cap_spy: u8,
    cap_pawns: u8,
    pris_flag: u8,
) -> Result<u16, SquareOverfull> {
    if spy > 1 || royal > 1 || pawns > 4 || cap_spy > 1 || cap_pawns > 4 {
        return Err(SquareOverfull { spy, pawns, royal, cap_spy, cap_pawns });
    }
    Ok(build_space_unchecked(side, dragon, spy, pawns, royal, cap_spy, cap_pawns, pris_flag))
}

/// [`build_space`] without the overfull guard, for the executors, where the caller has just
/// taken the pieces off a square that was itself legal. Feeding it a field that overflows
/// bleeds bits into the next one instead of failing -- use [`build_space`] anywhere the
/// counts came from outside the engine.
#[inline]
pub fn build_space_unchecked(
    side: u8,
    dragon: u8,
    spy: u8,
    pawns: u8,
    royal: u8,
    cap_spy: u8,
    cap_pawns: u8,
    pris_flag: u8,
) -> u16 {
    if (dragon | spy | pawns | royal | cap_spy | cap_pawns | pris_flag) == 0 {
        return 0;
    }

    // occupied, controlling side
    let mut code = BIT_OCCUPIED;
    if side != 0 {
        code |= BIT_SIDE;
    }

    if dragon != 0 {
        return code | BIT_DRAGON;
    }

    if spy != 0 {
        code |= BIT_SPY;
    }
    code |= (pawns as u16) << SHIFT_PAWNS;
    if royal != 0 {
        code |= BIT_ROYAL;
    }

    if (cap_spy | cap_pawns | pris_flag) == 0 {
        return code;
    }

    code |= BIT_PRIS;
    if cap_spy != 0 {
        code |= BIT_CAPSPY;
    }
    code | ((cap_pawns as u16) << SHIFT_CAPPAWNS)
}

/// A board with one square replaced. `t_space` is **1-based**, as everywhere a square is
/// named rather than indexed.
#[inline]
pub fn mod_space(board: &Board, t_space: u8, result: u16) -> Board {
    let mut next = *board;
    next[t_space as usize - 1] = result;
    next
}

/// The code sitting on a 1-based square.
#[inline]
pub fn get_space_data(board: &Board, t_space: u8) -> u16 {
    // squares are numbered from 1; anything else is a caller bug and the Python side raises
    // rather than reading the square in front. Keep it loud.
    assert!(
        (1..=49).contains(&t_space),
        "square {t_space} is off the board (squares run 1 to 49)"
    );
    board[t_space as usize - 1]
}

// ---- geometry -------------------------------------------------------------------------
//
// Every walk across the board -- a jump strand, the line a push shoves, the trail a break
// scatters along -- is a fixed sequence of squares that depends on where you start and which
// way you go, and on nothing else. Weight decides how far along you get, never what the
// sequence is. These four functions draw them; `tables` walks them once at startup so the
// search never does this arithmetic again.

/// Wraps a raw coordinate pair onto the board and returns a 0-based square index.
///
/// `rem_euclid`, not `%`: the push rays run 21 steps and go well negative, and Rust's `%`
/// keeps the sign where Python's does not. `+ 14` is Python's, kept so the two read the same.
#[inline]
pub fn wrap_to_board(x: i32, y: i32) -> u8 {
    (((x + 14).rem_euclid(7)) + ((y + 14).rem_euclid(7)) * 7) as u8
}

/// The 1-based square `n` steps along `direction` from a 1-based origin, wrapped.
#[inline]
pub fn push_square(origin: u8, direction: [i32; 2], n: i32) -> u8 {
    let x = (origin as i32 - 1) % 7 + direction[0] * n;
    let y = (origin as i32 - 1) / 7 + direction[1] * n;
    wrap_to_board(x, y) + 1
}

/// The unit step between two adjacent 1-based squares, or `[0, 0]` if they aren't actually
/// neighbours. A push only ever moves one square, so a gap of 6 along an axis means it
/// wrapped round the edge rather than travelled.
pub fn find_direction(origin: u8, destination: u8) -> [i32; 2] {
    let (o, d) = (origin as i32 - 1, destination as i32 - 1);
    let step_x = (d % 7 - o % 7 + 7) % 7;
    let step_y = (d / 7 - o / 7 + 7) % 7;

    let dir_x = match step_x {
        1 => 1,
        6 => -1,
        _ => 0,
    };
    let dir_y = match step_y {
        1 => 1,
        6 => -1,
        _ => 0,
    };

    [dir_x, dir_y]
}

/// Which entry of [`PUSH_DIRS`] a `[dx, dy]` is, or `None` if it is not one of them.
pub fn push_index(direction: [i32; 2]) -> Option<u8> {
    PUSH_DIRS.iter().position(|d| *d == direction).map(|i| i as u8)
}

// ---- winning --------------------------------------------------------------------------

/// Whether a code is a won square: a side's spy, all four of its pawns and its royal, all
/// standing together.
///
/// Prisoners can't include a royal, so only a standing stack ever qualifies, and a dragon
/// carries nothing -- both fall out of the field rules rather than needing to be tested.
#[inline]
pub const fn is_win_code(code: u16) -> bool {
    code & BIT_OCCUPIED != 0
        && code & BIT_DRAGON == 0
        && code & BIT_SPY != 0
        && code & BIT_ROYAL != 0
        && (code >> SHIFT_PAWNS) & 7 == 4
}

/// Scans the whole board for a finished game. Returns `(gameEnd, winner)` with `winner`
/// indexed by side: `winner[0]` is blue, `winner[1]` red.
///
/// The search asks this at every node, so it reads codes straight off the board rather than
/// unpacking all 49 squares to look at three fields.
pub fn check_for_winner(board: &Board) -> (bool, [u8; 2]) {
    let mut winner = [0u8; 2];
    for &code in board.iter() {
        if is_win_code(code) {
            winner[((code & BIT_SIDE) >> 1) as usize] = 1;
        }
    }
    (winner != [0, 0], winner)
}

/// The board the Entering phase starts from: empty but for the two dragons facing each other
/// down the d file. Blue holds d3 (square 18) and red d5 (square 32) -- the backup's
/// placement masks block blue around d3 and red around d5, which is what settles which
/// dragon is whose.
pub fn entering_board() -> Board {
    let mut board = EMPTY_BOARD;
    board[17] = build_space_unchecked(0, 1, 0, 0, 0, 0, 0, 0);
    board[31] = build_space_unchecked(1, 1, 0, 0, 0, 0, 0, 0);
    board
}

/// A **0-based** board index as algebraic notation, e.g. 17 -> "d3". The inverse lives in
/// `notation.py` and stays there -- the engine never parses what a player typed.
pub fn index_to_alg(index: u8) -> String {
    let alph = b"abcdefg";
    format!("{}{}", alph[(index % 7) as usize] as char, index / 7 + 1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_space_round_trips_every_code() {
        // Not every 13-bit pattern is reachable, but every one it produces must unpack to
        // the fields it was built from and pack back to itself.
        for code in 0..CODE_RANGE as u16 {
            let s = unpack_code(code);

            // Codes 1 and 3 are the one exception, and the Python side loses them the same
            // way: occupied set with nothing whatever on the square. There is no piece and
            // no prisoner flag to encode, so the square is empty and its side goes with it.
            // No position produces them -- a side is only ever recorded alongside something
            // standing there -- but the table covers every bit pattern, so they turn up.
            if !s.occupied && s.pris_flag == 0 {
                assert_eq!(
                    build_space_unchecked(s.side, 0, 0, 0, 0, 0, 0, 0),
                    0,
                    "code {code} holds nothing and should encode as empty"
                );
                continue;
            }

            let rebuilt = build_space_unchecked(
                s.side, s.dragon, s.spy, s.pawns, s.royal, s.cap_spy, s.cap_pawns, s.pris_flag,
            );
            assert_eq!(unpack_code(rebuilt), s, "code {code} lost a field on the round trip");
        }
    }

    #[test]
    fn overfull_squares_are_refused() {
        assert!(build_space(0, 0, 2, 0, 0, 0, 0, 0).is_err());
        assert!(build_space(0, 0, 0, 5, 0, 0, 0, 0).is_err());
        assert!(build_space(0, 0, 1, 4, 1, 1, 4, 1).is_ok());
    }

    #[test]
    fn wrapping_is_floor_mod_both_ways() {
        // a push ray runs 21 steps, so this has to hold well past the edge in both directions
        assert_eq!(wrap_to_board(-1, 0), 6);
        assert_eq!(wrap_to_board(7, 0), 0);
        assert_eq!(wrap_to_board(-15, -15), wrap_to_board(-1, -1));
        assert_eq!(push_square(1, [-1, 0], 1), 7);
        assert_eq!(push_square(1, [0, -1], 1), 43);
    }

    #[test]
    fn neighbours_only() {
        assert_eq!(push_index(find_direction(1, 2)), Some(3)); // right
        assert_eq!(push_index(find_direction(1, 8)), Some(0)); // down
        assert_eq!(push_index(find_direction(1, 7)), Some(2)); // left, round the edge
        assert_eq!(push_index(find_direction(1, 3)), None); // two squares apart
        assert_eq!(push_index(find_direction(1, 9)), None); // diagonal
    }

    #[test]
    fn entering_board_is_two_dragons() {
        let board = entering_board();
        assert_eq!(board.iter().filter(|&&c| c != 0).count(), 2);
        assert_eq!(unpack_code(board[17]).dragon, 1);
        assert_eq!(unpack_code(board[17]).side, 0);
        assert_eq!(unpack_code(board[31]).side, 1);
        assert_eq!(check_for_winner(&board), (false, [0, 0]));
    }
}
