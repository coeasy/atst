# tstdx

`tstdx` 是面向量化研究与交易决策的多 Provider 行情数据协议库。

**核心定位：以 TDX 为默认主 Provider；腾讯、新浪、东财、百度、集思录、中行、iWencai 等作为显式独立 Provider。选定 Provider 不可用时直接报错，不允许跨 Provider 静默替代。**

当前版本：`1.4.0`

## 核心原则

- **TDX 是默认主 Provider**：统一行情接口在未指定 Provider 时默认选择 TDX。
- **一个请求只执行一个 Provider**：`provider=` / 兼容 `source=` 在规划阶段解析为唯一 Provider。
- **禁止跨 Provider fallback**：TDX 失败不会自动改用腾讯、新浪或东财；其它 Provider 同理。
- **TDX 内部允许 host failover**：主站切换只发生在 TDX Provider 内，不改变数据来源身份。
- **实时数据必须可证明为真实且新鲜**：实时链路执行 freshness / integrity / provenance 检查。
- **stale cache / replay / synthetic 不冒充当前行情**：这些能力只能在明确允许的历史、测试或回放场景使用。
- **Provider-specific 能力保持独立**：不强行把东财资金流、iWencai 选股、F10 等压成错误的统一语义。

架构执行基线见：

- [TDX 主 Provider + 多 Provider 独立数据通道架构 v12](docs/TDX_PROVIDER_CHANNEL_ARCHITECTURE_PLAN_v12.md)
- [ADR-013：Provider / source / Channel 术语统一](docs/adr/ADR-013-provider-source-terminology.md)
- [Provider 接口目录](docs/providers/README.md)

## 主要能力

### TDX

- 7709 标准行情协议
- 7727 扩展市场
- GOODS 商品协议族
- MAC 协议族
- F10 资料协议族
- K 线、实时行情、证券列表、财务、资本变动等已验证命令
- vipdoc 本地历史数据读取
- TDX 主站池、连接复用、同 Provider host failover

### 独立 Web Provider

当前 Provider Registry 包括：

- `tencent`
- `sina`
- `eastmoney`
- `baidu`
- `jsl`
- `boc`
- `iwencai`

各 Provider 的真实能力、Channel 和限制以 [docs/providers/](docs/providers/README.md) 为准。

> Registry/Direct API 只应暴露已经有真实 adapter 的能力。未验证或尚未实现的数据能力不应通过其它数据冒充。

## 安装

基础安装保持零硬依赖：

```bash
pip install tstdx
```

常用可选依赖：

```bash
pip install "tstdx[web]"        # HTTP Provider
pip install "tstdx[dataframe]"  # pandas 输出
pip install "tstdx[server]"     # FastAPI / WebSocket 服务
pip install "tstdx[mcp]"        # MCP 服务
pip install "tstdx[all]"        # 完整可选能力
```

开发环境：

```bash
pip install -e ".[dev]"
```

## 推荐用法：统一 MarketDataService

### 1. 默认 Provider：TDX

```python
from tstdx import market_data

with market_data() as md:
    quotes = md.quotes(["sh600519", "sz000001"])
    bars = md.bars("sh600519", period="day", count=100)
```

没有指定 `provider` 时，支持的统一行情能力默认使用 TDX。

### 2. 显式选择 Provider

```python
from tstdx import market_data

with market_data() as md:
    tdx_quotes = md.quotes(["sh600519"], provider="tdx")
    tencent_quotes = md.quotes(["sh600519"], provider="tencent")
    sina_bars = md.bars(
        "sh600519",
        provider="sina",
        period="day",
        count=100,
    )
```

显式选择后是 **fail-closed** 语义：

```text
provider="tdx" 失败      -> 返回 TDX 错误
provider="tencent" 失败  -> 返回 Tencent 错误
provider="sina" 失败     -> 返回 Sina 错误
```

不会发生：

```text
TDX failed -> Tencent -> Sina -> Eastmoney
```

### 3. `source=` 兼容参数

`source=` 仅作为旧调用方式的 Provider selector 兼容别名：

```python
with market_data() as md:
    rows = md.quotes(["sh600519"], source="tencent")
```

如果同时提供 `provider=` 和 `source=`，两者必须指向同一个 Provider，否则直接报 `ValidationError`。

### 4. 获取 provenance / freshness 元数据

```python
with market_data() as md:
    result = md.quotes(
        ["sh600519"],
        provider="tdx",
        with_meta=True,
    )

    print(result.meta.provider)
    print(result.meta.channel)
    print(result.meta.freshness_status)
```

元数据用于审计：

- 实际 Provider
- 实际 Channel
- freshness 证据
- Provider timestamp（上游能够可靠提供时）
- 是否为真实数据
- 是否发生 cache/replay/synthetic

## Direct Provider API

统一 API 只处理真正同语义的公共能力。Provider 特有数据使用 Direct API。

### TDX F10

```python
with market_data() as md:
    catalog = md.tdx.f10.catalog("sh600519")
```

### 新浪新闻

```python
with market_data() as md:
    news = md.sina.news("sh600519", num=20)
```

### 东财热度排行

```python
with market_data() as md:
    rows = md.eastmoney.hot_rank(page=1, size=100)
```

### 中行外汇

```python
with market_data() as md:
    rates = md.boc.fx_rates()
```

### iWencai

```python
with market_data() as md:
    rows = md.iwencai.screen("市盈率小于20且ROE大于15%")
```

### 集思录

当前已落地的 Direct API 以可转债数据为主：

```python
with market_data() as md:
    bonds = md.jsl.bonds()
```

不要将未验证的数据接口当作已支持能力；具体状态以 [JSL Provider 文档](docs/providers/jsl.md) 与代码 Registry 为准。

## Low-level TDX API

需要直接操作 TDX 协议时可以使用低层客户端：

```python
from tstdx import TdxClient

client = TdxClient()
try:
    quotes = client.quotes(["sh600519"])
    bars = client.bars("sh600519", period="day", count=100)
finally:
    client.close()
```

低层 API 不负责跨 Provider 选择；它始终属于 TDX Provider。

## Async API

```python
import asyncio

from tstdx.async_service import async_market_data


async def main() -> None:
    async with async_market_data(max_workers=4) as md:
        tdx_rows = await md.quotes(["sh600519"], provider="tdx")
        tencent_rows = await md.quotes(["sh600519"], provider="tencent")
        print(len(tdx_rows), len(tencent_rows))


asyncio.run(main())
```

Async 层复用同一个 planned sync core，不维护第二套路由实现。取消 async 调用不会假装底层同步线程已经停止；关闭时会先 drain 已提交工作，再关闭自有 Provider runtime。

## 批量行情与部分结果

严格模式下，批量请求缺少标的会失败：

```python
with market_data() as md:
    rows = md.quotes(["sh600519", "sz000001"], provider="tdx")
```

需要审计部分失败时：

```python
with market_data() as md:
    result = md.quotes_batch(
        ["sh600519", "sz000001"],
        provider="tdx",
    )

    print(result.items)
    print(result.errors)
```

`BatchResult.errors` 会区分：

- 已实际请求并失败
- Provider 返回缺失
- 因前序 chunk 失败而未继续请求

不会把“未请求”伪装成“上游请求失败”。

## Streaming

v12 提供 Provider-bound 的 planned quote stream：

```python
from tstdx import PlannedQuoteStream


def on_quote(symbol: str, row: dict) -> None:
    print(symbol, row)


stream = PlannedQuoteStream(provider="tdx")
stream.subscribe(
    ["sh600519", "sz000001"],
    interval=1.0,
    on_quote=on_quote,
)
stream.start()
```

Streaming 规则：

- 一个 stream 实例绑定一个 Provider
- due-aware 调度，不让快订阅强制慢订阅同频轮询
- bounded callback queue
- per-symbol watermark / gap 状态
- reconnect / resubscribe epoch
- Provider 故障不会自动切换到其它 Provider

使用完成后：

```python
stream.stop()
```

## HTTP / 服务集成

官方 FastAPI factory 使用同一 planned runtime：

```python
from tstdx.integration import create_app

app = create_app()
```

显式 Provider REST 路径会保持 Provider identity；HTTP Provider client 还会校验请求 host、redirect history 与最终 response host，阻止 legacy adapter 绕过 Provider 边界。

## 本地历史数据与缓存

### vipdoc

vipdoc 是 **TDX local historical Channel**，不是实时失败时的兜底来源。

实时请求：

```text
TDX live failed -> error
```

不会变成：

```text
TDX live failed -> vipdoc historical
```

### Cache

Cache 只允许作为不改变 Provider、时间窗口和 freshness 契约的优化。

实时行情默认不会读取 stale persistent cache 来替代当前 Provider 请求。历史缓存 key 必须包含 Provider 和完整语义窗口，避免不同 Provider 的数据互相命中。

### Replay / Synthetic

Golden replay 与 synthetic 属于测试/回归工具，不是生产实时 Provider。

```text
production latest query != replay
production latest query != synthetic
```

## 错误语义

稳定错误树位于 `tstdx.errors`。

常见原则：

- `ValidationError`：请求语义不合法
- `SourceUnavailable`：已经选择的 Provider 当前不可用
- `FreshnessViolation`：结果无法满足 freshness 契约
- `IntegrityViolation`：响应与请求语义不一致
- `ReadTimeout`：请求/总 deadline 耗尽

错误不会授权跨 Provider 自动切换。

```python
from tstdx import market_data
from tstdx.errors import FreshnessViolation, SourceUnavailable

try:
    with market_data() as md:
        rows = md.quotes(["sh600519"], provider="tdx")
except (SourceUnavailable, FreshnessViolation) as exc:
    print(exc)
```

如果业务需要比较多个 Provider，请显式发起多个独立查询，并分别保留其 provenance/error；不要把其中一个结果静默替代另一个。

## Provider 文档

- [TDX](docs/providers/tdx.md)
- [Tencent](docs/providers/tencent.md)
- [Sina](docs/providers/sina.md)
- [Eastmoney](docs/providers/eastmoney.md)
- [Baidu](docs/providers/baidu.md)
- [JSL](docs/providers/jsl.md)
- [BOC](docs/providers/boc.md)
- [iWencai](docs/providers/iwencai.md)

## 协议与工程结构

主要目录：

```text
tstdx/
├── protocol/       # TDX 命令与 parser registry
├── codec/          # 二进制 framing / primitive
├── transport/      # 连接、主站池、限流与同 Provider failover
├── client/         # TDX 低层同步/异步客户端
├── providers/      # Provider/Channel registry 与 HTTP 边界
├── provider_api.py # Direct Provider API
├── service.py      # Provider execution core
├── query.py        # QuerySpec / QueryPlan
├── planned_service.py
├── facade/         # planned compatibility facade
├── sources/        # legacy-shaped compatibility surface（单 Provider 语义）
├── streaming/      # streaming engine / planned stream
├── reader/         # vipdoc 本地历史数据
├── integration/    # HTTP / WS / MCP 等边界
└── observability/  # metrics
```

协议规范：

- [PROTOCOL_SPEC](PROTOCOL_SPEC/README.md)

版本变更：

- [CHANGELOG](CHANGELOG.md)

## 开发与门禁

本地推荐：

```bash
pip install -e ".[dev]"
ruff check tstdx tests
ruff format --check tstdx tests
mypy tstdx
pytest
```

PR 合并条件：

- Ruff 通过
- mypy 通过
- Python / Windows / Linux 测试矩阵通过
- protocol/spec/golden/reachability 等架构门禁通过
- Native 内部真实 job 通过
- 所有必须门禁在同一个 head SHA 上绿色

不通过删除测试、降低阈值、恢复跨 Provider fallback 或允许 stale/synthetic 替代来换取绿色。

## License

MIT
