//! The executors: what a move does to a board.
//!
//! Every one of these returns a **new** board. That is not defensive style -- a board is its
//! own key in the ko set and the transposition table, so anything that edited one in place
//! would change a key that had already been filed under its old value.
//!
//! Three things here are counter-intuitive and are ported as they stand:
//!
//! * [`exe_push`]'s two-pass shuffle -- build every payload, then commit in **ascending**
//!   offset order -- because a push long enough to lap the board has two offsets landing on
//!   one square, and the later one must win.
//! * [`exe_break`] never frees anybody, which is a rule enforced entirely by
//!   [`crate::movegen::check_break`] refusing to scatter past a square holding prisoners.
//!   [`drop_piece`]'s capture branch would happily release them.
//! * [`exe_move`] has **no handling for the spyBreak case** -- moving your own spy out of an
//!   enemy square that holds it prisoner. The gap exists on both sides of the port and is
//!   kept: fixing it here would move `golden_moves.txt` with no way to tell which change did
//!   it.

use crate::board::{build_space, mod_space, Board};
use crate::movegen::{check_break, get_legal_push_length, parse_board, would_free};
use crate::tables::{BREAKRAY, PUSHFROM, PUSHRAY, UNPACK};
use crate::{Move, MoveKind};

/// A single piece, for the two places that move one at a time: entering, and a break's
/// scatter. Python names these by the field index they occupy in a parsed square.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Piece {
    Spy,
    Pawns,
    Royal,
}

/// [`build_space`] with the overfull square treated as the caller bug it is. Python raises
/// `ValueError` here; the guard has never fired in play, and if it ever does, the interesting
/// thing is the move that built the square rather than the board that came out.
#[inline]
#[allow(clippy::too_many_arguments)]
fn packed(
    side: u8,
    dragon: u8,
    spy: u8,
    pawns: u8,
    royal: u8,
    cap_spy: u8,
    cap_pawns: u8,
    pris_flag: u8,
) -> u16 {
    match build_space(side, dragon, spy, pawns, royal, cap_spy, cap_pawns, pris_flag) {
        Ok(code) => code,
        Err(e) => panic!("{e}"),
    }
}

/// Performs a jump: the stack sitting on `origin` lands on `destination`, both 1-based.
///
/// NOTE assumes the mover controls the origin. The spyBreak case -- moving your own spy out
/// of an enemy square that holds it prisoner -- needs its own handling and doesn't have any
/// yet, on either side of the port.
pub fn exe_move(
    board: &Board,
    origin: u8,
    destination: u8,
    contr: u8,
    moving_pris: bool,
) -> Board {
    // both squares come from move generation, so they are known-good 1-based numbers -- this
    // is the search's make-move path and skips the bounds check
    let o = UNPACK[board[origin as usize - 1] as usize];
    let d = UNPACK[board[destination as usize - 1] as usize];

    // the pieces making the trip, plus their prisoners if those were invited
    let mut new_spy = o.spy;
    let mut new_pawns = o.pawns;
    let mut new_royal = o.royal;
    let mut new_cap_spy = 0;
    let mut new_cap_pawns = 0;
    if moving_pris {
        new_cap_spy = o.cap_spy;
        new_cap_pawns = o.cap_pawns;
    }

    // An empty destination merges with nothing. Deliberately not `occupied`: that would count
    // a dragon, and a dragon square zeroes every other field, so merging with one would write
    // the arriving stack into a square with nowhere to put it.
    if d.side != 0 || d.spy != 0 || d.pawns != 0 || d.royal != 0 || d.cap_spy != 0 || d.cap_pawns != 0
    {
        if d.side == contr {
            // landing on your own stack: the two merge and its prisoners stay put
            new_spy += d.spy;
            new_pawns += d.pawns;
            new_royal += d.royal;
            new_cap_spy += d.cap_spy;
            new_cap_pawns += d.cap_pawns;
        } else {
            // landing on the enemy takes them prisoner. check_moves already refused this
            // square if it held a royal, a dragon, or allies of yours in captivity.
            new_cap_spy += d.spy;
            new_cap_pawns += d.pawns;
        }
    }

    let new_dest = packed(contr, o.dragon, new_spy, new_pawns, new_royal, new_cap_spy, new_cap_pawns, 0);

    // prisoners left behind go free, standing back up as a stack of their own side
    let new_origin =
        if moving_pris { 0 } else { packed(1 - contr, 0, o.cap_spy, o.cap_pawns, 0, 0, 0, 0) };

    let board = mod_space(board, destination, new_dest);
    mod_space(&board, origin, new_origin)
}

/// Drops a single piece belonging to `side` onto a 1-based square.
pub fn drop_piece(board: &Board, square: u8, side: u8, piece: Piece) -> Board {
    let t = UNPACK[board[square as usize - 1] as usize];

    let (mut spy, mut pawns, mut royal, mut cap_spy, mut cap_pawns) = (0, 0, 0, 0, 0);

    if t.spy == 0 && t.pawns == 0 && t.royal == 0 && t.cap_spy == 0 && t.cap_pawns == 0 {
        // empty square, nothing to reconcile
    } else if t.side == side {
        // our own square: join the stack, its prisoners stay prisoners
        spy = t.spy;
        pawns = t.pawns;
        royal = t.royal;
        cap_spy = t.cap_spy;
        cap_pawns = t.cap_pawns;
    } else {
        // landing on the enemy takes them captive, and frees any of ours they were holding
        cap_spy = t.spy;
        cap_pawns = t.pawns;
        spy = t.cap_spy;
        pawns = t.cap_pawns;
    }

    match piece {
        Piece::Spy => spy += 1,
        Piece::Pawns => pawns += 1,
        Piece::Royal => royal += 1,
    }

    mod_space(board, square, packed(side, 0, spy, pawns, royal, cap_spy, cap_pawns, 0))
}

/// Shatters the stack on `input_square`, scattering it one piece per square along
/// `direction` (an index into `PUSH_DIRS`).
///
/// Prisoners fall first, then the standing stack with the royal last, and whatever is still
/// in hand lands together on the final square.
pub fn exe_break(board: &Board, input_square: u8, direction: usize, _contr: u8) -> Board {
    let data = board[input_square as usize - 1];
    let s = UNPACK[data as usize];

    // a dragon is a single piece, so there is nothing to scatter
    if s.dragon != 0 {
        return *board;
    }

    let spaces = parse_board(board);
    let break_range = check_break(&spaces, input_square, data, direction);
    if break_range == 0 {
        return *board;
    }

    // the side holding the square owns the standing stack; the prisoners are the other's
    let control = s.side;

    // queue the pieces in the order they fall
    let mut queue: Vec<(u8, Piece)> = Vec::with_capacity(11);
    for _ in 0..s.cap_spy {
        queue.push((1 - control, Piece::Spy));
    }
    for _ in 0..s.cap_pawns {
        queue.push((1 - control, Piece::Pawns));
    }
    if s.spy != 0 {
        queue.push((control, Piece::Spy));
    }
    for _ in 0..s.pawns {
        queue.push((control, Piece::Pawns));
    }
    if s.royal != 0 {
        queue.push((control, Piece::Royal));
    }

    // clear the square, then let them fall back across it and onward
    let mut board = mod_space(board, input_square, 0);

    let scatter = &BREAKRAY[input_square as usize][direction];
    let mut next = 0usize;

    for i in 0..break_range {
        if next == queue.len() {
            break;
        }
        // the ray is 0-based, drop_piece numbers squares from 1
        let targ = scatter.as_slice()[i] + 1;

        if i + 1 == break_range {
            // the last square catches everything still falling
            while next < queue.len() {
                let (side, piece) = queue[next];
                next += 1;
                board = drop_piece(&board, targ, side, piece);
            }
        } else {
            let (side, piece) = queue[next];
            next += 1;
            board = drop_piece(&board, targ, side, piece);
        }
    }

    board
}

/// Performs a push: the stack on `origin` shoves the line of squares ahead of it one step,
/// then steps onto the vacated square. `destination` is 1-based and adjacent to `origin`.
///
/// `shatter` is whether a lone spy's shove goes on to scatter what it displaced. Always true
/// in play; `moveFlights` (which stays in Python) turns it off to get at the board in between
/// the shuffle and that scatter, which is the only place the scatter can be measured from.
pub fn exe_push(
    board: &Board,
    origin: u8,
    destination: u8,
    contr: u8,
    moving_pris: bool,
    free_pris: bool,
    shatter: bool,
) -> Board {
    // not neighbours, so there is no push to make
    let direction = match PUSHFROM[origin as usize][destination as usize] {
        Some(d) => d as usize,
        None => return *board,
    };

    let input_data = board[origin as usize - 1];
    let spaces = parse_board(board);
    let p_range =
        get_legal_push_length(&spaces, origin, input_data, direction, contr, moving_pris, free_pris);
    if p_range == 0 {
        return *board;
    }

    let o = UNPACK[input_data as usize];

    // The line this push runs along, offset 0 being the origin itself -- so `line[n]` is the
    // square n steps out, 0-based.
    let ray = &PUSHRAY[origin as usize][direction];
    let line = |n: usize| -> usize {
        if n == 0 {
            origin as usize - 1
        } else {
            ray.as_slice()[n - 1] as usize
        }
    };

    let adj = UNPACK[board[line(1)] as usize];

    // Shoving a square that holds pieces of ours captive frees them: the jailers get shoved
    // on alone and our own pieces stand back up where they were being held, joining the stack
    // that just walked in. Both branches are legal moves, so which one this is comes from the
    // caller -- and it has to agree exactly with what get_legal_push_length was asked, since
    // that is what granted the push its range in the first place. would_free is what keeps a
    // dragon out of here: build_space drops every other field on a dragon square, so a dragon
    // taking this branch would write the pieces it had just freed into itself and lose them.
    let freeing = free_pris && would_free(&o, &adj, contr, moving_pris);

    // grab every square the shuffle needs before touching anything
    let mut payloads = [0u16; PAYLOAD_SLOTS];
    for n in 0..p_range + 2 {
        payloads[n] = board[line(n)];
    }

    // Work out every square's new contents before committing any of them. Walking from the
    // far end inwards means each square is written after the one beyond it has already taken
    // its copy, so the later assignment to a given offset is the one that sticks.
    let mut new_payloads = [None::<u16>; PAYLOAD_SLOTS];
    for k in 0..p_range {
        let d = p_range - k;

        if freeing && d == 1 {
            // offset 2 is free by now: either the cascade above just emptied it, or the push
            // stopped there because it was empty to begin with.
            new_payloads[2] = Some(packed(adj.side, adj.dragon, adj.spy, adj.pawns, adj.royal, 0, 0, 0));
            new_payloads[1] = Some(packed(contr, 0, adj.cap_spy, adj.cap_pawns, 0, 0, 0, 0));
        } else {
            new_payloads[d + 1] = Some(payloads[d]);
            new_payloads[d] = Some(0);
        }
    }

    // and now the pushing stack itself steps forward
    if moving_pris {
        // the whole square travels, prisoners included
        new_payloads[1] = Some(payloads[0]);
        new_payloads[0] = Some(0);
    } else {
        // freed pieces may already be standing on the destination, so merge rather than set
        let mut new_spy = o.spy;
        let mut new_pawns = o.pawns;
        if freeing {
            new_spy += adj.cap_spy;
            new_pawns += adj.cap_pawns;
        }
        new_payloads[1] = Some(packed(contr, o.dragon, new_spy, new_pawns, o.royal, 0, 0, 0));
        // prisoners left behind go free, standing back up as a stack of their own side
        new_payloads[0] = Some(packed(1 - contr, 0, o.cap_spy, o.cap_pawns, 0, 0, 0, 0));
    }

    // One copy of the board for the whole shuffle. Still in ASCENDING offset order, because a
    // push long enough to lap the board has two offsets landing on one square and the later
    // of them is the one that stands.
    let mut cells = *board;
    for (n, payload) in new_payloads.iter().enumerate() {
        if let Some(code) = payload {
            cells[line(n)] = *code;
        }
    }

    // A lone spy's shove shatters what it hits. get_legal_push_length only ever grants a lone
    // spy a range of 1 -- it spends its single point of strength on the adjacent square and
    // has nothing left for a second -- so the stack it displaced is now sitting two squares
    // out, and it scatters onward from there.
    if shatter && o.spy != 0 && o.captors == 1 {
        return exe_break(&cells, line(2) as u8 + 1, direction, contr);
    }

    cells
}

/// Offsets a push can write. The ray is [`crate::board::PUSH_REACH`] long and the line adds
/// the origin in front of it, so `pRange + 1` can reach the last of them and no further --
/// a push that filled the ray would have been refused as a runaway.
const PAYLOAD_SLOTS: usize = crate::board::PUSH_REACH + 2;

/// Applies one move and returns the resulting board, leaving `board` alone.
///
/// **The `+ 1` is the contract**: `target` is 0-based for a jump, push or free, and is a
/// direction index -- not a square at all -- for a break. Every off-by-one this engine can
/// produce lives in that asymmetry.
pub fn perform_one_step(board: &Board, contr: u8, mv: Move) -> Board {
    match mv.kind {
        MoveKind::Jump => exe_move(board, mv.origin, mv.target + 1, contr, mv.moving_pris),
        MoveKind::Push => {
            exe_push(board, mv.origin, mv.target + 1, contr, mv.moving_pris, false, true)
        }
        MoveKind::Free => {
            exe_push(board, mv.origin, mv.target + 1, contr, mv.moving_pris, true, true)
        }
        MoveKind::Break => exe_break(board, mv.origin, mv.target as usize, contr),
    }
}
