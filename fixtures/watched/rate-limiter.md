# Rate limiter internals

The rate limiter runs as middleware ahead of the application handlers. It buckets requests per
caller and rejects anything over the configured ceiling with a 429.

Identifying the caller is the fiddly part. The limiter keys off the session store — it reads the
session to get a stable subject identifier, then uses that as the bucket key. For anonymous
traffic it falls back to the client IP, which is noticeably worse behind a shared NAT because
every caller behind it shares one bucket.

The buckets themselves live in a small in-memory ring per process, flushed to the session store
every thirty seconds so the counts survive a rolling restart. This is why the limiter cannot
currently run standalone: it has a hard dependency on the session store being reachable.

Configuration lives in `limits.yaml`. The defaults are deliberately conservative.
