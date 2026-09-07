# tstdx

> 通达信（TDX）行情数据通用协议库 —— 覆盖 5 套协议族，零硬依赖，跨平台。
> **当前版本**：1.2.0（修复波 F0-F5 全量落地，见 [CHANGELOG](CHANGELOG.md)）

## 特性

- **5 套协议族**：7709 标准 / 7727 扩展市场 / MAC 专属 / F10 资料 / 商品语义
- **85 命令账本 · 61 L1 精确解析器**：L1 精确 → L2 通用启发 → L3 原始透传 三级分派，解析器逃逸原生异常统一收口为 `ParseError`
- **同步/异步双 API**：`TdxClient` + `AsyncTdxClient`（签名镜像、奇偶门禁）
- **多协议族客户端**：GoodsClient / ExMarketClient / MacClient / F10Client
- **HTTP Web 45 源类（17 模块）**：东财/新浪/腾讯/集思录/港股/中行等，`httpx`/`urllib` 双栈
- **5 级降级路由**：tdx→web→reader→cache→synthetic
- **门面统一 API**：`UnifiedQuoteAPI`（46 公开方法，auto/tdx/web/local 四路由 + 熔断 + `adjust` 口径守卫）+ 统一响应形态 `ApiResponse{success,error,data,extra}` + 惰性 `.df`
- **流式订阅**：QuoteStream + AsyncQuoteStream（轮询 + diff，裸码归一、线程兜底）；engine 为其生产内核组件，push 推送通道为可选高级 API（见 ADR-011）
- **3 Sink 策略**：DataFrame / Parquet / DuckDB（原子写）
- **服务面**：HTTP REST 网关（42 端点，方法白名单 + TaskStore 钳制）/ WebSocket JSON-RPC / MCP stdio 12 工具
- **可观测性**：zero-dep 指标注册表 + Prometheus/StatsD/OTLP 三导出器 + `start_exporter` 装配工厂
- **40+ 异常类**：分类错误树 + `RetryAdvice`
- **零硬依赖**：所有第三方库均为可选 extra

## 安装

```bash
pip install tstdx                    # 零依赖基础安装
pip install "tstdx[all]"             # 完整功能
pip install "tstdx[dataframe,parquet,duckdb,web,metrics,server,mcp]"
```

### Optional Extras

| Extra       | 依赖                        | 功能               |
|-------------|---------------------------|--------------------|
| `config`    | pydantic                  | 严格配置校验       |
| `dataframe` | pandas                    | DataFrame 输出     |
| `parquet`   | pyarrow                   | ParquetSink        |
| `duckdb`    | duckdb                    | DuckDBSink         |
| `web`       | httpx                     | HTTP Web 行情源    |
| `metrics`   | prometheus-client         | Prometheus 导出    |
| `server`    | fastapi, uvicorn          | HTTP REST 网关     |
| `mcp`       | mcp                       | MCP 工具服务       |
| `all`       | 以上全部                   | 完整功能           |

## 快速开始

### 基础用法

```python
from tstdx import TdxClient

client = TdxClient()

bars = client.bars("sh600519", period="day", count=80)  # K 线
quotes = client.quotes(["sh600519", "sz000001"])  # 实时行情
count = client.security_count(market=1)  # 1=上海, 0=深圳
```

### 异步用法

```python
import asyncio
from tstdx import AsyncTdxClient


async def main():
    client = AsyncTdxClient()
    bars, quotes = await asyncio.gather(
        client.bars("sh600519", period="day", count=80),
        client.quotes(["sh600519", "sz000001"]),
    )
    print(len(bars), len(quotes))


asyncio.run(main())
```

### 门面统一响应（永不抛异常边界）

```python
from tstdx.facade import quote_api

api = quote_api()
resp = api.query("quotes", ["sh600519", "sz000001"])
if resp:
    print(len(resp.data), "条, 源=", resp.extra.get("source"))
    df = resp.df  # pandas DataFrame（可选）
else:
    print(f"失败: {resp.error} (code={resp.code})")
```

### CLI（19 子命令）

```bash
tstdx bars sh600519 --period day --count 80     # K 线
tstdx quotes sh600519 sz000001                  # 实时行情
tstdx server-test                               # 主站测速
tstdx serve --host 0.0.0.0 --port 8000          # HTTP 服务
tstdx probe 0x052D                              # 协议探测
tstdx feedback stats                            # 使用统计
```

## 核心代码文件地图

```
tstdx/
├── protocol/       # 协议核心：commands(85 账本)/registry(三级分派+异常收口)
│   └── parsers/    #   6 族 61 解析器（std7709/std7709_extra/std7727/mac/goods/f10）
├── codec/          # 编解码：framing(帧)/primitive(原语+count_guard+zlib strict)
├── transport/      # 传输：base(RLock 租约)/async_/pool(4 槽)/ratelimit/speedtest/hosts/sniff
├── client/         # TdxClient/AsyncTdxClient + 5 族客户端（_mixin 共享骨架 + sync/async_/factory）
├── facade/         # 门面：UnifiedQuoteAPI(四路由+熔断，取数委托 DataSourceRouter)/response/async_api/三兼容门面
├── web/            # 50 Source 类（惰性导入）+ 域 Mixin 会话 + _paginate 共享分页器
├── sources/        # 5 级降级路由 DataSourceRouter + golden 回放
├── domain/         # symbol(单一事实源)/models/adjust/calendar
├── streaming/      # QuoteStream/AsyncQuoteStream（轮询+diff）/push
├── reader/         # vipdoc 本地文件解析（day/min/板块/财务）
├── output/         # DataFrame/Parquet/CSV/DuckDB 原子写（v9 自 sinks/ 更名，旧名 shim 兼容）
├── sink/           # LocalDaySink：写回 vipdoc .day 二进制（与 sinks/ 职责不同）
├── charset/        # 字符集自动探测（GBK/GB18030/Big5/UTF-8；原 i18n，v8 更名定名）
├── profile/        # 数据规格探测（帧/文件双探测器 + presets）
├── config/         # 6 源合并 + 严格校验 + env 归一
├── errors.py       # 错误分类树（E1-E8，40+ 类）+ RetryAdvice
├── integration/    # http_server(42 端点白名单)/ws_server/mcp_server
├── observability/  # 指标注册表 + Prometheus/StatsD/OTLP 导出器 + start_exporter
├── feedback/       # 错误/用量上报 + 遥测 + 使用统计
├── security/       # 凭据三级存储（keyring/env/file 互斥写 + 损坏隔离）
├── tools/          # capture/spec_audit/codegen/golden_audit/golden_expand/check_originality
├── trade/          # 交易协议模拟器（独立可选：SimTransport 纯内存模拟，不连真实券商）
├── cli/            # CLI 入口（19 子命令；_common/cmds_market/cmds_web/cmds_hosts/parser）
```

## 系统文档导航

| 文档 | 内容 |
|---|---|
| [docs/FEATURE_MAP_AND_ROADMAP.md](docs/FEATURE_MAP_AND_ROADMAP.md) | 主体功能地图 + v1.2.0 后路线（I/J 批次）|
| [DESIGN.md](DESIGN.md) | 完整设计方案 v2.0（架构/协议/工程规范，历史版本见 docs/archive/）|
| [docs/api/README.md](docs/api/README.md) | API 索引（客户端/门面/服务面/工具）|
| [docs/quickstart.md](docs/quickstart.md) | 快速入门 |
| [docs/cookbook/](docs/cookbook/README.md) | 场景示例（批量 K 线/离线 vipdoc/流式/Sinks/自定义命令）|
| [docs/FAQ.md](docs/FAQ.md) · [docs/troubleshooting.md](docs/troubleshooting.md) | 常见问题与排障 |
| [docs/errors.md](docs/errors.md) | 错误体系与 RetryAdvice 使用指南（错误树速查/易混对照/扩展规则）|
| [docs/migration/](docs/migration/README.md) | 从 mootdx/easy_tdx/easyquotation 迁移 |
| [docs/adr/](docs/adr/README.md) | 架构决策记录 |
| [PROTOCOL_SPEC/](PROTOCOL_SPEC/README.md) | 协议命令 YAML 规范 + codegen/spec_audit 闭环 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |
| [docs/archive/](docs/archive/) | 历史计划与设计归档（v1 优化计划/开发计划/差距分析等）|

## 协议规范

协议命令以 YAML 描述，位于 `PROTOCOL_SPEC/`（当前 7709 族 8 条 + UNKNOWN 归档）：

```
PROTOCOL_SPEC/
├── README.md / SCHEMA.md
├── 7709/     # 标准 7709 协议族（8 条 YAML）
└── UNKNOWN/  # 自动发现未知命令（.gitkeep 占位）
```

工具链闭环：`capture(合规采集) → PROTOCOL_SPEC YAML → codegen 骨架 →
@register_parser → golden_audit 三旗标 → spec_audit 双向漂移检查`。

## 数据源降级

```
TDX 主站 → HTTP Web 源(45) → 本地 vipdoc → golden 缓存 → 合成数据
```

## 质量与门禁

```bash
pytest tests/                                   # 全量测试（76 文件）
make gates                                      # 六步门禁：lint→format→全量→对抗矩阵→golden 三旗标→可达性
python -m tstdx.tools.golden_audit --gate       # Golden L1 真实样本门禁（530 payload）
python -m pytest tests/adversarial -q           # 对抗矩阵（9 payload × 85 命令，逃逸=0）
python scripts/audit_reachability.py --strict   # 可达性门禁（孤儿=0）
```

## 贡献

请阅读：

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [GOVERNANCE.md](GOVERNANCE.md)
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [SECURITY.md](SECURITY.md)

## 许可证

MIT License - 详见 [LICENSE](LICENSE)
