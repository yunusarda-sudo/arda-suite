# Arda Core M2.3B — Live Source/Search Adapters

Durum: bounded live acquisition slice implemented  
LLM synthesis, browsing loops, browser automation and shell: kapsam dışı

## Pipeline

```text
provider search
  -> SearchCandidate
  -> SafeHTTPFetcher
  -> normalized FixtureSource
  -> M2.3A evidence/claim pipeline
```

Search snippets are candidate metadata only; they are not evidence. Evidence is
derived from bounded fetched content and retains source/provenance metadata.

## Search contract

`ProviderSearchAdapter` exposes provider-independent candidates with provider,
query, rank, title, URL, snippet, retrieval time and sanitized provider metadata.
The existing `arda.research` providers remain adapters; no provider is made
canonical by the worker.

## Safe fetch contract

`SafeHTTPFetcher`:

- permits HTTPS; HTTP requires explicit `allow_http=True` test/policy opt-in;
- rejects file/FTP, credentials in URLs, loopback, RFC1918/private, link-local,
  reserved, multicast, unspecified and private IPv6 destinations;
- validates the original hostname and every redirect destination;
- disables environment proxies and arbitrary request headers;
- bounds timeout, redirects and response bytes;
- accepts only `text/html`, `text/plain` and `application/json`;
- records HTTP status, content type, requested/final URL, retrieval time and raw
  content SHA-256.

HTML is reduced to text without executing JavaScript. Responses are never
silently truncated: over-limit content is an explicit failure.

## Acquisition and provenance

`LiveSourceAcquirer` deterministically deduplicates normalized URLs and identical
content hashes. Partial acquisition returns explicit typed failures; a failed
source never becomes evidence. A normalized source preserves search provider,
query, rank, redacted URLs, final URL, status, content type, retrieval timestamp
and content hash, so it can feed M2.3A without changing its claim/evidence rules.

API keys, cookies, auth headers and signed URL values are not accepted as fetch
inputs or written to provenance. Live internet tests are separate from offline
contract tests.

## Exclusions

No LLM synthesis, arbitrary link following, JavaScript/browser execution,
PDF/OCR, shell execution, autonomous task creation or persistent learning.
