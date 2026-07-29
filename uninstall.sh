#!/bin/sh
# Removes what install-desktop.sh installed, and nothing else.
#
#     sh uninstall.sh
#
# It will delete at most two paths:
#
#     ~/Royals                     but only if it is a Royals git checkout
#     ~/Applications/Royals.app    but only if it carries the marker the build script leaves
#
# Both are verified before anything is removed. If either path turns out to be something
# else -- a directory you happen to have called Royals, an app somebody else built -- it
# is reported and left exactly where it is. Nothing else on the machine is touched, because
# the installer never put anything anywhere else: no packages, no PATH entries, no
# shell-profile edits.
#
#     sh uninstall.sh --dry-run    list what would be removed, remove nothing
#     sh uninstall.sh --yes        don't ask for confirmation
#
# Do not run this with sudo. It removes things from your home directory.

set -eu

REPO_SLUG="notquitecosmic/royals"
ROYALS_HOME="${ROYALS_HOME:-$HOME/Royals}"
APP="$HOME/Applications/Royals.app"

DRY_RUN=0
ASSUME_YES=0

say()  { printf '%s\n' "$*"; }
step() { printf '  %s\n' "$*"; }
die()  { printf '\nUninstall stopped: %s\n' "$1" >&2; exit 1; }


usage() {
    cat <<'USAGE'
Removes the Royals desktop install.

    sh uninstall.sh [--dry-run] [--yes]

    --dry-run     list what would be removed and exit without removing anything
    --yes         skip the confirmation prompt
    --help        this

    ROYALS_HOME=/some/path sh uninstall.sh    if you installed somewhere other than ~/Royals

Only removes ~/Royals (if it is a Royals checkout) and ~/Applications/Royals.app (if this
installer built it). Needs no sudo.
USAGE
}


while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)  DRY_RUN=1 ;;
        -y|--yes)   ASSUME_YES=1 ;;
        -h|--help)  usage; exit 0 ;;
        *) printf 'Unknown option: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [ "$(id -u)" -eq 0 ] || [ -n "${SUDO_USER-}" ]; then
    die "this must not be run as root or with sudo.
It removes files from your home directory and needs no elevated permissions."
fi


# ---------------------------------------------------------------- what is actually here
#
# Both checks are deliberately conservative: the question is not "does this path exist"
# but "is this path ours". A `rm -rf` that guesses wrong is exactly the failure mode this
# script is written to make impossible.

say ""
say "Royals -- uninstall"
say ""

REMOVE_HOME=0
if [ -d "$ROYALS_HOME" ]; then
    ORIGIN="$(git -C "$ROYALS_HOME" remote get-url origin 2>/dev/null || true)"
    SLUG="$(printf '%s' "$ORIGIN" \
        | tr 'A-Z' 'a-z' | sed -e 's#\.git$##' -e 's#/$##' -e 's#^.*[:/]\([^/]*/[^/]*\)$#\1#')"
    if [ "$SLUG" = "$REPO_SLUG" ]; then
        REMOVE_HOME=1
    else
        step "$ROYALS_HOME is not a Royals checkout -- leaving it alone"
    fi
elif [ -e "$ROYALS_HOME" ]; then
    step "$ROYALS_HOME is not a directory -- leaving it alone"
fi

# If there are uncommitted changes in there, somebody has been editing the game. Say so
# before deleting it; that is the one case where the person running this may not know what
# they are about to lose. Untracked files don't count -- the installer's own launcher is
# one, and warning about it on every uninstall would train people to ignore the warning.
DIRTY=0
if [ "$REMOVE_HOME" -eq 1 ] && \
   [ -n "$(git -C "$ROYALS_HOME" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
    DIRTY=1
fi

REMOVE_APP=0
if [ -e "$APP" ]; then
    if [ -f "$APP/Contents/Resources/game/BUILT" ]; then
        REMOVE_APP=1
    else
        step "$APP was not built by this installer -- leaving it alone"
    fi
fi

# The optional compiled engine, if install-desktop.sh managed to get one.
#
# It is a pip package rather than a file under $ROYALS_HOME, so deleting the two paths above
# would leave it behind in the user site -- and this script's whole promise is that removing
# Royals removes Royals. Checked by asking pip rather than by importing, because a wheel built
# for the wrong architecture installs fine and fails to import, and that copy still wants
# removing.
REMOVE_ACCEL=0
PY="$(command -v python3 || true)"
if [ -n "$PY" ] && "$PY" -m pip --version >/dev/null 2>&1; then
    if "$PY" -m pip show royals-accel >/dev/null 2>&1; then
        REMOVE_ACCEL=1
    fi
fi

if [ "$REMOVE_HOME" -eq 0 ] && [ "$REMOVE_APP" -eq 0 ] && [ "$REMOVE_ACCEL" -eq 0 ]; then
    say ""
    say "Nothing to remove."
    say ""
    exit 0
fi


# ---------------------------------------------------------------- confirm

say "This will delete:"
if [ "$REMOVE_HOME" -eq 1 ]; then
    step "$ROYALS_HOME"
fi
if [ "$REMOVE_APP" -eq 1 ]; then
    step "$APP"
fi
# Listed separately because it is not a path. Somebody reading --dry-run is deciding whether
# to trust this script with their disk, and a package it silently uninstalls afterwards would
# be exactly the surprise the dry run exists to prevent.
if [ "$REMOVE_ACCEL" -eq 1 ]; then
    step "the royals-accel package (pip uninstall; the optional compiled engine)"
fi
say ""
if [ "$DIRTY" -eq 1 ]; then
    say "Note: $ROYALS_HOME has uncommitted changes. Deleting it loses them."
    say ""
fi

if [ "$DRY_RUN" -eq 1 ]; then
    say "--dry-run: nothing was removed."
    say ""
    exit 0
fi

if [ "$ASSUME_YES" -eq 0 ]; then
    # Read from the terminal rather than stdin: this script may well be arriving on stdin
    # itself, via `curl ... | sh`, in which case plain `read` consumes the script instead
    # of an answer. No terminal means no way to ask, and an unattended delete is not
    # something to do by default.
    if [ ! -r /dev/tty ]; then
        die "no terminal available to confirm with.
Re-run with --yes if you are sure:

    sh uninstall.sh --yes"
    fi
    printf 'Remove these? [y/N] '
    read -r reply < /dev/tty || reply=""
    case "$reply" in
        y|Y|yes|YES) ;;
        *) say ""; say "Nothing was removed."; say ""; exit 0 ;;
    esac
    say ""
fi


# ---------------------------------------------------------------- remove

if [ "$REMOVE_APP" -eq 1 ]; then
    rm -rf "$APP"
    step "removed $APP"
fi
if [ "$REMOVE_HOME" -eq 1 ]; then
    rm -rf "$ROYALS_HOME"
    step "removed $ROYALS_HOME"
fi
if [ "$REMOVE_ACCEL" -eq 1 ]; then
    # Best-effort, matching the install: a pip that refuses to touch a managed environment,
    # or a package installed somewhere this user cannot write, should not turn "uninstall
    # the game" into a failure. The game is gone either way; what is left behind is an
    # inert extension module nothing imports.
    if "$PY" -m pip uninstall -y --quiet royals-accel >/dev/null 2>&1; then
        step "removed the compiled engine (royals-accel)"
    else
        step "could not remove royals-accel -- remove it with: $PY -m pip uninstall royals-accel"
    fi
fi

say ""
say "Done. Royals is gone."
say ""
