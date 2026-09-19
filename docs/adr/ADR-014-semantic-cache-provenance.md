# ADR-014: Semantic cache identity and provenance

Status: Partially superseded — 语义缓存层（`SemanticResultCache`/L1/L2/`cache_ttl`）与遗留的 `QuoteCache`/`KlineCache` 已随 v16 Phase 2 物理删除，数据请求零缓存；本 ADR 的 **identity/provenance 不可伪造**结论保留为现行契约，落在 `tstdx/runtime/identity.py` 与 `tstdx/runtime/provenance.py`。

## Context

The legacy caches predate Provider-first planning. `QuoteCache` keyed only by the
symbol sequence, and `KlineCache` persisted by symbol/period/datetime (both are
since deleted; they are named here only as the history of this decision). Neither
identity is sufficient for a runtime where the same request may be served by
different Providers, Channels, currentness modes or semantic options.

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
