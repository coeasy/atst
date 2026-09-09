# tstdx

`tstdx` 是面向量化研究与交易决策的 **TDX-first、多 Provider 行情数据协议库**。

当前 Draft 开发版本：`1.4.0`  
最新已发布稳定版：`v1.0.0`（见 [v1.0.0 发布说明](docs/releases/v1.0.0.md)）

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

完整可选能力：

```bash
pip install "tstdx[all]"
```

源码开发环境使用和 CI 相同的声明式依赖入口：

```bash
python -m pip install -e ".[all,dev]"
python -m pip install build twine pre-commit==4.6.2
pre-commit install
```

不要手工维护另一套 pytest / Ruff / mypy 依赖列表。

## 核心用法

### Provider-first API

```python
import tstdx

# 默认 Provider = TDX
result = tstdx.query(
    symbols=["600519"],
    capability="quotes",
)

# 显式 Provider；不会在失败时偷偷切到其它 Provider
result = tstdx.query(
    symbols=["600519"],
    capability="quotes",
    provider="tencent",
)
```

### Direct Provider API

```python
from tstdx import direct

with direct("tdx") as api:
    result = api.quotes(["600519"])

with direct("eastmoney") as api:
    result = api.quotes(["600519"])
```

Direct API 与 Provider Registry 共用同一能力契约。未注册的 Provider / Channel / Capability 会明确失败，不会进入隐藏兼容分支。

### 兼容 TDX 客户端

```python
from tstdx import TdxClient

with TdxClient() as client:
    quotes = client.quotes(["600519", "000001"])
    bars = client.bars("600519", period="day", count=100)
```

兼容客户端仍然可用，但 Provider-first 新代码优先使用 Query/Direct API。

## Freshness 与缓存

当前数据默认要求 freshness 可验证：

- Direct snapshot 必须有 direct/current provenance。
- Historical closed bars 与 current series 使用不同 freshness mode。
- semantic cache key 绑定 Provider / Channel / Capability / symbols / period / window。
- cache hit、replay、synthetic 数据不会冒充 direct Provider 响应。
- Provider/Channel provenance 不匹配的缓存项会被拒绝并清理。

## Streaming

Provider-first 流式入口使用显式生命周期状态：

```text
CREATED -> RUNNING -> STOPPING -> CLOSED
                    \-> FAILED
```

`start()` 只在 worker 健康时允许幂等；`stop()` 是 terminal；异常 worker 退出或部分启动失败会 fail closed。

## 错误模型

对外边界统一使用稳定错误分类和 `ErrorEnvelope`：

- Python domain exception
- CLI
- REST
- WebSocket JSON-RPC
- MCP JSON-RPC
- Background TaskStore

原生异常不会把内部堆栈/敏感参数暴露给网络调用方。`KeyboardInterrupt`、`SystemExit`、async cancellation 保持进程/任务控制语义。

## 本地开发与门禁

推荐入口：

```bash
make install
make pre-commit
make gates
make build
```

其中：

- `make pre-commit`：快速静态/规格/文档门禁子集。
- `make gates`：确定性阻塞门禁，覆盖 Ruff、mypy、离线测试、Spec、Golden、Bridge、Adversarial、Reachability、Originality、Benchmark、Docs、Native compatibility。
- 覆盖率硬门禁保持 **77%**；不能为修复 CI 调低。
- `make build`：canonical wheel + sdist + metadata/PEP 561 校验。
- `make publish` 故意禁止本地发布；PyPI 只允许 GitHub Release OIDC Trusted Publishing。

公网探测单独执行，不作为确定性 merge gate：

```bash
make test-live
make host-audit
```

## 发布模型

项目是纯 Python / OS Independent 包：

1. 构建一份 canonical `py3-none-any` wheel + sdist。
2. 校验 `tstdx.__version__`、`pyproject.toml`、artifact filename、METADATA、PEP 561。
3. 同一 wheel 在 Linux / macOS / Windows × Python 3.10–3.13 安装冒烟。
4. 全矩阵通过后，通过 OIDC **一次**发布 wheel + sdist 到 PyPI。
5. 同一 canonical wheel/sdist 附加到对应 GitHub Release。
6. Docker 镜像消费同一个已验证 wheel，不重新从源码构建；正式版再更新 `latest`。

GitHub tag 必须严格匹配 `v{pyproject version}`。

## Docker

源码验证镜像：

```bash
docker build -t tstdx:dev .
```

Release 镜像使用单独的 `Dockerfile.release`，上下文由 `Dockerfile.release.dockerignore` 限制为 Dockerfile + canonical wheel，不把源码/测试复制进运行时镜像。

## 文档

- [快速开始](docs/quickstart.md)
- [API 索引](docs/api/README.md)
- [Provider 文档](docs/providers/README.md)
- [错误模型](docs/errors.md)
- [故障排查](docs/troubleshooting.md)
- [v1.0.0 稳定版历史说明](docs/releases/v1.0.0.md)
- [v12 架构计划](docs/TDX_PROVIDER_CHANNEL_ARCHITECTURE_PLAN_v12.md)

## PR 合并要求

在同一个 head SHA 上必须真实执行并全部绿色：

- Ruff check + format
- mypy
- Linux / Windows Python test matrix，coverage >= 77
- Bridge / Golden / Spec / Adversarial / Reachability / Originality / Benchmark / Docs
- Native compatibility & fallback parity

`steps=null`、runner 未分配、skipped、disabled、soft-fail 或降低门禁都不算通过。
