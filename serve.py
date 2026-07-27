#!/usr/bin/env python3
"""Start the Royals server and open it in a browser.

    python3 serve.py

`uvicorn` on its own is perfectly good, but it does two things that surprise people:
it never opens a browser -- it only listens and prints a URL -- and if the port is
already taken it exits with "[Errno 48] Address already in use", which looks like the
app failing rather than a second copy of it refusing to start. Both of those are handled
here: a busy port is stepped over rather than fatal, and the page is opened for you.

Options are passed straight through to uvicorn's defaults otherwise:

    python3 serve.py --port 9000     pick the port yourself
    python3 serve.py --no-browser    just listen
    python3 serve.py --reload        restart when a file changes (development)
"""

import argparse
import socket
import sys
import threading
import time
import webbrowser


HOST = "127.0.0.1"
FIRST_PORT = 8000
PORT_ATTEMPTS = 20


def port_is_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host, preferred):
    if port_is_free(host, preferred):
        return preferred, False
    for port in range(preferred + 1, preferred + PORT_ATTEMPTS):
        if port_is_free(host, port):
            return port, True
    raise SystemExit(
        "Could not find a free port between %d and %d. Something is holding them all.\n"
        "See what: lsof -nP -iTCP:%d -sTCP:LISTEN" % (preferred, preferred + PORT_ATTEMPTS, preferred))


def open_when_ready(url, host, port, timeout=25.0):
    """Wait until the server actually answers, then open the browser.

    Opening immediately races the server's startup and lands on a connection error,
    which is worse than waiting a beat.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            if s.connect_ex((host, port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser(description="Run the Royals web server.")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=None,
                        help="defaults to %d, or the next free port after it" % FIRST_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--reload", action="store_true",
                        help="restart on file changes (development)")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        raise SystemExit(
            "The web server's dependencies are not installed. Run:\n\n"
            "    python3 -m pip install -e ./engine\n"
            "    python3 -m pip install -e ./web\n")

    if args.port is not None:
        if not port_is_free(args.host, args.port):
            raise SystemExit(
                "Port %d is already in use -- most likely another copy of this server.\n"
                "Find it with: lsof -nP -iTCP:%d -sTCP:LISTEN\n"
                "Or just leave the port off and one will be chosen for you."
                % (args.port, args.port))
        port, moved = args.port, False
    else:
        port, moved = pick_port(args.host, FIRST_PORT)

    url = "http://%s:%d/" % (args.host, port)

    # flush explicitly: Python block-buffers stdout when it is not a terminal, and this
    # banner is the whole reason this script exists. Piped into a log or a pane, an
    # unflushed URL is an invisible one.
    banner = [""]
    if moved:
        banner.append("  Port %d was busy, so this is on %d instead." % (FIRST_PORT, port))
    banner += ["  Royals is running at  %s" % url, "  Press Ctrl-C to stop.", ""]
    print("\n".join(banner), flush=True)

    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url, args.host, port),
                         daemon=True).start()

    try:
        uvicorn.run("royals_web.main:app", host=args.host, port=port,
                    reload=args.reload, log_level="warning")
    except KeyboardInterrupt:
        pass


# The AI runs in a process pool, and on macOS a new worker process starts by re-importing
# whatever module is __main__ -- this one. Without this guard each worker would re-run
# main() and try to start its own server, which surfaces as a BrokenProcessPool the
# moment the computer is first asked to think.
if __name__ == "__main__":
    sys.exit(main())
