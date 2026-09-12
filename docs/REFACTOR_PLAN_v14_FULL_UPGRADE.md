# REFACTOR_PLAN_v14_FULL_UPGRADE

> Status: **implementation in progress**  
> Branch: `v14-runtime-phase1`  
> Draft PR: `#7`  
> Baseline: `main` / v1.0.0

## 1. Goal

V14 moves tstdx toward one orchestration runtime **without replacing the mature semantic contracts already present in the repository**.

The architecture source of truth is now:

```text
Python API / CLI / REST / WS / MCP
              |
              v
        Runtime boundary
        QueryRequest
              |
              v
      ExecutionPlanner
      (DAG/orchestration)
              |
              v
   canonical QueryPlanner
  QuerySpec -> QueryPlan
  (single Provider identity)
              |
       +------+------+
       |             |
       v             v
SemanticResultCache  dynamic Provider adapter
(QueryFingerprint)   tdx / local_vipdoc /
                     eastmoney / tencent / ...
       |             |
       +------+------+
              v
     canonical QueryResult
        + Provenance
              |
              v
       Runtime Response
```

Key distinction:

- `tstdx.query.QueryPlanner` owns deterministic semantic planning for **one Provider + Channel**.
- `tstdx.execution.ExecutionPlanner` owns cross-provider fallback and DAG orchestration.
- `tstdx.result.Provenance` owns data origin.
- `tstdx.cache_semantic.SemanticResultCache` owns semantic caching.
- `tstdx.provider` contains dynamic execution adapters only; it is **not** a second provider registry.
- `tstdx.providers` remains the canonical Provider/Channel/Capability registry.

## 2. Hard constraints

1. Existing `TdxClient`, `AsyncTdxClient` and `UnifiedQuoteAPI` APIs remain compatible.
2. Core installation remains zero hard dependencies.
3. Protocol parser behaviour is not rewritten as part of Runtime Phase 1.
4. New runtime paths require contract tests before replacing legacy routing.
5. No merge until the final same SHA executes the required CI steps and is green.
6. `tstdx.providers`, `tstdx.query`, `tstdx.result` and `tstdx.cache_semantic` remain semantic single sources of truth.
7. `web` is not a Provider identity; concrete Providers such as `eastmoney`, `tencent`, `sina` are separate trust boundaries.
8. Cache is not a Provider. A cache hit preserves original Provider provenance and only adds `cache_tier`.
9. Execution graphs reject duplicate nodes, missing dependencies and cycles.
10. Runtime fallback must expose the selected Provider and provider-attempt diagnostics without overwriting canonical data provenance.

## 3. Architecture correction made during implementation

The first V14 skeleton accidentally introduced three parallel concepts:

- `Planner` alongside the existing `QueryPlanner`;
- a runtime `cache_key` alongside canonical `QueryFingerprint`;
- `CacheProvider` / generic `web` / generic `local` identities alongside `tstdx.providers` and canonical `Provenance`.

The branch has been corrected:

- V14 `Planner` is now `ExecutionPlanner`.
- Runtime uses `request_key` only for tracing/single-flight style identity; semantic cache identity remains `QueryFingerprint`.
- `CacheProvider` was deleted.
- `WebProvider` requires a concrete canonical Provider id.
- `LocalProvider` uses canonical id `local_vipdoc`.
- TDX/Web/Local dynamic adapters consult the existing `PROVIDERS` registry for capability eligibility.
- Core `quotes` / `bars` execution is bridged into existing `QuerySpec -> QueryPlan -> QueryResult -> Provenance` contracts.

This correction is deliberate: V14 extends orchestration and does not create a shadow market-data architecture.

## 4. Current implementation status

### 4.1 Runtime boundary — IMPLEMENTED

```text
tstdx/runtime/
  __init__.py
  bootstrap.py
  context.py
  request.py
  response.py
  runtime.py
```

Implemented contracts:

- `QueryRequest` preserves positional arguments, keyword arguments and runtime metadata.
- `request_key` is explicitly diagnostic, not a semantic cache key.
- legacy `cache_key` remains only as a temporary branch compatibility alias to `request_key`.
- `ExecutionContext` carries `request_id`, `trace_id`, timeout and selected Provider.
- `QueryResponse` carries success/data/error/error-code plus runtime metadata.
- compatibility handlers remain supported.
- canonical `QueryResult` is unwrapped at the runtime boundary so v1-style callers still receive raw data.
- response metadata carries canonical `query_fingerprint`, channel and serialized `Provenance` when semantic execution is used.
- `create_runtime()` registers injected canonical execution backends without eager optional dependencies.

### 4.2 Execution DAG — IMPLEMENTED

```text
tstdx/execution/
  __init__.py
  node.py
  graph.py
  plan.py
  planner.py
  semantic.py
```

Implemented guarantees:

- duplicate execution nodes rejected;
- missing dependencies rejected;
- cycle detection includes a readable cycle path;
- explicit plan output node;
- `ExecutionPlanner` is separate from canonical `QueryPlanner`;
- core `quotes` / `bars` are compiled through canonical query semantics;
- non-core operations may still use generic dynamic execution until typed/canonical adapters are added.

### 4.3 Canonical semantic bridge — IMPLEMENTED FOR `quotes` / `bars`

`SemanticExecutionAdapter`:

1. converts the runtime call shape to existing `QuerySpec`;
2. compiles it through existing `QueryPlanner`;
3. checks existing `SemanticResultCache` by canonical `QueryFingerprint` when configured;
4. executes a concrete Provider adapter on miss;
5. wraps raw data in existing `QueryResult` + `Provenance`;
6. preserves requested Provider on cross-provider fallback;
7. returns cached results with original origin unchanged and only `cache_tier` added.

No new CacheEntry/Provenance model is introduced.

### 4.4 Dynamic Provider execution — IMPLEMENTED (Phase 1)

```text
tstdx/provider/
  __init__.py
  base.py
  router.py
  tdx.py
  web.py
  local.py
```

Rules:

- `tstdx.providers` is the static Provider/Channel/Capability source of truth.
- `tstdx.provider` only holds runtime execution objects.
- TDX adapter id is `tdx`.
- local adapter id is `local_vipdoc`.
- web adapters require explicit canonical ids such as `eastmoney`, `tencent`, `sina`.
- `CacheProvider` no longer exists.
- Router records `not_registered / unsupported / unhealthy / failed / selected`; semantic cache adds `cache_hit` diagnostics.
- provider-attempt diagnostics are runtime diagnostics, not canonical data provenance.

### 4.5 Facade Runtime Adapter — IMPLEMENTED, NOT DEFAULT

```text
tstdx/facade/runtime_adapter.py
```

Rules:

- `route='tdx'` -> Provider `tdx`.
- `route='local'` -> Provider `local_vipdoc`.
- `route='auto'` leaves selection to orchestration.
- `route='web'` is intentionally rejected unless explicit concrete `providers=(...)` are supplied.
- a concrete canonical Provider id may be used directly.
- `RuntimeFacadeAdapter` converts `QueryResponse` back to existing `ApiResponse` and preserves runtime error code/metadata.

`UnifiedQuoteAPI` itself remains on the legacy path until parity and same-SHA CI gates are satisfied.

## 5. Tests added

```text
tests/v14/
  test_runtime_execution.py
  test_runtime_bootstrap.py
  test_facade_runtime_adapter.py
  test_semantic_execution.py
```

Coverage includes:

- failed/unhealthy/selected fallback diagnostics;
- explicit Provider selection;
- compatibility handlers;
- unsupported-operation envelope;
- ExecutionPlanner/Router identity coherence;
- runtime `request_key` stability without claiming semantic cache identity;
- TDX positional/keyword argument preservation;
- canonical Provider capability routing;
- rejection of ambiguous `web` Provider identity;
- canonical local identity `local_vipdoc`;
- execution graph cycle/missing-dependency gates;
- facade route mapping and ambiguous web-route rejection;
- runtime error-code propagation;
- canonical QueryFingerprint / QueryResult / Provenance integration;
- semantic cache hit without Provider-origin corruption;
- cross-provider fallback provenance (`requested_provider`, `fallback=True`).

## 6. CI status

PR #7 remains Draft.

Observed during implementation:

- `Native` has repeatedly executed successfully on branch heads.
- several main `CI` runs have failed before runner assignment / before any steps existed; those runs provide no source-level failure evidence.
- the current head continues to trigger pull-request workflows.

Merge rule is unchanged: `mergeable=true` is not a release gate. Merge only when the final same SHA executes the required real steps and is green.

## 7. Phase 2 — Complete semantic migration

### 7.1 Expand canonical request bridging

After `quotes` / `bars`, migrate capabilities in groups using existing `QuerySpec`, `typed_query.py` and `PROVIDERS` capability contracts. Do not add generic kwargs-only semantics when a typed/canonical query already exists.

Priority:

1. F10 / finance statements;
2. fund / bond / futures / options typed queries;
3. news / research capabilities;
4. local-reader capabilities that have canonical Provider contracts.

### 7.2 Cache policy

Reuse `SemanticResultCache` only.

Next work:

- policy-driven TTL by capability/currentness;
- L1/L2 composition without changing Provenance origin;
- invalidation hooks;
- cache metrics;
- single-flight around identical `QueryFingerprint` requests.

Do **not** create a new `CacheEntry` hierarchy unless the existing semantic cache schema is formally migrated.

## 8. Phase 3 — Provider runtime policy

Next provider orchestration work:

- operation-specific health history;
- latency/success ranking;
- circuit state;
- retry advice integration with existing error taxonomy;
- capability-aware fallback order;
- failure attempt provenance/metrics;
- deterministic policy tests.

Static capability truth remains in `tstdx.providers`.

## 9. Phase 4 — Canonical Domain

Extend existing `tstdx.domain`; do not create a parallel domain hierarchy.

Priority:

1. Symbol / market identity;
2. Quote;
3. Bar;
4. Tick/minute;
5. corporate-action metadata;
6. normalization between existing provider payloads and domain models.

## 10. Phase 5 — Streaming Runtime

Integrate the existing StreamEngine rather than replacing it.

Potential orchestration additions:

```text
MarketEvent
EventBus
StateStore
WindowProcessor
Subscription
```

Invariants:

- QuoteStream / AsyncQuoteStream compatibility;
- reconnect/backpressure/gap-fill remain owned by existing streaming engine;
- replayability;
- bounded state policies;
- no second streaming state machine.

## 11. Phase 6 — Gateway convergence

REST, WebSocket, MCP and CLI translate boundary requests and delegate execution to Runtime.

Gateways must not implement independent Provider selection, fallback, cache or provenance logic.

## 12. Phase 7 — Optimizer

Only after semantic parity is stable:

- DAG common-subexpression elimination;
- parallel independent nodes;
- semantic-cache-aware rewrite;
- incremental execution for streaming updates;
- batch Provider execution.

The optimizer must not change `QuerySpec`, `QueryFingerprint`, Provider identity or observable result semantics.

## 13. Phase 8 — release hardening

Required gates:

- Ruff check + format;
- mypy;
- full non-network pytest matrix;
- coverage >= repository baseline;
- AST module reachability with zero accidental orphans;
- golden/spec/originality/adversarial gates;
- wheel/sdist install smoke;
- final same-SHA workflow evidence.

## 14. Explicit non-goals

- replacing `tstdx.query.QueryPlanner` with the DAG planner;
- replacing `tstdx.result.Provenance`;
- replacing `tstdx.cache_semantic` with a new cache model;
- treating cache as a Provider;
- treating `web` as one Provider;
- creating a second symbol/domain single source of truth;
- rewriting protocol parsers;
- changing v1 public signatures before parity gates exist.

## 15. Migration strategy

```text
Stage A  Runtime boundary + DAG contract
Stage B  Reconcile with existing Query/Result/Provider semantics   <-- current
Stage C  Core semantic parity + SemanticResultCache
Stage D  Opt-in UnifiedQuoteAPI runtime path
Stage E  Gateway reuse
Stage F  Runtime becomes default after same-SHA gates
Stage G  Remove duplicated legacy orchestration after deprecation window
```

## 16. Immediate next tasks

1. Let the latest same-SHA CI obtain a real runner and execute steps.
2. Fix actual Ruff/mypy/test failures before expanding scope.
3. Add canonical semantic parity tests against direct `QueryPlanner` for `quotes` and `bars`.
4. Extend semantic bridge to typed capabilities already represented by `typed_query.py`.
5. Add operation-aware Provider policy/health without duplicating `PROVIDERS` capability truth.
6. Wire `UnifiedQuoteAPI.query()` to Runtime behind an opt-in switch only after parity tests are green.

---

This document is the V14 implementation source of truth. Planned modules must not be marked complete before code and tests exist, and runtime orchestration must reuse existing semantic single sources of truth rather than creating parallel contracts.
