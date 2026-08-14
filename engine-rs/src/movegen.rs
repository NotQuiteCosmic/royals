//! Move generation: every rule about where a stack may go, and nothing about what happens
//! when it gets there (that is [`crate::exec`]).
//!
//! Ported from `engine.py` and the front half of `ai.py`, against `docs/PORTING.md`. The
//! comments there and here are the same comments: they explain rules that were arrived at
//! the hard way and would otherwise read as accidents.
//!
//! Two shapes are part of the contract and are not tidied here:
//!
//! * [`check_moves`] answers a six-list -- `[jumps, pushes, breaks, possMoves, alphBreaks,
//!   frees]` -- of which the AI reads 0, 1, 2 and 5. [`CheckMoves`] is that, as a struct,
//!   with the two derived entries as methods; `None` is Python's "no moves at all" `[]`.
//! * The generation order in [`list_all_moves`] is load-bearing. The port fixtures pick
//!   moves *by index* into its output, so a different order plays a different game on the
//!   very next line -- which is exactly how a mistake here is meant to surface.

use crate::board::{Board, Square, HEADINGS, PUSH_DIRS};
use crate::exec::Piece;
use crate::tables::{BREAKRAY, JUMPRAY, PUSHFROM, PUSHRAY, UNPACK};
use crate::{Move, MoveKind};

/// All 49 squares unpacked, in board order. One walk of the board serves a whole node: every
/// function here takes it rather than re-deriving squares it has already looked at.
pub type Spaces = [Square; 49];

pub fn parse_board(board: &Board) -> Spaces {
    let unpack = &*UNPACK;
    std::array::from_fn(|i| unpack[board[i] as usize])
}

/// A fixed-capacity list of squares. Move generation runs millions of times per search and
/// every one of these is short and bounded -- a jump reaches at most four strands of six --
/// so they live in the return value instead of on the heap.
#[derive(Clone, Copy, Debug)]
pub struct List<const N: usize> {
    items: [u8; N],
    len: u8,
}

impl<const N: usize> List<N> {
    const fn new() -> List<N> {
        List { items: [0; N], len: 0 }
    }

    #[inline]
    fn push(&mut self, value: u8) {
        self.items[self.len as usize] = value;
        self.len += 1;
    }

    #[inline]
    pub fn as_slice(&self) -> &[u8] {
        &self.items[..self.len as usize]
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

impl<const N: usize> Default for List<N> {
    fn default() -> Self {
        List::new()
    }
}

/// What [`check_moves`] answers: the six-list, minus the two entries derived from the others.
/// Targets are **0-based** squares, except `breaks`, which holds direction indices into
/// [`PUSH_DIRS`].
#[derive(Clone, Copy, Debug, Default)]
pub struct CheckMoves {
    pub jumps: List<24>,
    pub pushes: List<4>,
    pub breaks: List<4>,
    /// Freeing pushes, kept apart from `pushes` because they are a different move onto the
    /// same square: once prisoners are involved the destination alone no longer says what
    /// the push does. A square can be in both, and that is the point.
    pub frees: List<4>,
}

impl CheckMoves {
    /// Index 3 of the six-list. Jumps, pushes and frees, plus one entry standing for "and it
    /// could break". Only its length is ever read -- it is how the engine asks whether a
    /// square can move at all. Frees count: a stack whose only option is to free somebody is
    /// not stuck, and leaving them out reported it as such.
    pub fn poss_moves_len(&self) -> usize {
        self.jumps.len()
            + self.pushes.len()
            + self.frees.len()
            + usize::from(!self.breaks.is_empty())
    }

    /// Index 4 of the six-list: the break directions spelled out for a human. The terminal
    /// driver and the GUI print these; nothing in the search reads them.
    pub fn alph_breaks(&self) -> Vec<&'static str> {
        self.breaks.as_slice().iter().map(|&d| HEADINGS[d as usize]).collect()
    }
}

/// A chosen square plus how it is being moved out of. `square` is 1-based; `code` is the
/// square as it stands on the board, carried outright so nothing has to look it up again.
#[derive(Clone, Copy, Debug)]
pub struct Origin {
    /// The prisoners on the square come along for the ride.
    pub moving_pris: bool,
    /// This is our spy breaking out of an enemy square that holds it. The stack being
    /// scattered belongs to the captor, not to us, and breaking out is the only move it has.
    pub spy_break: bool,
    pub square: u8,
    pub code: u16,
}

pub fn make_origin(board: &Board, square: u8, moving_pris: bool, spy_break: bool) -> Origin {
    Origin { moving_pris, spy_break, square, code: board[square as usize - 1] }
}

/// Whether a stack is the sort of thing that can free allies it pushes into.
///
/// The rule is the jump rule: freeing means stepping onto the square the prisoners were held
/// on and standing with them, so anything that can't legally land on top of other pieces
/// can't free either.
///
/// * a spy can't, standing or in a stack. It slips into a square rather than onto it, which
///   is the same reason it shatters what it shoves instead of joining it.
/// * a dragon can't. Its square holds nothing but the dragon, so there is nowhere for the
///   freed pieces to stand.
/// * a stack carrying its own prisoners can't, whatever else is in it, royal included.
///   Freeing would leave the allies it just freed, the stack itself, and the prisoners it
///   brought along all on one square -- a sandwich, pushing over prisoners from both sides
///   at once.
///
/// The royal is no part of this. It decides the DISCOUNT, not the right to free.
pub fn can_free_prisoners(origin: &Square, moving_pris: bool) -> bool {
    if moving_pris {
        return false;
    }
    if origin.dragon != 0 {
        return false;
    }
    if origin.spy != 0 {
        return false;
    }
    true
}

/// Whether this particular push would be a freeing one: an eligible stack, shoving an enemy
/// square that is holding pieces of ours. Prisoners belong to whichever side does NOT
/// control the square, so they are ours exactly when the square is the enemy's.
#[inline]
pub fn would_free(origin: &Square, targ: &Square, contr: u8, moving_pris: bool) -> bool {
    // asked once per push direction per origin per node, so it reads the fields itself
    // rather than going through can_free_prisoners to ask the same three questions. Same
    // three questions, in the same order.
    if moving_pris {
        return false;
    }
    if targ.pris_flag == 0 {
        return false;
    }
    if targ.side == contr {
        return false;
    }
    !(origin.dragon != 0 || origin.spy != 0)
}

/// How far a push out of `input_space` along `dir_index` would travel, 0 meaning no push.
///
/// `free_pris` picks which of the two branches is being asked about. A push into a square
/// holding allies of ours is two different moves: free them, leaving the jailers to be
/// shoved on alone, or shove the whole square along with the prisoners still in it. They
/// cost differently, so they have to be measured separately, and [`check_moves`] offers
/// whichever come back legal.
///
/// (Python takes a `spyCap` argument here and never reads it -- a held spy is kept off this
/// path by its caller, not by this function. Dropped rather than carried.)
pub fn get_legal_push_length(
    spaces: &Spaces,
    input_space: u8,
    input_data: u16,
    dir_index: usize,
    contr: u8,
    moving_pris: bool,
    free_pris: bool,
) -> usize {
    // an empty ray is a first step off the edge, and there is no push without a square to
    // push into
    let ray = &PUSHRAY[input_space as usize][dir_index];
    if ray.is_empty() {
        return 0;
    }

    let origin = UNPACK[input_data as usize];
    let mut p_length = 0usize;

    let mut strength: i32 = if moving_pris { origin.strength as i32 } else { origin.captors as i32 };

    // Each step checks whether the next square is occupied and subtracts its weight from the
    // strength. While that stays at 0 or above, the push is still on.
    for &target in ray.as_slice() {
        let targ = &spaces[target as usize];

        // CLASSIC GAUNTLET STRUCTURE -- beyond emptiness, which is where the loop ends with
        // a success, the check is the strength minus everything accumulated so far.
        if !targ.occupied {
            return p_length;
        }

        // the first square of adjacency has exceptions to do with spies, royals and captured
        // pieces. BIG.
        if p_length == 0 {
            // the origin is just a spy: captors weighing 1 means the spy and nothing else
            let spy_only = origin.spy != 0 && origin.captors == 1;

            if free_pris {
                // Asking to free where that isn't on offer is not the same move made
                // expensively -- it isn't a move, so it reports no push rather than falling
                // through to the other branch.
                if !would_free(&origin, targ, contr, moving_pris) {
                    return 0;
                }

                // The royal's discount: it shoves the jailers aside for one, however many of
                // them are standing. Everyone else pays for the jailers in full -- the
                // prisoners are what is being freed, so they never cost anything here.
                if origin.royal != 0 {
                    strength -= 1;
                } else {
                    strength -= targ.captors as i32;
                }
            } else if spy_only {
                // a lone spy slips in, so that square always costs exactly 1
                strength -= 1;

                // ...and the stack it slipped into comes apart. Not decided here: this
                // function only ever answers a length, and a break is its own move type.
                // exe_push does the scattering once the shuffle is done.
            } else {
                // Shoving the whole square along, prisoners and all. They are being carried
                // rather than liberated, so their weight counts against the push exactly
                // like anyone else's. On a square holding nobody this is the same number
                // captors gives, which is why an ordinary push is unaffected by any of it.
                strength -= targ.weight as i32;
            }
        } else {
            strength -= targ.weight as i32;
        }

        if strength < 0 {
            return 0;
        }
        p_length += 1;
    }

    // Runaway backstop -- a push can't outrun the board. Falling off the end of the ray means
    // PUSH_REACH squares in a row were occupied and paid for, which is not a position.
    0
}

/// The furthest a push may travel: 1 under the standard rules, and under the push-range
/// variant the ceiling the mover chooses beneath.
///
/// Mirrors `Engine.pushMaxTravel`. Legality has already been decided by the time this is
/// asked -- `get_legal_push_length` said yes, so strength covers the line and the distance
/// below is at least 1 before anything clips it.
///
/// * **the pusher's edge** — the ray wraps and the pusher may not, so it stops at the last
///   square it can reach without the file or rank folding over. Only the pusher is clipped:
///   the shoved pieces are further along the same ray and wrap freely, which is what makes
///   "the pushing stack can't go off the edge, though pushed pieces can" a rule about one
///   square rather than about the line.
/// * **a collision** — offset `p_range` is the empty square the line stopped on. Past it
///   there may be anything, and at range the front of the line reaches it, so the distance is
///   cut to leave the front on the last free square before whatever it met.
pub fn push_max_travel(
    spaces: &Spaces,
    input_space: u8,
    input_data: u16,
    dir_index: usize,
    p_range: usize,
    free_pris: bool,
) -> usize {
    if !crate::push_range() {
        return 1;
    }
    if p_range == 0 {
        return 0;
    }

    let origin = UNPACK[input_data as usize];

    // A lone spy shoves anything and moves it one. It is the only thing that can touch a
    // finished six, so this exception is what keeps the endgame alive.
    if origin.spy != 0 && origin.captors == 1 {
        return 1;
    }

    // Freeing stays a one-square move: the freed pieces stand up into the square the pusher
    // walks onto, and at range there is no such square.
    if free_pris {
        return 1;
    }

    let ray = &PUSHRAY[input_space as usize][dir_index];
    if ray.is_empty() {
        return 0;
    }
    let ray = ray.as_slice();

    let strength: i32 =
        if origin.pris_count != 0 { origin.strength as i32 } else { origin.captors as i32 };
    let mut line_weight: i32 = 0;
    for n in 0..p_range {
        line_weight += spaces[ray[n] as usize].weight as i32;
    }

    let mut distance = strength - line_weight + 1;
    if distance < 1 {
        return 0;
    }

    let mut room: i32 = 0;
    for n in p_range..ray.len() {
        if spaces[ray[n] as usize].occupied {
            break;
        }
        room += 1;
        if room >= distance {
            break;
        }
    }
    if room < distance {
        distance = room;
    }
    if distance < 1 {
        return 0;
    }

    let dir = PUSH_DIRS[dir_index];
    let x = (input_space as i32 - 1) % 7;
    let y = (input_space as i32 - 1) / 7;
    let mut reach: i32 = 0;
    while reach < distance
        && (0..7).contains(&(x + dir[0] * (reach + 1)))
        && (0..7).contains(&(y + dir[1] * (reach + 1)))
    {
        reach += 1;
    }
    if reach < distance {
        distance = reach;
    }

    distance.max(0) as usize
}

/// How many squares a break out of `input_space` would cover, 0 meaning no break.
///
/// A break scatters the stack one piece per square along the direction, starting on the
/// origin itself, so the range can never exceed the piece count.
///
/// (Python takes `contr` here and never reads it: the square's own side is what the scatter
/// keys off. Dropped rather than carried.)
pub fn check_break(spaces: &Spaces, input_space: u8, input_data: u16, dir_index: usize) -> usize {
    let origin = UNPACK[input_data as usize];

    // nothing to scatter
    if !origin.occupied {
        return 0;
    }

    // weight equals piece count here: check_moves only calls this for stacks holding a spy,
    // which keeps dragons (weight 3, but a single piece) off this path.
    let pieces = origin.weight as usize;

    // the side holding the square owns the standing stack; the prisoners are the other's,
    // and they are the ones that fall first
    let control = origin.side;
    let prisoners = origin.pris_count as usize;

    let ray = &BREAKRAY[input_space as usize][dir_index];
    let mut break_range = 0usize;

    for i in 0..pieces {
        let targ = &spaces[ray.as_slice()[i] as usize];

        // every 7th step lands back on the origin, which is being emptied anyway, so it has
        // nothing to block against
        if i % 7 != 0 {
            // A royal or a dragon stops the scatter dead, either side's. The dragon is not
            // only a rule: drop_piece rebuilds the square through build_space, which zeroes
            // every field past a dragon, so a piece merged onto one would erase it.
            if targ.royal != 0 || targ.dragon != 0 {
                break;
            }

            // Everything left is asked of the piece that is FALLING and not of the player
            // breaking, and those are not always the same side: prisoners drop first, so the
            // early steps drop the other side's people and the rest our own. Getting this
            // wrong the obvious way -- measuring against the breaker -- lets a freed captive
            // land on a stack of its captor's and take the whole thing prisoner on the way.
            let dropping = if i < prisoners { 1 - control } else { control };

            // A square the falling piece is at home on is no obstacle at all; it joins
            // whatever is standing there. Only the other side's squares block, and only
            // these two ways.
            if targ.side != dropping {
                // More than one piece is too much to knock aside. The threshold grows by one
                // each full lap, which is what (i / 7) + 1 is for -- integer division, as it
                // was before the py2 port turned it into a float -- because by then this walk
                // has already dropped a piece here that the board it is reading doesn't show.
                // A lone enemy is not an obstacle: the piece lands on it and takes it captive.
                if targ.weight as usize > (i / 7) + 1 {
                    break;
                }

                // And an enemy holding anyone is an obstacle whatever it weighs. This is not
                // the test above said twice: a lone jailer weighs 2, so weight stops it on
                // the first lap, but by the second the tolerance is 2 as well and it would
                // slip straight through. What that would cost is the rule that a break never
                // frees anybody -- drop_piece's capture branch releases whoever the square it
                // lands on was holding, and this is the only thing keeping exe_break out of it.
                if targ.pris_flag != 0 {
                    break;
                }
            }
        }

        break_range += 1;
    }

    // a range of 1 drops every piece back on the origin, which is no move at all -- the break
    // has to get at least one piece off the square to be worth offering
    if break_range < 2 {
        return 0;
    }

    break_range
}

/// Every move out of one square. `None` where there are none at all, which is Python's `[]`
/// rather than a six-list of empties.
pub fn check_moves(origin: &Origin, contr: u8, spaces: &Spaces) -> Option<CheckMoves> {
    let moving_pris = origin.moving_pris;
    let spy_cap = origin.spy_break;
    let cache_space = origin.square;
    let cache_code = origin.code;
    let cache_s = UNPACK[cache_code as usize];

    let piece_weight: i32 =
        if moving_pris { cache_s.strength as i32 } else { cache_s.captors as i32 };

    // a dragon carries nothing and merges with nothing, so several checks below stop early
    let dragon_bool = cache_s.dragon != 0;
    // a spy in the moving stack, for capturing purposes
    let spy_bool = !dragon_bool && cache_s.spy != 0;

    let mut out = CheckMoves::default();

    // ###### DIAGONAL JUMP SUITE ######
    //
    // A spy still being held prisoner has exactly one move in it: breaking out. It doesn't
    // jump and it doesn't push, so both are skipped for it -- the stack it is breaking
    // belongs to its captor, not to it.
    //
    // A stack carrying more prisoners than it has pieces weighs less than nothing, and there
    // is no strand short enough for that.
    if !spy_cap && piece_weight > 0 {
        for strand in JUMPRAY[cache_space as usize].iter() {
            // The strand was drawn at import: as many squares as a full stack can travel,
            // already wrapped where this origin is entitled to wrap and already stopped
            // where it isn't. Weight decides how much of it is walked and nothing else, so
            // it is a slice.
            let reach = (piece_weight as usize).min(strand.len());

            for &square in &strand.as_slice()[..reach] {
                let check = &spaces[square as usize];

                let check_weight = check.weight as i32;

                // the wee movingPrisoners suite of breakages, and it has to come FIRST.
                // With prisoners in tow, ANY pieces at all, either side's, stop it. (check
                // is the whole square, so there is no per-side index here -- its weight
                // already covers both.) A stack carrying prisoners cannot change size, so
                // there is no landing on anyone, and the square stops the strand besides.
                //
                // This sat below the friendly branch, which `continue`s -- so it never saw a
                // square we control, and a carrying stack was offered a merge onto its own
                // pieces. "Either side's" was always the intent; the short-circuit shadowed
                // it. Nothing is lost by hoisting it: a dragon square can never hold
                // prisoners (build_space drops every other field on one), so moving_pris and
                // dragon_bool are mutually exclusive, and every gate it now precedes breaks
                // on these squares too. The way to land on an ally is to leave the prisoners
                // behind -- list_all_moves always offers that variant alongside this one.
                if moving_pris && check_weight != 0 {
                    break;
                }

                // Your own ground is open to you: any square your side holds is a legal
                // landing, whatever is standing there and whatever it weighs. Without this a
                // stack can't join a heavier friendly one, and since winning means gathering
                // the spy, all four pawns and the royal onto a single square, they could
                // never all arrive. Four pieces sit outside it, and they hold on either
                // side of the board: a spy jumps onto nothing, a royal is jumped onto by
                // nothing, a dragon does neither -- its square has no room for company, so a
                // merge wouldn't stack it, it would erase it -- and a stack carrying
                // prisoners lands on nothing at all, handled above.
                let friendly =
                    check.occupied && check.side == contr && check.dragon == 0 && check.royal == 0;

                if friendly && !dragon_bool && !spy_bool {
                    out.jumps.push(square);
                    continue;
                }

                // any pieces at all on the target and a spy is moving: stop
                if spy_bool && check_weight != 0 {
                    break;
                }
                // any pieces at all and a dragon is moving: pass over it, BECAUSE FLIGHT
                if dragon_bool && check_weight != 0 {
                    continue;
                }

                // allies captured on the target square
                if check.pris_flag != 0 {
                    if dragon_bool {
                        continue;
                    }
                    break;
                }

                // royals and dragons
                if check.royal != 0 || check.dragon != 0 {
                    if dragon_bool {
                        continue;
                    }
                    break;
                }

                // and finally: defenders heavier than attackers
                if check_weight > piece_weight {
                    break;
                }

                out.jumps.push(square);
            }
        }
    }

    // ###### ORTHO PUSH SUITE and BREAK SUITE ######
    //
    // Breaks are a spy's trick -- a stack holding one can scatter. That counts a spy held
    // prisoner just as much as one standing: breaking is how it gets out, and the stack that
    // scatters is its captor's.
    let can_break = cache_s.spy != 0 || spy_cap;

    for direction in 0..PUSH_DIRS.len() {
        // a held spy doesn't push either -- see the jump loop above
        if !spy_cap {
            let ray = &PUSHRAY[cache_space as usize][direction];

            // An empty ray is a first step off the edge. Neither branch below can come to
            // anything then -- freeing is still a push, and it needs a square to land on --
            // so the whole check is skipped rather than measured twice to reach nothing.
            if !ray.is_empty() {
                // the first square of the ray is the one get_legal_push_length checks first,
                // so the recorded destination is the square that was actually tested --
                // wrapped, and 0-based to match the jumps.
                let target = ray.as_slice()[0];

                let range_check = get_legal_push_length(
                    spaces, cache_space, cache_code, direction, contr, moving_pris, false,
                );
                if range_check != 0 {
                    out.pushes.push(target);
                }

                // The other branch. Only worth measuring where there is something to free,
                // which is why this asks would_free before paying for a second walk of the
                // line -- most pushes on most boards are into a square holding nobody.
                if would_free(&cache_s, &spaces[target as usize], contr, moving_pris) {
                    let free_check = get_legal_push_length(
                        spaces, cache_space, cache_code, direction, contr, moving_pris, true,
                    );
                    if free_check != 0 {
                        out.frees.push(target);
                    }
                }
            }
        }

        if can_break {
            let break_check = check_break(spaces, cache_space, cache_code, direction);
            if break_check != 0 {
                out.breaks.push(direction as u8);
            }
        }
    }

    if out.poss_moves_len() == 0 {
        None
    } else {
        Some(out)
    }
}

// ---- listing moves ----------------------------------------------------------------------

/// Every square the controller could move out of, **1 through 49 ascending**, paired with
/// whether this is our spy inside their square.
///
/// A square where its pieces are being held prisoner belongs to the other side, so it never
/// shows up here -- except in that one case: the spy can break out, and that is the only
/// move it has.
pub fn make_poss_list(spaces: &Spaces, contr: u8) -> Vec<(u8, bool)> {
    let mut out = Vec::with_capacity(12);

    for square in 1..50u8 {
        let s = &spaces[square as usize - 1];

        if s.side != contr {
            if s.cap_spy != 0 {
                out.push((square, true));
            }
            continue;
        }

        if s.my_pieces != 0 {
            out.push((square, false));
        }
    }

    out
}

/// Flattens a [`CheckMoves`] into moves out of one square, in the order the contract fixes:
/// **jumps, pushes, breaks, frees**.
pub fn list_moves(
    found: &CheckMoves,
    origin: u8,
    moving_pris: bool,
    into: &mut Vec<Move>,
    board: &Board,
    spaces: &Spaces,
    contr: u8,
) {
    for &target in found.jumps.as_slice() {
        into.push(Move::new(origin, MoveKind::Jump, target, moving_pris));
    }
    // Under the push-range variant a push is one move per distance it may travel, so a
    // four-strength stack shoving a lone pawn offers four. Off the variant every ceiling is 1
    // and this emits exactly the one move it always has, in the same order.
    for &target in found.pushes.as_slice() {
        let mut ceiling = 1usize;
        if crate::push_range() {
            if let Some(direction) = PUSHFROM[origin as usize][target as usize + 1] {
                let direction = direction as usize;
                let p_range = get_legal_push_length(
                    spaces, origin, board[origin as usize - 1], direction, contr, moving_pris,
                    false,
                );
                ceiling =
                    push_max_travel(spaces, origin, board[origin as usize - 1], direction,
                                    p_range, false);
            }
        }
        if ceiling <= 1 {
            into.push(Move::new(origin, MoveKind::Push, target, moving_pris));
        } else {
            for step in 1..=ceiling {
                into.push(Move::pushing(origin, target, moving_pris, step as u8));
            }
        }
    }
    // a break scatters the whole square, so there is no carrying-prisoners variant of one
    if !moving_pris {
        for &heading in found.breaks.as_slice() {
            into.push(Move::new(origin, MoveKind::Break, heading, false));
        }
    }
    // A push into allies being held is two moves onto one square -- shove the whole thing
    // along, or free them and stand where they stood. Both get listed, and a square can
    // appear in `pushes` and `frees` at once, which is the point of keeping them apart.
    for &target in found.frees.as_slice() {
        into.push(Move::new(origin, MoveKind::Free, target, moving_pris));
    }
}

/// Every move available to a side.
///
/// Squares holding prisoners get their jumps and pushes listed twice: once leaving the
/// prisoners behind and once dragging them along, in that order. A break always scatters the
/// whole square, so it has no variant. A push into allies being held is listed twice too, as
/// `push` and `free` -- but that pair comes out of [`check_moves`] rather than the loop here,
/// and only ever while not carrying prisoners, since a stack bringing its own can't free
/// anybody.
pub fn list_all_moves(board: &Board, contr: u8, spaces: &Spaces) -> Vec<Move> {
    let mut everything = Vec::new();

    for (origin, spy_break) in make_poss_list(spaces, contr) {
        if spy_break {
            // our spy on their square: the origin has to carry the spyBreak flag, and
            // breaking out is the only thing check_moves will offer back
            let t_origin = make_origin(board, origin, false, true);
            if let Some(found) = check_moves(&t_origin, contr, spaces) {
                list_moves(&found, origin, false, &mut everything, board, spaces, contr);
            }
            continue;
        }

        let s = &spaces[origin as usize - 1];
        let carrying: &[bool] = if s.pris_count != 0 { &[false, true] } else { &[false] };

        for &moving_pris in carrying {
            let t_origin = make_origin(board, origin, moving_pris, false);
            if let Some(found) = check_moves(&t_origin, contr, spaces) {
                list_moves(&found, origin, moving_pris, &mut everything, board, spaces, contr);
            }
        }
    }

    everything
}

// ---- entering ---------------------------------------------------------------------------
// Before anyone moves, the pieces go onto the board one at a time, players alternating:
// royals, then the four pawns, then the spies. A royal or a pawn may not be entered touching
// anything you already control, your dragon included; a spy ignores that and only needs an
// empty square. Blue enters first, so red takes the opening move.
//
// WHERE a piece goes is `chooseEntry`, which stays in Python for good: Perlin noise over
// floats, seeded through CPython's Mersenne Twister. These four are the rules around it,
// which are integer-only and portable.

/// The eight neighbours of a square. Entering does not wrap round the edges the way moves do
/// -- the backup's placement threw away any neighbour off the board, so this does too.
#[rustfmt::skip]
const ENTER_ADJ: [[i32; 2]; 8] = [
    [-1, -1], [-1, 0], [-1, 1],
    [ 0, -1],          [ 0, 1],
    [ 1, -1], [ 1, 0], [ 1, 1],
];

/// The on-board neighbours of a 1-based square, as 1-based squares.
pub fn entering_neighbours(square: u8) -> Vec<u8> {
    let x = (square as i32 - 1) % 7;
    let y = (square as i32 - 1) / 7;

    let mut out = Vec::with_capacity(8);
    for step in ENTER_ADJ {
        let (nx, ny) = (x + step[0], y + step[1]);
        if !(0..7).contains(&nx) || !(0..7).contains(&ny) {
            continue;
        }
        out.push((ny * 7 + nx + 1) as u8);
    }
    out
}

/// Whether a piece may be entered on a square. Pieces the other side controls are no
/// obstacle -- only your own crowd you out.
pub fn entering_legal(spaces: &Spaces, square: u8, contr: u8, is_spy: bool) -> bool {
    if spaces[square as usize - 1].occupied {
        return false;
    }

    // a spy slips in anywhere empty
    if is_spy {
        return true;
    }

    for neighbour in entering_neighbours(square) {
        let s = &spaces[neighbour as usize - 1];
        if s.occupied && s.side == contr {
            return false;
        }
    }

    true
}

/// Every square a piece could be entered on.
pub fn entering_options(spaces: &Spaces, contr: u8, is_spy: bool) -> Vec<u8> {
    (1..50u8).filter(|&square| entering_legal(spaces, square, contr, is_spy)).collect()
}

/// The whole entering order, as `(side, piece)` steps in the order they happen.
pub fn entering_sequence() -> Vec<(u8, Piece)> {
    let mut order = vec![Piece::Royal];
    for _ in 0..4 {
        order.push(Piece::Pawns);
    }
    order.push(Piece::Spy);

    let mut steps = Vec::with_capacity(order.len() * 2);
    for piece in order {
        // blue enters first at every stage
        for contr in 0..2u8 {
            steps.push((contr, piece));
        }
    }
    steps
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::board::{build_space, Board, EMPTY_BOARD};

    fn at(board: &mut Board, square: u8, code: u16) {
        board[square as usize - 1] = code;
    }

    fn moves(board: &Board, contr: u8) -> Vec<(u8, MoveKind, u8, bool)> {
        list_all_moves(board, contr, &parse_board(board))
            .iter()
            .map(|m| (m.origin, m.kind, m.target, m.moving_pris))
            .collect()
    }

    /// A red stack on d4 holding blue's spy prisoner, on an otherwise empty board.
    fn spy_in_the_cells() -> Board {
        let mut board = EMPTY_BOARD;
        at(&mut board, 25, build_space(1, 0, 1, 1, 0, 1, 0, 1).unwrap());
        board
    }

    #[test]
    fn a_held_spy_can_only_break_out() {
        // The square belongs to red, so blue has no business on it -- except that blue's spy
        // is inside, and breaking is how it gets out. Four directions, no jumps, no pushes.
        assert_eq!(
            moves(&spy_in_the_cells(), 0),
            (0..4).map(|d| (25, MoveKind::Break, d, false)).collect::<Vec<_>>()
        );
    }

    #[test]
    fn generation_order_is_the_contract() {
        // Red holds the same square, so it gets the full treatment, and the ORDER is what is
        // being asserted here: not carrying prisoners first (its jumps, then its breaks),
        // then the carrying pass (jumps only -- a break scatters the whole square, so it has
        // no carrying variant). The port fixtures pick moves by index into this list.
        let got = moves(&spy_in_the_cells(), 1);

        let jumps: Vec<_> = got.iter().filter(|m| m.1 == MoveKind::Jump && !m.3).collect();
        let breaks: Vec<_> = got.iter().filter(|m| m.1 == MoveKind::Break).collect();
        let carried: Vec<_> = got.iter().filter(|m| m.3).collect();

        assert_eq!(jumps.len(), 8, "weight 2 walks two squares of each strand");
        assert_eq!(breaks.len(), 4);
        // strength is 1 with the prisoner in tow, so the carrying pass reaches one square
        assert_eq!(carried.len(), 4);
        assert!(carried.iter().all(|m| m.1 == MoveKind::Jump));

        // and they arrive in that order, which is the part that matters
        assert!(got[..8].iter().all(|m| m.1 == MoveKind::Jump && !m.3));
        assert!(got[8..12].iter().all(|m| m.1 == MoveKind::Break));
        assert!(got[12..].iter().all(|m| m.3));
    }

    #[test]
    fn a_push_into_prisoners_is_offered_twice() {
        // Blue pawn and royal on d4, shoving a red pawn on e4 that is holding a blue pawn.
        // Two different moves onto one square: shove the lot along, or free the pawn and
        // stand where it stood. Both are legal, so both are listed -- push before free.
        let mut board = EMPTY_BOARD;
        at(&mut board, 25, build_space(0, 0, 0, 1, 1, 0, 0, 0).unwrap());
        at(&mut board, 26, build_space(1, 0, 0, 1, 0, 0, 1, 1).unwrap());

        let got = moves(&board, 0);
        let tail: Vec<_> = got[got.len() - 2..].to_vec();
        assert_eq!(tail, vec![(25, MoveKind::Push, 25, false), (25, MoveKind::Free, 25, false)]);
    }

    #[test]
    fn a_carrying_stack_lands_on_nobody_not_even_its_own() {
        // Blue, three pawns on d4 holding one red pawn captive: captors 3 with the prisoner
        // left behind, strength 2 with him in tow, so the reach differs between the two
        // passes. The up-right strand out of d4 is e5, f6, g7.
        //
        // What is under test is the ORDER of the gates, not their conditions. The carrying
        // check has to sit ABOVE the friendly short-circuit, because that branch `continue`s
        // -- anything below it never sees a square the mover controls, and a carrying stack
        // was being offered a merge onto its own pieces however plainly the comment said
        // "either side's". This port inherited that ordering from the Python and so
        // inherited the bug, which is exactly why neither golden could see it: the two
        // engines agreed. Mirrors tests/test_carry_rules.py.
        const E5: u8 = 32; // targets are 0-based; the squares themselves are 33, 41, 49
        const F6: u8 = 40;
        const G7: u8 = 48;

        let jumps_from_d4 = |board: &Board, pris: bool| -> Vec<u8> {
            moves(board, 0)
                .iter()
                .filter(|m| m.0 == 25 && m.1 == MoveKind::Jump && m.3 == pris)
                .map(|m| m.2)
                .collect()
        };

        let mut clear = EMPTY_BOARD;
        at(&mut clear, 25, build_space(0, 0, 0, 3, 0, 0, 1, 1).unwrap());
        let open = jumps_from_d4(&clear, true);
        assert!(open.contains(&E5) && open.contains(&F6), "strength 2 walks two squares");
        assert!(!open.contains(&G7), "and no further");

        // one of our own pawns on e5, and the strand is shut -- the landing AND the passage
        let mut blocked = clear;
        at(&mut blocked, 33, build_space(0, 0, 0, 1, 0, 0, 0, 0).unwrap());
        let carrying = jumps_from_d4(&blocked, true);
        assert!(!carrying.contains(&E5), "a carrying stack may not merge with an ally");
        assert!(!carrying.contains(&F6), "and the ally stops the strand, not just the landing");

        // leave the prisoner behind and the same square is open again, three squares deep
        let unencumbered = jumps_from_d4(&blocked, false);
        for target in [E5, F6, G7] {
            assert!(unencumbered.contains(&target), "target {} should be open", target);
        }
    }
}
