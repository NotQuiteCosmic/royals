#!/bin/sh
# Builds Royals.app -- the double-clickable launcher for the desktop window.
#
# The bundle carries its own copy of the game rather than pointing at this checkout, and
# that is not laziness. The checkout lives under ~/Documents, which macOS protects: an
# app launched from the Dock is refused entry there and is given no way to ask. Worse,
# handing the path to python3 does not help either, because the framework python is
# itself an application bundle -- it becomes the process responsible for the read and
# arrives without any permission of its own.
#
# So the bundle reads only itself, which is always allowed, and this script is how a new
# copy gets in. Re-run it after changing the game:
#
#     sh tools/build-royals-app.sh
#
# The launcher also tries to refresh itself from this checkout on every start, so if the
# Mac does happen to allow the read, it stays current on its own and this script is only
# needed the first time.

set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$HOME/Applications/Royals.app}"
PY="$(command -v python3)"

[ -f "$REPO/apps/desktop/royals_gui.py" ] || { echo "no royals_gui.py under $REPO" >&2; exit 1; }
[ -n "$PY" ] || { echo "python3 not found" >&2; exit 1; }

echo "building $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/game"

# the game itself, engine and window, with nothing else along for the ride
/usr/bin/rsync -a --exclude '__pycache__' --exclude '.*' \
    "$REPO/apps/desktop/" "$APP/Contents/Resources/game/desktop/"
# *.egg-info is left behind in the source tree by `pip install -e ./engine`, which is how
# this repo is developed. It is metadata about an install that does not exist inside the
# bundle, so copying it in only invites something to believe it.
/usr/bin/rsync -a --exclude '__pycache__' --exclude '.*' --exclude '*.egg-info' \
    "$REPO/engine/src/" "$APP/Contents/Resources/game/engine/"
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$APP/Contents/Resources/game/BUILT"

# The optional compiled engine, if this machine has one.
#
# The bundle cannot go looking for it later. It reads only itself -- that is the whole reason
# it carries a copy of the game rather than pointing at the checkout -- so an accelerator that
# is not copied in now is one the app will never see, whatever is installed elsewhere.
#
# Copied rather than installed: dropping the extension module beside the engine on the
# bundle's PYTHONPATH is enough for `import royals_accel` to find it, and it keeps the app
# self-contained with no pip, no user site, and nothing to go stale underneath it.
#
# Silence when there is nothing to copy is correct. The app runs the Python engine and plays
# exactly the same game, only slower.
# What to copy depends on how the wheel was laid out, and both layouts are normal: maturin
# emits a package directory (__init__.py re-exporting a .so beside it) for a mixed project and
# a single .so for a pure one. Asking importlib which it is beats guessing -- a build that
# switched layouts would otherwise bundle half of it and fail on import inside the app, where
# there is no console to say so.
ACCEL=$("$PY" - <<'FIND' 2>/dev/null
import importlib.util
spec = importlib.util.find_spec("royals_accel")
if spec:
    # A package has search locations; a bare extension module has only an origin.
    locs = list(spec.submodule_search_locations or [])
    print(locs[0] if locs else (spec.origin or ""))
FIND
)
if [ -n "$ACCEL" ] && [ -e "$ACCEL" ]; then
    /usr/bin/rsync -a --exclude '__pycache__' "$ACCEL" "$APP/Contents/Resources/game/engine/"
    echo "  bundled the compiled engine ($(basename "$ACCEL"))"
else
    echo "  no compiled engine on this machine; the app will use Python (same game, slower)"
fi

[ -f "$REPO/tools/royals-icon.icns" ] && cp "$REPO/tools/royals-icon.icns" "$APP/Contents/Resources/Royals.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>               <string>Royals</string>
    <key>CFBundleDisplayName</key>        <string>Royals</string>
    <key>CFBundleExecutable</key>         <string>Royals</string>
    <key>CFBundleIdentifier</key>         <string>local.royals.desktop</string>
    <key>CFBundleIconFile</key>           <string>Royals</string>
    <key>CFBundlePackageType</key>        <string>APPL</string>
    <key>CFBundleShortVersionString</key> <string>1.0</string>
    <key>CFBundleVersion</key>            <string>1</string>
    <key>NSHighResolutionCapable</key>    <true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/Royals" <<LAUNCH
#!/bin/sh
# Runs the copy of the game inside this bundle. See tools/build-royals-app.sh for why it
# is a copy and not the checkout.

BUNDLE="\$(cd "\$(dirname "\$0")/.." && pwd)"
GAME="\$BUNDLE/Resources/game"
REPO="$REPO"
PY="$PY"

LOG="\$HOME/Library/Logs/Royals.log"
mkdir -p "\$(dirname "\$LOG")" 2>/dev/null
{ echo; echo "=== \$(date) ==="; } >> "\$LOG" 2>&1

# If this Mac lets us read the checkout, take a fresh copy so the launcher keeps up with
# the source on its own. If it doesn't -- which is the normal case for ~/Documents -- say
# nothing and run what is already here.
if /usr/bin/rsync -a --delete --exclude '__pycache__' --exclude '.*' \\
        "\$REPO/apps/desktop/" "\$GAME/desktop/" 2>/dev/null \\
   && /usr/bin/rsync -a --delete --exclude '__pycache__' --exclude '.*' \\
        "\$REPO/engine/src/" "\$GAME/engine/" 2>/dev/null; then
    echo "refreshed from \$REPO" >> "\$LOG"
else
    # The normal outcome: ~/Documents is off limits to an app launched from the Dock.
    # Nothing is wrong -- run the copy inside the bundle, which is what it is there for.
    echo "using the copy built in on \$(cat "\$GAME/BUILT" 2>/dev/null)" >> "\$LOG"
fi

if [ ! -x "\$PY" ]; then
    /usr/bin/osascript -e 'display alert "Royals could not start" message "python3 was not found on this Mac." as critical' >/dev/null 2>&1
    exit 1
fi

PYTHONPATH="\$GAME/engine"
export PYTHONPATH
cd "\$GAME" || exit 1

# Started detached, and deliberately not exec'd. The framework python is its own
# application bundle, so exec'ing it leaves a process claiming to be this app while the
# window server is told it is Python -- LaunchServices treats that as a launch that never
# completed and kills it about ten seconds in, with no error anywhere to explain it.
# Handing the window off to a detached child and letting this script finish avoids the
# whole argument.
nohup "\$PY" "\$GAME/desktop/royals_gui.py" >> "\$LOG" 2>&1 &
exit 0
LAUNCH

chmod +x "$APP/Contents/MacOS/Royals"
touch "$APP"
echo "done: $APP"
