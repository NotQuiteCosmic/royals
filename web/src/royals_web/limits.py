"""A request body limit that counts what arrives, rather than believing what is declared.

The obvious way to bound a request body is to read Content-Length and refuse anything
too large. That was what this app did, and it does not work: **a chunked request has no
Content-Length at all**, so the check is simply skipped and the body is buffered in full.
Measured against the running server before this existed, a 5 MB body sailed past a 2 KB
limit and reached the JSON parser. No game id, no token, no authentication of any kind --
the cheapest denial of service there is, available to anybody who can reach the address.

So this counts. Which means it has to be ASGI middleware, not the `@app.middleware("http")`
kind: Starlette's HTTP middleware runs on an assembled `Request`, which is to say it runs
*after* the body it is supposed to be bounding has already been read into memory. The only
place to stand is between the server and the application, wrapping `receive` and watching
the chunks go past.

Two paths, deliberately:

- **Declared** -- a Content-Length that is already too big is refused before a single byte
  of body is read. Every honest client sends one for a body this small, so this is the
  path real people take and it gives them a clean 413.
- **Counted** -- chunks are added up as they arrive and the request is cut off the moment
  the total passes the limit. This is the path a request takes when it declined to say how
  big it was, which is not something an honest client does here.

The limit is per request and there is no attempt to bound *concurrent* requests; that is
the global rate limiter's job, and between them the memory a stranger can cause this
process to allocate is bounded.
"""

from starlette.datastructures import Headers


class BodyLimit:
    """Refuse request bodies over `max_bytes`, whether or not they admit their size."""

    def __init__(self, app, max_bytes):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        declared = Headers(scope=scope).get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            return await self._refuse(send)

        seen = 0
        exceeded = False

        async def counted():
            nonlocal seen, exceeded
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    exceeded = True
                    # A disconnect rather than an exception, because an exception is not
                    # ours to control once it leaves here: FastAPI wraps body parsing and
                    # turns anything raised during it into its own 400, which lands
                    # before this middleware gets to say 413. A disconnect is a thing the
                    # application already knows how to stop for.
                    return {"type": "http.disconnect"}
            return message

        async def watched(message):
            # Once the body is over the limit, whatever the application decided to say
            # about the truncated request it saw is not the answer -- the answer is 413,
            # and it is sent below.
            if not exceeded:
                await send(message)

        try:
            await self.app(scope, counted, watched)
        except Exception:
            # A disconnect mid-body is a legitimate thing to raise on. Anything raised
            # when the body was *not* over the limit is a real error and stays real.
            if not exceeded:
                raise

        if exceeded:
            await self._refuse(send)

    async def _refuse(self, send):
        # The limit is named rather than merely enforced. Every legitimate request here is
        # a handful of small fields and never comes close, with one exception: uploading a
        # game record to review it, which is the one request a person can make that is
        # large enough to be refused and long enough that they cannot see why. A number
        # they can compare their file against is the difference between a bug and a rule.
        body = ('{"detail":"request body too large -- the limit is %d bytes"}'
                % self.max_bytes).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
