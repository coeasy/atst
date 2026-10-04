# Provider 目录

> 本页反映当前实现（2026-10-04）。唯一事实源是 [`PROVIDERS`](../../atst/providers/__init__.py)；
> 下表只概括 Provider 与通道，不代表每个登记能力当前都能从远端成功取数。运行时状态以
> `/v13/capabilities`、`Client.capability_statuses()` 和对应 Provider 页为准。

代码接口由 `ProviderRegistry` 持有不可变的 `ProviderSpec` / `ChannelSpec` 清单；本页不再描述
旧版规划中的 `ChannelRegistry` 与 `CapabilityRegistry`，它们不是当前运行时对象。

## 当前架构

请求统一经过 `Client` / `AsyncClient`、`QuerySpec`、`UnifiedRuntime` 与
`DirectProviderExecutor`。一个 `QueryPlan` 绑定一个 Provider 和一个 Channel；
TDX 主站之间的重试属于同一 Provider。跨 Provider 尝试必须显式使用
`FallbackPolicy`，并由 `ProviderOrchestrator` 记录每次尝试及结果来源。

```python
from atst import Client, QuerySpec

with Client() as client:
    result = client.execute(
        QuerySpec.build(
            "quotes",
            symbols=["sh600519", "sz000001"],
            provider="tencent",
            currentness="live",
        )
    )
    print(result.meta.provider, result.meta.channel, result.data)
```

调用前可用 `client.capability_statuses()` 查询迁移能力状态；Provider/channel
组合是否被规划器接受由当前 capability 目录校验。运行期远端是否可达仍取决于外部服务。

## Provider 与通道

| Provider | 当前登记通道 | 说明 |
|---|---|---|
| `tdx` | `quotation`, `extended`, `goods`, `f10`, `mac` | TDX 二进制协议。扩展市场、MAC 和 F10 的线上可用性有限，见 [TDX 状态](tdx.md)。 |
| `local_vipdoc` | `vipdoc` | 本地通达信历史文件读取；不提供实时数据。 |
| `tencent` | `quote`, `kline`, `minute_kline`, `minute`, `ticks`, `global`, `market_stat`, `board_rank`, `catalog` | 行情及市场辅助数据。 |
| `sina` | `quote`, `history_kline`, `suggest`, `industry_board`, `board_list`, `board_member`, `fund_flow`, `news`, `catalog` | 行情、搜索、板块、资金流及新闻。 |
| `eastmoney` | `quote`, `kline`, `trends`, `rank`, `fund_flow`, `limit_pool`, `stock_changes`, `northbound`, `hot_rank`, `corporate`, `longhu`, `margin`, `index_constituents`, `fund`, `derivatives`, `datacenter`, `news`, `research`, `options`, `catalog` | 覆盖面最广的 Web 数据 Provider；具体端点的限频、字段和鉴权不同。 |
| `baidu` | `quote`, `kline`, `minute`, `ticks`, `catalog` | 行情及历史估值序列。 |
| `jsl` | `bond` | 集思录可转债数据。 |
| `boc` | `fx` | 中国银行外汇牌价。 |
| `iwencai` | `screening` | 问财自然语言筛选。 |
| `cninfo` | `catalog` | 巨潮资讯公告与互动问答。 |
| `ths` | `catalog` | 同花顺主题、概念与热度类数据。 |
| `wallstreet` | `catalog` | 华尔街见闻快讯。 |
| `builtin` | `catalog` | 内置静态/基础能力。 |
| `derived` | `catalog` | 明确登记的派生或聚合能力；不对应单个上游 Provider。 |

注册并不等同于线上已验证：能力目录同时区分 operational/unavailable；TDX 命令账本还区分
`online`、`offline`、`degraded`、`verified`。新 Provider 上线须同时提供真实 adapter、参数/输出契约、
来源元数据、错误语义和离线固定样本；仅添加注册行不算贯通。

## 多源容错

默认请求不会跨 Provider 自动切换。只有业务语义兼容且调用方明确同意更换数据源时才应使用：

```python
from atst import Client, FallbackPolicy

with Client() as client:
    result = client.quotes("sh600519", policy=FallbackPolicy.build("tdx", "tencent"))
    print(result.result.meta.provenance, result.attempts)
```

回退可能改变刷新频率、字段完整度和复权/时间口径；调用方应检查 provenance 与 warnings，
不要把不同口径的结果无条件拼接。

## Provider 详情

- [TDX](tdx.md)
- [Eastmoney](eastmoney.md)
- [Sina](sina.md)
- [Tencent](tencent.md)
- [Baidu](baidu.md)
- [JSL](jsl.md)
- [BOC](boc.md)
- [iWencai](iwencai.md)
