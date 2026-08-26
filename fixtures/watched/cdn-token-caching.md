---
title: CDN cache keys and signed URLs
tags: [performance, auth, infrastructure]
---

Notes on how the CDN layer handles signed asset URLs. Superficially this touches tokens and auth
and performance, but it has nothing to do with how callers authenticate to the API.

Signed URLs carry an expiry and an HMAC in the query string. The CDN validates the HMAC at the
edge without talking to us at all, which is the entire point — asset delivery should not depend
on our availability.

Cache keys deliberately exclude the signature, otherwise every signed URL would be a cache miss
and the CDN would be useless. We normalise the path, drop all query parameters except `v`, and
key on that.

Token TTL here is 24 hours, which is much longer than anything on the API side because the
consequence of a leaked asset URL is far lower than a leaked API credential.
