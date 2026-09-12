# Runtime Upgrade v12

Base: `main@4e8708963ae16ba2cbb5a5ef6c7fb52d0331d90a`

This document tracks the single integration branch used for the remaining runtime convergence. PR #1 remains reference/test material and is not a merge unit.

## Completed in this integration branch

- [x] Exact Direct Provider bindings for planner-visible `quotes` / `bars`
- [x] Runtime audit: registered unified capabilities cannot lack a Direct binding
- [x] UnifiedRuntime: `QuerySpec -> QueryPlan -> cache -> SingleFlight -> Direct Provider -> QueryResult`
- [x] Full-fingerprint SingleFlight with independent leader/follower results and errors
- [x] Short-lived terminal-only negative cache; transient `SourceUnavailable` excluded
- [x] Safe canonical ErrorEnvelope with context allow-list and generic E9000 native boundary
- [x] Public top-level runtime exports
- [x] `facade.runtime_api()` strict entrypoint while preserving legacy `UnifiedQuoteAPI`
- [x] CLI normal failures use the canonical ErrorEnvelope; KeyboardInterrupt keeps exit 130
- [x] MCP domain/native failures carry canonical envelope data while retaining JSON-RPC codes
- [x] Strict HTTP v2 quotes/bars/runtime-health surface uses UnifiedRuntime + ErrorEnvelope
- [x] FastAPI validation/framework errors use the same safe envelope body
- [x] Strict WebSocket JSON-RPC v2 quotes/bars/runtime-health surface uses UnifiedRuntime + ErrorEnvelope
- [x] WS notifications never emit failure responses and process-control BaseException is not normalized
- [x] RuntimeTaskStore retains bounded results / safe envelopes and clears expired result/error payloads
- [x] TDX/local/Tencent-minute/Baidu unsupported adjustment semantics fail closed instead of being ignored
- [x] Quote/Bar dataclasses serialize deterministically at strict HTTP/WS boundaries
- [x] Offline behavior gates for provider isolation, envelope sanitization, batch audit, SingleFlight copy isolation, negative-cache policy, cache identity, MCP/WS/task boundaries and public API retention

## Remaining cutover work

- [ ] legacy HTTP quotes/bars endpoints delegate to UnifiedRuntime without removing the existing endpoint set
- [ ] legacy WS quotes/bars dispatch delegates to UnifiedRuntime without changing existing JSON-RPC method names
- [ ] legacy explicit Provider requests in `UnifiedQuoteAPI` delegate to UnifiedRuntime
- [ ] `route="auto"` is reduced to an explicit compatibility orchestration policy, not kernel semantics
- [ ] add persistent semantic L2 promotion/verification before legacy cache retirement
- [ ] promote provider-specific auxiliary capabilities to QuerySpec only after executable bindings and contract tests exist
- [ ] run real Provider smoke/calibration for the new Direct bindings when network runners are healthy
- [ ] exact-head CI/Native gates must actually execute steps and become green

## Hard constraints

- no cross-Provider implicit fallback in the canonical runtime
- no replay/synthetic data impersonating live/direct
- no silent parameter downgrades
- no lowering/skipping/soft-passing gates
- no whole-PR merge of the historical PR #1 branch
- no feature regression from current main Web/fund/futures/bond/fundamental/options APIs
- legacy surfaces remain compatible until their replacement path has executed deterministic gates on the same SHA
