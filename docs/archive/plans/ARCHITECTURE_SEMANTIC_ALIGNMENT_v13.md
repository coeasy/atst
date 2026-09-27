# atst v13 Semantic & Core Alignment Specification

> Status: **authoritative architecture contract**
>
> Scope: semantic model, core runtime, core capabilities, end-to-end execution chain, protocol surfaces, migration and release gates
>
> Policy: **clean break**. No backward-compatibility requirement for legacy public APIs.
>
> Supersedes all earlier compatibility-cutover interpretations. `docs/REFACTOR_PLAN_v13_CLEAN_BREAK.md` remains the implementation roadmap; this document is the semantic/core SSOT used to decide whether a feature is actually aligned.

---

## 1. Definition of “fully aligned”

A feature is **not** considered aligned because a public method exists or because a Provider has an implementation.

A capability is aligned only when the following complete chain is present and semantically identical:

```text
Public intent
  -> typed request contract
  -> normalization
  -> QuerySpec / StreamSpec
  -> QueryPlanner / StreamPlanner
  -> QueryPlan / StreamPlan
  -> ProviderRegistry declaration
  -> exact Provider/Channel/Capability binding
  -> UnifiedRuntime / Stateful Stream Runtime
  -> cache / SingleFlight / negative-cache policy
  -> Provider-native adapter
  -> normalized domain model
  -> QueryResult / ResultMeta / Provenance
  -> ErrorEnvelope on failures
  -> Python / CLI / HTTP / WS / MCP adapters
  -> contract tests
  -> CI gate
```

If any link is missing, duplicated, bypassed or has different semantics, that capability remains **not aligned**.

The repository is fully aligned only when there is exactly one production interpretation for each semantic concept.

---

## 2. Semantic SSOT

### 2.1 Canonical concepts

The following names are authoritative:

- `Provider`: external or local data authority, e.g. `tdx`, `local_vipdoc`, `tencent`, `sina`, `eastmoney`, `baidu`.
- `Channel`: a Provider-native transport or endpoint family used to implement a capability.
- `Capability`: business operation such as `quotes`, `bars`, `trades`, `finance`, `snapshot`.
- `QuerySpec`: typed immutable query intent.
- `QueryPlan`: compiled executable single-Provider plan.
- `QueryFingerprint`: complete semantic identity of a query.
- `UnifiedRuntime`: single-Provider execution kernel.
- `FallbackPolicy`: explicit ordered cross-Provider policy.
- `ProviderOrchestrator`: only cross-Provider fallback executor.
- `QueryResult`: canonical successful result envelope.
- `ResultMeta`: canonical execution metadata.
- `Provenance`: canonical source lineage.
- `ErrorEnvelope`: canonical safe failure envelope.
- `StreamSpec` / `StreamPlan`: canonical streaming request/execution contracts.

### 2.2 Concepts removed from the target architecture

The following are legacy semantics and must not remain in the final public/core model:

- `route=`
- `route="auto"`
- `route="web"`
- `route="local"`
- `source` as a synonym for Provider in `QuerySpec`
- `web` as a Provider identity
- implicit `tdx -> web -> local/cache/synthetic` chains
- `DataSourceRouter` as business execution kernel
- facade-owned fallback
- facade-owned circuit breaker semantics
- cache keys that are weaker than `QueryFingerprint`
- generic non-batch `allow_partial=True`
- protocol-specific business logic
- protocol-specific native error semantics
- replay/synthetic data represented as DIRECT/live data

Any remaining occurrence is either a temporary migration artifact or a low-level implementation detail that must not leak into the canonical API.

---

## 3. Provider semantics

### 3.1 Provider identity rules

1. One QueryPlan executes exactly one Provider.
2. One QueryPlan executes exactly one canonical Channel.
3. `local_vipdoc` is a standalone Provider.
4. TDX host failover stays inside the `tdx` Provider.
5. Cross-Provider switching is never performed by `UnifiedRuntime`.
6. `web` is not a Provider; concrete Providers are named explicitly.
7. Aliases may be accepted only during normalization and must resolve to one canonical Provider before planning.
8. Ambiguous aliases fail before I/O.

### 3.2 Registry = executable truth

For every production capability:

```text
ProviderRegistry declaration
       ==
Direct executable binding
       ==
contract test
```

A registry declaration without an executable binding is a CI failure.
A binding not represented in the registry is a CI failure.
A production capability without contract tests is not complete.

### 3.3 Provider adapter boundary

Provider-native code owns only Provider-native concerns:

- connection/session lifecycle;
- transport/protocol encoding;
- host selection inside that Provider;
- Provider response parsing;
- Provider-specific validation that cannot be resolved earlier;
- mapping to canonical domain models.

Provider adapters must not own:

- cross-Provider fallback;
- shared semantic cache policy;
- public retry/fallback policy;
- external protocol error serialization;
- public result envelope semantics.

---

## 4. Query semantics

### 4.1 QuerySpec is the request SSOT

`QuerySpec` must become the only canonical query request model.

Target semantic fields:

- `capability`
- `provider`
- `symbols`
- optional channel selector when the Provider exposes multiple valid channels
- capability-specific typed parameters
- `period` when applicable
- range/start/count when applicable
- adjustment when applicable
- `currentness`
- `max_age`
- deterministic semantic options

The target model removes:

- `source`
- non-batch `allow_partial`
- weak catch-all options for semantics that can be typed

### 4.2 Normalization

Normalization occurs before fingerprinting and planning.

It must canonicalize:

- Provider ID
- symbols
- markets/exchanges
- periods/frequencies
- adjustment modes
- currentness
- capability-specific enums

Normalization must not silently downgrade unsupported semantics.

Examples:

- unsupported adjustment -> fail closed;
- unsupported period -> fail closed;
- ambiguous Provider alias -> fail closed;
- unsupported Provider/capability pair -> fail before network/file I/O.

### 4.3 QueryFingerprint

Fingerprint must contain every field capable of changing returned semantics.

Minimum identity:

- fingerprint schema version
- provider
- channel
- capability
- normalized symbols
- normalized period/frequency
- range/start/count
- adjustment
- currentness
- max_age/freshness semantics
- capability-specific semantic parameters

Rules:

1. Different Providers always produce different fingerprints.
2. Different Channels produce different fingerprints when Channel changes result semantics.
3. Different adjustment modes always differ.
4. Different currentness/max-age constraints differ.
5. Adding a new semantic parameter requires a fingerprint regression test.

---

## 5. Planner semantics

`QueryPlanner` is the only compiler from intent to executable query plan.

Planner responsibilities:

1. validate normalized QuerySpec;
2. resolve canonical Provider;
3. resolve exactly one Channel;
4. verify Provider supports the capability;
5. verify parameters are supported;
6. compile deterministic QueryFingerprint;
7. emit one immutable QueryPlan.

Planner must not:

- perform I/O;
- execute fallback;
- select a second Provider after failure;
- hide unsupported parameters;
- silently change requested currentness or adjustment semantics.

A planning failure occurs before Provider I/O.

---

## 6. UnifiedRuntime core flow

The canonical synchronous execution path is:

```text
Client.execute(QuerySpec)
  -> normalize
  -> QueryPlanner.compile
  -> QueryPlan
  -> L1 semantic cache lookup
  -> L2 semantic cache lookup + integrity/freshness verification
  -> optional L2 -> L1 promotion with remaining TTL only
  -> terminal negative-cache lookup
  -> SingleFlight(full fingerprint)
  -> leader re-check L1/L2/negative cache
  -> DirectProviderExecutor.execute(plan)
  -> canonical domain data
  -> QueryResult.from_plan(..., Provenance.direct(plan))
  -> write L1
  -> best-effort write L2 when policy permits
  -> clear stale negative entry
  -> deep-isolated result to caller
```

### 6.1 Hard runtime invariants

- Runtime executes one Provider only.
- Runtime never performs implicit cross-Provider fallback.
- Runtime never converts fallback/replay/synthetic provenance into DIRECT.
- Cache failure cannot fail a valid Provider query.
- SingleFlight identity is the complete QueryFingerprint.
- Leader and followers receive isolated result objects.
- `BaseException` is never swallowed/normalized by ordinary runtime boundaries.

---

## 7. Cross-Provider orchestration

Cross-Provider behavior exists only above UnifiedRuntime.

Canonical flow:

```text
Client.execute_with_policy(spec, FallbackPolicy[A,B,C])
  -> validate policy
  -> runtime.execute(spec with provider=A)
  -> if policy permits and A fails, record attempt
  -> runtime.execute(spec with provider=B)
  -> ...
  -> OrchestratedResult
```

Requirements:

- Provider order is explicit.
- Unknown Providers rejected before execution.
- Duplicate Providers rejected.
- Each attempt records canonical Provider and ErrorEnvelope code.
- Actual successful Provider remains the result Provider.
- `requested_provider` records the first requested Provider/policy origin.
- fallback success has `fallback=True` provenance.
- fallback result is never persisted in a DIRECT-only semantic L2.
- retry policy and fallback policy are separate concepts.

Future resilience such as circuit breaker, hedging, quorum and health-aware choice belongs here and must remain opt-in.

---

## 8. Cache semantics

### 8.1 L1 semantic cache

Target behavior:

- full QueryFingerprint key;
- identity verification on read;
- provenance-aware eligibility;
- currentness/freshness enforcement;
- bounded deterministic eviction (LRU/segmented LRU), never whole-map flush;
- hit/miss/reject/evict metrics.

### 8.2 L2 persistent semantic cache

Target behavior:

- SQLite + safe JSON-compatible codec initially;
- no pickle/executable deserialization;
- fingerprint embedded inside payload and verified against row key;
- Provider/Channel/Capability verified redundantly;
- payload checksum/hash;
- schema/codec versioning;
- TTL and freshness checked on every read;
- L2 -> L1 promotion never extends original expiry;
- DIRECT-only persistence by default;
- corruption/tampering -> invalidate/miss, never execute untrusted payload;
- read/write failure -> best effort, never breaks valid Provider execution.

### 8.3 Negative cache

- separate store from successful empty results;
- keyed by full QueryFingerprint;
- only non-retryable terminal domain errors are eligible;
- `SourceUnavailable`, timeouts and transport failures are excluded;
- short TTL;
- bounded deterministic eviction;
- success invalidates prior negative entry.

---

## 9. Result and provenance semantics

Every successful production query returns a canonical `QueryResult`.

`ResultMeta` must expose enough data to audit execution without leaking unsafe internals.

`Provenance` must distinguish at least:

- DIRECT
- FALLBACK/ORCHESTRATED
- REPLAY
- SYNTHETIC
- CACHE while preserving original source lineage

Rules:

1. Cache does not erase original Provider identity.
2. Cache does not change original provenance into DIRECT.
3. Fallback records requested and actual Provider.
4. Replay/synthetic can never satisfy a live DIRECT requirement unless the caller explicitly asked for that mode.
5. Protocol adapters serialize canonical result metadata; they do not invent their own source fields.

---

## 10. Error semantics

`TdxError` hierarchy is the internal domain failure model.
`ErrorEnvelope` is the external safe error model.

All external surfaces use the same conversion.

For domain errors:

- preserve canonical code;
- preserve safe message;
- preserve retryability;
- preserve only allow-listed context.

For unexpected ordinary exceptions:

- code `E9000`;
- generic internal-error classification;
- no native stack/message leakage.

Process control:

- `KeyboardInterrupt` remains process control;
- `SystemExit` remains process control;
- ordinary `except Exception` boundaries must not catch `BaseException`.

HTTP/WS/MCP/CLI only translate the same ErrorEnvelope into transport-specific framing.

---

## 11. Batch semantics

Partial success belongs only to explicit batch APIs.

Target models:

- `BatchSpec`
- `BatchItem`
- `BatchResult`

Canonical item states:

- `ok`
- `missing`
- `failed`
- `not_attempted`

Rules:

- remove generic `QuerySpec.allow_partial` from the final contract;
- each batch item has its own Provider execution identity/error;
- batch orchestration never hides individual failures;
- input ordering is stable unless an API explicitly documents another ordering;
- batch result is immutable/auditable.

---

## 12. Streaming semantics

Streaming is not a special legacy client path. It is a first-class runtime subsystem.

Canonical request/execution:

```text
Client.stream(StreamSpec)
  -> StreamPlanner.compile
  -> StreamPlan
  -> exact Stream Provider binding
  -> Stateful stream runtime
  -> normalized events
  -> provenance + lifecycle state
```

Canonical lifecycle:

- `CREATED`
- `RUNNING`
- `STOPPING`
- `CLOSED`
- `FAILED`

Rules:

- FAILED is terminal for that stream object;
- unexpected worker/task termination -> FAILED;
- explicit close -> CLOSED;
- backpressure is explicit;
- subscription identity is explicit;
- reconnect policy is explicit and Provider-local or orchestration-level as designed;
- old implicit `_thread/_task/_stop` lifecycle is removed;
- old `QuoteStream` / `AsyncQuoteStream` paths are retired after Stateful streaming covers the production use cases.

---

## 13. Public API

The final supported Python API is centered on:

```python
from atst import Client, AsyncClient, QuerySpec, FallbackPolicy
```

Canonical surface:

```python
client.execute(spec) -> QueryResult
client.quotes(..., provider="tdx") -> QueryResult
client.bars(..., provider="eastmoney", period="day") -> QueryResult
client.quotes_batch(...) -> BatchResult
client.execute_with_policy(..., policy=FallbackPolicy.build(...)) -> OrchestratedResult
client.stream(...) -> Stateful stream
```

There is no public `route=`.
There is no public `route="auto"`.
There is no public ambiguous `web` Provider.
There is no production requirement to keep `UnifiedQuoteAPI`.

`AsyncClient` must expose equivalent semantics, not a second architecture.

---

## 14. Protocol surfaces

All transport surfaces are adapters over the same Client/Runtime.

### 14.1 HTTP

- one versioned canonical API;
- typed request models map to QuerySpec/StreamSpec;
- result serializer maps QueryResult/BatchResult/OrchestratedResult;
- ErrorEnvelope maps to HTTP status/body;
- no HTTP-specific Provider routing.

### 14.2 WebSocket / JSON-RPC

- same method semantics as Client;
- notifications return no response;
- same ErrorEnvelope in error data;
- no independent fallback/cache logic.

### 14.3 MCP

- tools call Client/Runtime only;
- no split between direct TdxClient and legacy Facade;
- tool errors use ErrorEnvelope;
- task payloads are bounded.

### 14.4 CLI

- arguments map directly to typed request contracts;
- Provider is explicit;
- fallback policy is explicit;
- errors print canonical envelope;
- KeyboardInterrupt returns process-control exit status, not E9000.

### 14.5 Background tasks

- execute canonical Client operations;
- store bounded serialized canonical results;
- store safe ErrorEnvelope only;
- expiry/cancel clears retained payloads.

---

## 15. Core capability alignment matrix

Every capability below must pass the full-chain definition in Section 1.

### Tier A — core market data

- quotes
- bars
- snapshot / orderbook
- minute / intraday
- trades / ticks
- security_list
- security_count

### Tier B — corporate/fundamental data

- finance / fundamental
- corporate_action
- capital changes
- F10 catalog/content
- adjustment/factor data

### Tier C — market structure and rankings

- blocks / sectors / concepts / regions
- index catalog
- market statistics
- rankings
- auction
- volume-price distribution

### Tier D — asset-class capabilities

- funds
- bonds
- futures
- options
- extended market / commodities
- FX/rates where supported

### Tier E — supplemental information

- search/suggest
- news/events
- provider-specific supplemental endpoints

A capability is not “implemented” merely because an old Facade method exists.
It becomes production-complete only after typed request + Registry + exact binding + canonical result + tests + surface exposure are all present.

---

## 16. End-to-end chain ownership

### 16.1 Input side

Public adapters own syntax only.

- Python -> typed arguments
- CLI -> parsed flags
- HTTP -> request models
- WS -> JSON-RPC params
- MCP -> tool params

All must converge to the same typed semantic request.

### 16.2 Core side

Only these layers own business execution semantics:

- Query/Stream contracts
- Planner
- Provider Registry
- UnifiedRuntime / Stream Runtime
- ProviderOrchestrator
- semantic caches
- Direct Provider adapters
- canonical domain models/results/errors

### 16.3 Output side

Protocol adapters serialize canonical objects only.

They must not:

- infer Provider after execution;
- rewrite provenance;
- convert missing into success silently;
- invent retryability;
- expose native exception text;
- implement independent caches/fallbacks.

---

## 17. Legacy deletion map

The final architecture requires removing or decommissioning production use of:

- `UnifiedQuoteAPI` as public canonical client;
- `quote_api()` legacy facade factory;
- `RouteSelector` and `_try_routes` business routing;
- all public `route=` parameters;
- `route="auto"` semantics;
- `web` as a pseudo-provider selector;
- `DataSourceRouter` as multi-source runtime kernel;
- router-owned hidden cache/fallback/synthetic behavior;
- legacy cache identities weaker than QueryFingerprint;
- legacy HTTP/WS business implementations after canonical surfaces replace them;
- legacy streaming lifecycle implementations after Stateful stream parity;
- compatibility exports whose only purpose is preserving removed API shapes;
- stale docs teaching removed semantics.

Low-level reusable protocol/readers/parsers may remain if they have no duplicate orchestration semantics.

---

## 18. Implementation order

The migration must proceed in dependency order to avoid another dual architecture.

### Phase 0 — contract lock

- freeze this semantic document;
- finalize Provider IDs and Channel IDs;
- finalize capability names;
- finalize QuerySpec v13 fields;
- finalize QueryFingerprint v13 schema;
- finalize result/provenance/error contracts;
- create CI architecture guards.

### Phase 1 — canonical Client

- implement `Client` and `AsyncClient` as thin runtime adapters;
- top-level exports move to Client-first;
- no routing logic inside Client;
- add public API contract tests.

### Phase 2 — remove conflicting request semantics

- remove `source` from canonical QuerySpec;
- remove non-batch `allow_partial`;
- remove public route semantics;
- reject `web` pseudo-provider;
- remove legacy routing ownership.

### Phase 3 — capability migration

For each capability family:

1. define typed request semantics;
2. register Provider/Channel capability;
3. implement exact binding;
4. normalize result model;
5. add QueryFingerprint tests;
6. add Provider contract tests;
7. expose through Client;
8. expose through all required surfaces.

Do not migrate capabilities by copying Facade routing code.

### Phase 4 — protocol rewrite

- canonical HTTP;
- canonical WS;
- canonical MCP;
- canonical CLI;
- canonical task execution;
- remove parallel legacy protocol business logic.

### Phase 5 — streaming convergence

- StreamSpec/StreamPlan;
- stream registry/bindings;
- Stateful sync/async runtime;
- backpressure/reconnect semantics;
- remove legacy stream implementations.

### Phase 6 — cache/performance hardening

- L1 LRU;
- L2 integrity hash/schema migration;
- monotonic TTL semantics;
- bounded concurrency;
- metrics;
- benchmarks;
- memory/copy profiling.

### Phase 7 — repository cleanout

- delete legacy facade/routing files no longer referenced;
- delete orphan tests/docs;
- eliminate dead exports;
- dependency graph audit;
- ensure one production execution chain.

### Phase 8 — release gate

- exact same SHA;
- all deterministic gates execute real steps;
- no `runner_id=0` accepted as evidence;
- static/type/unit/integration/contract tests green;
- provider smoke tests where network is required;
- public API docs match implementation;
- no legacy semantic terms in canonical docs/code except migration history.

---

## 19. Architecture guard tests

CI must include guards that fail when semantic drift reappears.

Required checks:

1. Registry/binding parity.
2. Every production binding has contract tests.
3. QueryFingerprint changes when any semantic parameter changes.
4. Runtime never calls ProviderOrchestrator.
5. Provider adapters never import orchestration layer.
6. Public Client does not import legacy routing.
7. Canonical HTTP/WS/MCP/CLI do not import legacy Facade/Router.
8. No public `route=` parameter in canonical APIs.
9. No `web` pseudo-provider in ProviderRegistry.
10. No non-batch `allow_partial` in final QuerySpec.
11. Cache rejects provenance/identity mismatch.
12. L2 tamper/copy poisoning fails closed.
13. fallback result is not persisted as DIRECT.
14. unexpected native errors produce E9000 without leak.
15. `BaseException` escapes ordinary error normalization.
16. stream invalid state transitions fail closed.
17. no duplicate canonical endpoint/method implementations.
18. import/dependency graph has no legacy execution path reachable from Client.

---

## 20. Completion matrix

The project may claim **semantic alignment complete** only when all are true:

- [ ] Provider identity has one meaning everywhere.
- [ ] Channel identity has one meaning everywhere.
- [ ] Capability names have one registry and one contract.
- [ ] QuerySpec is the only query semantic request SSOT.
- [ ] StreamSpec is the only streaming request SSOT.
- [ ] QueryFingerprint includes every semantic parameter.
- [ ] QueryPlanner is the only query compiler.
- [ ] UnifiedRuntime is the only single-Provider query kernel.
- [ ] ProviderOrchestrator is the only cross-Provider fallback implementation.
- [ ] QueryResult/ResultMeta/Provenance are the only success contracts.
- [ ] ErrorEnvelope is the only external failure contract.
- [ ] Batch partial semantics exist only in explicit batch APIs.
- [ ] L1/L2/SingleFlight/NegativeCache use full fingerprint identity.
- [ ] Stateful streaming is the only production stream lifecycle.
- [ ] Python/Async/CLI/HTTP/WS/MCP/task surfaces converge on the same core.
- [ ] `source` alias is gone from canonical QuerySpec.
- [ ] `route=` is gone from canonical public APIs.
- [ ] `route="auto"` is gone.
- [ ] `web` pseudo-provider is gone.
- [ ] DataSourceRouter is unreachable from canonical production execution.
- [ ] UnifiedQuoteAPI is removed from the supported public surface.
- [ ] legacy caches are unreachable from canonical execution.
- [ ] replay/synthetic provenance cannot impersonate live DIRECT data.
- [ ] every production capability has Registry + Binding + Contract tests.
- [ ] every canonical protocol surface uses the same serializer/error semantics.
- [ ] architecture guard tests prevent reintroduction of old semantics.

The project may claim **core flow alignment complete** only when:

- [ ] every supported request enters through canonical typed contracts;
- [ ] every request compiles to an auditable plan;
- [ ] every plan executes one Provider/Channel;
- [ ] every result has canonical metadata/provenance;
- [ ] every failure has canonical error semantics;
- [ ] every cache path preserves semantic identity;
- [ ] every fallback is explicit and auditable;
- [ ] every transport is only an adapter;
- [ ] no second business execution chain remains.

The project may claim **core capability alignment complete** only when all Tier A capabilities are fully migrated and all retained Tier B-E capabilities are either fully migrated or explicitly removed from the product scope. There is no “legacy implementation counts as aligned” exception.

---

## 21. Current implementation status vs target

The current PR #6 runtime foundation already contains important aligned components:

- QuerySpec/QueryPlanner/QueryPlan;
- Provider Registry;
- QueryFingerprint;
- UnifiedRuntime;
- Direct Provider executor for the first unified capabilities;
- semantic L1 and safe persistent L2;
- SingleFlight;
- terminal negative cache;
- BatchResult/quotes batch path;
- QueryResult/Provenance;
- ErrorEnvelope;
- explicit FallbackPolicy/ProviderOrchestrator;
- Stateful streaming foundations;
- strict HTTP/WS/task surfaces for the new runtime path.

However, repository-level alignment is **not complete** until the conflicting legacy semantics and duplicate execution paths listed above are removed and the remaining capability families are migrated.

Therefore the implementation strategy from this point is **convergence and deletion**, not adding more compatibility wrappers.

---

## 22. Final architecture statement

The final atst architecture has one semantic center:

```text
Typed Intent
   -> Plan
   -> One Provider Runtime
   -> Canonical Result + Provenance
```

and one explicit resilience extension:

```text
FallbackPolicy
   -> ProviderOrchestrator
   -> repeated One Provider Runtime attempts
```

Everything else—Python API, async API, CLI, HTTP, WS, MCP, tasks, caches and streaming—is either an adapter or a subsystem of that same architecture.

No hidden routing, no ambiguous source identity, no duplicate cache semantics, no protocol-owned business logic, and no compatibility path is allowed to become a second core.
