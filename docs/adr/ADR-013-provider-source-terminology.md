# ADR-013: Provider / source / Channel 术语与模型统一

- Status: Accepted
- 编号警示：本目录里有**两份** ADR-013（本文 2026-09-08，与
  [`ADR-013-provider-first-runtime-contract.md`](ADR-013-provider-first-runtime-contract.md) 2026-09-06）。台账里「ADR-013 §11」「稳定公共资产」这类**带节号**的引用落在本文
  （只有本文有编号章节，`## 11. Error 术语`）；两份都不改号，因为号被在写的台账按文件名整段引用，
  改号会把活引用变成死指针。消歧表见 [`README.md`](README.md)。
- Branch: `refactor/industry-benchmark-v12`
- Date: 2026-09-08
- Scope: v12 architecture, public API, registries, docs, errors and provenance

## 1. 决策

`tstdx` 只保留一个正式的“数据提供方”领域实体：**Provider**。

`tdx`、`tencent`、`sina`、`eastmoney`、`baidu`、`jsl`、`boc`、`iwencai` 都是 Provider。

`source` 不再表示第二种领域对象，也不建立 `SourceRegistry` / `SourceManager` 与 Provider 并列。`source` 仅保留两种用途：

1. 兼容期 API selector 别名，例如 `source="tdx"`；
2. 历史字段/异常名中的兼容术语，例如现有 `SourceUnavailable`。

其值都必须解析成同一个 `ProviderId`。

因此可在自然语言中说“TDX 是行情 source”，但代码模型中它的正式类型是 `Provider`。

## 2. 规范层级

```text
Provider
  |
  +-- Channel
        |
        +-- Capability
              |
              +-- Endpoint / Host

Query 还携带：Market / AssetClass / Freshness / Schema 等维度
```

### Provider：谁提供数据

规范 ID：

```text
tdx
tencent
sina
eastmoney
baidu
jsl
boc
iwencai
```

Provider 决定数据 provenance、认证/限流策略、会话生命周期和“不允许跨站替代”的边界。

### Channel：Provider 内从哪一类通道取数据

示例：

```text
tdx/quotation
tdx/extended
tdx/goods
tdx/f10
tdx/mac
tdx/vipdoc

tencent/quote
tencent/kline
tencent/minute
tencent/ticks

eastmoney/fund_flow
eastmoney/limit_pool
eastmoney/corporate
```

Channel 是实现/接入族，不等于 Provider。

### Capability：用户要什么数据

示例：

```text
quotes
bars
minute
trades
finance
fund_flow
hot_rank
news
screening
fx_rates
```

Capability 是业务语义，统一 API 围绕 Capability 设计。一个 Provider 的多个 Channel 可以暴露不同 Capability。

### Endpoint / Host：实际连接到哪里

示例：TDX quotation 的多个 `IP:7709` 主站。

Endpoint/Host 只属于一个 Provider + Channel。Endpoint failover 不改变 Provider。

### Market / AssetClass：数据属于哪个市场/资产类别

```text
cn_a
hk
us
future
option
commodity
fund
bond
fx
```

`hk` / `us` 不是 Provider；`kline` / `minute` 也不是 Provider。

## 3. Provider 与 source 的关系

结论：**语义维度相同，模型实体只有 Provider。**

```text
source="tdx"      -> provider_id="tdx"
source="sina"     -> provider_id="sina"
source="tencent"  -> provider_id="tencent"
```

禁止出现：

```text
Provider(name="tdx")
Source(name="tdx")
```

这种重复对象会导致能力、健康、生命周期、文档分别维护两份。

## 4. Public API 命名

新 API 推荐 `provider=` 作为正式参数：

```python
md.quotes(["sh600519"], provider="tdx")
md.bars("sh600519", provider="tencent")
```

兼容期接受：

```python
md.quotes(["sh600519"], source="tdx")
```

归一化规则：

```text
provider supplied -> use provider
source supplied   -> map source to provider
both supplied and different -> ValidationError
both supplied and same      -> allowed only during compatibility window
neither supplied            -> CapabilityRegistry.default_provider
```

内部 `QuerySpec` 只保留：

```python
QuerySpec(provider="tdx", capability="quotes", ...)
```

不再同时保存 `source`。

## 5. Direct Provider API

每个 Provider 都拥有独立 namespace：

```python
md.tdx.*
md.tencent.*
md.sina.*
md.eastmoney.*
md.baidu.*
md.jsl.*
md.boc.*
md.iwencai.*
```

Provider-specific 数据优先通过 Direct API 暴露，不能为了统一而压平丢失字段。

## 6. TDX 与 vipdoc

`vipdoc` 不再作为与 `tdx/sina/tencent` 并列的 Provider。

它属于 TDX 生态的本地历史数据 Channel：

```text
provider=tdx
channel=vipdoc
mode=local_historical
```

Direct API：

```python
md.tdx.vipdoc.day(...)
md.tdx.vipdoc.minute(...)
```

这样可避免 `vipdoc` 被误认为一个独立网站/数据提供方，也能明确它不能替代 TDX 实时行情。

Golden Replay / Synthetic 更不属于 Provider，只存在于 testing/replay runtime。

## 7. TDX 内部“换源”统一改称内部容错

为了避免“换源”被误解为跨 Provider fallback，规范术语为：

- `host failover`：同一 TDX Channel 内切换主站；
- `endpoint failover`：同 Provider + Channel 内切等价 endpoint；
- `channel resolution`：同 Provider 内依据明确 capability 映射选 Channel；
- `provider switch`：从 TDX 切到新浪/腾讯/东财。

生产默认规则：

```text
host/endpoint failover = allowed
channel resolution     = allowed by registry
provider switch        = forbidden unless user explicitly launches another query
```

## 8. Registry 命名

保留：

```text
ProviderRegistry
ChannelRegistry
CapabilityRegistry
ProviderHealthRegistry
ProviderManager
```

删除/禁止新增平行概念：

```text
SourceRegistry
SourceManager
SourceHealthRegistry
```

如为兼容必须保留旧导入路径，只做 alias，不建立第二份状态。

## 9. ProviderSpec

目标结构：

```python
ProviderSpec(
    id="tdx",
    display_name="TDX",
    role="primary_live",
    channels=(...),
    markets=(...),
    auth_policy=...,
    rate_policy=...,
    freshness_profiles=...,
    production=True,
)
```

ChannelSpec：

```python
ChannelSpec(
    provider="tdx",
    id="quotation",
    capabilities=("quotes", "bars", "minute", "trades"),
    endpoint_group="quotation_hosts",
    protocol="7709",
)
```

CapabilitySpec 负责业务语义和默认 Provider：

```python
CapabilitySpec(
    id="quotes",
    providers=("tdx", "tencent", "sina", "eastmoney", "baidu"),
    default_provider="tdx",
    result_schema="Quote@v1",
    freshness_profile="live_quote",
)
```

## 10. ResultMeta / Provenance

新模型使用：

```python
ResultMeta(
    provider="tdx",
    channel="quotation",
    capability="quotes",
    market="cn_a",
    observed_at=...,
    source_timestamp=...,
    freshness="fresh",
    real=True,
)
```

兼容 JSON 可以临时输出：

```json
{"provider":"tdx","source":"tdx"}
```

但内部对象只存 `provider`；`source` 是序列化兼容 alias。

## 11. Error 术语

> **后续修订（2026-09-19，重构第 44 步 / §0.3 F-68 裁决 (a)）**：本节下方"某 E7050 类是稳定
> 公共资产"的判定已作废——那个类在全仓没有任何运行期抛点或投递点，随裁决连同声明与 `__all__`
> 条目一起删除。原文保留作术语决策的历史语境；现行错误面口径见 `docs/errors.md`（含 §一之二
> 退役登记）与 `docs/providers/README.md` §12。本节仍然成立的是最后一句：**不得再产生第二个
> 不同实现的"Provider 不可用"错误类**——v17 的答案是根本不要这类统一包装，真实失败以传输层
> 原异常呈现。

现有 `tstdx.errors.SourceUnavailable` / E7050 是稳定公共资产，不为了术语统一破坏兼容。

规范解释改为：

> `SourceUnavailable` = selected Provider cannot satisfy the query.

错误 context 使用：

```json
{
  "provider":"tdx",
  "channel":"quotation",
  "capability":"bars"
}
```

不得再产生第二个不同实现的 `ProviderUnavailable` 错误类。若希望对外提供新名字，只允许：

```python
ProviderUnavailable = SourceUnavailable
```

即同一 class object、同一 E7050。

## 12. 文档目录

Provider 级文档统一迁移到：

```text
docs/providers/README.md
docs/providers/tdx.md
docs/providers/tencent.md
docs/providers/sina.md
docs/providers/eastmoney.md
...
```

每个 Provider 文档内部再列 Channel、Capability、Market、Endpoint、Freshness、Direct API。

不再使用 `docs/channels/<provider>.md` 这种目录名与内容层级不一致的结构。

## 13. CI 术语门禁

最终增加检查：

```text
ProviderRegistry IDs == docs/providers/*.md IDs
no production SourceRegistry class
no production SourceManager class
QuerySpec has provider and no source state
source compatibility parser -> ProviderId
ResultMeta stores provider
integration APIs expose provider/source alias consistently
hk/us not registered as Provider
kline/minute/ticks not registered as Provider
vipdoc not registered as standalone Provider
```

## 14. 最终一句话

> **TDX、新浪、腾讯、东财等都是 Provider；“source”只是“选择哪个 Provider”的用户语言/API 兼容名，不是另一层对象。Provider 下面是 Channel，Channel 暴露 Capability，最底层连接 Endpoint/Host。**
