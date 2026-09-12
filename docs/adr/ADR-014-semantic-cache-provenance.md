# ADR-014: Semantic cache identity and provenance

Status: Accepted

## Context

The legacy caches predate Provider-first planning. `QuoteCache` keys only by the
symbol sequence, and `KlineCache` persists by symbol/period/datetime. Those APIs
remain useful as compatibility optimizations, but neither identity is sufficient
for a runtime where the same request may be served by different Providers,
Channels, currentness modes or semantic options.

A second problem is provenance laundering: a cache hit must not transform replay
or synthetic data into direct Provider data merely because it was retrieved from
a cache.

## Decision

The v11 runtime uses `SemanticResultCache` for QueryPlan results.

1. The canonical cache key is the full `QueryFingerprint`.
2. Every entry redundantly records Provider, Channel and Capability and validates
   them against the requesting `QueryPlan` before a hit is returned.
3. The original `Provenance.kind` is stored without a cache tier. Retrieval adds
   only the current cache tier.
4. Schema mismatch, expiry, corrupt entries, fingerprint mismatch and identity
   mismatch are misses and the entry is discarded.
5. Returned data is deep-copied so a caller cannot mutate the stored value.
6. Cache boundaries catch `Exception`, never `BaseException`; process-control
   signals remain observable.
7. Negative caching is deliberately excluded here and remains part of the later
   Batch/SingleFlight/negative-cache slice.
8. Legacy `KlineCache`/`QuoteCache` are not silently reinterpreted as semantic
   caches. They stay compatibility paths until Facade/DataSourceRouter cutover.

## Consequences

Cross-provider/channel cache pollution becomes structurally impossible on the
v11 path. A cached `DIRECT` result remains direct, while cached `REPLAY` and
`SYNTHETIC` results retain their original origin. Cache retrieval status is a
separate property and cannot promote trust.
