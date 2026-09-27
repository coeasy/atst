# 通达信行情通用包 — 完整设计方案

> **文档状态**：v1.0 完整规划稿
> **创建日期**：2026-08-31
> **工作区**：`D:\workspace\atst`
> **参考项目**：mootdx / easy_tdx / tdx-api / tdxrs / eltdx / bebopze/tdx

---

## 目录

1. [项目定位与目标](#1-项目定位与目标)
2. [技术栈选型](#2-技术栈选型)
3. [分层架构总览](#3-分层架构总览)
4. [Transport 传输层](#4-transport-传输层)
5. [Protocol 协议层](#5-protocol-协议层)
6. [Client 客户端层](#6-client-客户端层)
7. [Reader 本地文件解析层](#7-reader-本地文件解析层)
8. [领域服务层](#8-领域服务层)
9. [API 输出层](#9-api-输出层)
10. [集成层](#10-集成层)
11. [协议覆盖清单](#11-协议覆盖清单)
12. [本地文件格式规范](#12-本地文件格式规范)
13. [复权算法设计](#13-复权算法设计)
14. [连接与并发设计](#14-连接与并发设计)
15. [数据模型与错误体系](#15-数据模型与错误体系)
16. [测试策略](#16-测试策略)
17. [分阶段实施路线图](#17-分阶段实施路线图)
18. [参考项目能力对照表](#18-参考项目能力对照表)
19. [附录：License 与合规声明](#19-附录license-与合规声明)

---

## 1. 项目定位与目标

### 1.1 项目名称

**atst** — TongDaXin Standard Data eXchange（通达信标准数据交换库）

> 命名释义：工作区目录名 `atst`，即 "TongDaXin" 的缩写，同时隐含 "Standard" 定位。

### 1.2 核心定位

| 维度 | 定义 |
|---|---|
| **是什么** | 通达信行情数据的**通用底层协议库**，非应用层框架 |
| **不是什么** | 不是回测引擎、不是选股系统、不是交易终端、不是 Web 可视化平台 |
| **类比** | 相当于 HTTP 世界的 `requests`/`axios` —— 稳定、标准、可组合的基础设施 |
| **目标用户** | 量化交易开发者、金融数据工程师、行情中间件开发者、AI Agent 工具调用 |

### 1.3 设计目标

| # | 目标 | 量化指标 | 参考来源 |
|---|---|---|---|
| G1 | **协议全覆盖** | 三套协议族（7709 标准 / 7727 扩展 / MAC 专属）+ F10 资料协议，命令覆盖率 ≥ 95% | eltdx 21 命令 + opentdx 57 解析器 |
| G2 | **高性能** | 本地文件解析 ≥ 200,000 条/秒；网络请求单次延迟 < 50ms（排除网络往返） | tdxrs 日线 0.3ms/1000条 → 3,300,000 条/秒 |
| G3 | **高可用** | 多主站 failover + 健康评分 + 指数退避重试，连续 1000 次请求成功率 ≥ 99.5% | easy_tdx 健康评分机制 |
| G4 | **多输出格式** | `list[dict]` / `list[tuple]` / `DataFrame` 三态，遍历态比 dict 态快 40-60% | tdxrs 输出三态设计 |
| G5 | **多运行时** | 同步 / 异步 双模；Python 原生 + Rust 内核加速（可选） | tdxrs PyO3 / eltdx Rust 内核 |
| G6 | **多集成** | CLI / HTTP REST / WebSocket RPC / MCP 工具服务，4 种消费方式 | tdx-api HTTP + eltdx MCP |
| G7 | **可组合** | 分层清晰，每层可独立使用：只用 Reader 解析本地文件、只用 Client 拉线上行情、只用复权引擎 | mootdx Reader/Quotes/Affair 分离 |
| G8 | **合规安全** | 明确 License，标注数据版权，禁止商业滥用风险 | eltdx License 教训 |

### 1.4 与参考项目的关系

```
atst = 取各家之长，做协议级通用基础设施

  mootdx    →  Reader 本地文件解析的简洁设计 + CLI 体验
  easy_tdx  →  四层客户端架构 + 健康评分 failover + MAC 协议覆盖
  tdx-api   →  HTTP REST 接口设计 + Docker 部署体验
  tdxrs     →  Rust 内核加速 + 输出三态 + 统一错误码
  eltdx     →  Slot 并发模型 + Helpers 分层 + F10 完整覆盖 + MCP 集成
  bebopze   →  指标公式思路参考（非协议层，仅提供量化指标设计灵感）
```

---

## 2. 技术栈选型

### 2.1 主语言：Python 3.10+

**理由**：

| 考量 | 分析 |
|---|---|
| 生态 | 6 个参考项目中 4 个是 Python（mootdx / easy_tdx / tdxrs / eltdx），协议文档和测试数据以 Python 为基准 |
| 用户场景 | 量化交易 / 金融分析领域 Python 占绝对主导，pandas/numpy 生态不可替代 |
| ABI 兼容 | 选择 3.10+ 可用 `cp310-abi3` wheel，一次编译覆盖 3.10-3.13+（参考 eltdx 策略） |
| 异步 | `asyncio` + `anyio` 双模，兼顾不同异步框架用户 |

### 2.2 性能层：Rust + PyO3/maturin（可选内核）

**定位**：**可选加速**，非强制依赖。

```
atst
├── atst              # 纯 Python 实现（默认，零编译安装）
│   └── _native        # Rust 扩展（有则加速，无则 fallback）
└── atst-native       # 独立 wheel，提供 Rust 内核
```

**加速目标**（参考 tdxrs 基准）：

| 操作 | 纯 Python | Rust 内核 | 加速比 |
|---|---|---|---|
| 日线解析 1000 条 | ~2.8ms | ~0.3ms | 9.3× |
| 分钟线解析 1000 条 | ~5.1ms | ~0.5ms | 10.2× |
| 板块解析 | ~12.0ms | ~1.2ms | 10.0× |
| 财务数据解析 | ~8.5ms | ~0.8ms | 10.6× |
| 网络请求编解码 | 基准 | ~1.3-1.5× | 1.4× |

**Rust crate 依赖**：
- `pyo3` 0.22+（Python 绑定）
- `encoding_rs`（GBK 解码，替代 Python 的 `gbk` 编码，快 5-10×）
- `zstd` / `flate2`（zlib 解压）
- `tokio`（异步运行时，用于 AsyncClient）
- `bytes`（零拷贝缓冲区管理）
- `thiserror`（错误体系）

### 2.3 辅助工具链

| 工具 | 用途 | 版本 |
|---|---|---|
| `maturin` | Rust→Python wheel 构建 | 1.7+ |
| `ruff` | Linter + Formatter | 0.6+ |
| `pytest` | 测试框架 | 8.0+ |
| `pytest-asyncio` | 异步测试 | 0.23+ |
| `mypy` | 类型检查 | 1.11+ |
| `hatch` | 项目管理 / 版本发布 | 1.12+ |
| `pre-commit` | 提交前检查 | 3.8+ |

### 2.4 集成层依赖（可选）

| 集成方式 | 依赖 | 说明 |
|---|---|---|
| HTTP REST | `fastapi` + `uvicorn` | 参考 tdx-api 32 接口设计 |
| WebSocket RPC | `websockets` | 实时行情推送 |
| MCP 工具服务 | `mcp` SDK | AI Agent 工具调用 |
| CLI | `typer` + `rich` | 命令行体验 |
| DataFrame 输出 | `pandas`（可选） | 按需导入，非硬依赖 |

### 2.5 目录结构

```
atst/
├── DESIGN.md                    # 本文档
├── PLAN.md                      # 实施路线图（从本文档拆出）
├── pyproject.toml
├── README.md
├── LICENSE
│
├── atst/                       # 主包（纯 Python）
│   ├── __init__.py
│   ├── _version.py
│   ├── errors.py                # 统一错误码体系
│   ├── types.py                 # 数据模型 / TypedDict
│   ├── constants.py             # 协议常量 / 端口 / 命令号
│   │
│   ├── transport/               # 传输层
│   │   ├── __init__.py
│   │   ├── connection.py        # TCP 连接管理
│   │   ├── pool.py              # 连接池 / Slot 管理
│   │   ├── heartbeat.py         # 心跳保活
│   │   ├── server_list.py       # 主站列表 + 测速排名
│   │   └── codec.py             # GBK 编码 / zlib 解压
│   │
│   ├── protocol/                # 协议层
│   │   ├── __init__.py
│   │   ├── base.py              # BaseParser + 注册表装饰器
│   │   ├── registry.py          # @register_parser 注册表
│   │   ├── quotation/           # 7709 标准协议
│   │   │   ├── __init__.py
│   │   │   ├── handshake.py     # 0x000d
│   │   │   ├── heartbeat.py     # 0x0004
│   │   │   ├── stock_list.py    # 0x044d / 0x044e
│   │   │   ├── kline.py         # 0x052d
│   │   │   ├── minute.py        # 0x0537 / 0x051b
│   │   │   ├── quotes.py        # 0x053e / 0x0547 / 0x054b / 0x054c
│   │   │   ├── auction.py       # 0x056a
│   │   │   ├── gbbq.py          # 0x000f 股本变迁
│   │   │   ├── finance.py       # 0x0010 财务基础
│   │   │   ├── price_limit.py   # 0x0452 涨跌停
│   │   │   ├── file_download.py # 0x06b9
│   │   │   ├── history_minute.py # 0x0fb4 / 0x0feb
│   │   │   ├── trades.py        # 0x0fc5 / 0x0fc6
│   │   │   └── sparkline.py     # 0x0fd1
│   │   ├── ex_quotation/        # 7727 扩展市场协议
│   │   │   └── ... (港股/美股/期货)
│   │   ├── mac_quotation/       # MAC 专属协议
│   │   │   └── ... (板块/资金流向/统一K线)
│   │   └── f10/                 # F10 资料协议（7615/TQLEX）
│   │       └── ... (20+ Entry)
│   │
│   ├── client/                  # 客户端层
│   │   ├── __init__.py
│   │   ├── base.py              # BaseClient
│   │   ├── standard.py          # StandardClient (7709)
│   │   ├── extended.py          # ExtendedClient (7727)
│   │   ├── mac.py               # MacClient (MAC 专属)
│   │   ├── async_client.py      # AsyncTdxClient (asyncio)
│   │   └── factory.py           # 客户端工厂 / 自动选择
│   │
│   ├── reader/                  # 本地文件解析层
│   │   ├── __init__.py
│   │   ├── base.py              # BaseReader
│   │   ├── daily.py             # .day 日线
│   │   ├── minute.py            # .lc1/.lc5 分钟线
│   │   ├── block.py             # .dat 板块文件
│   │   └── financial.py         # gpcw*.dat 财务数据
│   │
│   ├── services/                # 领域服务层
│   │   ├── __init__.py
│   │   ├── adjust.py            # 复权引擎
│   │   ├── cache.py             # 内存/磁盘缓存
│   │   ├── ratelimit.py         # 请求限流
│   │   ├── downloader.py        # 财务数据下载
│   │   └── retry.py             # 重试 / failover
│   │
│   ├── api/                     # API 输出层
│   │   ├── __init__.py
│   │   ├── output.py            # 输出格式转换 (dict/tuple/df)
│   │   └── helpers.py           # 高级组合封装
│   │
│   └── integration/             # 集成层
│       ├── cli.py               # CLI
│       ├── http_server.py       # HTTP REST (FastAPI)
│       ├── ws_server.py         # WebSocket RPC
│       └── mcp_server.py        # MCP 工具服务
│
├── atst_native/                 # Rust 内核（可选）
│   ├── Cargo.toml
│   ├── src/
│   │   ├── lib.rs                # PyO3 入口
│   │   ├── transport.rs          # 连接/编解码加速
│   │   ├── reader.rs             # 文件解析加速
│   │   └── protocol.rs           # 协议解析加速
│   └── tests/
│
├── tests/
│   ├── unit/
│   ├── protocol/                 # 协议回放测试
│   ├── reader/                   # golden file 测试
│   ├── integration/              # 集成测试（需网络）
│   ├── golden/                   # golden test data
│   │   ├── day/
│   │   ├── lc1/
│   │   └── protocol/
│   └── conftest.py
│
├── benches/                      # 性能基准
│   ├── bench_reader.py
│   ├── bench_protocol.py
│   └── bench_compare.py          # 纯 Python vs Rust 对比
│
├── examples/
│   ├── 01_basic_quotes.py
│   ├── 02_kline.py
│   ├── 03_local_reader.py
│   ├── 04_adjust.py
│   ├── 05_async.py
│   ├── 06_http_api.py
│   ├── 07_mcp_tool.py
│   └── 08_full_workflow.py
│
└── docs/
    ├── protocol/                 # 协议文档
    │   ├── 7709_commands.md
    │   ├── 7727_commands.md
    │   ├── mac_commands.md
    │   └── f10_entries.md
    ├── file_format/             # 文件格式文档
    │   ├── day_format.md
    │   ├── lc1_lc5_format.md
    │   └── block_dat_format.md
    └── architecture.md          # 架构详解
```

---

## 3. 分层架构总览

### 3.1 七层架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        集成层 (Integration)                          │
│    CLI  ·  HTTP REST  ·  WebSocket RPC  ·  MCP Tool Service         │
├─────────────────────────────────────────────────────────────────────┤
│                        API 输出层 (API)                              │
│    Helpers 组合封装  ·  输出三态 (dict / tuple / DataFrame)           │
├─────────────────────────────────────────────────────────────────────┤
│                    领域服务层 (Domain Services)                      │
│  复权引擎  ·  缓存  ·  限流  ·  下载器  ·  重试/failover             │
├─────────────────────────────────────────────────────────────────────┤
│                      Client 客户端层                                │
│  StandardClient(7709) · ExtendedClient(7727) · MacClient(MAC)       │
│  AsyncTdxClient · 工厂自动选择                                       │
├─────────────────────────────────────────────────────────────────────┤
│                     Protocol 协议层 (核心)                           │
│  BaseParser + @register_parser 注册表                                │
│  ┌──────────────┬──────────────┬──────────────┬──────────────────┐  │
│  │ quotation/   │ ex_quotation/│ mac_quotation│     f10/         │  │
│  │  (7709)      │   (7727)     │  (MAC专属)   │  (7615/TQLEX)  │  │
│  │  ~24 parsers │  ~17 parsers │  ~16 parsers │  ~20 entries    │  │
│  └──────────────┴──────────────┴──────────────┴──────────────────┘  │
├─────────────────────────────────────────────────────────────────────┤
│                     Transport 传输层                                 │
│  TCP 连接 · 连接池/Slot · 心跳保活 · 主站测速排名 · GBK/zlib 编解码  │
├─────────────────────────────────────────────────────────────────────┤
│                     Reader 本地文件解析层                            │
│  .day 日线 · .lc1/.lc5 分钟线 · .dat 板块 · gpcw*.dat 财务          │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 数据流

#### 3.2.1 线上行情数据流

```
用户调用 client.quotes("600519")
    │
    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Client 层    │────▶│  Protocol 层  │────▶│ Transport 层  │
│  方法路由     │     │  请求打包     │     │  TCP 发送     │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                   │
                                           TDX 主站 (7709/7727)
                                                   │
┌──────────────┐     ┌──────────────┐     ┌──────▼───────┐
│  API 输出层   │◀────│  Protocol 层  │◀────│ Transport 层  │
│  格式转换     │     │  响应解析     │     │  TCP 接收     │
└──────────────┘     └──────────────┘     └──────────────┘
    │
    ▼
  list[dict] / list[tuple] / DataFrame
```

#### 3.2.2 本地文件数据流

```
用户调用 reader.daily("sh600519")
    │
    ▼
┌──────────────┐     ┌──────────────┐
│  Reader 层    │────▶│  文件系统     │
│  路径拼接     │     │  .day 文件    │
└──────┬───────┘     └──────────────┘
       │
       ▼
┌──────────────┐     ┌──────────────┐
│  二进制解析   │────▶│  API 输出层   │
│  32B/record  │     │  格式转换     │
└──────────────┘     └──────────────┘
```

### 3.3 核心设计原则

| # | 原则 | 说明 | 参考来源 |
|---|---|---|---|
| P1 | **协议注册表** | `@register_parser(msg_id, ...)` 装饰器自动注册，新增协议零侵入扩展 | opentdx |
| P2 | **分层可独立** | 每层零上层依赖：只用 Reader 不需 Client，只用 Client 不需 HTTP | mootdx |
| P3 | **输出三态** | 所有数据方法支持 `output="dict"/"tuple"/"dataframe"` | tdxrs |
| P4 | **同步异步同形** | `client.quotes()` 与 `async_client.quotes()` 签名一致 | easy_tdx |
| P5 | **健康评分 failover** | 每台主站维护健康分，自动降级/恢复 | easy_tdx |
| P6 | **Slot 并发** | `服务器数 × 连接数 = Slot 总数`，显式控制并发上限 | eltdx |
| P7 | **口径可配置** | `coefficient` / `volume_unit` 等参数应对版本差异 | tdxrs |
| P8 | **渐进加速** | 纯 Python 可用，Rust 内核有则加速无则 fallback | tdxrs |

---

## 4. Transport 传输层

### 4.1 主站列表与测速

```python
# 默认主站列表（内置 43 台候选，参考 eltdx）
DEFAULT_HOSTS_7709 = [
    ("115.238.56.198", 7709),  # 杭州
    ("115.238.90.165", 7709),  # 杭州
    ("117.184.140.156", 7709),  # 上海
    ("221.231.141.143", 7709),  # 南京
    # ... 共 43 台
]

DEFAULT_HOSTS_7727 = [
    ("111.202.155.114", 7727),  # 北京
    ("117.184.140.156", 7727),  # 上海
    # ... 共 16 台
]

MAC_HOSTS_7709 = [
    ("115.238.56.198", 7709),  # MAC 专属服务器仅 3 台
    ("117.184.140.156", 7709),
    ("221.231.141.143", 7709),
]
```

**测速流程**：

```
1. 对每台主站发起 TCP connect（超时 3s）
2. 记录连接延迟（ms）
3. 按延迟排序，取 Top N
4. 持久化到 ~/.atst/server_ranking.json
5. 后续连接优先使用排名最高的主站
```

```python
class ServerRanking:
    """主站测速排名与持久化"""

    def __init__(self, hosts: list[tuple[str, int]], cache_path: Path | None = None):
        self.hosts = hosts
        self.cache_path = cache_path or Path.home() / ".atst" / "server_ranking.json"
        self._scores: dict[str, float] = {}  # host:health_score

    async def benchmark(self, top_n: int = 5, timeout: float = 3.0) -> list[tuple[str, int, float]]:
        """测速并返回 Top N 主站列表"""

    def save(self):
        """持久化排名到 JSON"""

    def load(self):
        """从 JSON 加载历史排名"""
```

### 4.2 连接池与 Slot 模型

```python
@dataclass
class SlotConfig:
    """Slot 并发配置（参考 eltdx）"""

    num_servers: int = 2  # 使用主站数量
    connections_per_server: int = 4  # 每台主站 TCP 连接数
    # 总 Slot = 2 × 4 = 8（默认）
    # 可扩展到 20 × 8 = 160 Slot

    @property
    def total_slots(self) -> int:
        return self.num_servers * self.connections_per_server


class ConnectionPool:
    """连接池：管理 Slot 级别的 TCP 连接"""

    def __init__(self, config: SlotConfig, ranking: ServerRanking):
        self.config = config
        self.ranking = ranking
        self._slots: asyncio.Queue[TdxConnection] | None = None
        self._health: dict[str, float] = {}  # host -> health_score

    async def acquire(self) -> TdxConnection:
        """获取一个可用连接（健康评分最高的 Slot）"""

    async def release(self, conn: TdxConnection, success: bool):
        """归还连接，更新健康评分"""
        # 失败 ×0.5，成功 +0.2，上限 1.0
```

### 4.3 心跳保活

```python
class Heartbeat:
    """心跳保活，默认 30 秒"""

    interval: float = 30.0  # 秒
    command: int = 0x0004  # 心跳命令号

    async def start(self, conn: TdxConnection):
        """启动心跳定时器"""

    async def stop(self):
        """停止心跳"""
```

### 4.4 GBK 编码与 zlib 解压

```python
class Codec:
    """编码解码工具"""

    @staticmethod
    def gbk_decode(data: bytes) -> str:
        """GBK 解码（Rust 内核使用 encoding_rs，快 5-10×）"""

    @staticmethod
    def zlib_decompress(data: bytes) -> bytes:
        """zlib 解压（部分响应需解压）"""

    @staticmethod
    def pack_header(msg_id: int, body: bytes) -> bytes:
        """打包请求头"""

    @staticmethod
    def unpack_header(data: bytes) -> tuple[int, bytes]:
        """解析响应头"""
```

---

## 5. Protocol 协议层

### 5.1 注册表模式（核心设计）

参考 opentdx 的 `@register_parser` 装饰器，实现协议解析器的**可插拔注册**：

```python
# protocol/base.py


class BaseParser:
    """协议解析器基类"""

    # 子类定义
    msg_id: int  # 命令号，如 0x052d
    head: bytes  # 响应头标识
    customize: bool = False  # 是否自定义解析逻辑
    need_zip: bool = False  # 响应是否需要 zlib 解压

    @classmethod
    def pack_request(cls, **kwargs) -> bytes:
        """打包请求体"""
        raise NotImplementedError

    @classmethod
    def parse_response(cls, body: bytes, *, include_raw: bool = False) -> list[dict]:
        """解析响应体"""
        raise NotImplementedError


# protocol/registry.py

_PARSER_REGISTRY: dict[tuple[int, bytes], type[BaseParser]] = {}


def register_parser(msg_id: int, head: bytes, customize: bool = False, need_zip: bool = False):
    """
    注册协议解析器装饰器

    用法：
        @register_parser(msg_id=0x052d, head=b'\x04\x00')
        class KLineParser(BaseParser):
            ...
    """

    def decorator(cls):
        key = (msg_id, head)
        _PARSER_REGISTRY[key] = cls
        cls.msg_id = msg_id
        cls.head = head
        cls.customize = customize
        cls.need_zip = need_zip
        return cls

    return decorator


def get_parser(msg_id: int, head: bytes) -> type[BaseParser] | None:
    """根据命令号和响应头查找解析器"""
    return _PARSER_REGISTRY.get((msg_id, head))


def list_parsers() -> dict[str, list[int]]:
    """列出所有已注册的解析器（用于 CLI 和文档生成）"""
```

**注册示例**：

```python
# protocol/quotation/kline.py


@register_parser(msg_id=0x052D, head=b"\x04\x00")
class KLineParser(BaseParser):
    """K线/周期线 0x052d"""

    @classmethod
    def pack_request(cls, *, market: int, code: str, period: int, start: int, count: int) -> bytes:
        # 打包请求：市场(2) + 代码(6) + 周期(2) + 起始位置(2) + 数量(2)
        ...

    @classmethod
    def parse_response(cls, body: bytes, *, include_raw: bool = False) -> list[dict]:
        # 解析 N 条 K 线记录，每条 32 字节
        ...
```

### 5.2 三套协议族对照

| 协议族 | 端口 | 命令号范围 | 解析器数量 | 覆盖市场 | 对应 Client |
|---|---|---|---|---|---|
| `quotation` (标准) | 7709 | 0x00–0x1F (实际 0x000d–0x0fd1) | ~24 | A 股 / 基金 / 指数 / B 股 | StandardClient |
| `ex_quotation` (扩展) | 7727 | 0x00–0x12 | ~17 | 港股 / 美股 / 期货 | ExtendedClient |
| `mac_quotation` (MAC) | 7709 (专属服务器) | 0x120F–0x2562 | ~16 | 板块 / 资金流向 / 统一行情 | MacClient |
| `f10` (资料) | 7615 / TQLEX | Entry 字符串 | ~20 | F10 公司资料 | F10Client (内嵌) |

### 5.3 协议层目录与解析器清单

#### 5.3.1 quotation/ (7709 标准协议)

| 文件 | 命令号 | 功能 | 状态 |
|---|---|---|---|
| `handshake.py` | `0x000d` | 握手（服务端日期时间、交易时段、主站名） | MVP |
| `heartbeat.py` | `0x0004` | 心跳 | MVP |
| `stock_list.py` | `0x044d` | 代码表 / A 股数量 | MVP |
| `stock_list.py` | `0x044e` | 市场代码数量 | MVP |
| `kline.py` | `0x052d` | K 线 / 周期线 | MVP |
| `minute.py` | `0x0537` | 当日分时 | MVP |
| `minute.py` | `0x051b` | 分时副图 | P1 |
| `quotes.py` | `0x053e` | 旧版批量行情（原生五档） | MVP |
| `quotes.py` | `0x0547` | 五档刷新 / 推送队列 | P1 |
| `quotes.py` | `0x054b` | 分类行情（按市场/板块分页排序） | P1 |
| `quotes.py` | `0x054c` | 批量行情快照 | MVP |
| `auction.py` | `0x056a` | 集合竞价过程快照 | P1 |
| `gbbq.py` | `0x000f` | 股本变迁 / 除权除息 (GBBQ) | P1 |
| `finance.py` | `0x0010` | 财务基础信息 | P2 |
| `price_limit.py` | `0x0452` | 特殊品种涨跌停限制 | P1 |
| `file_download.py` | `0x06b9` | 服务器文件分块读取/下载 | P2 |
| `history_minute.py` | `0x0fb4` | 指定日期历史分时 | P1 |
| `history_minute.py` | `0x0feb` | 近期历史分时 | P1 |
| `trades.py` | `0x0fc5` | 当日成交明细 | P1 |
| `trades.py` | `0x0fc6` | 历史成交明细 | P1 |
| `sparkline.py` | `0x0fd1` | 小走势图 (sparkline) | P2 |

#### 5.3.2 ex_quotation/ (7727 扩展市场协议)

| 功能 | 状态 | 说明 |
|---|---|---|
| 港股代码表 | P1 | `0x044d` 扩展版 |
| 港股 K 线 | P1 | `0x052d` 扩展版 |
| 港股行情 | P1 | `0x054c` 扩展版 |
| 港股分时 | P1 | `0x0537` 扩展版 |
| 美股代码表 | P2 | — |
| 美股 K 线 | P2 | — |
| 美股行情 | P2 | — |
| 期货 K 线 | P2 | — |
| 期货行情 | P2 | — |

#### 5.3.3 mac_quotation/ (MAC 专属协议)

| 功能 | 命令号范围 | 状态 | 说明 |
|---|---|---|---|
| 板块列表 | `0x12xx` | P1 | 概念/指数/风格板块 |
| 板块成分股 | `0x12xx` | P1 | 板块内股票列表 |
| 统一 K 线 | `0x12xx` | P1 | 跨市场统一 K 线 |
| 统一行情 | `0x12xx` | P1 | 跨市场统一报价 |
| 资金流向 | `0x25xx` | P2 | 主力资金 |
| 主力监控 | `0x25xx` | P2 | 主力动向 |
| 竞价 | `0x12xx` | P2 | MAC 版竞价 |
| 多日分时 | `0x12xx` | P2 | 多日分时图 |

#### 5.3.4 f10/ (F10 资料协议 7615/TQLEX)

| Entry | 功能 | 状态 |
|---|---|---|
| `tdxf10_gg_comreq` | F10 请求入口 | P1 |
| `tdxf10_gg_gsgk` | 公司概况 | P1 |
| `tdxf10_gg_jyfx` | 主营构成 | P2 |
| `tdxf10_gg_gdyj` | 股东增减持 | P2 |
| `tdxf10_gg_fhrz` | 分红融资 | P2 |
| `tdxf10_gg_cwfx` | 财务报表 | P2 |
| `tdxf10_gg_cwzd` | 财务诊断 | P3 |
| `tdxf10_gg_ggzp` | 个股总评 | P2 |
| `tdxf10_gg_ybpj` | 盈利预测 | P2 |
| `tdxf10_gg_rdtc` | 热点题材 | P2 |
| `tdxf10_gg_zlcc` | 沪深股通持仓 | P2 |
| `tdxf10_gg_gszx` | 公司资讯研报 | P2 |
| `tdxf10_gg_idreq` | 详情正文 | P2 |
| `tdxf10_gg_zxts_rqpm` | 排名 | P3 |
| `tdxf10_gg_zbyz` | 资本运作治理 | P3 |
| `hq_nlp_tcihq` | 题材概念行情 | P3 |
| `hq_nlp_gpsj` | 估值市场数据 | P3 |
| `tzx_rcache` | 新闻/公告/路演 | P3 |

---

## 6. Client 客户端层

### 6.1 客户端矩阵

```
┌─────────────────────────────────────────────────────────┐
│                    BaseClient                           │
│  · 传输层初始化  · 健康评分  · 重试/failover            │
├─────────────┬──────────────┬──────────────┬────────────┤
│ Standard    │ Extended     │ MacClient    │ AsyncTdx   │
│ Client      │ Client       │              │ Client     │
│ (7709)      │ (7727)       │ (MAC专属)    │ (asyncio)  │
├─────────────┼──────────────┼──────────────┼────────────┤
│ A股/基金/   │ 港股/美股/   │ 板块/资金    │ 全协议     │
│ 指数/B股    │ 期货         │ 流向/统一行情│ 异步版     │
└─────────────┴──────────────┴──────────────┴────────────┘
```

### 6.2 StandardClient (7709)

```python
class StandardClient(BaseClient):
    """标准协议客户端 (端口 7709)

    覆盖：A 股 / 基金 / 指数 / B 股
    """

    PORT = 7709

    # --- 基础 ---
    def handshake(self) -> HandshakeResult:
        """0x000d 握手"""

    def heartbeat(self) -> bool:
        """0x0004 心跳"""

    # --- 股票列表 ---
    def stock_list(self, market: int = 0) -> list[StockInfo]:
        """0x044d 获取股票列表"""

    def market_count(self, market: int = 0) -> int:
        """0x044e 市场代码数量"""

    # --- K 线 ---
    def kline(
        self, code: str, *, period: int = 4, count: int = 800, start: int = 0, output: str = "dict"
    ) -> KLineResult:
        """0x052d K 线/周期线

        Args:
            code: 股票代码，如 "600519"
            period: 周期
                0=5分钟 1=15分钟 2=30分钟 3=60分钟
                4=日线 5=周线 6=月线 7=1分钟 8=1分钟
                9=日线 10=季线 11=年线
            count: 请求数量
            start: 起始位置（0=最近）
            output: 输出格式 "dict"|"tuple"|"dataframe"
        """

    # --- 分时 ---
    def minute(self, code: str, *, output: str = "dict") -> MinuteResult:
        """0x0537 当日分时"""

    def history_minute(self, code: str, date: str, *, output: str = "dict") -> MinuteResult:
        """0x0fb4 指定日期历史分时"""

    # --- 行情 ---
    def quotes(self, codes: list[str], *, output: str = "dict") -> QuoteResult:
        """0x054c 批量行情快照（单次上限 60 只）"""

    def category_quotes(
        self, market: int = 0, *, category: int = 0, output: str = "dict"
    ) -> QuoteResult:
        """0x054b 分类行情（按市场/板块分页排序）"""

    # --- 竞价 ---
    def auction(self, code: str, *, output: str = "dict") -> AuctionResult:
        """0x056a 集合竞价过程快照"""

    # --- 股本变迁 ---
    def gbbq(self, code: str, *, output: str = "dict") -> GBBQResult:
        """0x000f 股本变迁/除权除息"""

    # --- 成交明细 ---
    def trades(self, code: str, *, date: str | None = None, output: str = "dict") -> TradeResult:
        """0x0fc5/0x0fc6 成交明细"""

    # --- 涨跌停 ---
    def price_limit(self, code: str) -> PriceLimitResult:
        """0x0452 特殊品种涨跌停限制"""
```

### 6.3 ExtendedClient (7727)

```python
class ExtendedClient(BaseClient):
    """扩展市场协议客户端 (端口 7727)

    覆盖：港股 / 美股 / 期货
    """

    PORT = 7727
    # API 与 StandardClient 同形，但连接 7727 端口
    # 市场 ID 不同：
    #   A 股: market=0 (沪) / 1 (深)
    #   港股: market=2
    #   美股: market=3
```

### 6.4 MacClient (MAC 专属)

```python
class MacClient(BaseClient):
    """MAC 专属协议客户端 (端口 7709，但连接 MAC 专属服务器)

    覆盖：板块列表 / 成分股 / 统一K线 / 统一行情 / 资金流向 / 主力监控
    """

    PORT = 7709
    HOSTS = MAC_HOSTS_7709  # 仅 3 台专属服务器

    def block_list(self, *, block_type: str = "gn", output: str = "dict") -> BlockListResult:
        """板块列表（概念/指数/风格）"""

    def block_stocks(self, block_id: str, *, output: str = "dict") -> list[StockInfo]:
        """板块成分股"""

    def unified_kline(
        self, code: str, *, period: int = 4, count: int = 800, output: str = "dict"
    ) -> KLineResult:
        """统一 K 线（跨市场）"""

    def unified_quotes(self, codes: list[str], *, output: str = "dict") -> QuoteResult:
        """统一行情（跨市场）"""

    def capital_flow(self, code: str, *, output: str = "dict") -> CapitalFlowResult:
        """资金流向"""
```

### 6.5 AsyncTdxClient (异步版)

```python
class AsyncTdxClient:
    """异步客户端，与同步版 API 同形

    所有方法都是 async def，签名与 StandardClient 一致
    """

    def __init__(self, *, slot_config: SlotConfig | None = None):
        self._pool = ConnectionPool(slot_config or SlotConfig(), ...)

    async def handshake(self) -> HandshakeResult: ...
    async def quotes(self, codes: list[str], *, output: str = "dict") -> QuoteResult: ...
    async def kline(
        self, code: str, *, period: int = 4, count: int = 800, output: str = "dict"
    ) -> KLineResult: ...

    # ... 其余方法同形
```

### 6.6 客户端工厂

```python
def create_client(
    market: str = "auto", *, async_mode: bool = False, slot_config: SlotConfig | None = None
) -> BaseClient:
    """
    自动选择客户端

    Args:
        market: "a"=A股, "hk"=港股, "us"=美股, "auto"=自动判断
        async_mode: 是否使用异步客户端
        slot_config: Slot 并发配置
    """
```

---

## 7. Reader 本地文件解析层

### 7.1 Reader 矩阵

| Reader | 文件 | 格式 | 记录大小 | 参考 |
|---|---|---|---|---|
| `DailyBarReader` | `.day` | 日线 | 32 字节/条 | mootdx / tdxrs |
| `MinBarReader` | `.lc1` | 1 分钟线 | 32 字节/条 | mootdx / tdxrs |
| `LcMinBarReader` | `.lc5` | 5 分钟线 | 32 字节/条 | mootdx / tdxrs |
| `BlockReader` | `.dat` | 板块文件 | 不定长 | mootdx / tdxrs |
| `FinancialReader` | `gpcw*.dat` | 财务数据 | f32 字段数组 | mootdx / tdxrs |

### 7.2 DailyBarReader 设计

```python
class DailyBarReader(BaseReader):
    """日线文件解析器

    文件路径规则：
        沪市: vipdoc/sh/lday/sh{code}.day
        深市: vipdoc/sz/lday/sz{code}.day
        北郊: vipdoc/bj/lday/bj{code}.day

    记录格式（32 字节/条，小端序）：
        0-3   uint32  日期 YYYYMMDD
        4-7   uint32  开盘价 × coefficient (默认 0.01)
        8-11  uint32  最高价 × coefficient
        12-15 uint32  最低价 × coefficient
        16-19 uint32  收盘价 × coefficient
        20-23 float32 成交额（元）
        24-27 uint32  成交量（单位由 volume_unit 决定）
        28-31 uint32  上日收盘 × coefficient（期货为持仓量）
    """

    def __init__(
        self, vipdoc_path: str | Path, *, coefficient: float = 0.01, volume_unit: str = "share"
    ):
        """
        Args:
            vipdoc_path: vipdoc 根目录路径
            coefficient: 价格缩放系数
                - 0.01 (默认): 原始值 ×100，多数市场适用
                - 0.001: 原始值 ×1000，部分版本/品种可能使用
            volume_unit: 成交量单位
                - "share" (默认): 成交量单位为「股」
                - "lot": 成交量单位为「手」（1手=100股）
        """

    def read(
        self,
        code: str,
        *,
        market: str = "sh",
        start: str | None = None,
        end: str | None = None,
        output: str = "dict",
    ) -> list[dict] | list[tuple]:
        """读取日线数据

        Args:
            code: 股票代码，如 "600519"
            market: 市场 "sh"/"sz"/"bj"
            start: 起始日期 YYYYMMDD
            end: 结束日期 YYYYMMDD
            output: "dict" | "tuple" | "dataframe"
        """

    def read_all(self, market: str = "sh", output: str = "dict") -> dict[str, list]:
        """读取某市场全部股票日线"""
```

### 7.3 MinBarReader 设计

```python
class MinBarReader(BaseReader):
    """分钟线文件解析器

    文件路径规则：
        1分钟: vipdoc/{market}/minline/{market}{code}.lc1
        5分钟: vipdoc/{market}/fzline/{market}{code}.lc5

    记录格式（32 字节/条，小端序）：
        0-1   uint16  日期编码（解码见下）
        2-3   uint16  从 0 点起的分钟数
        4-7   float32 开盘价
        8-11  float32 最高价
        12-15 float32 最低价
        16-19 float32 收盘价
        20-23 float32 成交额
        24-27 uint32  成交量（股）
        28-31 保留

    日期解码：
        year  = floor(num / 2048) + 2004
        month = floor(mod(num, 2048) / 100)
        day   = mod(mod(num, 2048), 100)
    """

    def __init__(
        self,
        vipdoc_path: str | Path,
        *,
        period: str = "1min",  # "1min" | "5min"
        price_type: str = "float",
    ):
        """
        Args:
            price_type: 价格类型
                - "float" (默认): float32，多数版本适用
                - "int": int 类型，单位「分」，部分版本可能使用
        """
```

### 7.4 BlockReader 设计

```python
class BlockReader(BaseReader):
    """板块文件解析器

    文件路径：T0002/hq_cache/

    文件列表：
        block_gn.dat  概念板块
        block_zs.dat  指数板块
        block_fg.dat  风格板块

    格式：不定长记录，支持 flat / group 两种模式
    """

    def read(self, block_file: str, *, mode: str = "flat", output: str = "dict") -> list[dict]:
        """
        Args:
            block_file: "gn" | "zs" | "fg" 或完整路径
            mode: "flat" | "group"
        """
```

### 7.5 FinancialReader 设计

```python
class FinancialReader(BaseReader):
    """财务数据文件解析器

    文件：gpcw{date}.dat（需通过 Affair 下载）

    格式：f32 字段数组，字段定义见财务字段表
    """

    def read(
        self, gpcw_file: str | Path, *, fields: list[str] | None = None, output: str = "dict"
    ) -> list[dict]:
        """读取财务数据"""
```

---

## 8. 领域服务层

### 8.1 复权引擎

```python
class AdjustEngine:
    """复权计算引擎

    数据来源：0x000f GBBQ 股本变迁/除权除息事件
    复权模式：
        - qfq: 前复权（最常用）
        - hfq: 后复权
        - fixed_qfq: 定点前复权（以指定日期为基准）
        - fixed_hfq: 定点后复权
    """

    def __init__(self, client: BaseClient):
        self._gbbq_cache: dict[str, list[GBBQEvent]] = {}

    def adjust(
        self, kline: list[dict], code: str, *, mode: str = "qfq", anchor_date: str | None = None
    ) -> list[dict]:
        """对 K 线数据进行复权

        Args:
            kline: 原始 K 线数据
            code: 股票代码
            mode: "qfq"|"hfq"|"fixed_qfq"|"fixed_hfq"
            anchor_date: 定点复权的基准日期 (YYYYMMDD)
        """

    def _get_gbbq(self, code: str) -> list[GBBQEvent]:
        """获取除权除息事件（带缓存）"""
        if code not in self._gbbq_cache:
            raw = self._client.gbbq(code)
            self._gbbq_cache[code] = self._parse_gbbq(raw)
        return self._gbbq_cache[code]
```

**复权公式（A 股标准）**：

```
前复权（qfq）：
  调整因子 f = (送转股率 + 配股率 × 配股价/市价 + 分红率) / (1 + 配股率)
  adjusted_price = raw_price × f

后复权（hfq）：
  从最早日期开始累积调整因子

定点复权：
  以 anchor_date 的收盘价为基准，向前/向后调整
```

### 8.2 缓存

```python
class Cache:
    """多级缓存"""

    def __init__(
        self,
        *,
        memory_max: int = 256,  # MiB
        disk_path: Path | None = None,
        disk_max: int = 1024,
    ):  # MiB
        self._memory: LRUCache = LRUCache(memory_max)
        self._disk: DiskCache | None = DiskCache(disk_path, disk_max) if disk_path else None

    def get(self, key: str) -> Any | None:
        """先查内存，再查磁盘"""

    def set(self, key: str, value: Any, *, ttl: int | None = None):
        """写入缓存"""

    def invalidate(self, pattern: str | None = None):
        """按模式失效缓存"""
```

### 8.3 请求限流

```python
class RateLimiter:
    """交易时段自适应限流

    限流策略：
        - 盘中（9:25-15:00）: 15 req/s
        - 盘前盘后: 30 req/s
        - 休市: 60 req/s
    """

    TRADING_HOURS = {
        "pre_market": (915, 925),  # 9:15-9:25
        "auction": (925, 930),  # 9:25-9:30
        "morning": (930, 1130),  # 9:30-11:30
        "afternoon": (1300, 1500),  # 13:00-15:00
        "post_market": (1500, 1530),  # 15:00-15:30
    }

    def __init__(
        self, *, in_session: float = 15.0, off_session: float = 60.0, pre_post: float = 30.0
    ): ...

    async def acquire(self):
        """获取令牌（阻塞直到允许）"""

    def _current_limit(self) -> float:
        """根据当前时间返回限流值"""
```

### 8.4 下载器

```python
class Downloader:
    """财务数据下载器

    功能：
        - 多服务器分发下载（轮询 / 并行）
        - 自动翻页
        - 增量更新（仅下载新增日期）
        - 断点续传
    """

    async def download_financial(
        self, *, date: str | None = None, dest: Path | None = None
    ) -> Path:
        """下载 gpcw*.dat 财务数据文件"""

    async def download_block(self, *, dest: Path | None = None) -> Path:
        """下载板块数据文件"""
```

### 8.5 重试与 failover

```python
class RetryPolicy:
    """重试策略

    策略：
        - 指数退避：0.1s → 0.5s → 1.0s → 2.0s
        - 最大重试次数：4
        - 跨主站 failover：失败后切换到下一台主站
    """

    BACKOFF_FACTORS = (0.1, 0.5, 1.0, 2.0)
    MAX_RETRIES = 4

    async def execute(self, func, *args, **kwargs):
        """执行带重试的函数调用"""
```

---

## 9. API 输出层

### 9.1 输出三态

```python
def to_output(data: list[dict], output: str = "dict") -> OutputResult:
    """
    统一输出格式转换

    Args:
        data: 原始解析数据 (list[dict])
        output: 输出格式
            - "dict": list[dict]（默认，调试友好）
            - "tuple": list[tuple]（遍历快 40-60%）
            - "dataframe": pandas.DataFrame（分析回测友好）
    """
    if output == "dict":
        return data
    elif output == "tuple":
        return [tuple(d.values()) for d in data]
    elif output == "dataframe":
        try:
            import pandas as pd

            return pd.DataFrame(data)
        except ImportError:
            raise TstdxError("pandas not installed, use output='dict' or 'tuple'")
    else:
        raise ValueError(f"Unknown output format: {output}")
```

### 9.2 Helpers 高级组合封装

参考 eltdx 的 Helpers 层，组合多个低级 API 为**一步到位的高级方法**：

```python
class Helpers:
    """高级组合封装，一步到位"""

    def __init__(self, client: BaseClient):
        self._client = client

    def full_quotes(
        self, codes: list[str], *, include_gbbq: bool = True, output: str = "dict"
    ) -> FullQuoteResult:
        """完整行情（行情 + 涨跌停 + 股本变迁）

        组合：
            quotes(0x054c) + price_limit(0x0452) + gbbq(0x000f)
        """

    def auction_data(self, code: str, *, output: str = "dict") -> AuctionDataResult:
        """竞价数据（竞价 + 前一日收盘）

        组合：
            auction(0x056a) + quotes(0x054c)
        """

    def stock_topics(self, code: str, *, output: str = "dict") -> TopicResult:
        """股票题材（板块 + 资金流向）

        组合：
            MacClient.block_list + MacClient.capital_flow
        """

    def shortline_indicators(self, code: str, *, output: str = "dict") -> IndicatorResult:
        """短线指标（K线 + 分时 + 成交明细）

        组合：
            kline(0x052d) + minute(0x0537) + trades(0x0fc5)
        """

    def realtime_rank(
        self,
        *,
        market: int = 0,
        sort_by: str = "change_pct",
        count: int = 100,
        output: str = "dict",
    ) -> RankResult:
        """实时涨幅排名

        组合：
            category_quotes(0x054b) + 排序
        """

    def latest_stock_list(self, *, output: str = "dict") -> list[StockInfo]:
        """最新股票列表

        组合：
            stock_list(0x044d) + market_count(0x044e)
        """

    def stock_profile_table(self, code: str, *, output: str = "dict") -> ProfileResult:
        """个股概况表

        组合：
            F10 gsgk + quotes + gbbq
        """
```

---

## 10. 集成层

### 10.1 CLI

```python
# 用法示例
"""
# 获取行情
atst quotes 600519 000001

# 获取 K 线
atst kline 600519 --period daily --count 100 --output dataframe

# 读取本地文件
atst read-day 600519 --vipdoc /path/to/vipdoc

# 复权
atst adjust 600519 --mode qfq

# 主站测速
atst benchmark --top 5

# 启动 HTTP 服务
atst serve --port 8000

# 启动 MCP 服务
atst mcp
"""
```

### 10.2 HTTP REST API

参考 tdx-api 的 32 接口设计：

```
GET  /api/quote?codes=600519,000001        # 批量行情
GET  /api/kline?code=600519&period=4&count=100  # K 线
GET  /api/minute?code=600519               # 分时
GET  /api/trade?code=600519&date=20260830  # 成交明细
GET  /api/search?keyword=贵州              # 搜索
POST /api/batch-quote                      # 批量行情（POST）
GET  /api/kline-all?code=600519            # 全量 K 线
GET  /api/index?code=000001                # 指数
GET  /api/market-stats                     # 市场统计
GET  /api/workday                          # 交易日历
GET  /api/income?code=600519               # 财务
GET  /api/tasks                            # 异步任务列表
GET  /api/tasks/{id}                       # 异步任务状态
GET  /api/server-status                    # 服务器状态
GET  /api/health                           # 健康检查

# MAC 协议
GET  /api/blocks?type=gn                  # 板块列表
GET  /api/block-stocks?block=xxx           # 板块成分股
GET  /api/capital-flow?code=600519         # 资金流向

# F10
GET  /api/f10/profile?code=600519          # 公司概况
GET  /api/f10/finance?code=600519          # 财务报表
```

### 10.3 WebSocket RPC

```python
# 实时行情推送
"""
WS 连接后订阅：
    {"action": "subscribe", "codes": ["600519", "000001"], "type": "quote"}
    {"action": "subscribe", "codes": ["600519"], "type": "kline", "period": "1min"}

服务端推送：
    {"type": "quote", "code": "600519", "data": {...}}
    {"type": "kline", "code": "600519", "data": {...}}
"""
```

### 10.4 MCP 工具服务

```python
# MCP 工具定义（供 AI Agent 调用）
MCP_TOOLS = [
    {
        "name": "atst_quotes",
        "description": "获取A股/港股/美股实时行情",
        "inputSchema": {
            "type": "object",
            "properties": {
                "codes": {"type": "array", "items": {"type": "string"}},
                "market": {"type": "string", "enum": ["a", "hk", "us"]}
            },
            "required": ["codes"]
        }
    },
    {
        "name": "atst_kline",
        "description": "获取K线数据",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "period": {"type": "string", "enum": ["5min","15min","30min","60min","daily","weekly","monthly"]},
                "count": {"type": "integer", "default": 100},
                "adjusted": {"type": "string", "enum": ["none","qfq","hfq"], "default": "none"}
            },
            "required": ["code"]
        }
    },
    {
        "name": "atst_blocks",
        "description": "获取板块列表和成分股",
        ...
    },
    {
        "name": "atst_capital_flow",
        "description": "获取资金流向数据",
        ...
    },
    {
        "name": "atst_f10",
        "description": "获取F10公司资料",
        ...
    },
    {
        "name": "atst_read_local",
        "description": "读取本地通达信文件(.day/.lc1/.lc5)",
        ...
    }
]
```

---

## 11. 协议覆盖清单

### 11.1 7709 标准协议命令清单

| 命令号 | 名称 | 模块 | 优先级 | 状态 |
|---|---|---|---|---|
| `0x000d` | 握手 | `handshake.py` | MVP | 待实现 |
| `0x0004` | 心跳 | `heartbeat.py` | MVP | 待实现 |
| `0x044d` | 代码表/A股数量 | `stock_list.py` | MVP | 待实现 |
| `0x044e` | 市场代码数量 | `stock_list.py` | MVP | 待实现 |
| `0x052d` | K线/周期线 | `kline.py` | MVP | 待实现 |
| `0x0537` | 当日分时 | `minute.py` | MVP | 待实现 |
| `0x051b` | 分时副图 | `minute.py` | P1 | 待实现 |
| `0x053e` | 旧版批量行情 | `quotes.py` | MVP | 待实现 |
| `0x0547` | 五档刷新/推送 | `quotes.py` | P1 | 待实现 |
| `0x054b` | 分类行情 | `quotes.py` | P1 | 待实现 |
| `0x054c` | 批量行情快照 | `quotes.py` | MVP | 待实现 |
| `0x056a` | 集合竞价 | `auction.py` | P1 | 待实现 |
| `0x000f` | 股本变迁/除权除息 | `gbbq.py` | P1 | 待实现 |
| `0x0010` | 财务基础信息 | `finance.py` | P2 | 待实现 |
| `0x0452` | 涨跌停限制 | `price_limit.py` | P1 | 待实现 |
| `0x06b9` | 文件下载 | `file_download.py` | P2 | 待实现 |
| `0x0fb4` | 指定日期历史分时 | `history_minute.py` | P1 | 待实现 |
| `0x0feb` | 近期历史分时 | `history_minute.py` | P1 | 待实现 |
| `0x0fc5` | 当日成交明细 | `trades.py` | P1 | 待实现 |
| `0x0fc6` | 历史成交明细 | `trades.py` | P1 | 待实现 |
| `0x0fd1` | 小走势图 | `sparkline.py` | P2 | 待实现 |

**合计：21 个命令，MVP 需实现 8 个，P1 增补 9 个，P2 增补 4 个**

### 11.2 7727 扩展市场协议

| 功能 | 优先级 | 状态 |
|---|---|---|
| 港股代码表 | P1 | 待实现 |
| 港股 K 线 | P1 | 待实现 |
| 港股行情 | P1 | 待实现 |
| 港股分时 | P1 | 待实现 |
| 美股代码表 | P2 | 待实现 |
| 美股 K 线 | P2 | 待实现 |
| 美股行情 | P2 | 待实现 |
| 期货 K 线 | P2 | 待实现 |
| 期货行情 | P2 | 待实现 |

### 11.3 MAC 专属协议

| 功能 | 命令号范围 | 优先级 | 状态 |
|---|---|---|---|
| 板块列表 | `0x12xx` | P1 | 待实现 |
| 板块成分股 | `0x12xx` | P1 | 待实现 |
| 统一 K 线 | `0x12xx` | P1 | 待实现 |
| 统一行情 | `0x12xx` | P1 | 待实现 |
| 资金流向 | `0x25xx` | P2 | 待实现 |
| 主力监控 | `0x25xx` | P2 | 待实现 |
| 竞价 | `0x12xx` | P2 | 待实现 |
| 多日分时 | `0x12xx` | P2 | 待实现 |

### 11.4 F10 资料协议

| Entry | 功能 | 优先级 | 状态 |
|---|---|---|---|
| `tdxf10_gg_comreq` | 请求入口 | P1 | 待实现 |
| `tdxf10_gg_gsgk` | 公司概况 | P1 | 待实现 |
| `tdxf10_gg_jyfx` | 主营构成 | P2 | 待实现 |
| `tdxf10_gg_gdyj` | 股东增减持 | P2 | 待实现 |
| `tdxf10_gg_fhrz` | 分红融资 | P2 | 待实现 |
| `tdxf10_gg_cwfx` | 财务报表 | P2 | 待实现 |
| `tdxf10_gg_cwzd` | 财务诊断 | P3 | 待实现 |
| `tdxf10_gg_ggzp` | 个股总评 | P2 | 待实现 |
| `tdxf10_gg_ybpj` | 盈利预测 | P2 | 待实现 |
| `tdxf10_gg_rdtc` | 热点题材 | P2 | 待实现 |
| `tdxf10_gg_zlcc` | 沪深股通持仓 | P2 | 待实现 |
| `tdxf10_gg_gszx` | 资讯研报 | P2 | 待实现 |
| `tdxf10_gg_idreq` | 详情正文 | P2 | 待实现 |
| `tdxf10_gg_zxts_rqpm` | 排名 | P3 | 待实现 |
| `tdxf10_gg_zbyz` | 资本运作 | P3 | 待实现 |
| `hq_nlp_tcihq` | 题材行情 | P3 | 待实现 |
| `hq_nlp_gpsj` | 估值数据 | P3 | 待实现 |
| `tzx_rcache` | 新闻/公告 | P3 | 待实现 |

---

## 12. 本地文件格式规范

### 12.1 .day 日线格式

```
记录大小：32 字节/条，小端序

偏移  大小    类型      字段              说明
0     4       uint32    date              日期 YYYYMMDD
4     4       uint32    open_raw          开盘价 × coefficient
8     4       uint32    high_raw          最高价 × coefficient
12    4       uint32    low_raw           最低价 × coefficient
16    4       uint32    close_raw          收盘价 × coefficient
20    4       float32   amount            成交额（元）
24    4       uint32    volume            成交量（单位由 volume_unit 决定）
28    4       uint32    prev_close_raw     上日收盘 × coefficient
                                       （期货为持仓量）

coefficient 默认 0.01（即原始值 ×100）
  - 部分版本/品种可能使用 ×1000 → coefficient=0.001
  - 用户可通过构造参数覆盖

volume_unit 默认 "share"（股）
  - 部分品种/版本可能为 "lot"（手，1手=100股）
  - 用户可通过构造参数覆盖

记录数 = filesize / 32
```

### 12.2 .lc1/.lc5 分钟线格式

```
记录大小：32 字节/条，小端序

偏移  大小    类型      字段              说明
0     2       uint16    date_code         日期编码
2     2       uint16    time              从 0 点起的分钟数
4     4       float32   open              开盘价
8     4       float32   high              最高价
12    4       float32   low               最低价
16    4       float32   close             收盘价
20    4       float32   amount            成交额
24    4       uint32    volume            成交量（股）
28    4       reserved  保留

date_code 解码：
  year  = floor(num / 2048) + 2004
  month = floor(mod(num, 2048) / 100)
  day   = mod(mod(num, 2048), 100)

price_type:
  - "float" (默认): float32，多数版本适用
  - "int": int 类型，单位「分」，部分版本可能使用
```

### 12.3 文件路径规则

```
vipdoc/
├── sh/                         # 沪市
│   ├── lday/                   # 日线
│   │   └── sh600519.day
│   ├── minline/                # 1 分钟线
│   │   └── sh600519.lc1
│   └── fzline/                 # 5 分钟线
│       └── sh600519.lc5
├── sz/                         # 深市
│   ├── lday/
│   │   └── sz000001.day
│   ├── minline/
│   │   └── sz000001.lc1
│   └── fzline/
│       └── sz000001.lc5
├── bj/                         # 北交所
│   └── lday/
│       └── bj430047.day
│
T0002/
└── hq_cache/
    ├── block_gn.dat            # 概念板块
    ├── block_zs.dat            # 指数板块
    ├── block_fg.dat            # 风格板块
    └── gpcw20260831.dat       # 财务数据（日期后缀）
```

### 12.4 .dat 板块文件格式

```
支持两种模式：flat / group

flat 模式：
  [block_name][stock_count][stock1][stock2]...[stockN]
  每个 stock 为 6 字节代码

group 模式：
  [group_count]
  [group1_name][block_count][block1]...[blockN]
  [group2_name]...
    每个 block:
      [block_name][stock_count][stock1]...[stockN]
```

### 12.5 口径分歧处理策略

| 分歧点 | 方案 A（默认） | 方案 B（备选） | 处理方式 |
|---|---|---|---|
| 日线价格缩放 | ×100 (`coefficient=0.01`) | ×1000 (`coefficient=0.001`) | 构造参数可配置 + golden test 校验 |
| 日线成交量单位 | 股 (`volume_unit="share"`) | 手 (`volume_unit="lot"`) | 构造参数可配置 + golden test 校验 |
| 分钟线价格类型 | float32 (`price_type="float"`) | int 分 (`price_type="int"`) | 构造参数可配置 + golden test 校验 |
| 分钟线成交量 | 股 | — | 统一为「股」 |

**Golden Test 策略**：
```python
# 使用真实 .day 文件验证 coefficient 和 volume_unit
def test_daily_bar_golden():
    reader = DailyBarReader("tests/golden/day", coefficient=0.01, volume_unit="share")
    bars = reader.read("600519", market="sh")
    # 验证：开高低收在合理范围内（1-10000 元）
    assert 1.0 <= bars[0]["open"] <= 10000.0
    # 验证：成交量在合理范围内
    assert bars[0]["volume"] > 0
    # 如果开高低收出现 0.01 或 100000 级别，说明 coefficient 错误
```

---

## 13. 复权算法设计

### 13.1 除权除息事件来源

```
0x000f GBBQ 响应解析 → GBBQEvent 列表

GBBEvent 字段：
  - date: 除权除息日 (YYYYMMDD)
  - event_type: 事件类型
  -送股率 (每股送股数)
  - 转增率 (每股转增股数)
  - 分红率 (每股现金分红，元)
  - 配股率 (每股配股数)
  - 配股价 (元)
```

### 13.2 复权公式

#### 前复权 (qfq)

```
以前最近一次除权除息日为基准，向前调整历史价格

调整因子 f：
  f = (1 + 送转股率) / (1 + 配股率 × 配股价/市价 + 分红率)
  简化版（无配股）：
  f = (1 + 送转股率) / (1 + 分红率/市价)

adjusted_open  = raw_open  × f
adjusted_high  = raw_high  × f
adjusted_low   = raw_low   × f
adjusted_close = raw_close × f
```

#### 后复权 (hfq)

```
以最早日期为基准，向后累积调整因子

从最早到最近的每一次除权除息事件，
逐次累积调整因子
```

#### 定点复权 (fixed_qfq / fixed_hfq)

```
以 anchor_date 的收盘价为基准

fixed_qfq: anchor_date 之前的价格向前调整
fixed_hfq: anchor_date 之后的价格向后调整
```

### 13.3 MAC 协议特殊处理

```
MAC 协议服务端可能返回负值的复权数据，
需本地基于 GBBQ 重新计算 QFQ
```

---

## 14. 连接与并发设计

### 14.1 Slot 并发模型（参考 eltdx）

```
Slot = 服务器数 × 每台 TCP 连接数

默认配置：
  num_servers = 2
  connections_per_server = 4
  total_slots = 8

最大配置：
  num_servers = 20
  connections_per_server = 8
  total_slots = 160

内存预算：
  raw_buffer_max = 256 MiB
  decoded_buffer_max = 2 GiB
```

### 14.2 健康评分 failover（参考 easy_tdx）

```python
# 健康评分机制
class HealthScore:
    """
    每台主站维护一个健康分 (0.0-1.0)

    更新规则：
        - 请求成功: score += 0.2 (上限 1.0)
        - 请求失败: score *= 0.5

    降级阈值: score < 0.3 → 暂时移出可用列表
    恢复探测: 每 60 秒探测一次降级的主站
    """
```

### 14.3 请求限流（参考 tdxrs）

```
交易时段自适应：
  盘中 (9:25-15:00):  15 req/s
  盘前盘后:           30 req/s
  休市:               60 req/s

批量行情限制：
  单次请求 ≤ 60 只股票
  超过自动分批
```

### 14.4 重试与 failover

```
重试策略：
  - 指数退避: 0.1s → 0.5s → 1.0s → 2.0s
  - 最大重试: 4 次
  - 跨主站 failover: 失败后自动切换到下一台主站

流程：
  1. 请求主站 A
  2. 失败 → 健康分降级 → 切换到主站 B
  3. 重试（指数退避）
  4. 全部主站失败 → 抛出 TstdxConnectionError
```

### 14.5 push 队列（参考 eltdx）

```
五档推送队列：
  - 队列容量: 1024 帧
  - 内存上限: 64 MiB
  - pin 机制: 可固定订阅特定股票
  - 溢出策略: 丢弃最旧帧
```

---

## 15. 数据模型与错误体系

### 15.1 核心数据模型

```python
# types.py


class StockInfo(TypedDict):
    code: str  # 股票代码
    name: str  # 股票名称
    market: int  # 市场 ID
    category: int  # 分类


class KLineBar(TypedDict):
    datetime: str  # 时间
    open: float  # 开盘价
    high: float  # 最高价
    low: float  # 最低价
    close: float  # 收盘价
    volume: int  # 成交量
    amount: float  # 成交额


class Quote(TypedDict):
    code: str
    name: str
    price: float  # 当前价
    pre_close: float  # 昨收
    open: float  # 开盘
    high: float  # 最高
    low: float  # 最低
    volume: int  # 成交量
    amount: float  # 成交额
    bid_p1: float  # 买一价
    bid_v1: int  # 买一量
    ask_p1: float  # 卖一价
    ask_v1: int  # 卖一量
    # ... 五档


class MinuteBar(TypedDict):
    time: str  # 时间
    price: float  # 价格
    avg_price: float  # 均价
    volume: int  # 成交量
    amount: float  # 成交额


class GBBQEvent(TypedDict):
    date: str  # 日期
    event: str  # 事件类型
    fenshu: float  # 分红率
    songzhuan: float  # 送转率
    peigu: float  # 配股率
    peigujia: float  # 配股价


class BlockInfo(TypedDict):
    block_code: str  # 板块代码
    block_name: str  # 板块名称
    block_type: str  # 板块类型
    stock_count: int  # 成分股数量
    stocks: list[str]  # 成分股代码列表


class CapitalFlow(TypedDict):
    code: str
    date: str
    main_inflow: float  # 主力净流入
    main_inflow_pct: float  # 主力净流入占比
    super_large_inflow: float  # 超大单净流入
    large_inflow: float  # 大单净流入
    medium_inflow: float  # 中单净流入
    small_inflow: float  # 小单净流入
```

### 15.2 统一错误码体系（参考 tdxrs）

```python
# errors.py


class TstdxError(Exception):
    """atst 统一错误基类"""

    code: str
    message: str


class TstdxConnectionError(TstdxError):
    """连接错误 E1xxx"""

    # E1001: 连接超时
    # E1002: 连接被拒
    # E1003: 连接断开
    # E1004: 所有主站不可用


class TstdxProtocolError(TstdxError):
    """协议错误 E2xxx"""

    # E2001: 响应头不匹配
    # E2002: 响应体长度不足
    # E2003: 响应体解析失败
    # E2004: 未知命令号


class TstdxDataError(TstdxError):
    """数据错误 E3xxx"""

    # E3001: 股票代码不存在
    # E3002: 请求的数量超出限制
    # E3003: 日期范围无效
    # E3004: 文件不存在
    # E3005: 文件格式错误


class TstdxConfigError(TstdxError):
    """配置错误 E4xxx"""

    # E4001: 无效的 coefficient
    # E4002: 无效的 volume_unit
    # E4003: 无效的 price_type
    # E4004: Slot 配置无效


class TstdxRateLimitError(TstdxError):
    """限流错误 E5xxx"""

    # E5001: 请求频率超限


class TstdxTimeoutError(TstdxError):
    """超时错误 E6xxx"""

    # E6001: 请求超时
    # E6002: 心跳超时
```

---

## 16. 测试策略

### 16.1 测试金字塔

```
            ┌─────────┐
            │  E2E    │  真实环境 smoke (需网络)
           ┌┴─────────┴┐
           │ Integration │  集成测试 (需网络)
          ┌┴────────────┴┐
          │  Protocol     │  协议回放 (离线)
         ┌┴────────────────┴┐
         │   Golden File     │  本地文件 golden (离线)
        ┌┴────────────────────┴┐
        │     Unit Tests        │  单元测试 (离线)
       └────────────────────────┘
```

### 16.2 测试分类

| 类型 | 说明 | 依赖 | 数量目标 |
|---|---|---|---|
| **Unit** | 纯逻辑测试，无 IO | 无 | 200+ |
| **Golden File** | 用真实 `.day/.lc1/.lc5/.dat` 文件验证解析正确性 | 测试数据文件 | 30+ |
| **Protocol Replay** | 录制 TDX 响应 bytes，离线回放验证解析器 | 录制的响应数据 | 50+ |
| **Integration** | 连接真实 TDX 主站验证 | 网络 + 主站可用 | 20+ |
| **E2E Smoke** | 真实环境冒烟测试 | 网络 + 交易日 | 10+ |
| **Benchmark** | 性能基准 | 无 | 10+ |

### 16.3 Golden File 测试

```python
# tests/reader/test_daily_golden.py


class TestDailyBarReaderGolden:
    @pytest.fixture(scope="class")
    def golden_file(self):
        """真实 .day 文件"""
        return Path("tests/golden/day/sh600519.day")

    @pytest.fixture(scope="class")
    def bars(self, golden_file):
        reader = DailyBarReader(golden_file.parent.parent, coefficient=0.01)
        return reader.read("600519", market="sh")

    def test_record_count(self, bars):
        """记录数 = filesize / 32"""
        expected = 32 * 1000  # 假设文件 32000 字节
        assert len(bars) == expected

    def test_price_range(self, bars):
        """价格在合理范围内"""
        for bar in bars:
            assert 1.0 <= bar["open"] <= 10000.0
            assert bar["low"] <= bar["open"] <= bar["high"]

    def test_date_sequence(self, bars):
        """日期递增"""
        dates = [bar["datetime"] for bar in bars]
        assert dates == sorted(dates)

    def test_coefficient_detection(self, golden_file):
        """自动检测 coefficient"""
        # 如果用 0.01 解析价格出现 0.0001 或 100000，说明应该用 0.001
        reader_001 = DailyBarReader(..., coefficient=0.01)
        bars_001 = reader_001.read("600519", market="sh")

        if any(b["open"] > 50000 for b in bars_001):
            # 价格异常高 → 应该用 0.001
            reader_0001 = DailyBarReader(..., coefficient=0.001)
            bars_0001 = reader_0001.read("600519", market="sh")
            assert all(1.0 <= b["open"] <= 10000.0 for b in bars_0001)
```

### 16.4 协议回放测试

```python
# tests/protocol/test_kline_replay.py


class TestKLineParserReplay:
    @pytest.fixture
    def recorded_response(self):
        """录制的 TDX 0x052d 响应 bytes"""
        # 从 tests/golden/protocol/kline_052d.bin 读取
        return Path("tests/golden/protocol/kline_052d.bin").read_bytes()

    def test_parse_kline_response(self, recorded_response):
        parser = get_parser(0x052D, b"\x04\x00")
        bars = parser.parse_response(recorded_response)

        assert len(bars) > 0
        assert "open" in bars[0]
        assert "close" in bars[0]
        assert all(b["open"] > 0 for b in bars)

    def test_include_raw(self, recorded_response):
        parser = get_parser(0x052D, b"\x04\x00")
        bars = parser.parse_response(recorded_response, include_raw=True)

        assert "_raw" in bars[0]
        assert isinstance(bars[0]["_raw"], bytes)
```

### 16.5 纯 Python vs Rust 内核对比测试

```python
# tests/test_native_parity.py


class TestNativeParity:
    """确保 Rust 内核与纯 Python 实现结果完全一致"""

    @pytest.fixture(params=["python", "native"])
    def reader(self, request):
        if request.param == "native":
            pytest.importorskip("atst._native")
            return DailyBarReaderNative(...)
        return DailyBarReader(...)

    def test_daily_bar_parity(self, reader):
        bars = reader.read("600519", market="sh")
        # 两种实现的结果必须完全一致
        assert len(bars) == expected_count
        assert bars[0]["open"] == expected_open
```

---

## 17. 分阶段实施路线图

### Phase 0：项目初始化（1 周）

```
□ pyproject.toml + hatch 配置
□ 目录骨架
□ errors.py + types.py + constants.py
□ CI/CD (GitHub Actions)
□ pre-commit (ruff + mypy)
□ 基础 README
```

### Phase 1：MVP — 核心可用（3 周）

**目标**：A 股线上行情 + 本地文件解析，最小可用版本

```
□ Transport 层
  □ TCP 连接管理
  □ GBK 编码 + zlib 解压
  □ 主站测速 + 排名持久化
  □ 心跳保活

□ Protocol 层（8 个 MVP 命令）
  □ 0x000d 握手
  □ 0x0004 心跳
  □ 0x044d 代码表
  □ 0x044e 市场数量
  □ 0x052d K 线
  □ 0x0537 分时
  □ 0x053e 旧版批量行情
  □ 0x054c 批量行情快照

□ Client 层
  □ StandardClient (7709)
  □ 基础 failover

□ Reader 层
  □ DailyBarReader (.day)
  □ MinBarReader (.lc1/.lc5)

□ API 层
  □ 输出三态 (dict/tuple/dataframe)

□ 测试
  □ Unit tests (50+)
  □ Golden file tests (10+)
  □ Protocol replay tests (20+)
  □ E2E smoke tests (5+)

□ CLI
  □ atst quotes / kline / read-day / benchmark
```

### Phase 2：协议完善（4 周）

**目标**：7709 全部 21 命令 + 7727 扩展市场

```
□ Protocol 层 — 7709 剩余 13 命令
  □ 0x051b 分时副图
  □ 0x0547 五档推送
  □ 0x054b 分类行情
  □ 0x056a 集合竞价
  □ 0x000f 股本变迁
  □ 0x0010 财务基础
  □ 0x0452 涨跌停
  □ 0x06b9 文件下载
  □ 0x0fb4 历史分时
  □ 0x0feb 近期历史分时
  □ 0x0fc5 当日成交明细
  □ 0x0fc6 历史成交明细
  □ 0x0fd1 小走势图

□ Protocol 层 — 7727 扩展
  □ 港股代码表 / K 线 / 行情 / 分时

□ Client 层
  □ ExtendedClient (7727)
  □ AsyncTdxClient (异步版)

□ 领域服务层
  □ 复权引擎 (qfq/hfq)
  □ 缓存
  □ 限流
  □ 下载器
  □ 重试/failover 完善

□ Reader 层
  □ BlockReader (.dat)
  □ FinancialReader (gpcw*.dat)

□ 测试
  □ Unit tests (150+)
  □ Golden file tests (25+)
  □ Protocol replay tests (40+)
  □ Integration tests (15+)
```

### Phase 3：MAC + F10 + 集成（4 周）

**目标**：MAC 协议 + F10 资料 + 全部集成方式

```
□ Protocol 层 — MAC
  □ 板块列表 / 成分股
  □ 统一 K 线 / 统一行情
  □ 资金流向 / 主力监控
  □ 竞价 / 多日分时

□ Protocol 层 — F10
  □ tdxf10_gg_comreq 入口
  □ tdxf10_gg_gsgk 公司概况
  □ tdxf10_gg_cwfx 财务报表
  □ 其余 F10 Entry

□ Client 层
  □ MacClient
  □ 客户端工厂

□ API 层
  □ Helpers 高级封装

□ 集成层
  □ HTTP REST (FastAPI)
  □ WebSocket RPC
  □ MCP 工具服务
  □ CLI 完善

□ 并发
  □ Slot 并发模型
  □ push 队列
  □ 内存预算

□ 测试
  □ Unit tests (200+)
  □ Protocol replay tests (50+)
  □ Integration tests (20+)
```

### Phase 4：Rust 内核 + 生态（4 周）

**目标**：性能加速 + 多语言绑定 + 生态完善

```
□ Rust 内核
  □ transport.rs (连接/编解码加速)
  □ reader.rs (文件解析加速)
  □ protocol.rs (协议解析加速)
  □ PyO3 绑定
  □ 5 平台 wheel (Win x64, manylinux x64/ARM64, macOS x64/ARM64)
  □ Parity 测试 (纯 Python vs Rust)
  □ Benchmark 对比

□ 7727 完善
  □ 美股代码表 / K 线 / 行情
  □ 期货 K 线 / 行情

□ MAC 完善
  □ 资金流向 / 主力监控
  □ 竞价 / 多日分时

□ F10 完善
  □ 全部 18 个 Entry

□ 定点复权
  □ fixed_qfq / fixed_hfq

□ 文档
  □ 协议文档 (7709/7727/mac/f10)
  □ 文件格式文档
  □ 架构详解
  □ API Reference
  □ 迁移指南 (从 mootdx/tdxpy 迁移)
```

### 路线图总结

```
Phase 0: 初始化      ████████░░░░░░░░  Week 1
Phase 1: MVP         ░░░░████████████░░  Week 2-4
Phase 2: 协议完善    ░░░░░░░░░░████████████████  Week 5-8
Phase 3: MAC+F10+集成 ░░░░░░░░░░░░░░░░░░████████████████  Week 9-12
Phase 4: Rust+生态   ░░░░░░░░░░░░░░░░░░░░░░░░░░░░████████  Week 13-16

总计：约 16 周（4 个月），可按阶段交付
```

---

## 18. 参考项目能力对照表

| 能力维度 | mootdx | easy_tdx | tdx-api | tdxrs | eltdx | bebopze | **atst (目标)** |
|---|---|---|---|---|---|---|---|
| **语言** | Python | Python | Go | Rust+PyO3 | Py+Rust | TDX公式 | **Python+Rust** |
| **7709 标准协议** | 部分 | ✅ | ✅ | ✅ | ✅(21命令) | — | **✅(21命令)** |
| **7727 扩展协议** | 部分 | ✅ | 部分 | 部分 | — | — | **✅** |
| **MAC 专属协议** | — | ✅ | — | — | — | — | **✅** |
| **F10 资料** | — | 部分 | 部分 | feature gate | ✅(20+Entry) | — | **✅(18+Entry)** |
| **本地文件解析** | ✅ Reader | 部分 | — | ✅(5 Reader) | — | — | **✅(5 Reader)** |
| **复权** | — | ✅(本地QFQ) | — | — | — | — | **✅(qfq/hfq/fixed)** |
| **异步** | — | ✅ | — | ✅(tokio) | — | — | **✅(asyncio)** |
| **连接池/Slot** | multithread | ✅ | — | ✅(5+心跳) | ✅(2×4=8) | — | **✅(可配置)** |
| **健康评分 failover** | — | ✅ | — | ✅(重试+缓存) | ✅ | — | **✅** |
| **请求限流** | — | — | — | ✅(时段自适应) | ✅ | — | **✅** |
| **输出三态** | — | JSON | JSON | ✅(3态) | to_json | — | **✅(3态)** |
| **CLI** | ✅ | ✅ | ✅(WebUI) | ✅ | — | — | **✅** |
| **HTTP REST** | — | ✅(Web) | ✅(32接口) | — | ✅(http网关) | — | **✅** |
| **WebSocket** | — | — | — | — | ✅(WS RPC) | — | **✅** |
| **MCP** | — | ✅(AI适配) | — | — | ✅(stdio) | — | **✅** |
| **Rust 内核加速** | — | — | — | ✅(9-11×) | ✅(3.0起) | — | **✅(可选)** |
| **License** | MIT | 学习研究 | MIT | MIT | **禁止商用** | — | **MIT** |

### atst 的独有优势

1. **三协议族全覆盖**：唯一同时覆盖 7709 标准 + 7727 扩展 + MAC 专属的库
2. **F10 完整覆盖**：18+ Entry，比多数库只有部分 F10 更完整
3. **协议注册表**：`@register_parser` 装饰器，新增协议零侵入扩展
4. **口径可配置**：`coefficient` / `volume_unit` / `price_type` 应对版本差异
5. **定点复权**：`fixed_qfq` / `fixed_hfq` 支持任意基准日
6. **四集成方式**：CLI + HTTP + WebSocket + MCP，覆盖所有消费场景
7. **渐进加速**：纯 Python 可用，Rust 内核有则加速无则 fallback

---

## 19. 附录：License 与合规声明

### 19.1 License 选择

**推荐：MIT License**

理由：
- mootdx / tdx-api / tdxrs 均使用 MIT，兼容性最佳
- eltdx 禁止商用，**不可作为代码直接来源**，仅作为协议研究参考
- MIT 允许商用、修改、分发，仅要求保留版权声明

### 19.2 数据版权声明

```
本项目仅提供通达信行情数据的协议读取能力。
所有行情数据版权归通达信及相应数据商所有。
用户使用本项目获取的数据仅限个人学习研究用途，
不得用于商业用途或转售。
开发者不对数据准确性、完整性做任何担保。
```

### 19.3 参考项目致谢

```
本项目在架构设计上参考了以下开源项目：
- mootdx (MIT)      — Reader 简洁设计 + CLI 体验
- easy_tdx           — 四层客户端 + 健康评分 failover
- tdx-api (MIT)     — HTTP REST 接口设计
- tdxrs (MIT)       — Rust 内核 + 输出三态 + 错误码
- eltdx              — Slot 并发 + Helpers 分层 + F10 覆盖 (协议研究参考)
- opentdx            — @register_parser 注册表模式 (架构参考)
```

### 19.4 代码原创性声明

```
本项目代码为原创实现。
协议格式参考公开技术文档与上述项目的架构思路，
但不直接复制任何项目的源代码。
特别是 eltdx (禁止商用) 仅作为协议研究参考，
不引入其任何代码。
```

---

## 文档版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-08-31 | 初版完整方案，涵盖 19 章 |

---

> **下一步**：确认技术栈方向后，从 Phase 0 项目初始化开始实施。方案中标注 `□` 的条目为待实现项，可按优先级推进。
