# atst Refactor Plan v13 — Clean-Break Unified Runtime

> Status: design baseline for the next implementation phase
>
> Target branch: `refactor/runtime-integration-v12`
>
> Baseline: current Provider-first runtime work on PR #6
>
> Policy: **no backward-compatibility requirement for legacy public interfaces**. The repository converges on one modern architecture only.

---

## 1. Executive decision

The previous staged compatibility-cutover strategy is retired.

v13 adopts a **clean-break architecture**:

- one canonical query contract: `QuerySpec`;
- one planner: `QueryPlanner`;
- one strict execution kernel: `UnifiedRuntime`;
- one explicit cross-provider policy layer: `ProviderOrchestrator`;
- one result contract: `QueryResult + ResultMeta + Provenance`;
- one error contract: `ErrorEnvelope`;
- one semantic cache contract: full `QueryFingerprint` identity, L1 + optional safe L2;
- one streaming lifecycle model: explicit `StreamState`;
- protocol surfaces (Python API / CLI / HTTP / WS / MCP / task execution) are adapters over the same runtime, not independent business implementations.

Legacy APIs are removed instead of emulated.

The target is not “old behavior with a new engine”. The target is **one architecture with no duplicate routing, duplicate cache semantics, duplicate source identity, or duplicate error handling**.

---

## 2. Final target architecture

```text
User / Strategy / Application
        |
        +------------------------------+
        |                              |
        v                              v
   QuerySpec                     StreamSpec
        |                              |
        v                              v
   QueryPlanner                  StreamPlanner
        |                              |
        v                              v
   QueryPlan                     StreamPlan
        |                              |
        +-----------+------------------+
                    |
                    v
             UnifiedRuntime
                    |
        +-----------+-----------+
        |           |           |
        v           v           v
   L1 Semantic   SingleFlight   Negative Cache
      Cache                         (terminal only)
        |
        v
   Optional L2 Persistent Semantic Cache
        |
        v
        Direct Provider Executor
                    |
        +-----------+-----------+------------------+
        |           |           |                  |
        v           v           v                  v
       TDX      local_vipdoc   Tencent         Eastmoney / Sina / Baidu / ...
        |
        v
   Provider-native transport/adapters

Cross-provider fallback:

QuerySpec
   |
   v
FallbackPolicy
   |
   v
ProviderOrchestrator
   |
   +--> UnifiedRuntime(provider=A)
   +--> UnifiedRuntime(provider=B)
   +--> UnifiedRuntime(provider=C)

The kernel never performs implicit cross-provider fallback.
```

---

## 3. Architectural invariants

These are hard rules, not recommendations.

### 3.1 Provider identity

1. Every query executes against exactly one canonical Provider and one canonical Channel.
2. `local_vipdoc` remains a distinct Provider; it is never hidden inside TDX.
3. TDX host failover is internal to the TDX Provider and does not count as cross-provider fallback.
4. `web` is not a Provider. It must not exist as a runtime provider selector.
5. Ambiguous provider aliases are rejected at planning time.

### 3.2 Routing

1. `route="auto"` is removed.
2. `route="tdx" | "web" | "local"` is removed from public APIs.
3. Cross-provider fallback requires an explicit `FallbackPolicy`.
4. Every fallback attempt is recorded.
5. Fallback provenance records requested provider and actual provider.
6. Fallback results never masquerade as direct results.

### 3.3 Cache

1. Cache identity is the full `QueryFingerprint`.
2. Provider / Channel / Capability identity is redundantly verified.
3. LIVE queries can only be satisfied by DIRECT provenance.
4. Replay and synthetic data never become DIRECT by entering cache.
5. L2 uses safe serialization only; no pickle / executable deserialization.
6. Persistent rows embed and revalidate fingerprint inside payload.
7. L2 -> L1 promotion re-runs freshness and identity checks.
8. Negative cache is separate from successful empty data.
9. Retryable / transport / source-unavailable errors are not negative-cached.

### 3.4 Errors

1. Domain errors use `TdxError` subclasses.
2. External/native exceptions are normalized to a safe `ErrorEnvelope`.
3. Unknown internal failures become `E9000` without leaking private implementation details.
4. `KeyboardInterrupt`, `SystemExit` and other process-control `BaseException` signals are never normalized.
5. HTTP / WS / MCP / CLI only map the canonical envelope to their transport protocol.

### 3.5 Streaming

1. Only explicit lifecycle states are canonical: `CREATED/RUNNING/STOPPING/CLOSED/FAILED`.
2. FAILED is terminal unless a brand-new stream object is created.
3. Worker/task unexpected exit is a failure, not a silent close.
4. Backpressure and subscription identity are explicit runtime state.
5. Old implicit `_thread/_task/_stop` lifecycle APIs are removed after migration.

---

## 4. What is retained

The following assets remain authoritative and are expanded rather than replaced:

- `atst/query.py`
  - `QuerySpec`
  - `QueryFingerprint`
  - `QueryPlan`
  - `QueryPlanner`
- `atst/providers/`
  - `ProviderSpec`
  - `ChannelSpec`
  - `ProviderRegistry`
  - provider aliases and capability declaration
- `atst/result.py`
  - `ProvenanceKind`
  - `Provenance`
  - `ResultMeta`
  - `QueryResult`
- `atst/runtime.py`
  - `UnifiedRuntime`
- `atst/direct_provider.py`
  - exact provider/channel/capability bindings
  - binding audit
- `atst/cache_semantic.py`
  - in-memory L1 semantic cache
- `atst/cache_persistent.py`
  - safe persistent L2 semantic cache
- `atst/batch.py`
  - `BatchResult`
  - `SingleFlight`
  - `NegativeCache`
- `atst/error_envelope.py`
- `atst/orchestration.py`
  - `FallbackPolicy`
  - `ProviderOrchestrator`
- `atst/streaming/state.py`
- `atst/streaming/stateful.py`
- protocol parser / codec / reader / provider-specific adapter implementations that do not duplicate runtime orchestration.

---

## 5. What is removed or fundamentally rewritten

### 5.1 Remove legacy facade routing

Delete the old routing responsibility from:

- `UnifiedQuoteAPI`
- `RouteSelector`
- `_try_routes`
- route circuit state that exists only for legacy cross-source fallback
- public `route=` parameters

The modern Python API should expose runtime-native methods directly.

Target public style:

```python
from atst import Client

client = Client()
result = client.quotes(["sh600519"], provider="tdx")
result = client.bars("sh600519", provider="eastmoney", period="day")
```

Explicit fallback:

```python
result = client.quotes(
    ["sh600519"],
    policy=FallbackPolicy.build("tdx", "tencent", "sina"),
)
```

There is no `route="auto"`.

### 5.2 Remove DataSourceRouter as business-runtime kernel

`DataSourceRouter` currently owns historical multi-source degradation, legacy caches, reader routing, synthetic fallback and source bookkeeping.

That responsibility is removed.

Allowed end state:

- direct low-level utility helpers may survive if they are source-local and useful;
- no public runtime execution goes through `DataSourceRouter`;
- no automatic `tdx -> web -> reader -> cache -> synthetic` chain remains in the kernel;
- synthetic/replay paths are explicit providers or explicit test/replay tools, never fallback branches.

### 5.3 Replace UnifiedQuoteAPI

Do not keep `UnifiedQuoteAPI` as the canonical public object.

Create one modern high-level `Client` that is a thin adapter around:

- `UnifiedRuntime`
- `ProviderOrchestrator`
- canonical streaming runtime

`Client` must not contain source-specific fallback logic.

### 5.4 Rewrite protocol surfaces

Legacy protocol handlers are replaced rather than wrapped indefinitely.

Final surfaces:

- CLI: runtime-native command parser + ErrorEnvelope
- HTTP: one versioned API surface generated from runtime contracts
- WS: one JSON-RPC surface using the same request models and result serializer
- MCP: tools invoke `Client`/`UnifiedRuntime`; no direct TdxClient-vs-Facade split
- task/background execution: stores canonical result/error envelopes only

No separate legacy HTTP/WS execution implementation remains.

### 5.5 Retire old cache path

Retire runtime usage of:

- legacy quote TTL cache without Provider provenance;
- legacy Kline cache whose key does not match full QueryFingerprint;
- cache read/write paths hidden inside DataSourceRouter.

If a storage engine is still useful, adapt it behind the semantic-cache interface instead of preserving its old key semantics.

---

## 6. New public API design

### 6.1 Core client

Introduce:

```text
atst/client_api.py
  Client
  AsyncClient
```

The public client is intentionally small.

Core methods:

```python
Client.execute(spec: QuerySpec) -> QueryResult
Client.quotes(..., provider=...) -> QueryResult
Client.bars(..., provider=...) -> QueryResult
Client.quotes_batch(...) -> BatchResult
Client.stream(...) -> StatefulQuoteStream
Client.execute_with_policy(..., policy=FallbackPolicy) -> OrchestratedResult
```

The public client must not duplicate planner/provider/cache code.

### 6.2 Provider-specific capabilities

Do not force every specialized capability into generic `quotes/bars` abstractions.

Extend `QuerySpec` capability-by-capability only when all three are present:

1. Provider Registry declaration;
2. exact executable Direct binding;
3. contract tests.

Planned capability families:

- quotes
- bars
- minute
- trades
- snapshot/orderbook
- security_list
- security_count
- finance/fundamental
- corporate_action
- f10
- blocks
- funds
- futures
- bonds
- options
- market statistics / rankings
- news / events

No capability is considered implemented merely because a legacy Facade method exists.

---

## 7. Query model v13

`QuerySpec` becomes the single request SSOT.

Required fields remain explicit:

- capability
- provider
- symbols
- channel (optional selector, resolved to canonical channel)
- period
- count
- start/range
- adjustment
- currentness
- max_age
- partial policy

Add only structured, typed capability parameters. Avoid untyped `dict[str, Any]` escape hatches in hot paths.

### 7.1 QueryFingerprint

Fingerprint payload must include every value that can change result semantics:

- provider
- channel
- capability
- normalized symbols
- period
- range/count/start
- adjustment
- currentness
- max_age
- provider-specific semantic options
- schema/fingerprint version

Any new semantic option requires a fingerprint test.

---

## 8. Provider layer redesign

### 8.1 Registry is executable truth

For every registered capability/channel:

```text
Registry declaration
      ==
Direct binding
      ==
contract test
```

CI fails if any side is missing.

### 8.2 Provider adapter contract

Define a small runtime-facing adapter protocol instead of having the runtime know every implementation class.

Conceptually:

```python
class ProviderAdapter(Protocol):
    provider_id: str

    def execute(self, plan: QueryPlan) -> QueryResult: ...
```

Provider internals can still use TdxClient/httpx/readers, but cross-provider logic is forbidden inside adapters.

### 8.3 local_vipdoc

Keep it standalone.

Canonical periods:

- day -> `.day` -> `DayBarReader`
- 1min -> `.lc1` -> `MinBarReader(interval=1)`
- 5min -> `.lc5` -> `MinBarReader(interval=5)`

Unsupported periods fail at planning/binding time where possible, not after file I/O.

---

## 9. Runtime execution v13

Final execution sequence:

```text
validate QuerySpec
  -> normalize symbols/period/provider
  -> compile QueryPlan
  -> L1 lookup
  -> L2 lookup + verification + optional promotion
  -> negative-cache lookup
  -> SingleFlight acquire
  -> leader re-check cache
  -> execute exact Direct Provider binding
  -> build QueryResult + Provenance
  -> write L1
  -> best-effort write L2 when policy allows
  -> invalidate negative cache
  -> deep-isolated result to caller
```

### 9.1 No implicit orchestration in runtime

`UnifiedRuntime.execute()` never selects a second Provider after execution failure.

### 9.2 Batch

Batch semantics are explicit:

- `ok`
- `failed`
- `missing`
- `not_attempted`

`allow_partial=True` on a normal non-batch method is removed.

---

## 10. Explicit orchestration v13

`ProviderOrchestrator` becomes the only cross-provider fallback implementation.

Required features:

- ordered explicit Provider list;
- duplicate/unknown Provider rejection before execution;
- per-attempt status and canonical error code;
- requested vs actual Provider in provenance;
- optional retry policy separate from fallback policy;
- fallback result cannot be stored in DIRECT-only L2;
- no catch of process-control BaseException.

Future resilience features belong here, not in Provider adapters:

- circuit breaker
- hedged requests
- quorum / comparison mode
- health-aware policy
- latency-aware provider choice

Each feature must remain opt-in.

---

## 11. Cache v13

### 11.1 L1

Replace “clear entire dict when maxsize reached” with deterministic bounded eviction.

Preferred implementation:

- O(1) LRU or segmented LRU;
- counters: hit/miss/evict/reject;
- no global flush at capacity edge.

### 11.2 L2

Keep SQLite + JSON as default local backend initially.

Add:

- schema version migration policy;
- checksum/hash of serialized payload;
- optional compression after profiling;
- bounded vacuum/cleanup strategy;
- deterministic corruption handling;
- metrics for reject reasons.

### 11.3 Freshness clock

Separate:

- wall clock timestamp for persisted provenance;
- monotonic clock for in-process TTL bookkeeping where practical.

Persisted cache validation must remain robust across process restart.

---

## 12. Error system v13

One canonical hierarchy:

```text
TdxError
  ValidationError
  ProviderUnavailable
  ProviderProtocolError
  DataNotFound
  FreshnessViolation
  CapabilityUnsupported
  Authentication/Policy errors when applicable
  InternalError
```

`ErrorEnvelope` contains only safe fields:

- code
- type
- message
- retryable
- safe context allow-list
- protocol status mapping hints

Transport-specific mappings:

- HTTP status mapping
- JSON-RPC code mapping
- CLI exit code mapping
- MCP error payload mapping

The transport layer must not construct new business error meaning.

---

## 13. Streaming v13

Unify streaming under the same Provider-first model.

Introduce:

```text
StreamSpec
StreamPlan
StreamProviderBinding
StreamResult / StreamEvent provenance
```

The stream lifecycle remains `StreamLifecycle`.

Remove old stream implementations once all public entrances use `StatefulQuoteStream` / async equivalent.

Required gates:

- worker death -> FAILED;
- repeated start idempotence only while worker is alive;
- STOPPING timeout does not become CLOSED;
- failed/closed stream cannot restart;
- subscription IDs monotonic;
- bounded queue/backpressure behavior deterministic;
- cancellation does not erase failure provenance.

---

## 14. HTTP v13

One API only.

Recommended base path:

```text
/api/v1/runtime/*
```

Core endpoints:

- `POST /query`
- `POST /quotes`
- `POST /bars`
- `POST /batch/quotes`
- `GET /providers`
- `GET /providers/{id}/capabilities`
- `GET /health`

Do not maintain `/v2` plus legacy endpoint trees long-term.

Use explicit request/response models generated from runtime contracts where practical.

Every successful response carries ResultMeta/Provenance.

Every error carries ErrorEnvelope.

---

## 15. WebSocket v13

One JSON-RPC method namespace:

```text
runtime.query
runtime.quotes
runtime.bars
runtime.batch.quotes
stream.subscribe
stream.unsubscribe
stream.status
runtime.health
provider.list
```

No legacy method alias retention requirement.

Notifications remain response-free.

---

## 16. MCP v13

MCP stops choosing between `TdxClient` and `UnifiedQuoteAPI`.

All tools invoke the new `Client`.

Tool generation should be table-driven from capability metadata when practical.

MCP-specific code is limited to:

- tool schema
- parameter adaptation
- canonical result serialization
- ErrorEnvelope -> MCP error mapping

---

## 17. CLI v13

Redesign around Provider-first concepts.

Examples:

```text
atst providers
atst capabilities --provider eastmoney
atst quotes sh600519 --provider tdx
atst bars sh600519 --provider eastmoney --period day
atst quotes sh600519 --fallback tdx,tencent,sina
atst query --file query.json
```

Remove CLI options whose only purpose is legacy route/source compatibility.

---

## 18. Package/module cleanup

Target package shape:

```text
atst/
  query.py
  result.py
  errors.py
  error_envelope.py
  runtime.py
  orchestration.py
  batch.py
  cache_semantic.py
  cache_persistent.py
  client_api.py

  providers/
    registry.py
    protocol.py
    tdx.py
    local_vipdoc.py
    tencent.py
    sina.py
    eastmoney.py
    baidu.py
    ...

  streaming/
    spec.py
    plan.py
    state.py
    runtime.py

  integration/
    http.py
    ws.py
    mcp/
    tasks.py

  protocol/
  codec/
  reader/
  domain/
```

Remove or retire after migration:

- routing-only Facade modules;
- duplicate legacy source router execution paths;
- duplicate HTTP runtime implementations;
- duplicate WS runtime implementations;
- old compatibility wrappers that only rename equivalent APIs;
- obsolete ADRs/docs that describe removed architecture, after preserving relevant historical decisions in a migration note.

---

## 19. Implementation phases

### Phase 0 — Freeze the v13 contract

Deliverables:

- this plan;
- ADR for clean-break decision;
- final public API table;
- module deletion/retention manifest;
- capability inventory generated from current Registry and existing business methods.

Gate:

- no implementation proceeds without agreeing on canonical names and module ownership.

### Phase 1 — Public Client + Provider adapter protocol

Deliverables:

- `Client` / `AsyncClient`;
- Provider adapter protocol;
- exact binding audit adapted to provider modules;
- top-level exports rewritten around new API.

Remove:

- requirement to expose `UnifiedQuoteAPI` publicly.

Gate:

- quotes/bars/local reader direct tests;
- no source-specific fallback in Client.

### Phase 2 — Kill legacy routing kernel

Deliverables:

- all canonical quotes/bars execution uses UnifiedRuntime;
- explicit fallback uses ProviderOrchestrator only;
- delete `route="auto"` semantics;
- delete runtime dependence on DataSourceRouter.

Gate:

- repository search shows zero public `route=` runtime parameters;
- repository search shows zero canonical runtime calls to DataSourceRouter;
- no unscoped legacy quote/kline cache in canonical path.

### Phase 3 — Surface rewrite

Deliverables:

- new HTTP only;
- new WS only;
- MCP uses Client only;
- CLI uses Client only;
- task runtime uses Client only.

Gate:

- one behavior test suite runs against Python/HTTP/WS/MCP adapters using the same fake runtime;
- error/result parity across surfaces.

### Phase 4 — Capability expansion

Promote specialized capabilities in families.

For each family:

1. add typed QuerySpec parameters;
2. register capability/channel;
3. implement Direct binding;
4. add contract tests;
5. expose through Client;
6. expose through HTTP/WS/MCP only through shared adapter generation/mapping.

Suggested order:

1. snapshot + trades + minute;
2. security list/count;
3. finance/fundamental/F10/corporate actions;
4. funds/bonds/futures/options;
5. ranks/statistics/news/other web capabilities.

### Phase 5 — Streaming unification

Deliverables:

- StreamSpec/StreamPlan;
- Provider-first streaming adapter;
- remove legacy QuoteStream/AsyncQuoteStream public paths;
- HTTP/WS streaming uses same stream runtime.

### Phase 6 — Cache hardening and performance

Deliverables:

- LRU L1;
- L2 integrity hash;
- monotonic TTL where appropriate;
- metrics;
- concurrency/fault injection tests;
- benchmark gates.

### Phase 7 — Repository cleanup

Delete:

- old Facade routing code;
- old Router business execution code;
- old duplicate endpoint implementations;
- old compatibility exports;
- stale architecture docs.

Run orphan/reachability checks.

### Phase 8 — Release gate

Required on one exact SHA:

- Ruff
- mypy
- Python 3.10 / 3.11 / 3.12 / 3.13
- Windows and Linux required matrix
- Golden Gate
- Bridge Audit
- Spec Coverage
- Adversarial Payload Matrix
- Module Reachability / orphan gate
- docs link integrity
- security/originality gates
- Native gate if retained by repository policy
- real Provider smoke/calibration jobs where credentials/network are not required

`steps=[]`, `steps=null`, `runner_id=0`, skipped, disabled or workflow-shell success without executed jobs never count as green.

---

## 20. Test architecture

Tests are reorganized by contract instead of historical module.

```text
tests/runtime/
  contract/
    test_query.py
    test_provider_registry.py
    test_direct_binding.py
    test_result_provenance.py
    test_error_envelope.py
    test_cache_l1.py
    test_cache_l2.py
    test_singleflight.py
    test_negative_cache.py
    test_batch.py
    test_orchestration.py
    test_stream_state.py

  surfaces/
    test_python_api.py
    test_cli.py
    test_http.py
    test_ws.py
    test_mcp.py

  providers/
    test_tdx_contract.py
    test_local_vipdoc_contract.py
    test_tencent_contract.py
    ...
```

Critical negative tests:

- wrong provider/channel provenance;
- copied L2 row under another fingerprint;
- live query served by replay/synthetic;
- unsupported adjustment silently ignored;
- ambiguous provider selector;
- fallback result persisted as direct;
- retryable error negative-cached;
- dead worker reported RUNNING;
- process-control BaseException swallowed;
- provider capability registered without executable binding;
- direct binding exists without registry declaration;
- API surface returns data without metadata/provenance.

---

## 21. Performance targets

Performance work happens only after correctness contracts are stable.

Targets:

- no extra Provider round-trip introduced by abstraction;
- SingleFlight collapses identical concurrent requests to one leader I/O;
- cache hit path performs zero provider I/O;
- Direct Provider execution adds minimal allocation overhead;
- L1 lookup O(1);
- no full-cache clear during normal LRU eviction;
- HTTP/WS serialization avoids redundant model -> dict -> model conversions;
- batch execution supports bounded concurrency.

Benchmarks must compare the same provider/data path, not legacy fallback vs strict single-provider paths.

---

## 22. Observability

Add structured runtime events:

- query fingerprint
- provider
- channel
- capability
- cache tier
- cache hit/miss/reject reason
- singleflight leader/follower
- provider latency
- result count
- provenance kind
- fallback attempts
- canonical error code

Never log secret/private raw exception context by default.

---

## 23. Documentation rewrite

After implementation:

- README teaches only the v13 Client API;
- architecture docs describe only Provider-first runtime;
- examples use explicit Provider or explicit FallbackPolicy;
- delete examples containing `route="auto"`;
- provider capability matrix is generated/verified from Registry;
- migration document is one-way: “old API removed -> new API replacement”.

There is no promise of source-level backward compatibility.

---

## 24. Definition of done

The refactor is complete only when all conditions hold simultaneously:

1. There is one canonical public `Client` API.
2. `UnifiedQuoteAPI` is not part of the supported public API.
3. Public `route=` semantics are gone.
4. `DataSourceRouter` is not used by canonical runtime execution.
5. QuerySpec/QueryPlan are the SSOT for request semantics.
6. Registry capability declaration and Direct binding coverage are exactly aligned.
7. All canonical results carry provenance.
8. Cross-provider fallback exists only in ProviderOrchestrator.
9. L1/L2/SingleFlight/negative cache all key by full QueryFingerprint.
10. All public protocol surfaces share the same runtime and ErrorEnvelope.
11. Stateful streaming is the only supported streaming lifecycle.
12. No compatibility wrapper silently changes query semantics.
13. No orphan runtime modules or duplicate execution chains remain.
14. Same-SHA CI truly executes every required gate and all deterministic gates are green.
15. Real-provider smoke tests show no silent provider/channel misbinding.
16. Documentation contains no stale legacy routing instructions.

---

## 25. Immediate execution order

Once this design is accepted, implementation should proceed in this exact order:

1. **Create v13 ADR + API deletion manifest**.
2. **Introduce `Client` / `AsyncClient` as the sole public high-level API**.
3. **Move Direct Provider bindings behind provider adapter modules/protocol**.
4. **Remove `route=` and UnifiedQuoteAPI from top-level supported exports**.
5. **Remove canonical dependency on DataSourceRouter**.
6. **Rewrite HTTP/WS/MCP/CLI/tasks directly over Client/UnifiedRuntime**.
7. **Delete duplicate legacy endpoints and compatibility runtime code**.
8. **Expand specialized capabilities family-by-family with registry+binding+tests as one atomic unit**.
9. **Unify streaming under StreamSpec/StreamPlan**.
10. **Harden L1/L2 eviction, integrity and observability**.
11. **Run orphan/dead-code/docs cleanup**.
12. **Run full same-SHA CI matrix and real Provider smoke/calibration**.
13. **Only then merge PR #6 / successor integration branch and close historical PR #1**.

This order deliberately removes compatibility architecture early so new work cannot accidentally depend on it.
