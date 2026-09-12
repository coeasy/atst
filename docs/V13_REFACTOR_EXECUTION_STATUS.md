# tstdx v13 Clean-Break Refactor Execution Status

> This file records implementation state against `ARCHITECTURE_SEMANTIC_ALIGNMENT_v13.md`, `IMPLEMENTATION_ALIGNMENT_MATRIX_v13.md` and `REFACTOR_PLAN_v13_CLEAN_BREAK.md`.
>
> Status claims are source-level only until exact-head CI receives real runners and executes blocking steps.

## Completed source-level convergence

- [x] `QuerySpec` is Provider-only: legacy `source` removed.
- [x] generic `allow_partial` removed from normal Query semantics.
- [x] `BatchSpec` introduced for explicit partial/batch semantics.
- [x] Query fingerprint schema v2 includes market and all current semantic identity fields.
- [x] Provider Registry reduced to executable truth; historical unbound capabilities are no longer advertised.
- [x] `web` pseudo-provider rejected.
- [x] TDX Tier-A capabilities promoted: quotes, bars, snapshot, minute, trades, security_count, security_list.
- [x] Tier-A TDX capabilities have exact Direct bindings.
- [x] local_vipdoc remains a separate historical Provider with day/1min/5min exact reader bindings.
- [x] Tencent/Sina/Eastmoney/Baidu canonical quotes/bars bindings retained explicitly.
- [x] `UnifiedRuntime` exposes Tier-A execution through one Provider-only kernel.
- [x] `ProviderOrchestrator.execute()` is capability-generic and remains the only cross-Provider fallback layer.
- [x] `Client` / `AsyncClient` introduced as the sole supported high-level business API.
- [x] `StreamSpec` / `StreamPlan` / `StreamPlanner` introduced; current stream binding is explicitly TDX quotation only.
- [x] top-level package exports switched to Client-first v13 contracts; legacy business clients/facade removed from supported exports.
- [x] HTTP rewritten as `/v13` Client adapter for Tier A.
- [x] WS JSON-RPC rewritten as Client adapter for Tier A.
- [x] CLI rewritten as Client adapter for Tier A + Stateful stream.
- [x] MCP rewritten to use Client only; no facade/TdxClient dual target and no `use_facade` tools.
- [x] MCP tool manifest reduced to canonical promoted capabilities only.
- [x] shared transport result serializer preserves Provider/Channel/Fingerprint/Provenance.
- [x] L1 semantic cache upgraded to bounded deterministic LRU with metrics.
- [x] terminal negative cache upgraded to bounded deterministic LRU with metrics.
- [x] persistent L2 moved to schema/table v2 with deterministic payload SHA-256 integrity and constant-time hash comparison.
- [x] persistent L2 still accepts DIRECT non-fallback provenance only.
- [x] `UnifiedQuoteAPI`, legacy async facade, legacy RouteSelector and facade runtime bridge/factory removed from source tree.
- [x] obsolete v12 compatibility document removed.
- [x] old tests requiring legacy facade/route retention removed or rewritten as v13 clean-break gates.
- [x] architecture test added for public API, QuerySpec fields, Registry/Binding parity, Tier-A coverage, stream binding, MCP tool surface and retired facade.

## Canonical end-to-end chain after this refactor

```text
Python / Async / CLI / HTTP / WS / MCP
                |
                v
         Client / AsyncClient
                |
        +-------+--------+
        |                |
        v                v
    QuerySpec       FallbackPolicy
        |                |
        v                v
   QueryPlanner   ProviderOrchestrator
        |                |
        +-------+--------+
                v
          UnifiedRuntime
                |
   L1 -> L2 -> NegativeCache -> SingleFlight
                |
                v
      exact DirectProvider binding
                |
                v
    Provider-native implementation
                |
                v
 QueryResult / ResultMeta / Provenance
                |
                v
      shared transport serialization
```

Streaming:

```text
Client.stream
  -> StreamSpec
  -> StreamPlanner
  -> exact tdx/quotation stream binding
  -> StatefulQuoteStream / AsyncStatefulQuoteStream
  -> StreamState lifecycle
```

## Explicitly retired semantics

The following are no longer part of the supported v13 architecture:

- `UnifiedQuoteAPI`
- `AsyncUnifiedQuoteAPI`
- `quote_api()` / `runtime_api()` facade factories
- `RouteSelector`
- `route=`
- `route="auto"`
- `source` as a QuerySpec Provider synonym
- `web` as a Provider identity
- generic normal-query `allow_partial=True`
- facade-owned cross-source fallback
- MCP `use_facade`
- MCP direct TdxClient-vs-Facade business routing
- v12 compatibility-cutover plan

## Not claimed complete yet

The following must not be described as complete until verified/followed through:

- real Ruff/mypy/pytest/coverage execution on the exact latest head;
- real network Provider smoke/calibration;
- scan/removal of any now-orphan legacy CLI/facade/source files that are unreachable but still physically present;
- migration or deliberate removal of non-Tier-A historical capability families (finance, corporate action, F10, blocks, funds, bonds, futures, options, rankings, news/events, etc.);
- any source-level issue revealed by real deterministic gates.

## Release / merge gate

PR stays Draft. A workflow-level `success` does not count unless its blocking jobs received a runner and executed steps. `steps=[]`, `steps=null`, `runner_id=0`, skipped/disabled/soft-failed jobs are not valid green evidence.

The branch may leave Draft only when the same exact head SHA executes all deterministic blocking gates and they are green. Real Provider smoke gates are recorded separately and may be availability-sensitive, but they cannot be replaced by mock-only claims.
