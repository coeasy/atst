# ADR-013：Provider-first Runtime Contract

- 状态：Accepted（v13 起为现行契约；v16/v17 单内核收口后仍是唯一权威口径）
- 编号警示：本目录里有**两份** ADR-013（本文 2026-09-06，与
  [`ADR-013-provider-source-terminology.md`](ADR-013-provider-source-terminology.md) 2026-09-08）。引用不带节号、或引「契约 / 单内核权威口径」的是本文；引 `§11`
  或「稳定公共资产」的是术语那份。消歧表见 [`README.md`](README.md)。
- 日期：2026-09-12
- 基线：`main@7720c04952d9092fc24bf63a2225c381618e151c`

## Context

当前 `main` 同时存在两类选择语义：Facade 的 `auto/local/tdx/web` 路由，以及
`DataSourceRouter` 的跨源降级链。随着 Eastmoney、Tencent、Sina、JSL、BOC、
本地 vipdoc 等能力扩大，`web`/`local` 已经不足以表达真实数据身份。

PR #1 验证了 Provider-first 方向，但其长期分支与最新 main 已分叉，不适合整体
合并。v11 从最新 main 重新建立最小、可审计的运行时契约。

## Decision

1. TDX 是默认 Provider。
2. 一个 `QueryPlan` **恰好绑定一个 Provider 与一个 Channel**。
3. Planner 不执行 I/O，也不包含 fallback Provider 列表。
4. TDX 主站/endpoint failover 允许发生在 TDX Provider 内部；跨 Provider
   fallback 不允许发生在 Provider Executor 内部。
5. 跨 Provider 高可用如果保留，必须由后续显式 `FallbackPolicy`/Orchestrator
   生成多个独立 QueryPlan，并记录 requested/selected Provider 与 attempts。
6. `source=` 仅作为兼容 selector；最终必须归一成唯一 Provider。
7. `source="web"` 因为无法表达 Provider 身份而在新 Runtime 中 fail-fast。
8. vipdoc 作为独立 `local_vipdoc` Provider，不再以 TDX 在线数据身份出现。
9. Query fingerprint 必须包含 Provider、Channel、Capability、symbols、period、
   adjustment、currentness 和会影响上游语义的 options。
10. `QueryResult` 必须携带完整 Provenance。数据 origin 与 cache-hit 是两个独立
    事实；缓存不能改变 direct/replay/synthetic 的原始身份。

## Compatibility

旧 `route="auto"` 暂不直接删除。迁移期由兼容层映射到显式 fallback policy，
但 Provider Runtime 内核保持 fail-closed。新代码不得继续向 DataSourceRouter
增加新的 Provider 选择规则。

## Consequences

### Positive

- 数据身份可审计；
- cache/replay/synthetic 无法冒充实时 Provider；
- Provider contract、freshness、health、cache、SingleFlight 可围绕同一 fingerprint
  与 provenance 建立；
- Facade/CLI/REST/WS/MCP 最终可共享同一执行链。

### Cost

- 需要逐步迁移旧 `route="auto"` 与 `web` 聚合语义；
- 每个 Web 上游需要独立 Provider/Channel 登记；
- 兼容期会同时存在 legacy router 与新 runtime，但 legacy 层不得成为新功能落点。

## Verification

本 ADR 的第一批门禁位于 `tests/runtime/test_query_contracts.py`，锁定：

- 默认 TDX/quotation；
- Provider/source 冲突 fail-fast；
- `web` 歧义 selector fail-fast；
- local vipdoc 与在线 TDX 身份分离；
- core quotes/bars canonical Channel 不得静默切换；
- fingerprint 对周期别名/options 顺序稳定；
- latest-main 关键新增 capability 在 Registry 中保留；
- Provenance 必须与 QueryPlan identity 一致。
