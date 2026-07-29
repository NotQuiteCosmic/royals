//! Everything the engine knows before a game starts, computed at startup rather than shipped.
//!
//! Nothing here is a fact about a position -- it is all geometry and encoding, which is
//! exactly why it can be built once. The Python side reached these conclusions a step at a
//! time in the innermost loop of the innermost function of the search: two multiplications,
//! two additions, two modulos and a bounds test per square walked, plus a `range()` object
//! built to answer what a comparison answers. Drawing the sequences once turns all of that
//! into a slice.
//!
//! Building costs a few milliseconds and about 140KB. Shipping them as constants would save
//! the milliseconds and cost a table that can silently disagree with the code that used to
//! derive it -- and `UNPACK` alone is 8,192 rows. `engine-rs/tests/tables.rs` checks every
//! one of these against a dump from the Python engine, element for element.

use std::sync::LazyLock;

use crate::board::{
    unpack_code, wrap_to_board, Square, CODE_RANGE, JUMP_DIRS, MAX_SCATTER, MAX_STACK, PUSH_DIRS,
    PUSH_REACH,
};

/// One walk across the board: up to `N` squares, 0-based, in the order they are stepped on.
///
/// Fixed-width and `Copy` so a strand costs no allocation and no indirection -- the search
/// takes a slice of it and walks that. How far along you get is the caller's business
/// (weight decides); what the sequence *is* was settled here.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Ray<const N: usize> {
    squares: [u8; N],
    len: u8,
}

impl<const N: usize> Ray<N> {
    const EMPTY: Ray<N> = Ray { squares: [0; N], len: 0 };

    fn from_slice(squares: &[u8]) -> Ray<N> {
        let mut ray = Ray::EMPTY;
        ray.squares[..squares.len()].copy_from_slice(squares);
        ray.len = squares.len() as u8;
        ray
    }

    #[inline]
    pub fn as_slice(&self) -> &[u8] {
        &self.squares[..self.len as usize]
    }

    #[inline]
    pub fn len(&self) -> usize {
        self.len as usize
    }

    #[inline]
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
}

/// Rays are indexed `[1-based square][direction index]`, so row 0 is never read. It is kept
/// rather than shifted away because every caller already holds a 1-based square, and the
/// Python tables are laid out the same way.
pub type RayTable<const N: usize> = [[Ray<N>; 4]; 50];

/// Every code unpacked once. `Parse_Space` becomes a subscript rather than a parse, which
/// matters because move generation and the evaluator ask about whole boards at every node.
/// 8,192 rows covers every bit pattern, including ones no legal position produces -- a table
/// with holes would only move the test somewhere hotter.
pub static UNPACK: LazyLock<Vec<Square>> =
    LazyLock::new(|| (0..CODE_RANGE as u16).map(unpack_code).collect());

/// The diagonal strands a jump walks. A strand may only wrap round the edge if it set out
/// from one of the two long diagonals, and which diagonal that is depends on the direction's
/// slope -- so the wrap rule is baked in here and no jump loop ever tests for it again.
pub static JUMPRAY: LazyLock<RayTable<MAX_STACK>> = LazyLock::new(build_jump_rays);

/// The line a push shoves along. An **empty** ray means the first step would go off the
/// edge: you can't start a push by shoving something over the side, there has to be a square
/// next to you to push into. Everything past that first step wraps as normal.
pub static PUSHRAY: LazyLock<RayTable<PUSH_REACH>> = LazyLock::new(build_push_rays);

/// The trail a break scatters along. Unlike the other two this ray includes its own origin,
/// at offset 0, because a break starts by dropping a piece back onto the square it leaves.
pub static BREAKRAY: LazyLock<RayTable<MAX_SCATTER>> = LazyLock::new(build_break_rays);

/// Which push direction leads from one 1-based square to an adjacent one, or `None` if they
/// aren't neighbours. `find_direction`'s answer, looked up rather than worked out. Indexed
/// `[origin][destination]`, both 1-based, both rows/columns 0 unused.
pub static PUSHFROM: LazyLock<[[Option<u8>; 50]; 50]> = LazyLock::new(build_push_from);

/// How many jumps a lone piece has out of each square: 4 inside the board, 2 on the rim.
///
/// Python measures this off `checkMoves` so that it follows the rules if they move. Here it
/// is derived from [`JUMPRAY`] instead, which keeps `tables` free of any dependency on move
/// generation -- and it *is* the same number, because the only thing `checkMoves` can do to
/// a lone piece's first step on an empty board is take it. The fixture test is what holds
/// the two derivations together; if the jump rules ever change, that test is where it shows.
pub static JUMPREACH: LazyLock<[u8; 50]> = LazyLock::new(build_jump_reach);

/// How far apart two squares are in lone-piece jumps, breadth first. Indexed
/// `[1-based start][1-based square]`, with index 0 of each row unused.
///
/// A lone piece is the slowest thing on the board -- stacks jump their own weight -- so
/// these over-estimate, but consistently, which is all a ranking needs. Jumps run along
/// diagonals, so they never change a square's colour: half of every row is [`UNREACHABLE`],
/// which is why nothing built on this needs a separate parity term.
pub static JUMPDIST: LazyLock<[[u8; 50]; 50]> = LazyLock::new(build_jump_dist);

/// Not far away -- shut off. Two squares of opposite colour are never joined by any number
/// of jumps, and the entering heuristic wants a number rather than an absence.
pub const UNREACHABLE: u8 = 12;

fn build_jump_rays() -> RayTable<MAX_STACK> {
    let mut rays = [[Ray::EMPTY; 4]; 50];

    for square in 1..50u8 {
        let origin_x = (square as i32 - 1) % 7;
        let origin_y = (square as i32 - 1) / 7;

        for (d, j) in JUMP_DIRS.iter().enumerate() {
            // Diag1 is the anti-diagonal (x + y == 6) and Diag2 the main one (x == y); a
            // strand may wrap only if its origin sits on the one matching its slope.
            let can_wrap = if j[0] + j[1] == 0 {
                origin_x + origin_y == 6
            } else {
                origin_x == origin_y
            };

            let (mut move_x, mut move_y) = (origin_x, origin_y);
            let mut strand = Vec::with_capacity(MAX_STACK);

            for _ in 0..MAX_STACK {
                move_x += j[0];
                move_y += j[1];

                if !((0..7).contains(&move_x) && (0..7).contains(&move_y)) {
                    // off the edge and not entitled to wrap: the strand simply stops
                    if !can_wrap {
                        break;
                    }
                    move_x = (move_x + 7) % 7;
                    move_y = (move_y + 7) % 7;
                }

                strand.push((move_x + move_y * 7) as u8);
            }

            rays[square as usize][d] = Ray::from_slice(&strand);
        }
    }

    rays
}

fn build_push_rays() -> RayTable<PUSH_REACH> {
    let mut rays = [[Ray::EMPTY; 4]; 50];

    for square in 1..50u8 {
        let x0 = (square as i32 - 1) % 7;
        let y0 = (square as i32 - 1) / 7;

        for (d, dir) in PUSH_DIRS.iter().enumerate() {
            // the only step tested for the edge is the first -- see PUSHRAY's comment
            if !((0..7).contains(&(x0 + dir[0])) && (0..7).contains(&(y0 + dir[1]))) {
                continue;
            }

            let line: Vec<u8> = (1..=PUSH_REACH as i32)
                .map(|n| wrap_to_board(x0 + dir[0] * n, y0 + dir[1] * n))
                .collect();
            rays[square as usize][d] = Ray::from_slice(&line);
        }
    }

    rays
}

fn build_break_rays() -> RayTable<MAX_SCATTER> {
    let mut rays = [[Ray::EMPTY; 4]; 50];

    for square in 1..50u8 {
        let x0 = (square as i32 - 1) % 7;
        let y0 = (square as i32 - 1) / 7;

        for (d, dir) in PUSH_DIRS.iter().enumerate() {
            let trail: Vec<u8> = (0..MAX_SCATTER as i32)
                .map(|n| wrap_to_board(x0 + dir[0] * n, y0 + dir[1] * n))
                .collect();
            rays[square as usize][d] = Ray::from_slice(&trail);
        }
    }

    rays
}

fn build_push_from() -> [[Option<u8>; 50]; 50] {
    let mut table = [[None; 50]; 50];

    for origin in 1..50u8 {
        for destination in 1..50u8 {
            table[origin as usize][destination as usize] =
                crate::board::push_index(crate::board::find_direction(origin, destination));
        }
    }

    table
}

/// The graph [`JUMPREACH`] counts and [`JUMPDIST`] walks: where a lone piece can go in one
/// jump, 1-based. A weight-1 stack walks one square of each strand, and on an empty board
/// nothing stops it, so this is the first square of every strand that has one.
fn lone_piece_graph() -> [Vec<u8>; 50] {
    let jumpray = &*JUMPRAY;
    std::array::from_fn(|square| {
        if square == 0 {
            return Vec::new();
        }
        jumpray[square]
            .iter()
            .filter_map(|strand| strand.as_slice().first().map(|t| t + 1))
            .collect()
    })
}

fn build_jump_reach() -> [u8; 50] {
    let graph = lone_piece_graph();
    std::array::from_fn(|square| graph[square].len() as u8)
}

fn build_jump_dist() -> [[u8; 50]; 50] {
    let graph = lone_piece_graph();
    let mut table = [[UNREACHABLE; 50]; 50];

    for start in 1..50usize {
        table[start][start] = 0;
        let mut frontier = vec![start as u8];

        while !frontier.is_empty() {
            let mut onwards = Vec::new();
            for square in frontier {
                for &target in &graph[square as usize] {
                    if table[start][target as usize] != UNREACHABLE {
                        continue;
                    }
                    table[start][target as usize] = table[start][square as usize] + 1;
                    onwards.push(target);
                }
            }
            frontier = onwards;
        }
    }

    table
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_push_ray_is_empty_only_at_the_edge_it_faces() {
        // square 1 is a1: up and left both step off, down and right don't
        assert!(PUSHRAY[1][1].is_empty()); // up
        assert!(PUSHRAY[1][2].is_empty()); // left
        assert!(!PUSHRAY[1][0].is_empty()); // down
        assert_eq!(PUSHRAY[1][0].len(), PUSH_REACH);
    }

    #[test]
    fn a_break_ray_starts_where_it_stands() {
        for square in 1..50usize {
            for d in 0..4 {
                assert_eq!(BREAKRAY[square][d].len(), MAX_SCATTER);
                assert_eq!(BREAKRAY[square][d].as_slice()[0], square as u8 - 1);
            }
        }
    }

    #[test]
    fn jump_distance_is_shut_off_across_colours() {
        // a1 and b1 are opposite colours, and no number of diagonal jumps joins them
        assert_eq!(JUMPDIST[1][2], UNREACHABLE);
        assert_eq!(JUMPDIST[1][1], 0);
        assert_eq!(JUMPDIST[1][9], 1);
    }

    #[test]
    fn the_rim_has_half_the_jumps_of_the_middle() {
        assert_eq!(JUMPREACH[1], 2); // a1, a corner
        assert_eq!(JUMPREACH[25], 4); // d4, the middle
    }
}
