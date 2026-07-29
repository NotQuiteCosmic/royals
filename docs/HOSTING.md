# Putting Royals somewhere other people can reach it

```sh
sh tools/tunnel.sh up       # start, and prove the address actually serves
sh tools/tunnel.sh status   # is it still up? and if not, which half is wrong?
sh tools/tunnel.sh url      # print the address
sh tools/tunnel.sh down     # stop
```

That is the whole interface. The rest of this file is what the four words mean and what
they cannot do for you.

## What a quick tunnel is

`cloudflared tunnel --url http://127.0.0.1:8000` opens an outbound connection to
Cloudflare and asks for a hostname. Cloudflare invents one —
`some-four-random-words.trycloudflare.com` — terminates HTTPS at its edge, and forwards
everything back down the connection to the server on this machine. No account, no
payment, no port forwarding, and no inbound firewall hole: the connection is outbound
only.

The server itself binds `127.0.0.1`, so the tunnel is the only way in. That is deliberate,
and it is also why `--behind-proxy` is safe here — see the comment on `client_key` in
[web/src/royals_web/main.py](../web/src/royals_web/main.py).

## What it cannot do

**The address is temporary.** A new one every `up`. It has no relationship to the previous
one, and there is no way to ask for a particular name. A link you sent yesterday is dead.

**It dies when the Mac sleeps.** `up` prints a `caffeinate` command that keeps the machine
awake for exactly as long as the server runs.

**Cloudflare withdraws the hostname** once the tunnel's connections lapse — not merely
stops routing it, but removes it from DNS entirely. A visitor then gets a connection
failure rather than an error page, which looks like the internet being broken rather than
like a game having ended.

**Games survive all of that; links do not.** The games are in `royals.db` and come back
when the server does. Only the address is disposable.

If you want a link that still works tomorrow, you want a real deployment — a small
always-on machine and a domain you own. That is a separate piece of work and is not what
this script is for.

## Reading `status`

Four things can be wrong, they look identical in a browser, and they have four different
fixes. That is the entire reason `status` exists rather than "just try the link".

| It says | What happened | What to do |
|---|---|---|
| `server down` | The Python process is gone. | `down` then `up`. Check `.run/server.log`. |
| `server BAD` | The process is alive but not answering. | Look at `.run/server.log`; it is wedged, not missing. |
| `address BAD` | The hostname resolves, but Royals does not answer through it. | Almost always the server; look one line up. |
| `address GONE` | No resolver has the hostname. Cloudflare took it back. | `down` then `up` for a new one. The old link is unrecoverable. |
| `address up (but this Mac cannot resolve it)` | **The link works for everybody else.** This machine has a stale negative DNS entry. | Flush, as printed — or wait a few minutes. |

That last row is worth knowing about, because it is the one that will make you think you
have broken something when you have not. A freshly minted hostname takes a few seconds to
appear in DNS; anything that asks for it during those seconds gets `NXDOMAIN`, and macOS
caches the *negative* answer for minutes afterwards. The address then looks dead from the
machine hosting it while serving perfectly to the rest of the world. `status` tells the
two apart by asking a public resolver for a second opinion, and by fetching the health
endpoint straight from the edge address when the local resolver will not cooperate.

## Why the script and not two commands

Because two commands are what was tried first, and the result was a link that was
confidently handed over, checked once at the moment of creation, and dead two and a half
minutes later — with the tunnel still running and still reporting itself healthy, because
nothing had asked it whether anything was behind it.

So `up` refuses to print an address until Royals has answered `/api/health` *through the
public hostname*. That distinction matters: Cloudflare will cheerfully serve its own error
page from that hostname, with a 200-shaped experience in a browser, when the origin is
gone. Requiring `{"ok":true}` from the health endpoint means the thing that answered was
this program.

The processes are started with a real `setsid`, not `nohup … &`. The original failure was
that the server sat in the process group of the shell that launched it and was collected
along with it; the tunnel, which happened to survive, then spent eight hours logging
`connection refused` to nobody.
