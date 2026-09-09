# tstdx

`tstdx` 是面向量化研究与交易决策的 **TDX-first、多 Provider 行情数据协议库**。

当前版本：`1.4.0`

核心执行路径：

```text
QuerySpec -> QueryPlan -> Provider -> Channel -> Capability -> Endpoint/Host
```

## 不可破坏的运行时语义

- **TDX 是默认主 Provider**；腾讯、新浪、东财、百度、集思录、中行、iWencai 等是显式独立 Provider。
- **一个 QueryPlan 只执行一个 Provider / canonical Channel**。
- **禁止跨 Provider silent fallback**：选中的 Provider 不可用、过期、不可验证或契约不合法时 fail closed。
- **TDX host failover 只发生在 TDX Provider 内部**，不能改变数据来源身份。
- **当前行情默认要求真实、新鲜且 provenance 可验证**；stale cache / replay / synthetic 不能冒充 live data。
- `KeyboardInterrupt` / `SystemExit` 等进程控制信号不能被包装成 Provider 业务错误。

架构基线：

- [TDX 主 Provider + 多 Provider 独立数据通道架构 v12](docs/TDX_PROVIDER_CHANNEL_ARCHITECTURE_PLAN_v12.md)
- [ADR-013：Provider / source / Channel 术语统一](docs/adr/ADR-013-provider-source-terminology.md)
- [Provider 接口目录](docs/providers/README.md)

## 主要能力

### TDX

- 7709 标准行情协议
- 7727 扩展市场
- GOODS / MAC / F10 协议族
- K 线、实时行情、证券列表、财务、资本变动等已验证命令
- vipdoc 本地历史数据读取
- TDX 主站池、连接复用与同 Provider host failover

### 显式 Web Provider

Provider Registry 当前包括：

- `tencent`
- `sina`
- `eastmoney`
- `baidu`
- `jsl`
- `boc`
- `iwencai`

Registry / Direct API 只暴露已经有真实 adapter 的能力；Provider-specific 数据不会被强行压成错误的统一语义。

## 安装

基础包保持零硬依赖：

```bash
pip install tstdx
```

常用 extras：

```bash
pip install "tstdx[web]"        # HTTP Provider
pip install "tstdx[dataframe]"  # pandas 输出
pip install "tstdx[server]"     # FastAPI / WebSocket
pip install "tstdx[mcp]"        # MCP
pip install "tstdx[all]"        # 完整可选运行能力
```

`tstdx` 声明为 PEP 561 typed package；发布 wheel 必须包含 `tstdx/py.typed`。

## 推荐入口：Unified MarketDataService

### 默认 Provider：TDX

```python
from tstdx import market_data

with market_data() as md:
    quotes = md.quotes(["sh600519", "sz000001"])
    bars = md.bars("sh600519", period="day", count=100)
```

### 显式选择 Provider

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

显式选择后保持 fail-closed：

```text
provider="tdx" 失败      -> TDX error
provider="tencent" 失败  -> Tencent error
provider="sina" 失败     -> Sina error
```

绝不会自动变成：

```text
TDX failed -> Tencent -> Sina -> Eastmoney
```

### `source=` 兼容参数

`source=` 仅是旧调用方式的 Provider selector 别名：

```python
with market_data() as md:
    rows = md.quotes(["sh600519"], source="tencent")
```

同时提供 `provider=` 和 `source=` 时，两者必须解析到同一 Provider，否则直接 `ValidationError`。

### provenance / freshness 元数据

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

Cache 命中也必须保留并验证 Provider / Channel / Capability / freshness / QueryFingerprint provenance。有效 payload 被复制到另一个 semantic cache key 时不能被提升为合法命中。

## Direct Provider API

统一 API 只承载真正同语义的公共能力；Provider 特有数据使用 Direct API：

```python
with market_data() as md:
    catalog = md.tdx.f10.catalog("sh600519")
    news = md.sina.news("sh600519", num=20)
    hot = md.eastmoney.hot_rank(page=1, size=100)
    fx = md.boc.fx_rates()
    selected = md.iwencai.screen("市盈率小于20且ROE大于15%")
    bonds = md.jsl.bonds()
```

Direct API 的 Provider/Channel 映射由 Registry 合同自动验证；TDX `vipdoc` 保持 local-only，不能冒充 online TDX。

## Low-level TDX API

```python
from tstdx import TdxClient

client = TdxClient()
try:
    quotes = client.quotes(["sh600519"])
    bars = client.bars("sh600519", period="day", count=100)
finally:
    client.close()
```

低层 API 始终属于 TDX Provider，不参与跨 Provider 选择。

## Async API

```python
import asyncio

from tstdx.async_service import async_market_data


async def main() -> None:
    async with async_market_data(max_workers=4) as md:
        rows = await md.quotes(["sh600519"], provider="tdx")
        print(len(rows))


asyncio.run(main())
```

Async 层复用同一个 planned sync core，不维护第二套路由实现。

## Batch / SingleFlight / negative cache

需要审计部分结果时：

```python
with market_data() as md:
    result = md.quotes_batch(
        ["sh600519", "sz000001"],
        provider="tdx",
    )

    print(result.items)
    print(result.errors)
```

`BatchResult` 明确区分 `failed` / `missing` / `not_attempted`。SingleFlight followers 获得隔离结果/异常对象；negative cache 只缓存短生命周期、稳定终态失败，并按完整 QueryFingerprint 隔离。瞬时 `SourceUnavailable` 不进入 negative cache。

## Streaming

```python
from tstdx import PlannedQuoteStream, StreamState


def on_quote(symbol: str, row: dict) -> None:
    print(symbol, row)


stream = PlannedQuoteStream(provider="tdx")
stream.subscribe(["sh600519"], interval=1.0, on_quote=on_quote)
stream.start()
print(stream.state is StreamState.RUNNING)
stream.stop()
```

生命周期显式为：

```text
CREATED -> RUNNING -> STOPPING -> CLOSED
                    \-> FAILED
```

半死 worker、意外退出、restart-after-terminal 等情况都 fail closed；慢 callback / provider shutdown 不能产生第二套 dispatcher。

## HTTP / WebSocket / MCP / background task

官方集成入口复用同一个 planned runtime 和 canonical `ErrorEnvelope`：

```python
from tstdx.integration import create_app

app = create_app()
```

CLI / REST / WS / MCP / TaskStore 的业务错误使用同一安全 envelope；原生未知异常压缩为内部错误，不泄露敏感 native detail。框架 404/405、请求校验、body 限制等边界也统一归一化。

## vipdoc / Replay / Synthetic

vipdoc 是 **TDX local historical Channel**，不是实时失败的兜底：

```text
TDX live failed -> error
```

而不是：

```text
TDX live failed -> vipdoc historical
```

Golden replay / synthetic 只服务测试、回归和明确回放场景。

## Native compatibility 状态

历史 `tstdx_native` Rust 扩展源码不属于当前仓库；`tstdx.native` 自 v1.4.0 起是退役兼容 facade：

- 不自动发现或执行环境中同名的第三方 `tstdx_native` 模块；
- 兼容函数只走仓库内 canonical Python 实现；
- `selftest()` 验证 float / `.day` / K-line fallback parity；
- PR 上的阻塞 job 是 **Native compatibility & fallback parity**，不是不存在的 maturin/Rust build。

## 开发环境与本地门禁

不要手工维护一套比 CI 更宽松的命令。推荐入口：

```bash
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
# .venv\Scripts\activate       # Windows

make install
pre-commit install
```

快速提交前检查：

```bash
make pre-commit
```

完整确定性 PR 门禁：

```bash
make gates
```

`make gates` 与阻塞 CI 对齐，包含：

- Ruff check + format（`tstdx/ tests/ scripts/`）
- mypy + `--warn-unused-ignores`
- 非联网 pytest + **coverage >= 77** + `coverage.xml`
- Bridge Audit
- Golden 三旗标
- strict Spec Coverage
- Adversarial matrix
- Reachability
- Originality
- synthetic benchmark smoke
- docs relative-link integrity
- Native compatibility/fallback parity

77% 是当前硬门禁下限，不是长期目标；新增代码应维持或提高覆盖率，继续向 80%+ 收敛。禁止通过删测试、降低阈值、移除 strict、`continue-on-error` 或恢复 silent fallback 换取绿色。

联网探测与确定性 merge gate 分离：

```bash
make test-live
make host-audit
```

GitHub Actions 中 `Live Smoke` 和 `Host Audit` 也是独立 operational workflows；公网波动不会伪装成源码 gate。

详细贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 构建与发布

本地构建：

```bash
make build
```

该入口默认使用 PEP 517 isolation，要求：

- 恰好一个 `py3-none-any` wheel + 一个 sdist；
- wheel 包含 `tstdx/py.typed`；
- 自定义 `--dist-out` 不能指向仓库根/祖先、受保护源码树或 symlink；
- 清理只删除 tstdx 构建产物，不递归删除自定义输出目录；
- clean venv 中验证版本、CLI、PEP 561 marker 和 `pip check`。

本地直接发布被禁用：

```bash
make publish
# -> intentionally fails
```

正式发布只通过 `.github/workflows/wheels.yml`：

1. `tstdx.__version__ == pyproject.toml [project].version`；
2. GitHub Release tag 必须严格等于 `v{version}`；
3. 只构建一个 canonical `py3-none-any` wheel + sdist，并用 Twine 校验；
4. **同一个 wheel** 在 Linux/macOS/Windows × Python 3.10–3.13 做 12-cell 安装冒烟，且禁止从 sdist 重建；
5. 全部绿色后，通过 OIDC Trusted Publishing 一次性发布 PyPI；
6. Docker Release 镜像下载并安装同一个已验证 wheel，不重新从源码造第二份 artifact；prerelease 不覆盖 Docker `latest`。

## PR 合并条件

同一个 head SHA 上必须真实执行并通过：

- Ruff
- mypy
- Linux / Windows Python test matrix
- Bridge / Golden / Spec / Adversarial / Reachability / Originality / Benchmark / Docs
- Native compatibility & fallback parity

`steps=null`、runner 未分配、没有 checkout/命令日志的 workflow failure 不是源码 gate 已执行的证据；也不能通过把 gate 改成 skipped/soft-fail 来绕过。

## Provider 文档

- [TDX](docs/providers/tdx.md)
- [Tencent](docs/providers/tencent.md)
- [Sina](docs/providers/sina.md)
- [Eastmoney](docs/providers/eastmoney.md)
- [Baidu](docs/providers/baidu.md)
- [JSL](docs/providers/jsl.md)
- [BOC](docs/providers/boc.md)
- [iWencai](docs/providers/iwencai.md)

协议规范：[PROTOCOL_SPEC](PROTOCOL_SPEC/README.md)  
版本变更：[CHANGELOG](CHANGELOG.md)

## License

MIT
