"""Who is allowed to move: seat tokens, and the one comparison that must be constant-time.

There are no accounts, and for playing a friend by link there do not need to be. A seat
is claimed by whoever holds its token, the token is 128 bits of `secrets`, and that is
the whole of the authorization model. It is worth being explicit about what that does and
does not buy, because the temptation is to reason about it as though it were a password:

**Only the hash is stored.** The token is handed to a browser exactly once -- at creation,
or when an invite is claimed -- and what the server keeps is its SHA-256. A leak of the
database is then not a leak of anyone's seat. There is no key-stretching here and there
should not be: KDFs like Argon2 exist to make *guessable* secrets expensive to guess, and
a 128-bit random token is not guessable. A plain hash is the right tool for a
high-entropy secret, and a slow one on every request would be a denial-of-service knob.

**Comparison is `compare_digest`.** A byte-at-a-time `==` on a secret leaks, through
timing, how long a shared prefix was, and an attacker who can measure that can extend a
guess one byte at a time. It is a narrow channel over a network and a wide one on the
same host. The fix costs nothing, so there is no version of this worth arguing about.

**The transport is a header, not a cookie** -- see main.py. That is what keeps CSRF out
of the design rather than defended against.

What this model deliberately does not do is survive losing the browser that holds the
token. There is no account to recover a seat against, so the only recovery is the resume
link, which carries the token in a URL fragment. That is a real trade, made knowingly:
the stake is a game of an abstract strategy game, and the alternative was shipping
accounts first.
"""

import hashlib
import secrets

# 16 bytes -> 22 URL-safe characters. Long enough that guessing is not a threat model,
# short enough to sit in a link somebody pastes into a message.
TOKEN_BYTES = 16


def mint():
    """A fresh seat or invite token."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token):
    """What actually gets stored. Never the token itself."""
    if not isinstance(token, str):
        raise TypeError("a token is a string")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify(presented, stored_hash):
    """Constant-time in the hash, and false rather than raising on anything malformed.

    `presented` is untrusted and arrives from a header, so it may be None, empty, or not
    a string at all. Those are all simply "no", and none of them may take a detectably
    different amount of time from a wrong-but-well-formed token.
    """
    if not stored_hash or not isinstance(presented, str) or not presented:
        return False
    return secrets.compare_digest(hash_token(presented), stored_hash)
