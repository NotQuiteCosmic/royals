//! What a position is worth.
//!
//! **Every number here is an integer, and that is not a style choice.** Scores were floats
//! once, and alpha-beta compares its numbers exactly (`beta <= alpha`, `score > bestScore`),
//! so a difference in the last place -- which is all it takes for two interpreters to
//! disagree about a square root -- flips a cutoff and changes the move played. Measured over
//! 3,064 positions, CPython and PyPy differed on 400 of them by up to 1.5e-14 relative: never
//! enough to show at four decimal places, occasionally enough to pick a different move. The
//! regression suite passed under one interpreter and failed under the other on exactly that.
//!
//! So: no `f32`, no `f64`, no `sqrt`. The one irrational term is the spread penalty, and
//! [`spread_penalty`] draws its root with [`isqrt`], which every machine truncates the same
//! way.

use crate::board::Board;
use crate::tables::{JUMPREACH, UNPACK};

/// A score, in thousandths of a point. `i64` rather than `i32` because the spread penalty
/// squares board coordinates against a scale of a million on its way to the root.
pub type Score = i64;

/// Scores are whole numbers of thousandths of a point.
pub const SCALE: Score = 1000;

/// What a won position scores, before the depth scaling [`crate::search::minimax`] puts on
/// it. Comfortably above anything the terms below can reach, so a win is never confused with
/// a good position.
pub const WIN_SCORE: Score = 1000 * SCALE;

/// Stands in for the infinities the search used to open its window with, so that no float
/// enters the search at all. A win scaled by depth stays far below this.
pub const INFINITY: Score = WIN_SCORE * 1000;

/// What a jump's worth of mobility is worth against the stacking score. `JUMPREACH` is 4 in
/// the middle of the board and 2 on the rim, so at a quarter point a lone piece is worth up
/// to half a point for where it stands and a stack of four up to two -- a nudge toward good
/// ground, nowhere near enough to turn down a merge for it.
pub const DIAG_WEIGHT: Score = SCALE / 4;

/// Holding a prisoner is worth 0.8.
pub const PRISONER_WEIGHT: Score = (SCALE * 4) / 5;

/// Each separate group costs 1.5.
pub const GROUP_PENALTY: Score = (SCALE * 3) / 2;

/// Floor square root, exactly. Python's `math.isqrt`; `u64::isqrt` in Rust 1.84 and later,
/// hand-rolled here because the crate builds on older stable and because a float `sqrt` is
/// the precise hazard this engine spent a bug learning about.
///
/// Integer Newton from a power-of-two start above the answer, which decreases monotonically
/// and lands on `floor(sqrt(n))`.
pub fn isqrt(n: u64) -> u64 {
    if n == 0 {
        return 0;
    }

    // 2^ceil(bits/2) is at or above the root, which is what makes the iteration monotone
    let bits = 64 - n.leading_zeros();
    let mut x = 1u64 << ((bits + 1) / 2);

    loop {
        let next = (x + n / x) / 2;
        if next >= x {
            return x;
        }
        x = next;
    }
}

/// The spread penalty: 1.5 times the larger of the two coordinate standard deviations, in
/// [`SCALE`] units, exactly.
///
/// The standard deviation of `n` whole numbers with sum `s` and sum of squares `s2` is
/// `sqrt(n*s2 - s*s) / n`, and the quantity under the root is itself a whole number. Both
/// axes always have the same `n` -- a group contributes one coordinate to each -- so which
/// axis is larger can be decided by comparing those two whole numbers, before any root is
/// drawn.
///
/// Scaling by `(3*SCALE)^2` going in and dividing by `2n` coming out gives
/// `1.5 * stdev * SCALE` with no float involved. It truncates rather than rounds, so the
/// answer can sit a thousandth of a point under the true value -- that is a rounding of the
/// heuristic, not a disagreement: every machine truncates to the same integer.
///
/// Both operands of the division are non-negative here, so Rust's `/` is Python's `//`.
pub fn spread_penalty(n: Score, sx: Score, sx2: Score, sy: Score, sy2: Score) -> Score {
    let q = (n * sx2 - sx * sx).max(n * sy2 - sy * sy);
    if q <= 0 {
        return 0;
    }

    (isqrt((9 * SCALE * SCALE * q) as u64) as Score) / (2 * n)
}

/// Scores a position from both sides at once, as `[blue, red]`.
///
/// Stacks are worth the square of their size, prisoners you hold are worth 0.8 apiece,
/// pieces of yours in captivity cost double what the stack would otherwise be worth, and the
/// whole score is penalised for being spread thin. Six pieces on one square is the spy, four
/// pawns and the royal -- so it is a win.
///
/// The search wants the difference between the two, and every square has something to say to
/// both of them: whoever holds it counts the stack standing there, and the other side counts
/// whatever of theirs is being held in it. So one walk answers both questions, where scoring
/// each side in turn walked the board twice and asked 49 empty squares to say nothing, twice.
/// Empty squares are skipped outright, and on a real board most of them are empty.
///
/// (A square-colour term stood here once, meant to favour the main diagonals because those
/// are the squares that wrap. The premise is right -- a main-diagonal square averages 3.38
/// jumps out against 2.67 for the other squares of its colour -- but the term did the
/// opposite, and it rewarded concentration on a colour, 25 squares, when the advantage
/// belongs to 13 of them. The `DIAG_WEIGHT` term replaced it: it asks `JUMPREACH` about the
/// square a piece is actually on instead of counting colours.)
pub fn evaluate_sides(board: &Board) -> [Score; 2] {
    let unpack = &*UNPACK;
    let jumpreach = &*JUMPREACH;

    let mut adv = [0 as Score; 2];
    let mut groups = [0 as Score; 2];
    let (mut sx, mut sx2) = ([0 as Score; 2], [0 as Score; 2]);
    let (mut sy, mut sy2) = ([0 as Score; 2], [0 as Score; 2]);
    let mut royal_idiot = [false; 2];
    // a side with six on one square scores the win outright, whatever else it has going on
    let mut won = [false; 2];

    for square in 1..50usize {
        let code = board[square - 1];
        // says nothing to either side
        if code == 0 {
            continue;
        }

        let s = unpack[code as usize];
        // the side holding the square, and the side whose people are held in it
        let w = s.side as usize;
        let o = 1 - w;

        let x = ((square - 1) % 7) as Score;
        let y = ((square - 1) / 7) as Score;

        // ###### the holder's own stack ######
        let my_pieces = s.my_pieces as Score;
        if my_pieces != 0 {
            // the dragon sits outside all of this -- it can't stack, be captured, or win
            if s.dragon == 0 {
                groups[w] += 1;
                if my_pieces < 6 {
                    adv[w] += my_pieces * my_pieces * SCALE;
                } else if my_pieces == 6 {
                    won[w] = true;
                }

                // drives pieces towards each other
                sx[w] += x;
                sx2[w] += x * x;
                sy[w] += y;
                sy2[w] += y * y;

                // mobile ground is worth standing on. One lookup, no search.
                adv[w] += DIAG_WEIGHT * jumpreach[square] as Score * my_pieces;
            }

            // holding prisoners is worth something...
            let prisoners = s.pris_count as Score;
            if prisoners != 0 {
                adv[w] += prisoners * PRISONER_WEIGHT;
            }

            // DON'T PUT UR DANG ROYAL AND SPY IN THE SAME PLACE
            if s.spy != 0 && s.royal != 0 && my_pieces != 6 {
                royal_idiot[w] = true;
            }
        }

        // ###### and the other side's people held in it ######
        // a captured group is never a dragon and never holds prisoners of its own, so the
        // mobility, prisoner and royal-and-spy terms have nothing to say about it
        let theirs = s.pris_count as Score;
        if theirs != 0 {
            groups[o] += 1;
            if theirs < 6 {
                adv[o] += theirs * theirs * SCALE;
            } else if theirs == 6 {
                won[o] = true;
            }

            sx[o] += x;
            sx2[o] += x * x;
            sy[o] += y;
            sy2[o] += y * y;

            // ...and being held costs double
            adv[o] -= theirs * theirs * 2 * SCALE;
        }
    }

    for side in 0..2usize {
        if won[side] {
            adv[side] = WIN_SCORE;
            continue;
        }

        // swept off the board, so the terms below have nothing to divide by
        if groups[side] == 0 {
            continue;
        }

        // Tuning moved each of these off its value in turn and nothing beat where they
        // already stand. The spread penalty is the one that matters: at 0 the AI plays
        // measurably worse.
        adv[side] -= groups[side] * GROUP_PENALTY;
        adv[side] -= spread_penalty(groups[side], sx[side], sx2[side], sy[side], sy2[side]);

        // if the royal and the spy are together, the penalty grows as the groups thin out
        if royal_idiot[side] {
            adv[side] -= (groups[side] * groups[side]) * 5 * SCALE;
        }
    }

    adv
}

/// One side's score. Nothing in the search uses this -- it evaluates both sides at once --
/// but it is the shape the heuristic is easiest to read in, and it keeps the two definitions
/// from drifting apart by being the same one.
pub fn check_position(board: &Board, contr: u8) -> Score {
    evaluate_sides(board)[contr as usize]
}

/// The difference between the two sides, from `contr`'s point of view. This is what the
/// search reads at a leaf, and what the golden records after every move.
pub fn full_check(board: &Board, contr: u8) -> Score {
    let adv = evaluate_sides(board);
    adv[contr as usize] - adv[1 - contr as usize]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::board::{build_space, EMPTY_BOARD};

    #[test]
    fn isqrt_is_the_floor_of_the_root() {
        // floor(sqrt(n)) is definitionally the x with x*x <= n < (x+1)^2, so checking that
        // is checking against Python's math.isqrt without needing Python in the room.
        for n in 0..20_000u64 {
            let x = isqrt(n);
            assert!(x * x <= n && n < (x + 1) * (x + 1), "isqrt({n}) = {x}");
        }

        // and across the range the spread penalty actually reaches: 9 * SCALE^2 * q, with q
        // bounded by twelve groups on a seven-square board
        for q in 0..6_000u64 {
            let n = 9 * 1_000_000 * q;
            let x = isqrt(n);
            assert!(x * x <= n && n < (x + 1) * (x + 1), "isqrt({n}) = {x}");
        }

        // exact squares, where an off-by-one would hide
        for r in 0..100_000u64 {
            assert_eq!(isqrt(r * r), r);
            if r > 0 {
                assert_eq!(isqrt(r * r - 1), r - 1);
            }
        }
    }

    #[test]
    fn a_gathered_side_beats_a_scattered_one() {
        // four pawns on one square against four pawns spread down a file: same pieces, and
        // the stacking term plus the spread penalty should be decisive
        let mut gathered = EMPTY_BOARD;
        gathered[24] = build_space(0, 0, 0, 4, 0, 0, 0, 0).unwrap();

        let mut scattered = EMPTY_BOARD;
        for i in [0, 14, 28, 42] {
            scattered[i] = build_space(0, 0, 0, 1, 0, 0, 0, 0).unwrap();
        }

        assert!(evaluate_sides(&gathered)[0] > evaluate_sides(&scattered)[0]);
        // and nothing said anything about red either way
        assert_eq!(evaluate_sides(&gathered)[1], 0);
    }

    #[test]
    fn six_on_a_square_is_a_win_outright() {
        let mut board = EMPTY_BOARD;
        board[24] = build_space(1, 0, 1, 4, 1, 0, 0, 0).unwrap();

        let adv = evaluate_sides(&board);
        assert_eq!(adv[1], WIN_SCORE, "the win is not the sum of its terms");
        assert_eq!(full_check(&board, 1), WIN_SCORE);
        assert_eq!(full_check(&board, 0), -WIN_SCORE);
    }

    #[test]
    fn captivity_costs_double() {
        // one blue pawn, standing; then the same pawn held by a red one
        let mut standing = EMPTY_BOARD;
        standing[24] = build_space(0, 0, 0, 1, 0, 0, 0, 0).unwrap();

        let mut held = EMPTY_BOARD;
        held[24] = build_space(1, 0, 0, 1, 0, 0, 1, 1).unwrap();

        // the group and spread terms are the same either way, so the gap is the doubling:
        // +1 for the group becomes -1 net, and red collects 0.8 for holding it
        assert_eq!(evaluate_sides(&held)[0], evaluate_sides(&standing)[0] - 3 * SCALE);
        assert_eq!(evaluate_sides(&held)[1] - evaluate_sides(&standing)[0], PRISONER_WEIGHT);
    }

}
