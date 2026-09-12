# Runtime Upgrade v12

Base: `main@4e8708963ae16ba2cbb5a5ef6c7fb52d0331d90a`

This document tracks the single integration branch used for the remaining runtime convergence. PR #1 remains reference/test material and is not a merge unit.

## Completed in this integration branch

- [x] Exact Direct Provider bindings for planner-visible `quotes` / `bars`
- [x] Runtime audit: registered unified capabilities cannot lack a Direct binding
- [x] UnifiedRuntime: `QuerySpec -> QueryPlan -> cache -> SingleFlight -> Direct Provider -> QueryResult`
- [x] Full-fingerprint SingleFlight
- [x] Short-lived terminal-only negative cache
- [x] Safe canonical ErrorEnvelope
- [x] Public top-level runtime exports
- [x] `facade.runtime_api()` strict entrypoint while preserving legacy `UnifiedQuoteAPI`
- [x] Offline behavior gates for provider isolation, envelope sanitization, batch audit, SingleFlight copy isolation, negative-cache policy and cache identity

## Remaining cutover work

- [ ] REST uses ErrorEnvelope + UnifiedRuntime for canonical quotes/bars
- [ ] WebSocket JSON-RPC uses the same runtime/error boundary
- [ ] MCP uses the same runtime/error boundary while preserving JSON-RPC error codes
- [ ] CLI uses the same safe ErrorEnvelope contract
- [ ] background task storage keeps safe envelopes only and clears expired result/error payloads
- [ ] legacy explicit Provider requests delegate to UnifiedRuntime
- [ ] `route="auto"` becomes an explicit compatibility orchestration policy, not kernel semantics
- [ ] add persistent semantic L2 promotion/verification before legacy cache retirement
- [ ] provider-specific auxiliary capabilities are promoted to QuerySpec only after executable bindings and contract tests exist
- [ ] exact-head CI/Native gates must actually execute steps and become green

## Hard constraints

- no cross-Provider implicit fallback
- no replay/synthetic data impersonating live/direct
- no lowering/skipping/soft-passing gates
- no whole-PR merge of the historical PR #1 branch
- no feature regression from current main Web/fund/futures/bond/fundamental/options APIs
