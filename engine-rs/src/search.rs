//! Alpha-beta, and the tables it carries between moves. All of the speed lives here.
//!
//! Alpha-beta only prunes when a good move is tried early: search the best move first and it
//! examines roughly the square root of the tree, search the worst first and it degenerates
//! towards plain minimax. None of the ordering machinery below changes the score a search
//! returns -- it only changes the order moves are tried, and so how much of the tree can be
//! skipped.
//!
//! Four things here produce a search that works and plays subtly worse if they are wrong,
//! which is the worst failure mode available:
//!
//! * [`Search::order_moves`] sorts **stably, on the score alone**, so equal-scoring moves
//!   keep board order. [`Move`] is deliberately not `Ord` so that sorting the pairs whole --
//!   which would fall through to comparing moves and quietly reorder the ties -- doesn't
//!   compile.
//! * Scores go into the table from the **side to move's** point of view and are negated on
//!   probe; the `Lower`/`Upper` bound flags swap with the sign.
//! * Root entries are stamped with the ko generation, everything below the root with `-1`.
//! * The staged-candidate optimisation is safe *because* the table is keyed on the board
//!   itself and not on a hash of it. See [`Search::minimax`].
//!
//! ## State, and who owns it
//!
//! Python keeps all of this in module-level globals, which is correct for one game per
//! process and a correctness hazard for two. [`Search`] holds it instead, and the free
//! functions at the bottom of this file wrap one global [`Search`] to give the Python side
//! the module it already talks to. Anything serving several games at once -- `ai_pool.py` --
//! should own a [`Search`] per game rather than reaching for those.

use std::collections::{HashMap, HashSet};
use std::hash::{BuildHasherDefault, Hasher};
use std::sync::{LazyLock, Mutex};

use crate::board::{check_for_winner, Board};
use crate::eval::{full_check, Score, INFINITY, WIN_SCORE};
use crate::exec::perform_one_step;
use crate::movegen::{list_all_moves, parse_board, Spaces};
use crate::{Move, MoveKind};

/// How many positions one generation of the table holds before it is retired, unless a caller
/// says otherwise. Mirrors the Python module's `AI.TABLE_LIMIT` default.
///
/// This is the ceiling for a machine playing one game with memory to spare. It is deliberately
/// **not** the ceiling a server should use: `ai_pool.py` runs several workers on a small box and
/// drops it to 50,000 for exactly that reason, so the limit is a field on [`Search`] rather than
/// a constant. A hard-coded constant here would silently override that decision the moment the
/// compiled engine was installed -- the knob would still be there, still be set, and no longer
/// reach anything.
pub const DEFAULT_TABLE_LIMIT: usize = 300_000;

/// Whether any of the table survives from one move to the next. Turning this off restores
/// what the search did before -- everything thrown away at the start of every move -- which
/// is what the gain from keeping it is measured against.
pub const PERSIST: bool = true;

/// Whether a node tries the table's move before generating any others. Off restores
/// generating everything up front, which is the only way to get the node counts this search
/// produced before staging existed -- so it is what the change is measured against.
pub const STAGED: bool = true;

/// What a stored score is worth. A search that cut off never finished looking, so its score
/// is only a bound -- treating one as exact is the usual way this goes wrong.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Flag {
    /// the search completed inside its window: this is the real value
    Exact,
    /// it cut off high, so the true value is at least this
    Lower,
    /// nothing beat alpha, so the true value is at most this
    Upper,
}

impl Flag {
    /// A floor on a score becomes a ceiling once the score is negated.
    fn flipped(self) -> Flag {
        match self {
            Flag::Exact => Flag::Exact,
            Flag::Lower => Flag::Upper,
            Flag::Upper => Flag::Lower,
        }
    }
}

/// One table entry: how deep the search that produced it went, what it found, what that
/// score is worth, and the move it liked.
#[derive(Clone, Copy, Debug)]
pub struct Entry {
    pub depth: i32,
    pub score: Score,
    pub flag: Flag,
    pub best: Option<Move>,
}

/// The board itself, the side to move, and a stamp. **Not a hash of the board** -- the board
/// is 49 codes and one canonical value per position, which is the whole reason this is cheap
/// here, and it is what makes the staged candidate safe.
type TableKey = (Board, u8, i64);

// ---- hashing ----------------------------------------------------------------------------

/// A small non-cryptographic hasher, in the FxHash shape rustc uses on its own maps.
///
/// SipHash over 98 bytes at every interior node is real time spent to defend against hash
/// flooding by an adversary who would have to be choosing our board positions. **This does
/// not weaken the key**: the map still stores and compares the whole board, so a hash
/// collision costs a comparison and never a wrong answer. That is exactly the distinction
/// that makes playing a stored move without verifying it legal safe here, and it is why
/// switching to a Zobrist *key* would be a different and much more delicate change.
#[derive(Default, Clone, Copy)]
pub struct FxHasher {
    hash: u64,
}

const FX_SEED: u64 = 0x51_7c_c1_b7_27_22_0a_95;

impl FxHasher {
    #[inline]
    fn add(&mut self, word: u64) {
        self.hash = (self.hash.rotate_left(5) ^ word).wrapping_mul(FX_SEED);
    }
}

impl Hasher for FxHasher {
    #[inline]
    fn write(&mut self, bytes: &[u8]) {
        let mut chunks = bytes.chunks_exact(8);
        for chunk in &mut chunks {
            self.add(u64::from_ne_bytes(chunk.try_into().unwrap()));
        }
        let rest = chunks.remainder();
        if !rest.is_empty() {
            let mut buf = [0u8; 8];
            buf[..rest.len()].copy_from_slice(rest);
            self.add(u64::from_ne_bytes(buf));
        }
    }

    #[inline]
    fn write_u8(&mut self, value: u8) {
        self.add(value as u64);
    }

    #[inline]
    fn write_u64(&mut self, value: u64) {
        self.add(value);
    }

    #[inline]
    fn write_i64(&mut self, value: i64) {
        self.add(value as u64);
    }

    #[inline]
    fn write_usize(&mut self, value: usize) {
        self.add(value as u64);
    }

    #[inline]
    fn finish(&self) -> u64 {
        self.hash
    }
}

type FxBuild = BuildHasherDefault<FxHasher>;
type FxMap<K, V> = HashMap<K, V, FxBuild>;
type FxSet<K> = HashSet<K, FxBuild>;

// ---- the search -------------------------------------------------------------------------

/// Everything one game's search remembers.
pub struct Search {
    /// Positions already searched, so the same one reached by a different order of moves
    /// doesn't get searched twice.
    ///
    /// This used to be emptied at the start of every move, so the only reuse it ever got was
    /// between the deepening passes inside one search -- which threw away most of its value,
    /// since a move advances the game by one ply and the position the next search starts from
    /// is one the last search had already looked at, along with most of the tree under it.
    ///
    /// What kept it from persisting is that it has to stay bounded and that root answers go
    /// stale; the two generations below handle the first and the ko stamp the second.
    table: FxMap<TableKey, Entry>,
    /// The retired generation. When `table` fills it becomes this and a fresh one starts, so
    /// the table never holds more than two generations and what ages out is what has gone
    /// longest without being written -- rather than everything at once, which is what
    /// clearing did.
    table_old: FxMap<TableKey, Entry>,

    /// Moves that caused a cutoff, keyed by depth, two deep.
    killers: FxMap<i32, Vec<Move>>,
    /// A running tally of which moves have caused cutoffs anywhere, keyed by the move itself.
    history: FxMap<Move, Score>,

    /// Every position the game has stood in. A move may not put the board back into any of
    /// them -- not a window of recent turns but the whole history.
    ko_track: FxSet<Board>,
    /// Bumped whenever that set changes, and never reused. Root answers had ko-breaking moves
    /// struck off them, so they are only good for the history that was standing at the time;
    /// stamping them with this retires them the moment another position is recorded, without
    /// having to work out which of them the new entry actually invalidated.
    ko_generation: i64,

    /// How many boards the last search looked at.
    pub calc_count: u64,

    /// How many entries one generation holds before it is retired. Owned per-`Search` so that a
    /// server can cap it without every other caller inheriting the cap -- see
    /// [`DEFAULT_TABLE_LIMIT`].
    table_limit: usize,
}

impl Default for Search {
    fn default() -> Self {
        Search::new()
    }
}

impl Search {
    pub fn new() -> Search {
        Search {
            table: FxMap::default(),
            table_old: FxMap::default(),
            killers: FxMap::default(),
            history: FxMap::default(),
            ko_track: FxSet::default(),
            ko_generation: 0,
            calc_count: 0,
            table_limit: DEFAULT_TABLE_LIMIT,
        }
    }

    /// Cap how much the transposition table may hold.
    ///
    /// Deliberately not clamped upwards to anything: a caller that wants one entry gets one
    /// entry. The search still answers correctly with a tiny table -- it just re-searches
    /// positions it would otherwise have remembered -- so the failure mode of setting this too
    /// low is slowness, which is visible, rather than a wrong move, which is not.
    pub fn set_table_limit(&mut self, limit: usize) {
        self.table_limit = limit;
    }

    pub fn table_limit(&self) -> usize {
        self.table_limit
    }

    /// Forgets everything learned about the game just played. The table stays true across
    /// moves, but a new game is a different game, and leaving a full one standing would only
    /// spend memory on positions the new one is unlikely to reach.
    pub fn new_game(&mut self) {
        self.killers.clear();
        self.history.clear();
        self.table.clear();
        self.table_old.clear();
    }

    // ---- ko ------------------------------------------------------------------------------
    // A move may not put the board back into any position the game has already stood in.
    // Because a board is one canonical value that hashes on its own, the comparison is cheap
    // enough to make BEFORE the move instead of after: the search asks about each of its root
    // moves and never offers one that repeats, so there is no rejected move to remember.

    /// Wipes the history. A new game has nothing to repeat.
    pub fn ko_reset(&mut self) {
        self.ko_track.clear();
        self.ko_generation += 1;
    }

    /// Files a position the game has actually reached.
    pub fn ko_record(&mut self, board: &Board) {
        self.ko_track.insert(*board);
        self.ko_generation += 1;
    }

    /// Whether a board returns the game to a position it has already been in.
    pub fn ko_breaks(&self, board: &Board) -> bool {
        self.ko_track.contains(board)
    }

    pub fn ko_generation(&self) -> i64 {
        self.ko_generation
    }

    /// Replaces the ko history wholesale -- how the Python side hands its `koTrack` over when
    /// it delegates a search.
    pub fn set_ko_track(&mut self, positions: impl IntoIterator<Item = Board>) {
        self.ko_track = positions.into_iter().collect();
        self.ko_generation += 1;
    }

    // ---- ordering ------------------------------------------------------------------------

    /// Called when a move caused a cutoff, so it gets looked at sooner next time.
    fn remember_cutoff(&mut self, mv: Move, depth_track: i32) {
        let slot = self.killers.entry(depth_track).or_default();
        if !slot.contains(&mv) {
            slot.insert(0, mv);
            slot.truncate(2);
        }

        // deep cutoffs pruned more, so they count for more
        *self.history.entry(mv).or_insert(0) += (depth_track * depth_track) as Score;
    }

    /// Sorts moves so the promising ones go first: the move the last, shallower search liked,
    /// then captures biggest-first, then whatever has caused cutoffs before.
    pub fn order_moves(
        &self,
        moves: Vec<Move>,
        spaces: &Spaces,
        contr: u8,
        depth_track: i32,
        pv_move: Option<Move>,
    ) -> Vec<Move> {
        // hoisted: read once per move, and this is the busiest loop in the search after move
        // generation itself
        let slot = self.killers.get(&depth_track);
        let mut scored: Vec<(Score, Move)> = Vec::with_capacity(moves.len());

        for mv in moves {
            if Some(mv) == pv_move {
                scored.push((1_000_000, mv));
                continue;
            }

            let mut score: Score = 0;

            if mv.kind != MoveKind::Break {
                let targ = &spaces[mv.target as usize];

                if mv.kind == MoveKind::Jump && targ.occupied && targ.side != contr {
                    // a jump onto the enemy takes everything standing there
                    score += 1000 + targ.captors as Score * 100;
                } else if mv.kind == MoveKind::Push && targ.pris_flag != 0 && targ.side != contr {
                    // a push into a jailer frees our own people
                    score += 800 + targ.pris_count as Score * 100;
                }
            }

            if let Some(slot) = slot {
                if let Some(i) = slot.iter().position(|k| *k == mv) {
                    score += 500 - i as Score * 10;
                }
            }

            score += self.history.get(&mv).copied().unwrap_or(0);

            scored.push((score, mv));
        }

        // A stable sort, so equal-scoring moves keep board order and the search stays
        // repeatable -- and on the score alone, never on the pair, for the same reason.
        scored.sort_by(|a, b| b.0.cmp(&a.0));
        scored.into_iter().map(|(_, mv)| mv).collect()
    }

    // ---- the search itself ----------------------------------------------------------------

    /// Alpha-beta. Returns `(score, move)`, with no move where there was nothing to choose.
    ///
    /// Scores always read from `root_contr`'s side of the board, so the search maximises on
    /// that side's turn and minimises on the other's.
    ///
    /// `pv_move` is what a previous, shallower pass thought was best here; it is tried first.
    /// `at_root` says this is the move actually about to be played, so the ko rule applies --
    /// deeper nodes are hypothetical and carry a history of their own that the game's record
    /// knows nothing about, which is why the backup only tested at the root either.
    pub fn minimax(
        &mut self,
        board: &Board,
        contr: u8,
        root_contr: u8,
        mut alpha: Score,
        mut beta: Score,
        mut depth_track: i32,
        mut pv_move: Option<Move>,
        at_root: bool,
        // How many unresolved-gather extensions this path may still spend. See the leaf check
        // below. One is all the rule needs; the count exists so a pathological position cannot
        // walk the extension down forever.
        mut gather_ext: u8,
    ) -> (Score, Option<Move>) {
        self.calc_count += 1;

        // A gather is not a win until it has survived a reply. The rule is stated as a property
        // of the position rather than as an extra turn -- **you have won if, at the start of
        // your own turn, your spy, four pawns and royal are still on one square** -- which is
        // the same thing and needs no flag carried between plies.
        //
        // So this node is terminal only when the side to move is the side that has gathered:
        // they got there, the opponent had their answer, and it did not come. A gather by the
        // side that just moved is not terminal, because `contr` is precisely the player who
        // still has a reply to find. Mirrors ai.py's minimax exactly; the two must agree or
        // golden_search.txt will say so.
        let (_, winner) = check_for_winner(board);
        if winner[contr as usize] != 0 {
            // scaling by the depth left makes a win now beat the same win three moves out
            let scale = (depth_track + 1) as Score;
            if contr == root_contr {
                return (WIN_SCORE * scale, None);
            }
            return (-WIN_SCORE * scale, None);
        }

        // An unresolved gather is not a position to stand and evaluate. The terminal test above
        // deliberately does not fire for a stack the side to move has just been handed --
        // `contr` is the player who still owes a reply -- but a leaf has no ply left to find it
        // in, and `evaluate_sides` answers WIN_SCORE flat for six on a square. So the horizon
        // undoes the rule: at depth N the search sees a gather made on the last ply, calls it
        // won, and never looks at the lone spy standing next to it whose push scatters the lot.
        //
        // One more ply is exactly what the rule asks for and no more, because the rule is "it
        // has to survive one reply". Extending to 1 makes this leaf behave as an ordinary
        // depth-1 node, which is not a coincidence and is what lets the result go in the table
        // as one: a real depth-1 search of this position does the same thing, since its
        // children are depth-0 nodes where `contr` has flipped and the terminal test fires.
        //
        // Mirrors ai.py's minimax exactly; the two must agree or golden_search.txt will say so.
        if depth_track == 0 {
            if gather_ext == 0 || winner[1 - contr as usize] == 0 {
                return (full_check(board, root_contr), None);
            }
            depth_track = 1;
            gather_ext -= 1;
        }

        // Only interior nodes consult the table, and only below the two checks above. Probing
        // first would let a leaf answer with a stored one-ply search instead of the standing
        // evaluation -- searching deeper than it was asked to, which makes a depth-N answer
        // stop meaning depth N. It also skips building the key at leaves, which are most nodes.
        //
        // The root has moves struck off it that the same position reached deeper still has,
        // so its answer is filed apart from theirs rather than standing in for them. It is
        // also the only answer that goes stale, because those strikings-off came from the ko
        // history. Everything below the root never looked at that history to begin with, so
        // it is filed under -1 and stays good for the rest of the game -- which is what makes
        // keeping the table worth anything.
        let stamp = if at_root { self.ko_generation } else { -1 };
        let table_key: TableKey = (*board, contr, stamp);

        let entry = self
            .table
            .get(&table_key)
            .copied()
            .or_else(|| self.table_old.get(&table_key).copied());

        // Which way this node is being read. alpha and beta are kept in root_contr's terms
        // all the way down, so a node is a max node exactly when the side to move is the side
        // the search is being run for.
        let maximizing = contr == root_contr;

        if let Some(e) = entry {
            if e.depth >= depth_track {
                // Searched before, at least as deep as we need now.
                //
                // Entries are filed from the point of view of the side to move in them, not
                // of the side the search is being run for. Those were the same thing while
                // the table lasted a single move, but it now outlives the move and the two
                // sides take turns being root_contr. Filed the old way, blue's search would
                // read back red's score for the same position and take it at face value with
                // the sign the wrong way round.
                let score = if maximizing { e.score } else { -e.score };
                let flag = if maximizing { e.flag } else { e.flag.flipped() };

                match flag {
                    Flag::Exact => return (score, e.best),
                    Flag::Lower => alpha = alpha.max(score),
                    Flag::Upper => beta = beta.min(score),
                }

                // the bound alone already settles it
                if alpha >= beta {
                    return (score, e.best);
                }
            }
        }

        // The window the search below actually runs with, after any tightening the table did.
        // What the answer is worth has to be judged against this, not against the window the
        // caller asked for -- a value landing between the two is a bound, not a real score.
        let search_alpha = alpha;
        let search_beta = beta;

        // whatever won here last time is the best guess at what wins here now
        if pv_move.is_none() {
            if let Some(e) = entry {
                pv_move = e.best;
            }
        }

        // Most interior nodes cut off -- 85% of them, measured -- and 78% of those cut off on
        // the very first move tried. Where there is a stored move, that first move IS the
        // stored move, because order_moves puts it in front of everything else. So generating
        // the other forty, scoring them and sorting them is work done to be thrown away.
        //
        // So the stored move is tried before anything is generated. What follows is the same
        // list in the same order with that one move lifted out, so the search sees exactly
        // the sequence it saw before: same nodes, same cutoffs, same answer, minus the
        // generation at every node the stored move settles. Parsing the board goes with it,
        // since only ordering needs it.
        //
        // Safe because the table is keyed on the board itself and not on a hash of it: there
        // are no collisions to guard against, so a stored move was generated for exactly this
        // position and is legal here by construction. An engine keying on a Zobrist hash
        // would have to verify the move before playing it.
        let staged_first = if STAGED { pv_move } else { None };
        let mut staged = staged_first;
        let mut generated: Option<std::vec::IntoIter<Move>> = None;

        let mut best_score: Option<Score> = None;
        let mut best_move: Option<Move> = None;

        loop {
            let mv = match staged.take() {
                Some(mv) => mv,
                None => {
                    if generated.is_none() {
                        let spaces = parse_board(board);
                        let mut rest = self.order_moves(
                            list_all_moves(board, contr, &spaces),
                            &spaces,
                            contr,
                            depth_track,
                            pv_move,
                        );
                        if staged_first.is_some() {
                            // order_moves scores the pv above everything, so this drops the
                            // first entry and nothing else
                            rest.retain(|mv| Some(*mv) != staged_first);
                        }
                        generated = Some(rest.into_iter());
                    }

                    match generated.as_mut().unwrap().next() {
                        Some(mv) => mv,
                        None => break,
                    }
                }
            };

            let child = perform_one_step(board, contr, mv);

            // a move that returns the game somewhere it has already been isn't one to offer
            if at_root && self.ko_breaks(&child) {
                continue;
            }

            let score = self
                // gather_ext rides down the path, not across the tree: a sibling branch gets
                // whatever this node was handed, and only the branch that spent one is short.
                .minimax(&child, 1 - contr, root_contr, alpha, beta, depth_track - 1, None, false,
                         gather_ext)
                .0;

            let better = match best_score {
                None => true,
                Some(best) => {
                    if maximizing {
                        score > best
                    } else {
                        score < best
                    }
                }
            };
            if better {
                best_score = Some(score);
                best_move = Some(mv);
            }

            if maximizing {
                alpha = alpha.max(score);
            } else {
                beta = beta.min(score);
            }

            // the other side would never let us down this branch, so stop reading it
            if beta <= alpha {
                self.remember_cutoff(mv, depth_track);
                break;
            }
        }

        // nothing legal anywhere, so the position stands as it is
        let best_score = match best_score {
            Some(score) => score,
            None => return (full_check(board, root_contr), None),
        };

        // File the answer away, recording how much of it we actually established. Scores that
        // come back from a win are left out on purpose: they are scaled by the depth
        // remaining, so the same win filed at one depth reads as a different score at another.
        if best_score.abs() < WIN_SCORE {
            // fell short of the window, so all we learned is a ceiling; ran past it, so all
            // we learned is a floor; landed inside it and the search really did settle it
            let mut flag = if best_score <= search_alpha {
                Flag::Upper
            } else if best_score >= search_beta {
                Flag::Lower
            } else {
                Flag::Exact
            };

            // filed from the side to move's point of view, the same turn the probe undoes
            let mut keep_score = best_score;
            if !maximizing {
                keep_score = -best_score;
                flag = flag.flipped();
            }

            if entry.is_none() || depth_track >= entry.unwrap().depth {
                // writes always land in the live generation, so an entry found in the old one
                // and still worth having is carried forward rather than aged out again
                self.table.insert(
                    table_key,
                    Entry { depth: depth_track, score: keep_score, flag, best: best_move },
                );
            }
        }

        (best_score, best_move)
    }

    /// Picks a side's move, as `(score, move)`.
    ///
    /// Searches depth 1, then 2, and so on, handing each pass's answer to the next to try
    /// first. The shallow passes look like waste, but the tree grows several times over per
    /// level, so they cost little and the ordering they hand up saves far more than they
    /// spend. It also means an interrupted search still has a usable move to fall back on.
    ///
    /// The window opens at `±INFINITY` rather than the backup's `±2048`, which a win could
    /// exceed once the depth scaling pushed it past 2048 -- that clipped the pruning against
    /// real scores.
    pub fn choose_move(&mut self, board: &Board, contr: u8, depth: i32) -> (Score, Option<Move>) {
        self.calc_count = 0;

        if !PERSIST {
            self.new_game();
        }

        // Killers are a move that cut off at a given depth, with no record of where -- worth
        // keeping across the deepening passes of one search, where the position really is the
        // same, and misleading across a move, where it isn't. So they start each move empty.
        self.killers.clear();

        // History is keyed by the move itself and is meant to say which moves tend to be
        // worth trying anywhere, so it is worth carrying between moves. Halving it each time
        // keeps what recent searches learned worth more than what old ones did, and stops the
        // counts growing until they swamp the capture and killer bonuses they are meant to
        // break ties between. Entries that have decayed to nothing are dropped rather than
        // kept at zero.
        if !self.history.is_empty() {
            self.history = self
                .history
                .iter()
                .map(|(mv, count)| (*mv, count / 2))
                .filter(|(_, half)| *half != 0)
                .collect();
        }

        // Retire a generation when the live one fills. Everything the retired one holds is
        // still reachable until the next changeover, and anything still being asked for gets
        // written forward into the live table as it is found.
        if self.table.len() > self.table_limit {
            self.table_old = std::mem::take(&mut self.table);
        }

        let mut best: (Score, Option<Move>) = (0, None);
        for step in 1..=depth {
            best = self.minimax(board, contr, contr, -INFINITY, INFINITY, step, best.1, true, 1);
        }

        best
    }

    /// Picks a move and plays it. Returns `(board, move, score)`, with the board unchanged if
    /// there was no legal move.
    pub fn take_turn(&mut self, board: &Board, contr: u8, depth: i32) -> (Board, Option<Move>, Score) {
        let (score, mv) = self.choose_move(board, contr, depth);
        match mv {
            None => (*board, None, score),
            Some(mv) => (perform_one_step(board, contr, mv), Some(mv), score),
        }
    }
}

// ---- the one global game ----------------------------------------------------------------
// Python keeps the table, the killers, the history and the ko record in module-level globals
// -- fine for one game per process, a correctness hazard for two. These wrap a single Search
// so that the Python module's shape survives the port; anything serving several games at once
// owns its own Search instead. See ai_pool.py, which loads one game's state, runs, and drops.

static GAME: LazyLock<Mutex<Search>> = LazyLock::new(|| Mutex::new(Search::new()));

/// The one global search state, for the Python-facing API. Poisoning is treated as fatal
/// rather than recoverable: a panic mid-search leaves the tables saying things about a tree
/// that was never finished.
pub fn game() -> std::sync::MutexGuard<'static, Search> {
    GAME.lock().expect("the search state was poisoned by an earlier panic")
}

pub fn new_game() {
    game().new_game();
}

pub fn ko_reset() {
    game().ko_reset();
}

pub fn ko_record(board: &Board) {
    game().ko_record(board);
}

pub fn ko_breaks(board: &Board) -> bool {
    game().ko_breaks(board)
}

pub fn choose_move(board: &Board, contr: u8, depth: i32) -> (Score, Option<Move>) {
    game().choose_move(board, contr, depth)
}

pub fn take_turn(board: &Board, contr: u8, depth: i32) -> (Board, Option<Move>, Score) {
    game().take_turn(board, contr, depth)
}

/// How many boards the last search looked at.
pub fn calc_count() -> u64 {
    game().calc_count
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::board::{build_space, EMPTY_BOARD};
    use crate::eval::SCALE;

    /// Blue with the spy and four pawns gathered on d4, and the royal a jump away on e5.
    /// One move from six on a square, which is the win.
    ///
    /// The stray red pawn on a1 is load-bearing under the delayed win, and it is easy to
    /// leave out. Gathering only wins once it has survived the opponent's reply, so a board
    /// where the opponent has no legal move never reaches the turn that confirms it -- the
    /// search finds nothing to play, returns the standing evaluation, and the depth scaling
    /// this test is named for never appears. The pawn is far enough away to be irrelevant to
    /// the position and near enough to be a move.
    fn one_move_from_winning() -> Board {
        let mut board = EMPTY_BOARD;
        board[24] = build_space(0, 0, 1, 4, 0, 0, 0, 0).unwrap();
        board[32] = build_space(0, 0, 0, 0, 1, 0, 0, 0).unwrap();
        board[0] = build_space(1, 0, 0, 1, 0, 0, 0, 0).unwrap();
        board
    }

    /// Blue's six already gathered on d4, with a lone red spy orthogonally beside them on d5.
    /// Under the delayed win this is not a won game: red is to move, and a lone spy's push
    /// shatters what it hits, which is the one answer a completed stack has. A break needs
    /// the enemy's own spy inside the stack and landing on it needs six of your own, so
    /// nothing else on the board could touch it.
    fn gathered_with_a_spy_alongside() -> Board {
        let mut board = EMPTY_BOARD;
        board[24] = build_space(0, 0, 1, 4, 1, 0, 0, 0).unwrap();
        board[31] = build_space(1, 0, 1, 0, 0, 0, 0, 0).unwrap();
        board
    }

    #[test]
    fn a_gathered_six_is_not_won_while_a_lone_spy_can_shatter_it() {
        let board = gathered_with_a_spy_alongside();
        assert_ne!(
            check_for_winner(&board).1[0],
            0,
            "the fixture should already have blue's six on one square"
        );

        let mut search = Search::new();
        let (after, mv, _) = search.take_turn(&board, 1, 3);

        assert!(mv.is_some(), "red has a reply to find");
        assert_eq!(
            check_for_winner(&after).1[0],
            0,
            "red's spy should have shattered the six rather than letting it stand"
        );
    }

    #[test]
    fn a_win_in_one_is_found_and_scaled_by_depth() {
        let mut search = Search::new();
        let board = one_move_from_winning();

        let (score, mv) = search.choose_move(&board, 0, 3);
        let mv = mv.expect("there is a move");

        // the royal jumps in, and the win is scored at WIN_SCORE * (depth left + 1) so that a
        // win now beats the same win three moves out
        assert_eq!(mv.origin, 33);
        assert_eq!(mv.kind, MoveKind::Jump);
        assert_eq!(mv.target, 24);
        assert!(score > WIN_SCORE, "a win in one should out-score any position: {score}");

        let (after, _, _) = search.take_turn(&board, 0, 3);
        assert!(check_for_winner(&after).0);
    }

    #[test]
    fn the_table_does_not_change_the_answer() {
        // The transposition table is an optimisation, and an optimisation that changes the
        // move played is a bug. Same position, one state carrying everything the last search
        // learned and one that has never seen a board: the same move, at every depth.
        let board = one_move_from_winning();

        let mut warm = Search::new();
        for depth in 1..=3 {
            warm.choose_move(&board, 0, depth);
        }

        for depth in 1..=3 {
            let mut cold = Search::new();
            assert_eq!(
                warm.choose_move(&board, 0, depth).1,
                cold.choose_move(&board, 0, depth).1,
                "depth {depth}: a warm table played a different move"
            );
        }
    }

    #[test]
    fn the_ko_rule_strikes_a_move_off_the_root() {
        let board = one_move_from_winning();

        let mut search = Search::new();
        let (winning, _, _) = search.take_turn(&board, 0, 2);

        // file the winning position as one the game has already stood in, and the same search
        // must decline to walk back into it
        let mut fenced = Search::new();
        fenced.ko_record(&winning);
        let (_, mv) = fenced.choose_move(&board, 0, 2);
        let played = perform_one_step(&board, 0, mv.expect("some other move exists"));
        assert_ne!(played, winning, "the ko rule did not strike the repeat off");
    }

    #[test]
    fn killers_keep_two_and_the_most_recent_first() {
        let mut search = Search::new();
        let a = Move::new(1, MoveKind::Jump, 8, false);
        let b = Move::new(2, MoveKind::Jump, 9, false);
        let c = Move::new(3, MoveKind::Jump, 10, false);

        search.remember_cutoff(a, 3);
        search.remember_cutoff(b, 3);
        search.remember_cutoff(a, 3); // already there, so the slot doesn't move
        search.remember_cutoff(c, 3);

        assert_eq!(search.killers[&3], vec![c, b]);
        // deep cutoffs pruned more, so they count for more: two cutoffs at depth 3
        assert_eq!(search.history[&a], 18);
    }

    #[test]
    fn history_halves_and_drops_what_decays_to_nothing() {
        let mut search = Search::new();
        let mv = Move::new(1, MoveKind::Jump, 8, false);
        search.history.insert(mv, 1);

        // choose_move halves it on the way in; 1 // 2 is 0, so the entry goes rather than
        // being kept at zero
        search.choose_move(&EMPTY_BOARD, 0, 1);
        assert!(!search.history.contains_key(&mv));
    }

    #[test]
    fn a_side_with_nothing_to_move_scores_the_standing_position() {
        let mut search = Search::new();
        let (score, mv) = search.choose_move(&EMPTY_BOARD, 0, 3);
        assert_eq!(mv, None);
        assert_eq!(score, full_check(&EMPTY_BOARD, 0));
        assert_eq!(score, 0 * SCALE);
    }
}
