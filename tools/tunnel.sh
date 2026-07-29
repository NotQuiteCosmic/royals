#!/bin/sh
# Put Royals on a public HTTPS address, and be able to prove it is still there.
#
#     sh tools/tunnel.sh up       start the server and a Cloudflare quick tunnel
#     sh tools/tunnel.sh status   are both halves alive, and does the URL still serve?
#     sh tools/tunnel.sh url      print the address, for pasting into a message
#     sh tools/tunnel.sh down     stop both
#
# This script exists because of a specific failure, and both halves of that failure are
# worth knowing about before changing anything here.
#
# The first half: the server was started with `( cmd & )`, which leaves it in the process
# group of whatever launched it. When that shell went away the server went with it -- two
# and a half minutes after it started -- while cloudflared, which happened to survive,
# carried on forwarding to a port with nothing behind it. The tunnel log filled up with
#
#     Unable to reach the origin service ... dial tcp 127.0.0.1:8000: connection refused
#
# for eight hours. So `spawn` below does a real double-fork-and-setsid, and the processes
# it starts belong to nobody.
#
# The second half is the one that actually cost somebody an evening: the URL was checked
# once, immediately after being created, and then handed out. A 200 at t=0 says nothing
# about t=3min. That is why `up` does not print an address until it has fetched
# /api/health *through the public hostname* -- so the answer has to come from Royals and
# not from Cloudflare's own error page -- and why `status` exists at all.
#
# What this cannot fix: a quick tunnel is ephemeral. The hostname changes every `up`, it
# dies when the Mac sleeps, and Cloudflare withdraws it from DNS once the tunnel's
# connections lapse. Games survive that -- they are in SQLite -- but the link does not.
# A link that still works tomorrow needs a real deployment. See docs/HOSTING.md.

set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$REPO/.run"
CLOUDFLARED="$REPO/tools/cloudflared"
PORT="${ROYALS_PORT:-8000}"

SERVER_PID="$RUN/server.pid"
TUNNEL_PID="$RUN/tunnel.pid"
SERVER_LOG="$RUN/server.log"
TUNNEL_LOG="$RUN/tunnel.log"
URL_FILE="$RUN/url"

# The deepest search a public server will agree to run. Depth 6 is seconds of pinned CPU
# that anyone who can reach the address may ask for by clicking a menu.
MAX_DEPTH="${ROYALS_MAX_DEPTH:-5}"

# ---------------------------------------------------------------------------

# Start a command truly detached: its own session, its own process group, no controlling
# terminal, stdio on a log. `nohup ... &` is not enough on its own -- it stops the child
# hearing a hangup but leaves it in the caller's process group, so anything that signals
# the group still takes it down. That is exactly what happened before. python3 is already
# a hard dependency of everything here, and os.setsid is the portable way to say this.
spawn() {
    _pidfile="$1"; _logfile="$2"; shift 2
    python3 - "$_pidfile" "$_logfile" "$@" <<'PY'
import os, sys
pidfile, logfile, argv = sys.argv[1], sys.argv[2], sys.argv[3:]
pid = os.fork()
if pid:                                   # parent: record the child and get out of the way
    with open(pidfile, "w") as f:
        f.write(str(pid))
    sys.exit(0)
os.setsid()                               # new session: no process group left to kill us
fd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd, 1)
os.dup2(fd, 2)
devnull = os.open(os.devnull, os.O_RDONLY)
os.dup2(devnull, 0)
os.execvp(argv[0], argv)
PY
}

alive() {   # alive <pidfile>
    [ -f "$1" ] || return 1
    kill -0 "$(cat "$1")" 2>/dev/null
}

stop() {    # stop <pidfile> <label>
    if alive "$1"; then
        _pid="$(cat "$1")"
        kill "$_pid" 2>/dev/null || true
        _n=0
        while kill -0 "$_pid" 2>/dev/null && [ "$_n" -lt 50 ]; do
            sleep 0.1
            _n=$((_n + 1))
        done
        kill -9 "$_pid" 2>/dev/null || true
        echo "  stopped $2 (pid $_pid)"
    else
        echo "  $2 was not running"
    fi
    rm -f "$1"
}

# Does Royals itself answer here? Not "is something listening" -- Cloudflare will happily
# answer with its own error page, and a browser cannot tell the difference.
royals_answers() {   # royals_answers <base-url> [curl-args...]
    _u="$1"; shift
    curl -fsS --max-time 20 "$@" "$_u/api/health" 2>/dev/null | grep -q '"ok":true'
}

host_of() { printf "%s" "$1" | sed 's|^https\{0,1\}://||; s|/.*||'; }

# What a resolver that is not this Mac thinks. A freshly minted quick-tunnel hostname
# takes a few seconds to appear in DNS, and asking too early does lasting damage: the
# NXDOMAIN comes back, macOS caches the *negative* answer for minutes, and from then on
# the address looks dead here while working perfectly for everybody else. That happened
# while writing this script. So the public resolver is the second opinion that tells the
# two apart.
public_ip() {   # public_ip <host>
    dig +short +time=5 +tries=1 @1.1.1.1 "$1" A 2>/dev/null |
        grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -1
}

system_resolves() {   # system_resolves <host>
    python3 -c 'import socket,sys; socket.gethostbyname(sys.argv[1])' "$1" 2>/dev/null
}

# The state of a public address, as one word:
#   serving      Royals answers, and this Mac can reach it normally
#   stale-dns    Royals answers, but only via a public resolver -- local cache is behind
#   no-origin    the hostname resolves, but Royals does not answer through it
#   withdrawn    no resolver has it; Cloudflare has taken the hostname away
probe_url() {   # probe_url <url>
    _url="$1"
    _host="$(host_of "$_url")"

    if system_resolves "$_host" >/dev/null; then
        royals_answers "$_url" && { echo serving; return; }
        echo no-origin; return
    fi

    _ip="$(public_ip "$_host")"
    [ -z "$_ip" ] && { echo withdrawn; return; }

    # Resolves elsewhere but not here: prove whether it actually serves by going straight
    # to the address, so the answer is about the tunnel and not about this Mac's cache.
    if royals_answers "$_url" --resolve "$_host:443:$_ip"; then
        echo stale-dns
    else
        echo no-origin
    fi
}

explain_dns() {   # explain_dns <url>
    cat <<EOF
           This Mac's DNS cache is holding a stale "no such host" for it. The link
           works for anyone else right now. To see it here too:

             sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder

           Or just wait -- the negative answer expires on its own in a few minutes.
EOF
}

require_cloudflared() {
    [ -x "$CLOUDFLARED" ] && return 0
    cat >&2 <<EOF
tools/cloudflared is missing. It is a single binary and is deliberately not committed:

  curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz \\
    | tar xz -C tools/

(Use -darwin-arm64 on Apple silicon. Homebrew also works if your Command Line Tools are
current enough to build it, which is a bigger detour than one download.)
EOF
    exit 1
}

# ---------------------------------------------------------------------------

cmd_up() {
    require_cloudflared
    mkdir -p "$RUN"

    # A second tunnel would mint a second hostname and quietly orphan the first, which is
    # precisely the sort of confusion this script is here to end.
    if alive "$TUNNEL_PID"; then
        echo "A tunnel is already running. Its address:"
        echo "  $(cat "$URL_FILE" 2>/dev/null || echo '(unknown -- check .run/tunnel.log)')"
        echo "Stop it first with: sh tools/tunnel.sh down"
        exit 1
    fi

    if alive "$SERVER_PID"; then
        echo "Reusing the server already running on port $PORT."
    else
        echo "Starting the server on port $PORT..."
        ROYALS_MAX_DEPTH="$MAX_DEPTH" \
            spawn "$SERVER_PID" "$SERVER_LOG" \
            python3 "$REPO/serve.py" --behind-proxy --no-browser --port "$PORT"
    fi

    # Wait for the origin before starting the tunnel. Pointing a tunnel at a port that is
    # not listening yet is how the failure being fixed here began.
    printf "Waiting for the origin"
    n=0
    while ! royals_answers "http://127.0.0.1:$PORT"; do
        n=$((n + 1))
        if [ "$n" -gt 60 ]; then
            echo " -- gave up."
            echo "The server never answered. Last of $SERVER_LOG:"
            tail -20 "$SERVER_LOG" 2>/dev/null | sed 's/^/  /'
            stop "$SERVER_PID" "the server" >/dev/null 2>&1 || true
            exit 1
        fi
        printf "."
        sleep 0.5
    done
    echo " up."

    echo "Opening the tunnel..."
    : > "$TUNNEL_LOG"
    spawn "$TUNNEL_PID" "$TUNNEL_LOG" \
        "$CLOUDFLARED" tunnel --url "http://127.0.0.1:$PORT"

    printf "Waiting for an address"
    url=""
    n=0
    while [ -z "$url" ]; do
        url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1)"
        [ -n "$url" ] && break
        n=$((n + 1))
        if [ "$n" -gt 90 ]; then
            echo " -- gave up."
            tail -20 "$TUNNEL_LOG" | sed 's/^/  /'
            cmd_down >/dev/null 2>&1 || true
            exit 1
        fi
        printf "."
        sleep 1
    done
    echo " $url"
    printf "%s\n" "$url" > "$URL_FILE"

    # Let DNS catch up before asking anything of it. Polling a hostname that does not
    # exist yet is what teaches this Mac to believe it never will -- see public_ip above.
    host="$(host_of "$url")"
    printf "Waiting for DNS"
    n=0
    while [ -z "$(public_ip "$host")" ]; do
        n=$((n + 1))
        if [ "$n" -gt 30 ]; then
            echo " -- gave up; the hostname never appeared."
            tail -15 "$TUNNEL_LOG" | sed 's/^/  /'
            cmd_down >/dev/null 2>&1 || true
            exit 1
        fi
        printf "."
        sleep 2
    done
    echo " there."

    # The step whose absence caused all of this. An address is not a working address until
    # Royals has answered through it.
    printf "Checking it end to end"
    n=0
    while :; do
        state="$(probe_url "$url")"
        [ "$state" = serving ] && { echo " confirmed."; break; }
        [ "$state" = stale-dns ] && { echo " confirmed (via a public resolver)."; break; }
        n=$((n + 1))
        if [ "$n" -gt 20 ]; then
            echo " -- FAILED ($state)."
            echo "The tunnel exists but Royals did not answer through it."
            tail -15 "$TUNNEL_LOG" | sed 's/^/  /'
            exit 1
        fi
        printf "."
        sleep 2
    done

    cat <<EOF

  Royals is at  $url
EOF
    [ "$state" = stale-dns ] && { echo; explain_dns "$url"; }
    cat <<EOF

  It stays up while this Mac is awake and until you run:  sh tools/tunnel.sh down
  Stop it sleeping:                                       caffeinate -i -w $(cat "$SERVER_PID")
  Check it later:                                         sh tools/tunnel.sh status

  The address is temporary -- a new one every 'up', and gone when the tunnel stops.
  Games survive in royals.db; the link does not.
EOF
}

cmd_status() {
    ok=0

    if alive "$SERVER_PID"; then
        if royals_answers "http://127.0.0.1:$PORT"; then
            echo "  server   up   (pid $(cat "$SERVER_PID"), port $PORT)"
        else
            echo "  server   BAD  (pid $(cat "$SERVER_PID") alive but not answering on $PORT)"
            ok=1
        fi
    else
        echo "  server   down"
        ok=1
    fi

    if alive "$TUNNEL_PID"; then
        echo "  tunnel   up   (pid $(cat "$TUNNEL_PID"))"
    else
        echo "  tunnel   down"
        ok=1
    fi

    url="$(cat "$URL_FILE" 2>/dev/null || true)"
    if [ -z "$url" ]; then
        echo "  address  none recorded"
        ok=1
    else
        # These four look identical in a browser and have four different fixes, which is
        # the entire reason this subcommand exists.
        case "$(probe_url "$url")" in
            serving)
                echo "  address  up   $url" ;;
            stale-dns)
                echo "  address  up   $url"
                echo "           (serving, but this Mac cannot resolve it)"
                explain_dns "$url" ;;
            no-origin)
                echo "  address  BAD  $url"
                echo "           the hostname resolves, but Royals did not answer through it."
                echo "           usually the server: check 'server' above and .run/server.log."
                ok=1 ;;
            withdrawn)
                echo "  address  GONE $url"
                echo "           no resolver has this hostname -- Cloudflare withdrew it,"
                echo "           which it does once a tunnel's connections lapse."
                echo "           'down' then 'up' will mint a new one."
                ok=1 ;;
        esac
    fi

    [ "$ok" -eq 0 ] && echo "  everything is serving."
    return "$ok"
}

cmd_down() {
    echo "Stopping:"
    stop "$TUNNEL_PID" "the tunnel"
    stop "$SERVER_PID" "the server"
    rm -f "$URL_FILE"
}

cmd_url() {
    if [ -s "$URL_FILE" ]; then
        cat "$URL_FILE"
    else
        echo "No tunnel is running. Start one with: sh tools/tunnel.sh up" >&2
        exit 1
    fi
}

case "${1:-}" in
    up)     cmd_up ;;
    down)   cmd_down ;;
    status) cmd_status ;;
    url)    cmd_url ;;
    *)
        sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
        exit 1
        ;;
esac
