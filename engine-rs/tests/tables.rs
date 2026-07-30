//! Every startup table, checked element for element against a dump from the Python engine.
//!
//! This is the cheapest bug-catcher in the crate, and it needs no game rule to be right: the
//! encoding and the geometry are settled before a move is generated, so if either disagrees
//! by one square this says which square, where every test above it would say only that a
//! golden diff thousands of lines long had moved. Re-dump with:
//!
//! ```text
//! python3 engine-rs/tests/fixtures/dump_tables.py
//! ```
//!
//! A fixture that moves means the Python side's tables moved, which is a rules change and
//! needs the same justification `golden_moves.txt` does. The files are whitespace-separated
//! integers rather than JSON so that this test needs no dev-dependency and `cargo test`
//! works offline.

use royals_engine::board::{
    check_for_winner, entering_board, is_win_code, Board, CODE_RANGE, MAX_SCATTER, MAX_STACK,
    PUSH_REACH,
};
use royals_engine::tables::{
    Ray, RayTable, BREAKRAY, JUMPDIST, JUMPRAY, JUMPREACH, PUSHFROM, PUSHRAY, UNPACK,
};

fn fixture(name: &str) -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/");
    std::fs::read_to_string(format!("{path}{name}"))
        .unwrap_or_else(|e| panic!("{name}: {e} -- run tests/fixtures/dump_tables.py"))
}

/// One line of a fixture as integers. Everything dumped is small and signed (`strength` goes
/// negative, `PUSHFROM` uses -1 for "not neighbours"), so i32 covers all of it.
fn rows(name: &str) -> Vec<Vec<i32>> {
    fixture(name)
        .lines()
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .map(|line| line.split_whitespace().map(|n| n.parse().unwrap()).collect())
        .collect()
}

#[test]
fn unpack_matches_python() {
    let expected = rows("unpack.txt");
    assert_eq!(expected.len(), CODE_RANGE, "fixture covers a different code range");

    for (code, row) in expected.iter().enumerate() {
        let s = UNPACK[code];
        let got = [
            s.side as i32,
            s.dragon as i32,
            s.spy as i32,
            s.pawns as i32,
            s.royal as i32,
            s.cap_spy as i32,
            s.cap_pawns as i32,
            s.pris_flag as i32,
            s.occupied as i32,
            s.weight as i32,
            s.strength as i32,
            s.captors as i32,
            s.pris_count as i32,
            s.my_pieces as i32,
        ];
        assert_eq!(&got[..], &row[..], "code {code} unpacks differently");
    }
}

/// The ray fixtures are one line per (square, direction): square, direction, length, then
/// that many 0-based squares.
fn check_rays<const N: usize>(name: &str, table: &RayTable<N>) {
    let expected = rows(name);
    assert_eq!(expected.len(), 49 * 4, "{name}: wrong number of rays");

    for row in expected {
        let (square, direction, len) = (row[0] as usize, row[1] as usize, row[2] as usize);
        let ray: &Ray<N> = &table[square][direction];
        let got: Vec<i32> = ray.as_slice().iter().map(|&s| s as i32).collect();

        assert_eq!(ray.len(), len, "{name}: square {square} direction {direction} is a different length");
        assert_eq!(got, row[3..], "{name}: square {square} direction {direction} walks elsewhere");
    }
}

#[test]
fn rays_match_python() {
    check_rays::<MAX_STACK>("jumpray.txt", &JUMPRAY);
    check_rays::<PUSH_REACH>("pushray.txt", &PUSHRAY);
    check_rays::<MAX_SCATTER>("breakray.txt", &BREAKRAY);
}

#[test]
fn push_from_matches_python() {
    let expected = rows("pushfrom.txt");
    assert_eq!(expected.len(), 50);

    for (origin, row) in expected.iter().enumerate() {
        for (destination, &want) in row.iter().enumerate() {
            let got = PUSHFROM[origin][destination].map_or(-1, |d| d as i32);
            assert_eq!(got, want, "PUSHFROM[{origin}][{destination}]");
        }
    }
}

/// Both of these are measured off `checkMoves` in Python and derived from `JUMPRAY` here, so
/// this is the assertion that holds the two derivations together.
#[test]
fn jump_reach_and_distance_match_python() {
    for row in rows("jumpreach.txt") {
        assert_eq!(JUMPREACH[row[0] as usize] as i32, row[1], "JUMPREACH[{}]", row[0]);
    }

    for row in rows("jumpdist.txt") {
        let start = row[0] as usize;
        for (square, &want) in row[1..].iter().enumerate() {
            assert_eq!(JUMPDIST[start][square] as i32, want, "JUMPDIST[{start}][{square}]");
        }
    }
}

#[test]
fn win_codes_match_python() {
    let expected: Vec<u16> =
        rows("wincodes.txt").iter().map(|row| row[0] as u16).collect();
    let got: Vec<u16> =
        (0..CODE_RANGE as u16).filter(|&code| is_win_code(code)).collect();
    assert_eq!(got, expected, "the set of won squares differs");

    // and the scan built on it agrees with itself, both ways round
    let mut board: Board = entering_board();
    assert_eq!(check_for_winner(&board), (false, [0, 0]));
    board[0] = expected[0];
    let (over, winner) = check_for_winner(&board);
    assert!(over);
    assert_eq!(winner.iter().sum::<u8>(), 1);
}

#[test]
fn entering_board_matches_python() {
    let expected: Vec<i32> = rows("entering_board.txt").remove(0);
    let got: Vec<i32> = entering_board().iter().map(|&c| c as i32).collect();
    assert_eq!(got, expected);
}
