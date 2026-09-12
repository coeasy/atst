# tstdx v13 Implementation Alignment Matrix

> Companion document to `ARCHITECTURE_SEMANTIC_ALIGNMENT_v13.md` and `REFACTOR_PLAN_v13_CLEAN_BREAK.md`.
>
> Purpose: convert the v13 architecture into concrete implementation/deletion/test gates.

---

## 1. Status vocabulary

- `ALIGNED`: target semantics and implementation chain are complete.
- `PARTIAL`: v13 foundation exists but a legacy path or missing downstream link remains.
- `LEGACY`: implementation still belongs to the old architecture.
- `MISSING`: target implementation does not yet exist.
- `REMOVE`: component must disappear from canonical production use.

A row becomes `ALIGNED` only when code, tests, docs and protocol exposure agree.

---

## 2. Semantic/core matrix

| Area | Current | Target | Required action | Completion gate |
|---|---|---|---|---|
| Provider identity | PARTIAL | one canonical Provider ID | remove pseudo-provider `web`, remove ambiguous source aliases | same Provider identity in QuerySpec/Registry/Result/surfaces |
| Channel identity | PARTIAL | canonical per Provider | freeze channel IDs and bind exactly | registry-binding parity gate |
| Capability identity | PARTIAL | single capability namespace | inventory all legacy methods and map/remove | every retained capability in registry |
| QuerySpec | PARTIAL | sole query SSOT | remove `source`, remove non-batch `allow_partial`, type options | no alternate request contract |
| QueryFingerprint | PARTIAL/HIGH | complete semantic identity | version schema, add typed capability fields | mutation tests for every semantic field |
| QueryPlanner | ALIGNED for current scope | sole compiler | extend capability validation | no direct execution bypass |
| UnifiedRuntime | ALIGNED for current scope | sole single-provider kernel | expand capability bindings | runtime never orchestrates providers |
| ProviderOrchestrator | PARTIAL/HIGH | only cross-provider fallback | add policy/retry separation and full surface use | no other fallback implementation |
| Result/Provenance | PARTIAL/HIGH | sole success contract | extend serialization across all surfaces | all successful calls return canonical result metadata |
| ErrorEnvelope | PARTIAL/HIGH | sole external error contract | replace remaining native/protocol-specific errors | all surfaces share code/retry/context semantics |
| Batch | PARTIAL | explicit BatchSpec/BatchResult | remove partial flag from normal QuerySpec | only batch APIs support partial success |
| Streaming | PARTIAL | StreamSpec/StreamPlan + Stateful runtime | migrate sync/async legacy streams | one lifecycle model only |
| L1 cache | PARTIAL | fingerprint + bounded LRU | replace whole-map capacity behavior | deterministic eviction + metrics |
| L2 cache | PARTIAL/HIGH | safe persistent semantic cache | checksum/schema migration/metrics | poison/tamper/freshness gates |
| Negative cache | PARTIAL/HIGH | terminal-only separate cache | deterministic eviction + metrics | retryable failures never cached |
| SingleFlight | ALIGNED for current scope | full-fingerprint coalescing | generalize to all query capabilities | leader/follower isolation tests |
| Python public API | LEGACY/PARTIAL | `Client` / `AsyncClient` | create Client-first surface, remove facade exports | no public route semantics |
| HTTP | PARTIAL | one canonical runtime adapter | replace legacy business endpoints | no facade/router imports |
| WS | PARTIAL | one canonical runtime adapter | replace legacy dispatch | same request/result/error semantics as Client |
| MCP | PARTIAL | Client/Runtime only | remove direct legacy split | no direct facade/TdxClient business routing |
| CLI | PARTIAL | typed canonical request adapter | rework commands around Provider/QuerySpec | same semantics/errors as Client |
| Tasks | PARTIAL/HIGH | bounded canonical execution store | use Client universally | canonical result/error only |
| DataSourceRouter | LEGACY | REMOVE from canonical runtime | migrate useful readers/adapters, delete orchestration role | unreachable from Client/runtime surfaces |
| UnifiedQuoteAPI | LEGACY | REMOVE | migrate retained capabilities then delete | absent from supported exports |
| RouteSelector | LEGACY | REMOVE | delete after Client migration | no route state/fallback semantics |

---

## 3. Core flow alignment

### 3.1 Canonical request path

Target:

```text
Public adapter
 -> typed request
 -> QuerySpec
 -> normalize
 -> QueryPlanner.compile
 -> QueryPlan
 -> UnifiedRuntime.execute
```

Required changes:

- every canonical Python method builds QuerySpec;
- CLI/HTTP/WS/MCP build the same semantics, not custom routes;
- no surface directly selects legacy router order;
- no surface silently rewrites Provider/frequency/adjustment.

Gate:

- architecture test traces imports/calls from every public surface into QueryPlanner/UnifiedRuntime;
- fail CI on legacy runtime import from canonical surfaces.

### 3.2 Canonical execution path

Target:

```text
L1
 -> L2
 -> negative cache
 -> SingleFlight
 -> exact Direct binding
 -> normalized domain data
 -> QueryResult/Provenance
 -> cache write
```

Required changes:

- all new capabilities use this path;
- remove capability-local caches that change semantics;
- Provider adapters return normalized data only;
- no Provider adapter performs cross-provider fallback.

Gate:

- direct-binding audit;
- cache identity tests;
- Provider isolation tests;
- provenance tests.

### 3.3 Canonical fallback path

Target:

```text
FallbackPolicy
 -> ProviderOrchestrator
 -> runtime(provider A)
 -> runtime(provider B)
 -> ...
```

Required changes:

- delete `route="auto"`;
- remove router/facade fallback;
- separate retry from fallback;
- expose explicit policy through Client and protocol surfaces.

Gate:

- search/static guard proves no second cross-provider fallback chain exists.

### 3.4 Canonical output path

Target:

```text
QueryResult / BatchResult / OrchestratedResult
 -> transport serializer
```

Required changes:

- normalize all surface serializers;
- preserve Provider/Channel/Provenance;
- stop returning source fields invented by individual transports.

Gate:

- golden contract snapshots for Python/HTTP/WS/MCP/CLI semantics.

---

## 4. Core capability migration matrix

### Tier A — must all be aligned before calling the core complete

| Capability | Current source | Target | Status | Required work |
|---|---|---|---|---|
| quotes | runtime + legacy facade | QuerySpec/Registry/Binding/Runtime | PARTIAL/HIGH | remove facade route path, expose via Client |
| bars | runtime + legacy facade/router | QuerySpec/Registry/Binding/Runtime | PARTIAL/HIGH | remove router path, complete period/adjustment contracts |
| snapshot/orderbook | legacy TDX facade | typed capability | LEGACY | registry + exact bindings + models + tests |
| minute/intraday | legacy facade/provider-specific | typed capability | LEGACY | capability contract + bindings |
| trades/ticks | legacy facade | typed capability | LEGACY | capability contract + bindings |
| security_list | legacy facade/client | typed capability | LEGACY | registry/binding/result tests |
| security_count | legacy facade/client | typed capability | LEGACY | registry/binding/result tests |

Tier A completion is mandatory for “core capability aligned”.

### Tier B — corporate/fundamental

| Capability | Target action |
|---|---|
| finance/fundamental | define typed parameters and normalized model |
| corporate_action | unify source lineage and action schema |
| capital changes | map to canonical corporate-action/factor semantics |
| F10 catalog/content | explicit capability, no facade-only API |
| adjustment/factors | separate raw bars from adjustment engine; fingerprint all semantics |

### Tier C — market structure/rankings

- blocks/sectors/concepts/regions;
- index catalog;
- market stats;
- rankings;
- auction;
- volume-price distribution.

Each retained capability must have Provider support matrix and exact binding coverage.

### Tier D — asset classes

- funds;
- bonds;
- futures;
- options;
- extended market/commodities;
- rates/FX where retained.

Do not force incompatible asset semantics into equity bars without typed capability parameters.

### Tier E — supplemental

- search/suggest;
- news/events;
- provider-specific supplemental datasets.

Retain only capabilities with clear product value and canonical contracts; otherwise remove.

---

## 5. Public API migration

### Create

- `tstdx/client_api.py`
  - `Client`
  - `AsyncClient`

### Canonical methods

- `execute`
- `quotes`
- `bars`
- `quotes_batch`
- `execute_with_policy`
- `stream`

Capability-specific convenience methods may exist only as thin QuerySpec builders.

### Remove from supported surface

- `UnifiedQuoteAPI`
- `quote_api`
- public route selectors
- route-aware convenience methods
- `web` pseudo-provider

### Gate

Top-level `tstdx` exports must present the new Client-first API only after migration completes.

---

## 6. Legacy removal checklist

- [ ] remove `source` alias from canonical QuerySpec
- [ ] remove generic `allow_partial`
- [ ] remove `route=` parameters
- [ ] remove `route="auto"`
- [ ] remove `RouteSelector`
- [ ] remove `_try_routes`
- [ ] remove facade circuit state used for source switching
- [ ] remove `web` pseudo-provider semantics
- [ ] remove DataSourceRouter from canonical execution
- [ ] migrate/delete router hidden caches
- [ ] migrate/delete router synthetic fallback
- [ ] remove UnifiedQuoteAPI from supported exports
- [ ] remove quote_api factory
- [ ] remove legacy HTTP business handlers
- [ ] remove legacy WS business dispatch
- [ ] remove MCP direct legacy routing
- [ ] remove legacy stream lifecycle
- [ ] remove stale docs/examples/tests teaching removed APIs

Deletion occurs only after retained business capabilities have canonical replacements.

---

## 7. Surface parity matrix

For every retained capability, verify semantic parity across relevant surfaces:

| Contract | Python | Async | CLI | HTTP | WS | MCP |
|---|---:|---:|---:|---:|---:|---:|
| Provider explicit | required | required | required | required | required | required |
| typed capability params | yes | yes | yes | yes | yes | yes |
| QueryFingerprint semantics | same | same | same | same | same | same |
| QueryResult provenance | same | same | serialized | serialized | serialized | serialized |
| ErrorEnvelope | exception/context | exception/context | JSON/text framing | HTTP framing | JSON-RPC framing | JSON-RPC/tool framing |
| fallback policy | explicit | explicit | explicit | explicit | explicit | explicit |
| no legacy route semantics | yes | yes | yes | yes | yes | yes |

Transport framing may differ; business semantics may not.

---

## 8. Test and CI matrix

### Static architecture gates

- no canonical surface imports legacy facade/router;
- no Provider adapter imports orchestrator;
- no runtime imports legacy route selector;
- ProviderRegistry/binding parity;
- no public route parameters in Client/surfaces;
- no pseudo-provider `web` in registry.

### Unit gates

- normalization;
- planner validation;
- fingerprint identity;
- cache eligibility;
- provenance construction;
- ErrorEnvelope sanitation;
- batch states;
- stream transitions.

### Contract gates

Per Provider/capability:

- supported parameter matrix;
- unsupported parameter fail-closed behavior;
- output model shape;
- error classification;
- no cross-provider switch.

### Integration gates

- Client -> Runtime -> mocked Provider;
- HTTP -> Client -> Runtime;
- WS -> Client -> Runtime;
- MCP -> Client -> Runtime;
- CLI -> Client -> Runtime;
- persistent cache restart/tamper tests;
- orchestration audit trail.

### Real Provider smoke gates

Run separately where network/provider availability is required. They supplement but do not replace deterministic tests.

### CI evidence rule

A gate counts only if the exact head SHA received a runner and executed real steps. `steps=[]`, `steps=null`, `runner_id=0`, skipped jobs or outer workflow success without executed blocking jobs are not green evidence.

---

## 9. Documentation alignment

Canonical documentation set after migration:

1. `ARCHITECTURE_SEMANTIC_ALIGNMENT_v13.md` — semantic/core SSOT.
2. `REFACTOR_PLAN_v13_CLEAN_BREAK.md` — architectural implementation roadmap.
3. `IMPLEMENTATION_ALIGNMENT_MATRIX_v13.md` — status/action/test matrix.
4. ADRs — irreversible design decisions.
5. Public API guide — Client/AsyncClient only.
6. Provider capability matrix — generated or verified against Registry.
7. Protocol docs — generated from canonical contracts where possible.

Earlier v12 compatibility documents remain historical only and must carry a superseded marker or be removed before release.

---

## 10. Definition of Done

### Semantic DoD

- one name and one meaning for Provider/Channel/Capability;
- QuerySpec/StreamSpec are sole semantic requests;
- fingerprint fully represents query semantics;
- no old route/source ambiguity;
- no silent downgrade.

### Core-flow DoD

- one query planner;
- one single-provider runtime;
- one explicit orchestrator;
- one result/provenance model;
- one error model;
- one cache identity model;
- one streaming lifecycle.

### Core-function DoD

- all Tier A capabilities fully canonical;
- every retained Tier B-E capability either canonical or removed;
- Provider support is executable/tested, not documentation-only.

### End-to-end DoD

- every public input reaches the same core;
- every successful output comes from canonical result models;
- every error comes from canonical error semantics;
- every fallback is explicit/auditable;
- no canonical path can reach legacy runtime/router/facade logic;
- exact-head CI executes and passes all blocking gates.

Only after all four DoD groups are satisfied may the project state that semantic, core-flow, core-function and front-to-back alignment are complete.
