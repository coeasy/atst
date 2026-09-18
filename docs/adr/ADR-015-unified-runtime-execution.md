# ADR-015: Unified Runtime Execution Cutover

Status: Superseded in part — 本文描述的链路含 `SemanticResultCache / NegativeCache` 等缓存节点，该等节点已随 v16 Phase 2 删除。现行唯一路径为 `Client → QuerySpec → QueryPlan → DirectProviderExecutor → QueryResult`（零缓存、不自动换源），见 `docs/ARCHITECTURE.md`。

## Decision

The canonical execution path is now:

```text
Surface
  -> QuerySpec
  -> QueryPlanner
  -> exactly one Provider + Channel
  -> SemanticResultCache / NegativeCache
  -> SingleFlight
  -> DirectProviderExecutor
  -> QueryResult + Provenance
  -> ErrorEnvelope at external boundaries
```

`UnifiedQuoteAPI(route="auto")` remains a compatibility orchestration surface only. It no longer defines the semantics of the core runtime.

## Invariants

1. One QueryPlan executes one Provider and one Channel.
2. No Direct binding may cross Provider boundaries.
3. TDX host failover is allowed only inside the TDX Provider.
4. `local_vipdoc` remains an independent Provider.
5. Cache identity uses the full QueryFingerprint and preserves provenance.
6. `currentness="live"` cannot be satisfied by replay/synthetic data.
7. SingleFlight keys use the exact QueryFingerprint.
8. Followers receive independent mutable values/exceptions.
9. Negative cache stores only non-retryable TdxError failures; transient SourceUnavailable is excluded.
10. External boundaries normalize only `Exception`; `KeyboardInterrupt` and `SystemExit` remain process-control signals.
11. Native/unexpected exceptions become safe E9000 envelopes without leaking internal text/context.

## Direct Provider coverage

The unified `quotes`/`bars` planner surface has exact bindings for:

- TDX quotation
- local_vipdoc bars
- Tencent quote/kline/minute_kline
- Sina quote/history_kline
- Eastmoney quote/kline
- Baidu quote/kline

Provider-specific auxiliary capabilities remain under their existing dedicated APIs until their semantic contracts are promoted into QuerySpec.

## Migration

Phase A (this integration PR): establish the canonical runtime beside the legacy facade and expose it publicly.

Phase B: migrate REST/WS/MCP/CLI/background-task entrypoints to the same ErrorEnvelope and UnifiedRuntime.

Phase C: make legacy facade quotes/bars delegate to UnifiedRuntime for explicit Provider requests; keep `route="auto"` as an explicit compatibility orchestration policy.

Phase D: remove duplicate source-routing/cache semantics once compatibility telemetry shows the old path can be retired.

## Merge gate

Do not interpret workflow-level success as proof. A blocking gate counts only when a runner is assigned, its steps actually execute, and the exact integration SHA is green.