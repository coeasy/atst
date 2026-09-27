# tstdx 功能梳理与优化改进方案 v1（已归档）

> **状态：Archived / 已归档**  
> 初版日期：2026-09-02  
> 当前执行基线：[`TDX_PROVIDER_CHANNEL_ARCHITECTURE_PLAN_v12.md`](TDX_PROVIDER_CHANNEL_ARCHITECTURE_PLAN_v12.md)  
> 术语决策：[`adr/ADR-013-provider-source-terminology.md`](../../adr/ADR-013-provider-source-terminology.md)  
> Provider 文档：[`providers/README.md`](../../providers/README.md)

本文档记录的是 v1 阶段的历史优化思路，完整原文保留在 Git 历史中，不再作为当前实现或执行依据。

## 为什么归档

v1 文档中的部分架构描述已经被 v12 明确废弃，尤其包括旧的多级自动降级/跨数据源路由语义。当前项目的硬约束是：

- TDX 是默认主 Provider；
- 新浪、腾讯、东财等是独立 Provider，不是 TDX 的 fallback；
- 用户选择某个 Provider 后，该 Provider 不可用时应明确报错；
- TDX 只允许在 TDX Provider 内部进行 host/endpoint failover；
- 不允许 `tdx K线失败 -> 新浪/腾讯/东财 K线`；
- 实时结果必须满足真实数据、freshness、integrity 和 provenance 契约；
- replay/synthetic/stale cache 不得冒充当前真实行情。

## 当前代码入口

新代码应优先使用：

```python
from tstdx import UnifiedMarketDataService

with UnifiedMarketDataService() as md:
    quotes = md.quotes(["sh600519"], provider="tdx")
    bars = md.bars("sh600519", provider="tdx", period="day")
```

需要访问 Provider 特有能力时，使用 Direct Provider API，例如：

```python
with UnifiedMarketDataService() as md:
    result = md.eastmoney.fund_flow("sh600519")
```

低层 TDX 协议客户端仍保留在 [`atst/client/`](../../../atst/client/) 中，供需要直接协议控制的调用方使用。

## 当前唯一执行原则

后续架构、接口、错误、缓存、批量、Streaming、REST/WS/MCP 和测试门禁均以 v12 文档及其 ADR/Provider 文档为准。历史 v1 内容不得用于重新引入跨 Provider fallback。
