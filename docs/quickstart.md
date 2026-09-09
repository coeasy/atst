# tstdx 快速开始

本文面向当前 `1.4.0` Draft 开发线；最新已发布稳定版是 `v1.0.0`，历史行为与
发布产物见 [v1.0.0 发布说明](releases/v1.0.0.md)。要求 Python 3.10 或更高版本。

## 安装

```bash
# 基础安装（零硬依赖）
pip install tstdx

# 完整可选能力
pip install "tstdx[all]"
```

## 5 分钟上手

### 1. Provider-first 查询

```python
import tstdx

# 未指定 provider 时默认 TDX；一个 QueryPlan 只执行一个 Provider。
result = tstdx.query(
    symbols=["600519"],
    capability="quotes",
)

# 显式 Provider。失败时不会偷偷切换到其它 Provider。
tencent = tstdx.query(
    symbols=["600519"],
    capability="quotes",
    provider="tencent",
)
```

### 2. 兼容 TDX 客户端

```python
from tstdx import TdxClient

with TdxClient() as client:
    bars = client.bars("sh600519", period="day", count=80)
    quotes = client.quotes(["sh600519", "sz000001"])

print(f"K线 {len(bars)} 条，行情 {len(quotes)} 只")
```

`TdxClient` 仍是 TDX 协议的兼容入口；新业务编排优先使用 Provider-first Query/Direct API。

### 3. 异步并发

```python
import asyncio
from tstdx import AsyncTdxClient


async def main():
    async with AsyncTdxClient() as client:
        bars, quotes = await asyncio.gather(
            client.bars("sh600519", period="day", count=80),
            client.quotes(["sh600519", "sz000001"]),
        )
        print(f"K线 {len(bars)} 条，行情 {len(quotes)} 只")


asyncio.run(main())
```

异步连接池与同步连接池共享 generation/lease、live-health 与 circuit 语义；任务取消
保持 cancellation 语义，不会被包装成 Provider/主站失败。

### 4. 数据落地

```python
from tstdx.output import write

with TdxClient() as client:
    bars = client.bars("sh600519", period="day", count=250)

# DataFrame（需 pandas）
write(bars, "output://dataframe")

# Parquet（需 pyarrow）
write(bars, "parquet://kline_600519.parquet")

# DuckDB（需 duckdb）
write(bars, "duckdb://market.db?table=kline")
```

### 5. CLI

```bash
tstdx bars sh600519 --period day --count 20
tstdx quotes sh600519 sz000001
tstdx server-test
tstdx hosts audit --family standard --strict
tstdx stream sh600519
```

### 6. TDX 主站池与热更新

TDX Provider 内部允许 host failover；这只是同一 Provider 内 endpoint 切换，不改变数据来源身份。
连接池默认惰性建连，并按 live request health / circuit 状态选择主站。`bestip()` 和
`hosts audit` 提供测速/审计证据；后台测速的 probe RTT 与真实请求 live RTT 分离。

热更新使用 generation + connection lease：在飞请求可以安全完成，旧 generation 的迟到结果
不能污染新 generation。HALF_OPEN 同时只允许一个探测请求。

## 核心概念

### Provider / Channel / Capability

```text
QuerySpec -> QueryPlan -> Provider -> Channel -> Capability -> Endpoint/Host
```

关键规则：

- TDX 是默认 Provider，但不是其它 Provider 失败时的自动兜底，反之亦然。
- 腾讯、新浪、东财、百度、集思录、中行、iWencai 等必须显式选择。
- TDX host failover 只能发生在 TDX Provider 内部。
- 当前行情默认要求 direct/current freshness 可验证。
- cache / replay / synthetic 数据有自己的 provenance，不能冒充实时 Direct Provider 响应。
- Provider 不可用或 provenance 不满足契约时 fail closed。

旧 `DataSourceRouter` 仅作为兼容表面存在；不要把它理解为新 Provider-first runtime 的
跨 Provider 自动降级策略。

### Direct Provider API

```python
from tstdx import direct

with direct("tdx") as api:
    tdx_result = api.quotes(["600519"])

with direct("eastmoney") as api:
    em_result = api.quotes(["600519"])
```

Direct API 的 Provider/Channel/Capability 映射由 canonical registry 校验；未声明能力会在
service I/O 之前失败。

### 三态输出

兼容 TDX 客户端仍支持：

```python
client.bars("sh600519", as_format="dict")
client.bars("sh600519", as_format="tuple")
client.bars("sh600519", as_format="dataframe")
```

### 错误处理

```python
from tstdx.errors import TdxError, advice_for

try:
    bars = client.bars("sh600519")
except TdxError as exc:
    print(f"错误码: {exc.code}")
    advice = advice_for(exc)
    if advice.switch_host:
        print("可在当前 TDX Provider 内切换主站重试")
```

CLI / REST / WebSocket / MCP / background task 的外部失败统一使用 canonical ErrorEnvelope；
内部异常不会直接泄露到网络边界。

## 本地验证

```bash
make install
make pre-commit
make gates
make build
```

阻塞覆盖率门禁保持 `77%`。联网 smoke/Host Audit 是独立 operational workflow，不能拿公网
状态替代确定性源码 gate。

## 下一步

- [Provider 文档](providers/README.md)
- [API 参考](api/README.md)
- [Cookbook 食谱集](cookbook/README.md)
- [FAQ](FAQ.md)
- [故障排查](troubleshooting.md)
- [v1.0.0 历史发布说明](releases/v1.0.0.md)
