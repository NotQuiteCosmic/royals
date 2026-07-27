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
# No packages are installed. That is not a shortcut -- royals_engine imports the standard
# library and nothing else, an invariant the test suite enforces, so the game runs from a
# checkout with PYTHONPATH pointing at it. No pip, no venv, no virtualenv activation, and
# nothing outside your home directory is touched. Removing it is `sh uninstall.sh`, or
# deleting those two paths by hand.
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

say()  { printf '%s\n' "$*"; }
step() { printf '  %s\n' "$*"; }
die()  { printf '\nInstall stopped: %s\n' "$1" >&2; exit 1; }


usage() {
    cat <<'USAGE'
Installs the Royals desktop game into ~/Royals (and ~/Applications/Royals.app on macOS).

    sh install-desktop.sh [--dry-run] [--no-launch]

    --dry-run     print what would be done and exit without changing anything
    --no-launch   install, but don't start the game afterwards
    --help        this

    ROYALS_HOME=/some/path sh install-desktop.sh    install somewhere other than ~/Royals

Installs no Python packages and needs no sudo. To remove it: sh uninstall.sh
USAGE
}


# ---------------------------------------------------------------- arguments

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)   DRY_RUN=1 ;;
        --no-launch) LAUNCH=0 ;;
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
# under PyPy; see CONTRIBUTING.md.
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
BUILD_APP=0
APP_NOTE=""
if [ "$PLATFORM" = macos ]; then
    if [ ! -e "$APP" ]; then
        BUILD_APP=1
    elif [ -f "$APP/Contents/Resources/game/BUILT" ]; then
        BUILD_APP=1
    else
        APP_NOTE="$APP exists but was not built by this installer -- leaving it alone"
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
if [ "$LAUNCH" -eq 1 ]; then
    step "start the game"
fi
say ""
step "using $PY (Python $PY_VERSION)"
step "installing no Python packages, and touching nothing outside your home directory"
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
