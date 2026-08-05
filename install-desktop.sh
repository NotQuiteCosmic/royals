#!/bin/sh
# Installs the Royals desktop game.
#
#     curl -fsSLO https://raw.githubusercontent.com/NotQuiteCosmic/royals/master/install-desktop.sh
#     sh install-desktop.sh
#
# It writes to two places and nowhere else:
#
#     ~/Royals                     a git clone of the game        (set ROYALS_HOME to move it)
#     ~/Applications/Royals.app    a double-clickable launcher     (macOS only)
#
# Nothing the game needs gets installed. That is not a shortcut -- royals_engine imports the
# standard library and nothing else, an invariant the test suite enforces, so the game runs
# from a checkout with PYTHONPATH pointing at it. No venv, no virtualenv activation, and
# nothing outside your home directory is touched.
#
# One package is *tried*, and it is optional: royals-accel, a compiled build of the same
# engine that thinks about thirty-six times faster. It goes in with `pip install --user`, so
# still inside your home directory, and every way it can fail ends in carrying on without it.
# `--no-accel` skips the attempt. See "the optional accelerator" further down for why that is
# a best-effort bonus rather than a step that can fail the install.
#
# Removing it is `sh uninstall.sh`, or deleting those two paths by hand.
#
# Do not run this with sudo. It installs into your home directory; run as root it would
# create root-owned files there that you then cannot delete without sudo again.
#
#     sh install-desktop.sh --dry-run      print exactly what would happen, change nothing
#     sh install-desktop.sh --no-launch    install but don't start the game
#
# Every check runs before the first write, so a failed preflight leaves the machine
# exactly as it found it.

set -eu

REPO_URL="https://github.com/NotQuiteCosmic/royals.git"
REPO_SLUG="notquitecosmic/royals"
ROYALS_HOME="${ROYALS_HOME:-$HOME/Royals}"
APP="$HOME/Applications/Royals.app"
MIN_PY_MAJOR=3
MIN_PY_MINOR=10

DRY_RUN=0
LAUNCH=1
# The compiled engine is a best-effort extra, not part of the install -- see the block that
# tries for it further down. This turns even the attempt off, for anyone who would rather no
# pip ran at all.
SKIP_ACCEL=0

say()  { printf '%s\n' "$*"; }
step() { printf '  %s\n' "$*"; }
die()  { printf '\nInstall stopped: %s\n' "$1" >&2; exit 1; }


usage() {
    cat <<'USAGE'
Installs the Royals desktop game into ~/Royals (and ~/Applications/Royals.app on macOS).

    sh install-desktop.sh [--dry-run] [--no-launch] [--no-accel]

    --dry-run     print what would be done and exit without changing anything
    --no-launch   install, but don't start the game afterwards
    --no-accel    don't even look for the optional compiled engine
    --help        this

    ROYALS_HOME=/some/path sh install-desktop.sh    install somewhere other than ~/Royals

Needs no sudo, and nothing the game needs is installed -- it runs from the checkout,
because the engine imports the standard library and nothing else.

The one exception is optional and best-effort: if pip is available, this tries for
royals-accel, a compiled build of the same engine that is about thirty-six times faster.
It plays identically; it just thinks quicker. Every way that can fail is treated as
"carry on without it", and --no-accel skips the attempt entirely.

To remove it: sh uninstall.sh
USAGE
}


# ---------------------------------------------------------------- arguments

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)   DRY_RUN=1 ;;
        --no-launch) LAUNCH=0 ;;
        --no-accel)  SKIP_ACCEL=1 ;;
        -h|--help)   usage; exit 0 ;;
        # An unrecognised flag is an error rather than something to ignore. Silently
        # skipping "--no-lanuch" and then launching the game is the kind of surprise this
        # script exists to avoid.
        *) printf 'Unknown option: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done


# ---------------------------------------------------------------- preflight
#
# Nothing below this line writes anything until the "install" section. If any check fails
# the machine is untouched.

say ""
say "Royals -- desktop install"
say ""

# Root. Everything here lives in $HOME, so root is never right: at best it makes
# root-owned files in your home directory, at worst it does that somewhere else entirely
# because $HOME is /var/root under sudo.
if [ "$(id -u)" -eq 0 ]; then
    die "this must not be run as root or with sudo.
It installs into your home directory and needs no elevated permissions.
Run it again as yourself, without sudo."
fi
if [ -n "${SUDO_USER-}" ]; then
    die "this appears to be running under sudo.
It installs into your home directory and needs no elevated permissions.
Run it again as yourself, without sudo."
fi

case "$(uname -s)" in
    Darwin) PLATFORM=macos ;;
    Linux)  PLATFORM=linux ;;
    *)      die "unsupported system: $(uname -s). This installer covers macOS and Linux." ;;
esac

# git
if ! command -v git >/dev/null 2>&1; then
    if [ "$PLATFORM" = macos ]; then
        die "git is not installed.
Install Apple's command line tools and try again:

    xcode-select --install"
    else
        die "git is not installed.
Install it with your package manager and try again, e.g.:

    sudo apt install git        (Debian, Ubuntu)
    sudo dnf install git        (Fedora)"
    fi
fi

# python3. The repo assumes `python3` throughout -- there is deliberately no `python`
# fallback, because on the machines where `python` exists it is as likely to be 2.7.
PY="$(command -v python3 || true)"
[ -n "$PY" ] || die "python3 is not installed.
On macOS, install it from https://www.python.org/downloads/ (that build includes the
Tk support the game's window needs). On Linux, use your package manager."

# Version gate. The engine targets >= 3.10 and stays there on purpose so it keeps running
# under PyPy, which is what a machine with no compiled wheel has for speed; see
# CONTRIBUTING.md.
PY_VERSION="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
[ -n "$PY_VERSION" ] || die "could not run $PY -- it is on PATH but does not work."
PY_MAJOR="${PY_VERSION%%.*}"
PY_MINOR="${PY_VERSION##*.}"
if [ "$PY_MAJOR" -lt "$MIN_PY_MAJOR" ] || \
   { [ "$PY_MAJOR" -eq "$MIN_PY_MAJOR" ] && [ "$PY_MINOR" -lt "$MIN_PY_MINOR" ]; }; then
    die "Python $MIN_PY_MAJOR.$MIN_PY_MINOR or newer is needed, but $PY is $PY_VERSION.
Install a newer Python and run this again."
fi

# tkinter. The window is tkinter, and a Python without Tk is common enough -- Homebrew's
# and Debian's both ship it separately -- that it is worth catching here, before anything
# is downloaded, rather than as an ImportError after a successful-looking install.
if ! "$PY" -c 'import tkinter' >/dev/null 2>&1; then
    if [ "$PLATFORM" = macos ]; then
        die "this Python has no tkinter, which the game's window needs.
  $PY (Python $PY_VERSION)

The python.org installer includes it; Homebrew's does not by default:

    brew install python-tk        (if you use Homebrew's python3)
    https://www.python.org/downloads/   (otherwise)"
    else
        die "this Python has no tkinter, which the game's window needs.
  $PY (Python $PY_VERSION)

Install it with your package manager and try again:

    sudo apt install python3-tk       (Debian, Ubuntu)
    sudo dnf install python3-tkinter  (Fedora)"
    fi
fi

# Where it is going. Three cases, and only two of them proceed: an existing Royals clone
# gets updated, an empty path gets a fresh clone, and anything else stops the install.
# This script never clears a path to make room for itself.
MODE=clone
if [ -e "$ROYALS_HOME" ]; then
    [ -d "$ROYALS_HOME" ] || die "$ROYALS_HOME exists and is a file, not a directory.
Move it, or set ROYALS_HOME to somewhere else:

    ROYALS_HOME=/some/other/path sh install-desktop.sh"

    EXISTING_ORIGIN="$(git -C "$ROYALS_HOME" remote get-url origin 2>/dev/null || true)"
    # Normalise https/ssh, .git suffix and case down to "owner/repo" before comparing.
    EXISTING_SLUG="$(printf '%s' "$EXISTING_ORIGIN" \
        | tr 'A-Z' 'a-z' | sed -e 's#\.git$##' -e 's#/$##' -e 's#^.*[:/]\([^/]*/[^/]*\)$#\1#')"
    if [ "$EXISTING_SLUG" = "$REPO_SLUG" ]; then
        MODE=update
    else
        die "$ROYALS_HOME already exists and is not a Royals checkout.
Nothing has been changed and that directory has been left alone.
Move it aside, or install somewhere else:

    ROYALS_HOME=/some/other/path sh install-desktop.sh"
    fi
fi

# Royals.app. tools/build-royals-app.sh opens with `rm -rf` on this path, so if something
# other than a previous run of this installer put an app there, don't call it at all.
# The BUILT marker inside the bundle is what that script leaves behind, and its absence is
# the only evidence available that the bundle is somebody else's.
#
# The marker is not evidence enough by itself, and this is the case it missed. Anyone working
# on the game runs tools/build-royals-app.sh from their own checkout, and that writes the same
# marker -- so a bundle carrying weeks of unreleased work looked exactly like one of ours, and
# a routine update quietly replaced it with whatever the public clone happens to be at. Which
# on a machine whose work has not been pushed is a rollback, arriving with no warning and no
# way back but to notice and rebuild.
#
# The bundle does say where it came from: its launcher bakes in the checkout it was built from.
# So the question "is this mine?" has an honest answer, and it is asked here.
BUILD_APP=0
APP_NOTE=""
if [ "$PLATFORM" = macos ]; then
    if [ ! -e "$APP" ]; then
        BUILD_APP=1
    elif [ ! -f "$APP/Contents/Resources/game/BUILT" ]; then
        APP_NOTE="$APP exists but was not built by this installer -- leaving it alone"
    else
        # A bundle that cannot say where it came from is the case the marker alone was always
        # deciding, and it is updated as it always was. Refusing those would turn a guard into
        # an installer that has stopped working.
        BUILT_FROM=$(sed -n 's/^REPO="\(.*\)"$/\1/p' "$APP/Contents/MacOS/Royals" 2>/dev/null \
                     | head -1)
        BUILT_FROM=${BUILT_FROM%/}

        if [ -z "$BUILT_FROM" ] || [ "$BUILT_FROM" = "${ROYALS_HOME%/}" ]; then
            BUILD_APP=1
        else
            APP_NOTE="$APP was built from $BUILT_FROM, not from $ROYALS_HOME -- leaving it
    alone, since rebuilding it here would replace that copy with this one. To update it:

        sh $BUILT_FROM/tools/build-royals-app.sh"
        fi
    fi
fi


# ---------------------------------------------------------------- the plan

say "This will:"
if [ "$MODE" = clone ]; then
    step "clone $REPO_URL"
    step "  into $ROYALS_HOME"
else
    step "update the existing checkout at $ROYALS_HOME"
fi
step "write $ROYALS_HOME/royals-desktop  (the launcher)"
if [ "$BUILD_APP" -eq 1 ]; then
    step "build $APP"
fi
if [ -n "$APP_NOTE" ]; then
    step "skip the app bundle: $APP_NOTE"
fi
if [ "$SKIP_ACCEL" -ne 1 ]; then
    step "try for royals-accel (optional, --user; the game runs fine without it)"
fi
if [ "$LAUNCH" -eq 1 ]; then
    step "start the game"
fi
say ""
step "using $PY (Python $PY_VERSION)"
# Worth stating precisely rather than reassuringly. The game itself still needs nothing
# installed -- that has not changed and is the whole point of the stdlib-only rule. What
# changed is that there is now one optional package this will *try* for, into --user, and
# saying "installing no Python packages" while doing that would be a lie of exactly the kind
# an install script should never tell.
if [ "$SKIP_ACCEL" -eq 1 ]; then
    step "installing no Python packages, and touching nothing outside your home directory"
else
    step "the game needs no Python packages; the one optional extra goes to your user site"
    step "touching nothing outside your home directory"
fi
say ""

if [ "$DRY_RUN" -eq 1 ]; then
    say "--dry-run: nothing was changed."
    say ""
    exit 0
fi


# ---------------------------------------------------------------- install
#
# First write happens here.

if [ "$MODE" = clone ]; then
    say "Downloading..."
    git clone --depth 1 "$REPO_URL" "$ROYALS_HOME" >/dev/null 2>&1 \
        || die "could not clone $REPO_URL.
Check your network connection and try again. Nothing was installed."
else
    say "Updating $ROYALS_HOME..."
    if ! git -C "$ROYALS_HOME" fetch --depth 1 origin master >/dev/null 2>&1; then
        step "could not reach GitHub -- keeping the copy already here"
    elif [ -n "$(git -C "$ROYALS_HOME" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
        # Somebody has edited this checkout. Their work wins; a game installer has no
        # business discarding it. --ff-only below would refuse anyway, but saying so
        # plainly is better than a git error.
        #
        # --untracked-files=no because the launcher this script writes lives inside the
        # checkout: counting untracked files would make every install permanently "dirty"
        # and no update would ever run again. A fast-forward cannot silently eat an
        # untracked file anyway -- git refuses if the merge would overwrite one.
        step "there are local changes here, so they have been left alone and not updated"
    elif git -C "$ROYALS_HOME" merge --ff-only FETCH_HEAD >/dev/null 2>&1; then
        step "up to date"
    else
        # Only ever fast-forward. If history has diverged, the checkout still runs.
        step "local history has diverged from GitHub -- keeping what is here"
    fi
fi

[ -f "$ROYALS_HOME/apps/desktop/royals_gui.py" ] \
    || die "the download looks incomplete: no apps/desktop/royals_gui.py under $ROYALS_HOME."

# The launcher. royals_engine is stdlib-only, so PYTHONPATH is the whole install step --
# the same trick tools/build-royals-app.sh uses inside the bundle. royals_gui.py has no
# working-directory requirement, so there is nothing to cd into.
LAUNCHER="$ROYALS_HOME/royals-desktop"
cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/bin/sh
# Starts the Royals window. Written by install-desktop.sh; safe to delete along with the
# rest of $ROYALS_HOME.
#
# No install step is needed because royals_engine imports the standard library only, so
# putting engine/src on PYTHONPATH is enough to run it straight from the checkout.
exec env PYTHONPATH="$ROYALS_HOME/engine/src" "$PY" "$ROYALS_HOME/apps/desktop/royals_gui.py" "\$@"
LAUNCHER_EOF
chmod 755 "$LAUNCHER"
step "wrote $LAUNCHER"

# ---------------------------------------------------------------- the optional accelerator
#
# royals-accel is a compiled build of the same engine, about thirty-six times faster, and the
# game is entirely playable without it. Everything above this point is the install; this is
# a bonus that is *tried* and then forgotten about.
#
# So it is best-effort on purpose, and every branch below ends in "carry on":
#
#   - no pip on the machine                     -> skip
#   - no wheel built for this platform          -> skip
#   - offline, or PyPI unreachable              -> skip
#   - pip refuses to touch a managed environment -> skip
#
# The promise at the top of this file is that nothing the game NEEDS gets installed, and that
# nothing outside your home directory is touched. This is the one package that goes in, it is
# not needed, and --user keeps it inside $HOME -- so both halves of that promise survive.
# Turning a failure here into an error would not: someone whose machine has no wheel would be
# told the install failed, when what actually happened is that the game will run in Python and
# be slower.
#
# --user keeps it out of any system location, and --only-binary :all: means pip will never
# quietly decide to build it from source, which would need a Rust toolchain and take
# minutes. If there is no wheel, there is no install, which is the intended answer.
ACCEL=0
if [ "$SKIP_ACCEL" -ne 1 ]; then
    if "$PY" -m pip --version >/dev/null 2>&1; then
        step "looking for the optional compiled engine (the game works without it)"
        if "$PY" -m pip install --user --quiet --only-binary :all: \
                --disable-pip-version-check royals-accel >/dev/null 2>&1; then
            # Installing it is not the same as it working: a wheel built for the wrong
            # architecture installs happily and fails on import. Ask the engine itself.
            if "$PY" -c 'import royals_accel' >/dev/null 2>&1; then
                ACCEL=1
                step "compiled engine installed -- the computer player will think faster"
            fi
        fi
        [ "$ACCEL" -eq 1 ] || step "no compiled engine for this machine; using Python (slower, identical play)"
    fi
fi

# The macOS app bundle. tools/build-royals-app.sh already does this properly -- it carries
# its own copy of the game because an app launched from the Dock cannot read ~/Documents,
# and it has the LaunchServices workarounds that took a while to find. Call it, don't
# reimplement it.
APP_BUILT=0
if [ "$BUILD_APP" -eq 1 ]; then
    if [ -f "$ROYALS_HOME/tools/build-royals-app.sh" ]; then
        if sh "$ROYALS_HOME/tools/build-royals-app.sh" "$APP" >/dev/null 2>&1; then
            APP_BUILT=1
            step "built $APP"
        else
            # Not fatal: the launcher above works regardless, and the bundle is a
            # convenience on top of it.
            step "could not build the app bundle -- the launcher below still works"
        fi
    else
        step "tools/build-royals-app.sh is missing -- skipping the app bundle"
    fi
elif [ -n "$APP_NOTE" ]; then
    step "$APP_NOTE"
fi


# ---------------------------------------------------------------- done

say ""
say "Installed."
say ""
if [ "$APP_BUILT" -eq 1 ]; then
    step "Royals.app is in your Applications folder -- double-click it any time."
    step "Or from a terminal: $LAUNCHER"
else
    step "Start it with: $LAUNCHER"
fi
step "The game lives in $ROYALS_HOME. To remove it: sh $ROYALS_HOME/uninstall.sh"
say ""

if [ "$LAUNCH" -eq 1 ]; then
    if [ "$APP_BUILT" -eq 1 ]; then
        if ! open "$APP" >/dev/null 2>&1; then
            "$LAUNCHER" >/dev/null 2>&1 &
        fi
    else
        # Detached, so the game outlives this script and the terminal it ran in.
        "$LAUNCHER" >/dev/null 2>&1 &
    fi
    say "Starting Royals..."
    say ""
fi
