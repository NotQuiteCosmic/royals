//! The rules contract, run as a test: the emitter's output against `tests/golden_moves.txt`.
//!
//! This is the same check as
//!
//! ```text
//! cargo run --release --bin royals-golden | diff - ../tests/golden_moves.txt
//! ```
//!
//! wired into `cargo test` so it can't be forgotten. It runs the binary rather than calling
//! the emitter, because the binary IS the emitter -- there is nothing to import, and running
//! it means the thing being checked is the thing that ships.
//!
//! If this fails and you did not intend to change the rules, the port has a bug. Do not
//! re-record the golden.

use std::process::Command;

const GOLDEN: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../tests/golden_moves.txt");
const PUSH_GOLDEN: &str =
    concat!(env!("CARGO_MANIFEST_DIR"), "/../tests/golden_push_moves.txt");

#[test]
fn the_emitter_reproduces_the_golden() {
    check(&[], GOLDEN);
}

/// The variant's contract, held to exactly the standard the original is.
///
/// The push-range rule is one a game opts into, so there are two rule sets and each needs its
/// own statement of what the rules say a move does. Implementing the variant twice is only
/// worth anything if the two implementations are checked against each other, which is what
/// this is -- the same differential oracle, pointed at the other rule set.
#[test]
fn the_emitter_reproduces_the_push_range_golden() {
    check(&["--push-range"], PUSH_GOLDEN);
}

fn check(args: &[&str], golden: &str) {
    let run = Command::new(env!("CARGO_BIN_EXE_royals-golden"))
        .args(args)
        .output()
        .expect("could not run royals-golden");

    assert!(
        run.status.success(),
        "royals-golden failed: {}",
        String::from_utf8_lossy(&run.stderr)
    );

    let got = String::from_utf8(run.stdout).expect("emitter wrote something that isn't UTF-8");
    let want = std::fs::read_to_string(golden)
        .unwrap_or_else(|e| panic!("{golden}: {e} -- run `cd tests && python3 regress.py write all`"));

    let got: Vec<&str> = got.lines().collect();
    let want: Vec<&str> = want.lines().collect();

    // Report the first difference rather than a wall of them: the walks are sequential, so
    // everything after the first divergence is a consequence of it.
    for (i, (a, b)) in want.iter().zip(got.iter()).enumerate() {
        assert_eq!(
            a,
            b,
            "line {} differs\n  want {}\n  got  {}",
            i + 1,
            a,
            b
        );
    }

    assert_eq!(want.len(), got.len(), "the emitter produced a different number of lines");
}
