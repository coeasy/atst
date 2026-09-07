# 通达信行情通用包 — 完整设计方案 v2.0

> **文档状态**：v2.0（重大升级）
> **创建日期**：2026-08-31
> **工作区**：`D:\workspace\tstdx`
> **历史版本**：`docs/archive/DESIGN_v1.0.md`

---

## v2.0 升级说明

针对四项硬性约束重新设计：

| 约束 | v1.0 状态 | v2.0 方案 |
|---|---|---|
| **协议全部支持** | 21 个命令，P1/P2 分级延后 | **全命令空间三层覆盖**：36+ 精确解析 + 通用启发式解析 + 原始透传 + 协议探针 |
| **原创实现（禁复制）** | 一句声明 | **洁净室工程规范**：规格驱动开发 + License 隔离 + AST 相似度审计 + Golden 数据自采集 |
| **兼容所有数据** | coefficient 单参数 | **DataProfile 规格档案系统**：市场/品种/周期/口径/编码全维度参数化 + 自动探测 |
| **支持实时数据** | 未涉及 | **Streaming 子系统**：推送+轮询双模 + 增量合并 + 断线补数 + 背压控制 |

---

## 目录

1. [项目定位与目标](#1-项目定位与目标)
2. [技术栈选型](#2-技术栈选型)
3. [分层架构总览](#3-分层架构总览)
4. [原创性工程规范](#4-原创性工程规范)
5. [协议全覆盖体系](#5-协议全覆盖体系)
6. [数据兼容 Profile 系统](#6-数据兼容-profile-系统)
7. [Transport 传输层](#7-transport-传输层)
8. [Client 客户端层](#8-client-客户端层)
9. [Reader 本地文件解析层](#9-reader-本地文件解析层)
10. [实时数据 Streaming 子系统](#10-实时数据-streaming-子系统)
11. [领域服务层](#11-领域服务层)
12. [API 输出层](#12-api-输出层)
13. [集成层](#13-集成层)
14. [可观测性](#14-可观测性)
15. [数据模型与错误体系](#15-数据模型与错误体系)
16. [测试策略](#16-测试策略)
17. [分阶段实施路线图](#17-分阶段实施路线图)
18. [参考项目能力对照表](#18-参考项目能力对照表)
19. [附录：License 与合规声明](#19-附录license-与合规声明)

---

## 1. 项目定位与目标

**tstdx** — TongDaXin Standard Data eXchange

通达信行情数据的**通用底层协议基础设施**。类比 HTTP 世界的 `requests` —— 稳定、标准、可组合，不做应用层业务。

| 维度 | 定义 |
|---|---|
| **是** | 协议库 / 数据接入层 / 可组合的基础设施 |
| **不是** | 回测引擎、选股系统、交易终端、Web 可视化平台 |
| **目标用户** | 量化交易开发者、金融数据工程师、行情中间件开发者、AI Agent |

### 1.1 v2.0 设计目标

| # | 目标 | 量化指标 |
|---|---|---|
| **G1** | **协议全覆盖** | 已知命令 100% 精确解析；未知命令 100% 不丢包（通用解析或原始透传） |
| **G2** | **原创合规** | 零复制代码；AST 相似度审计通过；License 依赖白名单 100% 合规 |
| **G3** | **数据全兼容** | 覆盖 12 类市场 × 10 类品种 × 12 档周期 × 全口径组合；自动探测准确率 ≥ 99% |
| **G4** | **实时能力** | 五档推送延迟 P95 < 100ms；逐笔吞吐 ≥ 5000 笔/秒；断线重连 < 3s；补数完整性 100% |
| **G5** | **高性能** | 本地解析 ≥ 200,000 条/秒；单次编解码 < 1ms（Rust 内核） |
| **G6** | **高可用** | 多主站 failover + 健康评分 + 指数退避；连续 1000 次请求成功率 ≥ 99.5% |
| **G7** | **可组合** | 每层可独立使用，零强制上层依赖 |
| **G8** | **可观测** | 全链路指标 / 结构化日志 / 请求追踪，问题定位 < 5 分钟 |

---

## 2. 技术栈选型

### 2.1 主语言：Python 3.10+

| 考量 | 分析 |
|---|---|
| 生态 | 6 个参考项目中 4 个为 Python，协议文档与测试数据以 Python 为基准 |
| 场景 | 量化/金融分析领域 Python 占主导，pandas/numpy 生态不可替代 |
| ABI | 3.10+ 可用 `cp310-abi3` wheel，一次编译覆盖 3.10–3.13+ |
| 异步 | `asyncio`，无需引入第三方事件循环 |

### 2.2 性能层：Rust + PyO3/maturin（可选内核）

**定位**：可选加速，非强制依赖。纯 Python 零编译可用，Rust 内核有则加速。

| 操作 | 纯 Python | Rust 内核 | 加速比 |
|---|---|---|---|
| 日线解析 1000 条 | ~2.8ms | ~0.3ms | 9.3× |
| 分钟线解析 1000 条 | ~5.1ms | ~0.5ms | 10.2× |
| 板块解析 | ~12.0ms | ~1.2ms | 10.0× |
| 财务数据解析 | ~8.5ms | ~0.8ms | 10.6× |
| 网络请求编解码 | 基准 | — | 1.3–1.5× |

**关键设计**：Rust 内核与纯 Python 实现必须**结果完全一致**，由 Parity 测试强制保证（见 §16.5）。

**Rust crate 依赖**（全部为宽松 License，见 §4.4）：
```
pyo3 / maturin     — Python 绑定（MIT/Apache-2.0）
encoding_rs        — GBK/GB18030 解码（Apache-2.0/MIT/BSD-3）
bytes              — 零拷贝缓冲区（MIT）
thiserror / anyhow — 错误体系（MIT/Apache-2.0）
tokio              — 异步运行时（MIT）
```

### 2.3 目录结构（v2.0 新增部分标 ★）

```
tstdx/
├── DESIGN.md
├── PLAN.md
│
├── PROTOCOL_SPEC/                 ★ 协议规格说明书（代码之唯一依据）
│   ├── wire_format.md             ★ 通用报文格式 + 变长数值编码
│   ├── 7709/                      # 每命令一份 spec：000d_login.md ...
│   ├── 7727/                      # 扩展市场
│   ├── mac/                       # MAC 协议
│   ├── f10/                       # F10 资料
│   └── UNKNOWN/                   ★ 未知命令观测记录（探针产出）
│
├── ORIGINALITY/                   ★ 原创性合规档案
│   ├── LICENSE_ALLOWLIST.md
│   ├── AUDIT_REPORT.md
│   └── CLEANROOM_PROCESS.md
│
├── tstdx/
│   ├── errors.py / types.py / constants.py
│   │
│   ├── transport/
│   │   ├── connection.py / pool.py / heartbeat.py
│   │   ├── server_list.py / codec.py
│   │   └── sniff.py              ★ 协议探针（抓包/录制）
│   │
│   ├── protocol/
│   │   ├── base.py               # BaseParser
│   │   ├── registry.py           # @register_parser 注册表
│   │   ├── wire.py               ★ 报文头编解码（12B req / 16B resp）
│   │   ├── varint.py             ★ TDX 变长价格/成交量编码
│   │   ├── generic.py            ★ 通用启发式解析器（未知命令兜底）
│   │   ├── quotation/            # 7709 标准（36+ 解析器）
│   │   ├── ex_quotation/         # 7727 扩展市场
│   │   ├── mac_quotation/        # MAC 专属
│   │   └── f10/                  # F10 资料
│   │
│   ├── profile/                  ★ 数据兼容 Profile 系统
│   │   ├── base.py / registry.py / detect.py / presets.py
│   │
│   ├── client/
│   │   ├── base.py / standard.py / extended.py / mac.py
│   │   ├── goods.py              ★ 商品语义客户端（期货/期权/外汇）
│   │   ├── async_client.py / factory.py
│   │
│   ├── streaming/                ★ 实时数据子系统
│   │   ├── subscription.py / push.py / tick.py
│   │   ├── merger.py / gapfill.py / backpressure.py / reconnect.py
│   │
│   ├── reader/
│   │   ├── daily.py / minute.py / block.py
│   │   ├── financial.py / professional_finance.py  ★
│   │
│   ├── services/
│   │   ├── adjust.py / cache.py / ratelimit.py
│   │   ├── downloader.py / retry.py / validate.py  ★
│   │
│   ├── api/output.py / api/helpers.py
│   ├── observability/            ★ metrics.py / logging.py / tracing.py
│   └── integration/cli.py / http_server.py / ws_server.py / mcp_server.py
│
├── tstdx_native/                  # Rust 内核（可选）
│   └── src/{transport,reader,protocol,streaming}.rs
│
├── tools/
│   ├── check_originality.py       ★ 原创性检查（pre-commit + CI）
│   └── collect_golden.py          ★ Golden 数据自采集
│
├── tests/
│   ├── unit / protocol / reader / integration / e2e
│   ├── parity/                    ★ 纯 Python vs Rust 一致性
│   ├── compatibility/             ★ 兼容性矩阵
│   └── golden/                    # 自采集数据
│
├── benches/ / examples/ / docs/
```

---

## 3. 分层架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                        集成层 (Integration)                           │
│      CLI  ·  HTTP REST  ·  WebSocket RPC  ·  MCP Tool Service        │
├──────────────────────────────────────────────────────────────────────┤
│                   可观测性（横切）                                     │
│         Metrics  ·  Structured Logging  ·  Request Tracing           │
├──────────────────────────────────────────────────────────────────────┤
│                        API 输出层                                     │
│        Helpers 组合封装  ·  输出三态 (dict / tuple / DataFrame)        │
├──────────────────────────────────────────────────────────────────────┤
│                      领域服务层                                        │
│   复权  ·  缓存  ·  限流  ·  下载器  ·  重试/failover  ·  数据校验    │
├──────────────────────────────────────────────────────────────────────┤
│              ★ 实时数据 Streaming 子系统                              │
│   订阅管理 · 五档推送 · 逐笔成交 · 增量合并 · 断线补数 · 背压控制     │
├──────────────────────────────────────────────────────────────────────┤
│                      Client 客户端层                                  │
│  Standard(7709) · Extended(7727) · Mac · Goods ★ · AsyncTdx · F10    │
├──────────────────────────────────────────────────────────────────────┤
│                   Protocol 协议层（三层覆盖）★                        │
│  ┌─────────────┬─────────────┬─────────────┐                        │
│  │ L1 精确解析  │ L2 通用解析  │ L3 原始透传  │ ← 覆盖整个命令空间    │
│  │  36+ 已知    │  启发式      │  raw bytes   │                      │
│  └─────────────┴─────────────┴─────────────┘                        │
│  @register_parser 注册表  ·  ProtocolSniffer 探针  ·  ProtocolProber  │
│  ┌──────────┬──────────┬──────────┬──────────┐                       │
│  │quotation/│ex_quot./ │mac_quot./│   f10/   │                       │
│  │  7709    │  7727    │   MAC    │  7615    │                       │
│  │ 36+ 命令  │ 扩展市场  │ ~16 命令  │ 18+ Entry│                      │
│  └──────────┴──────────┴──────────┴──────────┘                       │
├──────────────────────────────────────────────────────────────────────┤
│                     Transport 传输层                                  │
│  TCP · 连接池/Slot · 心跳 · 主站测速 · GBK/zlib · 报文头 · 变长编码   │
├──────────────────────────────────────────────────────────────────────┤
│              ★ Profile 数据规格档案（横切）                            │
│     市场 × 品种 × 周期 × 口径 × 编码 × 字节序 × 时区                  │
├──────────────────────────────────────────────────────────────────────┤
│                     Reader 本地文件解析层                             │
│     .day 日线 · .lc1/.lc5 分钟线 · .dat 板块 · gpcw*.dat 财务        │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.1 核心设计原则

| # | 原则 | 说明 |
|---|---|---|
| **P1** | **规格驱动** | 先写 `PROTOCOL_SPEC/*.md`，再写代码。代码是 spec 的翻译，不是逆向的产物 |
| **P2** | **协议三层覆盖** | 已知精确解析 → 未知通用解析 → 原始透传，永不丢包 |
| **P3** | **Profile 参数化** | 所有数据差异抽象为 Profile，运行时可覆盖，杜绝硬编码口径 |
| **P4** | **自动探测优先** | 口径/编码/字节序先自动探测，失败才降级到预设，最后才报错 |
| **P5** | **注册表可扩展** | `@register_parser` 装饰器，新增协议零侵入 |
| **P6** | **分层可独立** | 每层零上层依赖 |
| **P7** | **同步异步同形** | 签名一致，代码复用 |
| **P8** | **实时优先** | Streaming 是一等公民，不是轮询的补丁 |
| **P9** | **原创可审计** | 每次提交过相似度检查，依赖过 License 白名单 |
| **P10** | **可观测内建** | 指标/日志/追踪从第一天就有，不是事后补 |

---

## 4. 原创性工程规范

> **硬性约束：不得复制其他开源项目代码。**
> 本节将约束转化为可执行的工程流程。

### 4.1 洁净室（Clean Room）开发流程

```
┌─────────────────────────────────────────────────────────┐
│  A 组：规格分析（只读外部事实）                           │
│  · 公开技术文档 / 通达信官方帮助中心                      │
│  · 自有环境网络抓包（黑盒观测）                          │
│  · 自有本地文件二进制结构分析                            │
│  ↓ 产出：PROTOCOL_SPEC/*.md（纯事实描述，无代码）        │
│         禁止粘贴任何开源项目源码片段                      │
└─────────────────────┬───────────────────────────────────┘
                      │ 仅传递规格文档
                      ▼
┌─────────────────────────────────────────────────────────┐
│  B 组：实现（只读 A 组规格，不接触参考项目源码）          │
│  · 依据 spec 独立编写解析代码                            │
│  · 用自采集 golden 数据验证正确性                        │
│  ↓ 产出：tstdx/protocol/**.py                           │
└─────────────────────────────────────────────────────────┘
```

**执行要点**：
- A 组产出**仅含事实**（字节偏移、字段类型、编码方式、示例数据），**不含任何实现代码**
- B 组开发期间**不打开** mootdx/tdxrs/eltdx 等项目的源码文件
- 参考项目仅用于**架构思路**借鉴（如"用注册表管理解析器"这类设计模式），不借鉴实现

### 4.2 协议规格说明书（Spec）规范

每个协议命令必须有独立 spec 文件，作为代码的唯一依据。

```markdown
# PROTOCOL_SPEC/7709/052d_kline.md

## 基本信息
- 命令号：0x052d
- 协议族：quotation (7709)
- 依赖：需先完成 0x000d + 0x0fdb 两步握手

## 请求报文（头部格式见 wire_format.md）
| 偏移 | 大小 | 类型   | 字段   | 说明                     |
|------|------|--------|--------|--------------------------|
| 0    | 2    | uint16 | market | 0=沪 1=深                |
| 2    | 6    | char   | code   | GBK 编码，右补 \0        |
| 8    | 2    | uint16 | period | 4=日线 8=1分钟 ...       |
| 10   | 2    | uint16 | start  | 起始位置（0=最近）       |
| 12   | 2    | uint16 | count  | 请求数量                 |

## 响应报文
- 响应头：16 字节（见 wire_format.md）
- 记录数：body[0:2] uint16
- 每条记录 32 字节：
| 偏移 | 大小 | 类型   | 字段     | 说明                  |
|------|------|--------|----------|-----------------------|
| 0    | 4    | uint32 | date     | YYYYMMDD              |
| 4    | 4    | uint32 | open_raw | 开盘价 × price_scale  |
| ...  | ...  | ...    | ...      | ...                   |

## 口径说明
- price_scale 由所属 Profile 决定（见 profile/presets.py）
- 指数品种 amount 字段语义可能不同

## 验证数据
- golden 文件：tests/golden/protocol/kline_052d.bin
- 预期记录数：800
- 首条记录：date=20260828, open=1680.00

## 观测来源（★ 只允许以下三类）
- 自有环境网络抓包（2026-08-31）
- 自有本地 .day 文件交叉验证
- 官方公开文档
（★ 禁止填写：参考了 XX 项目的 XX.py）
```

**关键**：`观测来源` 一栏**只允许**填写自有抓包、自有文件分析、公开官方文档。**禁止**填写参考项目的源码路径。

### 4.3 原创性审计流程

```python
# tools/check_originality.py（pre-commit + CI 双重执行）


class OriginalityChecker:
    """原创性检查"""

    FORBIDDEN_PATTERNS = [
        # 禁止出现其他项目的标识符
        r"\bmootdx\b",
        r"\bpytdx\b",
        r"\btdxrs\b",
        r"\beltdx\b",
        # 禁止出现其他项目的特有函数名
        r"\bget_security_bars\b",
        r"\bTdxHq_API\b",
        # 禁止出现"从 XX 复制/移植/改编"类注释
        r"(?i)(copied|ported|adapted|derived)\s+from",
    ]

    def check_similarity(self, file_path: Path) -> SimilarityResult:
        """代码相似度检测
        - 与参考项目做 AST 级比对（非文本比对，避免误报）
        - 阈值：单文件 AST 相似度 > 0.70 → 阻断提交
        - 阈值：函数级 AST 相似度 > 0.85 → 阻断提交
        """

    def check_license_compliance(self) -> LicenseReport:
        """依赖 License 合规检查
        - 扫描 pyproject.toml / Cargo.toml 全部依赖（含传递依赖）
        - 比对 LICENSE_ALLOWLIST.md
        - 出现非白名单 License → 阻断构建
        """

    def check_spec_coverage(self) -> CoverageReport:
        """Spec 覆盖检查
        - 每个 protocol/**/*.py 解析器必须有对应 PROTOCOL_SPEC/**/*.md
        - 缺失 spec → 阻断提交（防止"先抄后补"）
        """
```

**CI 流水线门禁**：

| 阶段 | 检查项 | 失败动作 |
|---|---|---|
| pre-commit | ruff / mypy / 禁用模式扫描 | 阻断提交 |
| CI Stage 1 | AST 级相似度检测 | 阻断 PR |
| CI Stage 2 | License 白名单扫描 | 阻断 PR |
| CI Stage 3 | Spec 覆盖率检查 | 阻断 PR |
| CI Stage 4 | Unit + Golden + Parity + Compatibility | 阻断 PR |

### 4.4 依赖 License 白名单

```markdown
# ORIGINALITY/LICENSE_ALLOWLIST.md

## 允许（Permissive）
| License | 依赖示例 |
|---|---|
| MIT | pandas, numpy, requests, pyo3, bytes, tokio |
| Apache-2.0 | encoding_rs, thiserror |
| BSD-2 / BSD-3 | — |
| ISC / Python-2.0 | setuptools |

## 禁止（Forbidden）
| License | 原因 |
|---|---|
| GPL / LGPL / AGPL | 传染性，与 MIT 不兼容 |
| 自定义禁止商用条款 | 明确限制商业使用 |
| 无 License | 法律风险未知 |
| 「仅供学习研究」声明 | 商业使用风险 |

## 特别标注
以下项目**仅作为架构思路与协议事实的参考**，
不作为代码来源，不出现在依赖树中，不进行任何代码复制：
- mootdx (MIT)      — 架构思路参考
- easy_tdx          — 四层客户端架构思路参考
- tdx-api (MIT)     — HTTP 接口设计思路参考
- tdxrs (MIT)       — Rust+PyO3 分层思路参考
- eltdx (禁止商用)  — ★ 仅协议事实参考，绝不引入代码
- opentdx           — 注册表设计模式参考
```

### 4.5 数据来源合规

```
✓ 允许：
  · 自有环境网络抓包（连接公开 TDX 主站）
  · 自有通达信客户端本地文件分析
  · 通达信官方帮助中心公开文档（help.tdx.com.cn）
  · 公开技术标准（GBK 编码标准、zlib 规范等）

✗ 禁止：
  · 复制参考项目源码（任何形式：直接复制、改名复制、翻译复制）
  · 使用参考项目的测试用例数据作为 golden data（必须自行采集）
  · 引入许可证不明的第三方代码

△ 数据版权：
  行情数据版权归通达信及数据商所有，
  库仅提供协议读取能力，不分发任何数据。
```

---

## 5. 协议全覆盖体系

> **硬性约束：协议要求全部支持。**
> 关键认知：不能只覆盖"已知的 36 个命令"，必须覆盖**整个 16 位命令空间（0x0000–0xFFFF）**。

### 5.1 三层覆盖模型

```
                    收到响应（msg_id = X）
                            │
                ┌───────────┴───────────┐
                ▼                       ▼
        X 在注册表中？               X 未注册
                │                       │
        ┌───────┴───────┐               ▼
        ▼               ▼        ┌──────────────┐
   解析成功        解析失败      │ L2 通用解析   │
        │               │        │ 启发式结构化  │
        ▼               └───────▶│ + 结构推测    │
   ┌─────────┐                   └──────┬───────┘
   │ L1 精确 │                           │
   │  解析   │                    ┌──────┴───────┐
   └─────────┘                    ▼              ▼
                            推测成功        推测失败
                                 │              │
                                 ▼              ▼
                          ┌───────────┐  ┌────────────┐
                          │ 带警告返回 │  │ L3 原始透传 │
                          │ 推测结果   │  │ raw bytes   │
                          └───────────┘  └──────┬─────┘
                                                │
                                                ▼
                                       ┌─────────────────┐
                                       │ ProtocolSniffer  │
                                       │ 记录+归档+生成   │
                                       │ spec 草案        │
                                       └─────────────────┘
```

**保证**：**任何响应都不会被丢弃或抛异常**。L1 失败降级 L2，L2 失败降级 L3，L3 必定成功（至少返回 raw bytes）。

### 5.2 L1：精确解析器（36 个标准命令）

基于多源交叉验证的完整命令空间：

| msg_id | 名称 | 领域 | 模块 | 优先级 |
|---|---|---|---|---|
| `0x0002` | ExchangeAnnouncement 交易所公告 | Session | `session.py` | P2 |
| `0x0004` | HeartBeat 心跳 | Session | `session.py` | **MVP** |
| `0x000a` | Announcement 公告 | Session | `session.py` | P2 |
| `0x000d` | Login 登录/握手 | Session | `session.py` | **MVP** |
| `0x000f` | XDXR 除权除息 | Financial | `xdxr.py` | P1 |
| `0x0010` | Finance 财务信息 | Financial | `finance.py` | P2 |
| `0x0015` | Info / Ping 服务器信息 | Session | `session.py` | P1 |
| `0x02c5` | Meta 文件元数据 | File | `file.py` | P2 |
| `0x02cf` | Category 公司信息分类 | Financial | `company_info.py` | P2 |
| `0x02d0` | Content 公司信息内容 | Financial | `company_info.py` | P2 |
| `0x044d` | List 证券列表 | Stock List | `security_list.py` | **MVP** |
| `0x044e` | Count 证券数量 | Stock List | `security_list.py` | **MVP** |
| `0x0450` | List2 旧版证券列表 | Stock List | `security_list.py` | P1 |
| `0x0452` | f452 证券特征/涨跌停 | Stock List | `security_feature.py` | P1 |
| `0x051a` | VolumeProfile 量价分布 | Quotes | `volume_profile.py` | P2 |
| `0x051b` | 分时副图 | Chart | `minute.py` | P1 |
| `0x051c` | IndexMomentum 指数动量 | Analytics | `index_momentum.py` | P2 |
| `0x051d` | IndexInfo 指数信息 | Quotes | `index_info.py` | P1 |
| `0x0523` | K_Line 旧版 K 线 | K-Line | `kline_legacy.py` | P2 |
| `0x052d` | K_Line_Offset K 线 | K-Line | `kline.py` | **MVP** |
| `0x0537` | TickChart 分时图 | Chart | `minute.py` | **MVP** |
| `0x053e` | QuotesDetail 旧版行情 | Quotes | `quotes.py` | **MVP** |
| `0x053f` | TopBoard 涨停板 | Analytics | `top_board.py` | P1 |
| `0x0547` | QuotesEncrypt 五档推送 | Quotes | `quotes_push.py` | **MVP** |
| `0x054b` | QuotesList 分类行情 | Quotes | `quotes.py` | P1 |
| `0x054c` | Quotes 新版批量行情 | Quotes | `quotes.py` | **MVP** |
| `0x0563` | Unusual 异动 | Analytics | `unusual.py` | P2 |
| `0x056a` | Auction 集合竞价 | Analytics | `auction.py` | P1 |
| `0x06b9` | Download / Block 文件·板块 | File | `file.py` | P1 |
| `0x0fb4` | HistoryOrders 历史委托 | Transaction | `transaction.py` | P2 |
| `0x0fb5` | HistoryTransaction 历史逐笔 | Transaction | `transaction.py` | P1 |
| `0x0fc5` | Transaction 逐笔成交 | Transaction | `transaction.py` | **MVP** |
| `0x0fc6` | HistoryTransactionWithTrans | Transaction | `transaction.py` | P1 |
| `0x0fd1` | ChartSampling 图表采样 | Chart | `chart_sampling.py` | P2 |
| `0x0fdb` | UpgradeTip 登录2 | Session | `session.py` | **MVP** |
| `0x0feb` | HistoryTickChart 历史分时 | Chart | `minute.py` | P1 |

**合计 36 个**：MVP 8 个 / P1 12 个 / P2 16 个。

### 5.3 扩展市场协议（7727）

| 能力组 | 接口 | 覆盖 | 优先级 |
|---|---|---|---|
| 证券列表 | `ExListExtra` 扩展列表 | 港股/美股/期货 | P1 |
| K 线 | `ExKLine2` 扩展 K 线 | 港股/美股/期货 | P1 |
| 行情/分时 | 扩展行情快照 / 分时 | 港股/美股 | P1/P2 |
| 映射 | `ExMapping2562` 代码映射 | 全市场 | P2 |
| 待观测 | `ExExperiment2487` / `ExExperiment2488` | 待探针发现 | P3 |

### 5.4 商品语义协议组（GoodsClient）★

> v1.0 完全遗漏的重要能力组。期货/期权/外汇走**独立的"商品语义"协议族**，与股票协议结构不同。

| 接口 | 功能 | 优先级 |
|---|---|---|
| `GoodsCount` | 商品数量 | P2 |
| `GoodsCategoryList` | 商品分类列表 | P2 |
| `GoodsList` | 商品列表 | P2 |
| `GoodsVarieties` | 品种列表 | P2 |
| `GoodsQuote` / `GoodsQuotes` / `GoodsQuotesList` | 单/批量/列表行情 | P2 |
| `GoodsKLine` | 商品 K 线（含持仓量/结算价） | P2 |
| `GoodsTickChart` | 商品分时 | P3 |
| `GoodsChartSampling` | 商品图表采样 | P3 |
| `GoodsHistoryTransaction` | 商品历史逐笔 | P3 |

**覆盖交易所**：中金所(CFFEX)、上期所(SHFE)、大商所(DCE)、郑商所(CZCE)、能源中心(INE)、广期所(GFEX)、外汇/贵金属(FX)。

### 5.5 MAC 专属协议 / F10 资料协议

**MAC（~16 解析器）**：板块列表/成分股、统一 K 线/统一行情、资金流向/主力监控、竞价/多日分时。

**F10（7615/TQLEX，18+ Entry）**：
```
tdxf10_gg_comreq   请求入口      tdxf10_gg_gsgk    公司概况
tdxf10_gg_jyfx     主营构成      tdxf10_gg_gdyj    股东增减持
tdxf10_gg_fhrz     分红融资      tdxf10_gg_cwfx    财务报表
tdxf10_gg_cwzd     财务诊断      tdxf10_gg_ggzp    个股总评
tdxf10_gg_ybpj     盈利预测      tdxf10_gg_rdtc    热点题材
tdxf10_gg_zlcc     沪深股通持仓  tdxf10_gg_gszx    资讯研报
tdxf10_gg_idreq    详情正文      tdxf10_gg_zbyz    资本运作
tdxf10_gg_zjhhy    证监会行业    tdxf10_gg_tdxhy   通达信行业
tdxf10_gg_zxts_rqpm 排名
hq_nlp_tcihq       题材概念行情  hq_nlp_gpsj       估值市场数据
tzx_rcache         新闻/公告/路演
```

### 5.6 专业财务数据（字段元数据驱动）★

通达信官方公开的专业财务数据项共**数百项**，分 7 大类（每股指标 / 资产负债表 / 利润表 / 现金流量表 / 财务指标 / 杜邦分析 / 行业对比）。

**设计**：用**字段元数据表**（JSON）驱动解析，而非硬编码数百字段。字段表从官方公开文档独立整理。

```json
{
  "version": "2026.08",
  "source": "help.tdx.com.cn 公开文档（自行整理）",
  "categories": {
    "per_share": {
      "name": "每股指标",
      "fields": [
        {"id": 1, "name": "basic_eps", "label": "基本每股收益",
         "type": "float32", "unit": "yuan"},
        {"id": 2, "name": "diluted_eps", "label": "扣非每股收益",
         "type": "float32", "unit": "yuan"}
      ]
    },
    "balance_sheet": {"name": "资产负债表", "fields": [ "...60+ 项..." ]}
  }
}
```

### 5.7 L2：通用启发式解析器

针对**未注册命令**（含未来新增命令、MAC 未覆盖命令、厂商私有命令）：

```python
class GenericParser(BaseParser):
    """通用启发式解析器 —— 未知命令兜底

    策略（按序尝试，返回置信度最高的结果）：

    S1 定长记录推测
       · 候选记录长度：32 / 16 / 24 / 40 / 8 / 64 字节
       · 校验 body_len % record_len == 0 且记录数 ∈ [1, 100000]

    S2 记录数前缀识别（TDX 最常见结构）
       · body[0:2] uint16 作为记录数 N
       · 校验 (body_len - 2) % N == 0 → record_len = (body_len-2)/N

    S3 字段类型推断
       对每个 4 字节槽位并行假设：uint32 / int32 / float32 / varint_price
       启发式打分：
         · uint32 日期：19000101 <= v <= 21001231
         · uint32 时间：v <= 2359 或 v <= 1439
         · float32：1e-6 <= v <= 1e9 且指数位合理
         · varint：首字节高 2 位标记位匹配

    S4 输出
       返回推测字段列表 + 每字段置信度 + 整体置信度 + raw bytes
    """

    @classmethod
    def parse(cls, body: bytes, *, msg_id: int) -> GenericResult:
        """GenericResult:
        record_len: int | None
        record_count: int
        fields: list[FieldGuess]   # (offset, size, type, confidence)
        confidence: float          # 0-1
        raw: bytes
        warnings: list[str]
        """
```

**使用**：
```python
result = client.call_raw(msg_id=0x1234, payload=b"...")
# result.tier       = "L2"
# result.confidence = 0.62
# result.fields     = [FieldGuess(offset=0, size=4, type="uint32_date", confidence=0.95), ...]
# result.warnings   = ["msg_id 0x1234 未注册，使用通用解析器，置信度 0.62"]
```

### 5.8 L3：原始透传 + ProtocolSniffer

```python
class ProtocolSniffer:
    """协议探针 —— 未知命令的观测、记录、归档

    触发条件：L1 未注册 或 L2 置信度 < 0.5

    动作：
      1. 记录完整请求/响应字节
         → ~/.tstdx/sniffer/<date>/<msg_id>_<seq>.bin
      2. 记录上下文：主站地址、时间戳、请求 payload、会话状态
      3. 生成 spec 草案
         → PROTOCOL_SPEC/UNKNOWN/<date>_<msg_id>.md
         含 hexdump、长度分析、字节熵值、通用解析推测结果
      4. （可选）上报遥测，供社区协同补全协议
    """

    def capture(
        self, *, msg_id: int, request: bytes, response: bytes, context: RequestContext
    ) -> SnifferRecord: ...

    def generate_spec_draft(self, record: SnifferRecord) -> Path: ...
```

**Sniffer 产出的 spec 草案示例**：
```markdown
# PROTOCOL_SPEC/UNKNOWN/2026-08-31_0x1234.md

## 观测记录
- msg_id: 0x1234
- 主站: 115.238.56.198:7709
- 时间: 2026-08-31 10:15:32.123
- 请求 payload: 0100 36303035313900 0400 0000 2001
- 响应长度: 12832 bytes

## 长度分析
- body_len = 12830
- 12830 / 32 = 400.9          ✗ 非整除
- (12830 - 2) / 32 = 400.875  ✗
- body[0:2] = 0x0190 = 400 → 12828 / 400 = 32.07  ✗
- 推测：非定长记录结构，或含变长字段

## 字节分布
- 熵值: 6.2 bits/byte（中等，非加密非纯文本）
- 前 16 字节 hexdump: ...

## 通用解析推测
- 置信度 0.41（低于阈值 0.5，判定为无法自动解析）

## 待人工分析
[ ] 确认记录结构   [ ] 确认字段语义   [ ] 注册精确解析器
```

### 5.9 主动协议探测（ProtocolProber）

```python
class ProtocolProber:
    """主动探测命令空间

    对指定 msg_id 范围发送探测请求，观察响应：
      · 空响应 / 错误响应 → 该命令不存在或需参数
      · 有响应 → 记录到 Sniffer，尝试通用解析

    ⚠ 安全约束（强制）：
      · 默认关闭，需显式启用
      · 强制限速 ≤ 1 req/s，避免对主站造成压力
      · 仅在非交易时段允许
      · 单会话探测上限 100 次
      · 探测前需用户确认
    """
```

### 5.10 覆盖度度量与 CLI

```bash
tstdx protocol coverage              # 覆盖度报告
tstdx protocol list                  # 列出所有已注册命令
tstdx protocol unknown               # 列出 Sniffer 捕获的未知命令
tstdx protocol draft 0x1234          # 生成/查看 spec 草案
tstdx protocol probe --range 0x1000-0x1100 --rate-limit 1
```

```python
def protocol_coverage_report() -> CoverageReport:
    """指标：
    L1 精确覆盖数 / 已知命令总数
    未知命令观测数（Sniffer 记录）
    未知命令中已补全数
    命令空间探测覆盖率
    """
```

---

## 6. 数据兼容 Profile 系统

> **硬性约束：要求兼容所有数据。**
> 核心思路：把所有数据差异**参数化**，用 `DataProfile` 统一描述，运行时自动探测 + 显式覆盖。

### 6.1 Profile 定义

```python
@dataclass(frozen=True)
class DataProfile:
    """数据规格档案 —— 描述一类数据的全部解码规则"""

    # === 身份 ===
    name: str  # 预设名，如 "a_share_equity_daily"
    market: Market
    instrument: InstrumentType
    period: Period

    # === 数值口径 ===
    price_scale: float
    # 0.01   → 原始值 ×100（A股/指数常见）
    # 0.001  → 原始值 ×1000（部分版本/债券/港股）
    # 0.0001 → 原始值 ×10000（外汇/美股高精度）
    # 1.0    → 无缩放（float 直读，分钟线常见）
    price_encoding: PriceEncoding  # FIXED_INT / FLOAT32 / VARINT
    volume_unit: VolumeUnit  # SHARE(股) / LOT(手) / CONTRACT(合约)
    volume_encoding: VolumeEncoding  # FIXED_UINT32 / VARINT / FLOAT32
    amount_unit: AmountUnit  # YUAN(元) / WAN_YUAN(万元) / YI_YUAN(亿元)
    amount_encoding: AmountEncoding

    # === 时间与编码 ===
    time_encoding: TimeEncoding
    # YYYYMMDD_UINT32 / LC_UINT16 / MINUTE_OFFSET / EPOCH
    encoding: str  # gbk / gb18030 / utf-8 / big5
    endian: str  # little（TDX 标准）
    timezone: str  # Asia/Shanghai / Asia/Hong_Kong / America/New_York

    # === 结构 ===
    record_size: int  # 0 表示变长
    has_count_prefix: bool
    ohlc_order: tuple[str, ...]

    # === 品种特化 ===
    extra_fields: dict[str, FieldSpec]
    # 期货: open_interest(持仓量), settlement(结算价)
    # 期权: implied_vol, delta, gamma, theta, vega
    # 债券: ytm(到期收益率), duration(久期), coupon(票面利率)
    # 基金: nav(净值), discount_rate(折溢价率)
```

### 6.2 市场 × 品种 × 周期 覆盖矩阵

**市场（12 类）**

| Market | 说明 | 端口/协议 |
|---|---|---|
| `SH` | 上交所（含科创板） | 7709 |
| `SZ` | 深交所（含创业板） | 7709 |
| `BJ` | 北交所 | 7709 |
| `HK` | 中国香港市场 | 7727 |
| `US` | 美股 | 7727 |
| `CFFEX` | 中金所（股指/国债期货） | Goods |
| `SHFE` | 上期所 | Goods |
| `DCE` | 大商所 | Goods |
| `CZCE` | 郑商所 | Goods |
| `INE` | 上海能源中心 | Goods |
| `GFEX` | 广期所 | Goods |
| `FX` | 外汇/贵金属/现货 | Goods |

**品种（10 类）**

| InstrumentType | 说明 | 特化字段 |
|---|---|---|
| `EQUITY` | 股票（A股/B股） | — |
| `FUND` | 基金（ETF/LOF/场外） | 净值、折溢价率 |
| `BOND` | 债券（国债/企债/可转债） | 到期收益率、久期、票面利率 |
| `INDEX` | 指数 | 成分股数量、加权方式 |
| `WARRANT` | 权证 | 行权价、行权比例 |
| `FUTURE` | 期货 | 持仓量、结算价、合约乘数 |
| `OPTION` | 期权（ETF/股指/商品） | 隐含波动率、希腊字母 |
| `FOREX` | 外汇 | 点差、买卖价 |
| `SPOT` | 现货（贵金属等） | — |
| `REPO` | 回购 | 回购利率、期限 |

**周期（12 档）**

`TICK` / `MIN1`(8) / `MIN5`(0) / `MIN15`(1) / `MIN30`(2) / `MIN60`(3) / `DAILY`(4,9) / `WEEKLY`(5) / `MONTHLY`(6) / `QUARTERLY`(10) / `YEARLY`(11) / `MULTI_DAY`

### 6.3 预设 Profile 库（示例）

```python
PROFILES = {
    "a_share_equity_daily": DataProfile(
        market=Market.SH, instrument=InstrumentType.EQUITY, period=Period.DAILY,
        price_scale=0.01, price_encoding=PriceEncoding.FIXED_INT,
        volume_unit=VolumeUnit.SHARE, volume_encoding=VolumeEncoding.FIXED_UINT32,
        amount_unit=AmountUnit.YUAN, amount_encoding=AmountEncoding.FLOAT32,
        time_encoding=TimeEncoding.YYYYMMDD_UINT32,
        encoding="gbk", endian="little", timezone="Asia/Shanghai",
        record_size=32, has_count_prefix=True,
        ohlc_order=("open", "high", "low", "close"),
    ),

    "a_share_equity_min1": DataProfile(
        # 分钟线：float32 价格，无缩放；LC_UINT16 日期编码
        price_scale=1.0, price_encoding=PriceEncoding.FLOAT32,
        time_encoding=TimeEncoding.LC_UINT16, record_size=32, ...
    ),

    "a_share_bond_daily": DataProfile(
        instrument=InstrumentType.BOND, price_scale=0.001,
        extra_fields={"ytm": ..., "duration": ..., "coupon": ...},
    ),

    "cffex_future_daily": DataProfile(
        market=Market.CFFEX, instrument=InstrumentType.FUTURE,
        price_scale=0.01, volume_unit=VolumeUnit.CONTRACT,
        extra_fields={"open_interest": FieldSpec(offset=28, size=4, type="uint32"),
                      "settlement": FieldSpec(...)},
    ),

    "etf_option_daily": DataProfile(
        instrument=InstrumentType.OPTION, volume_unit=VolumeUnit.CONTRACT,
        extra_fields={"implied_vol": ..., "delta": ..., "gamma": ...},
    ),

    "hk_equity_daily": DataProfile(
        market=Market.HK, price_scale=0.001,   # 港股 3 位小数
        timezone="Asia/Hong_Kong", encoding="big5", ...
    ),

    "us_equity_daily": DataProfile(
        market=Market.US, price_scale=0.0001,  # 美股 4 位小数
        timezone="America/New_York", encoding="utf-8", ...
    ),

    "fx_spot_daily": DataProfile(
        market=Market.FX, instrument=InstrumentType.FOREX,
        price_scale=0.0001, ...
    ),
}
```

### 6.4 自动探测（ProfileDetector）

```python
class ProfileDetector:
    """Profile 自动探测

    流程：
      1. 从 (market, instrument, period) 查预设表
      2. 命中后用实际数据校验；未命中或校验失败 → 启动探测

    探测方法：
      A. 记录长度（本地文件）
         record_size ∈ {8,16,24,32,40,64}，校验 filesize % size == 0

      B. 价格缩放
         候选 scale ∈ {1.0, 0.1, 0.01, 0.001, 0.0001} 解码前 N 条
         选使价格落在 [0.0001, 100000] 且 OHLC 自洽的 scale

      C. 编码类型
         试 float32 → 值域合理且 OHLC 自洽 → FLOAT32
         试 uint32 × scale → 合理 → FIXED_INT

      D. 成交量单位
         avg = amount / volume
         avg 落在价格量级 → SHARE 正确
         avg 偏离 100 倍  → 应为 LOT（手）

      E. 时间编码
         YYYYMMDD_UINT32: 19000101 <= v <= 21001231
         LC_UINT16:       解码后年月日合理
         EPOCH:           946684800 <= v <= 4102444800

      F. 字符编码
         GBK / GB18030 / UTF-8 / Big5 启发式 + 中文字符集校验

    返回：ProbeResult(profile, confidence, evidence)
    """
```

**置信度阈值**：
| 置信度 | 行为 |
|---|---|
| ≥ 0.9 | 静默采用 |
| 0.7–0.9 | 采用 + warning 日志 |
| 0.5–0.7 | 采用 + 结果附 `profile_warning` 字段 |
| < 0.5 | 抛 `TstdxProfileError(E4002)`，要求显式指定 |

### 6.5 三级覆盖机制

```python
# L1 显式指定完整 Profile
reader = DailyBarReader(vipdoc, profile=PROFILES["hk_equity_daily"])

# L2 部分覆盖（继承预设，覆盖单字段）
reader = DailyBarReader(
    vipdoc,
    profile_overrides={
        "price_scale": 0.001,
        "volume_unit": VolumeUnit.LOT,
    },
)

# L3 传统参数（向后兼容，等价于 profile_overrides）
reader = DailyBarReader(vipdoc, coefficient=0.01, volume_unit="lot")

# L4 自动探测（默认开启）
reader = DailyBarReader(vipdoc, auto_detect=True)
```

### 6.6 兼容性矩阵测试

```python
# tests/compatibility/test_matrix.py

COMBINATIONS = itertools.product(
    [
        Market.SH,
        Market.SZ,
        Market.BJ,
        Market.HK,
        Market.US,
        Market.CFFEX,
        Market.SHFE,
        Market.DCE,
        Market.CZCE,
    ],
    [
        InstrumentType.EQUITY,
        InstrumentType.FUND,
        InstrumentType.BOND,
        InstrumentType.INDEX,
        InstrumentType.FUTURE,
        InstrumentType.OPTION,
    ],
    [Period.MIN1, Period.MIN5, Period.DAILY, Period.WEEKLY],
)


@pytest.mark.parametrize("market,instrument,period", COMBINATIONS)
def test_matrix(market, instrument, period):
    """每个组合必须能正确解码

    校验项：
      · 不抛异常（或明确返回"无数据"）
      · 记录数 > 0
      · OHLC 自洽：low <= open <= high, low <= close <= high
      · price > 0, volume >= 0
      · 时间戳递增且落在合理范围
      · Profile 探测置信度 >= 0.7
    """
```

---

## 7. Transport 传输层

### 7.1 报文格式（Wire Format）

```
请求头（Request Header，12 字节）：
┌────────┬────────┬─────────┬─────────┬────────┬────────┐
│ 1 byte │ 4 byte │ 1 byte  │ 2 byte  │ 2 byte │ 2 byte │
│  zip   │ seq_id │ pkt_type│ pkg_len1│pkg_len2│ method │
└────────┴────────┴─────────┴─────────┴────────┴────────┘
  0x0c    递增     0x01      len+2    len+2    msg_id

响应头（Response Header，16 字节）：
┌────────┬───────┬────────┬───────┬────────┬─────────┬──────────┐
│ 4 byte │1 byte │ 4 byte │1 byte │ 2 byte │ 2 byte  │  2 byte  │
│   I1   │  I2   │ seq_id │  I3   │ method │ zip_size│unzip_size│
└────────┴───────┴────────┴───────┴────────┴─────────┴──────────┘

压缩：zip_size != unzip_size 时 body 需 zlib 解压
```

```python
class WireFormat:
    REQ_HEADER_SIZE = 12
    RESP_HEADER_SIZE = 16
    MAX_MESSAGE_SIZE = 1 << 15  # 32KB

    @staticmethod
    def pack_request(msg_id: int, payload: bytes, seq_id: int, zip: bool = False) -> bytes: ...

    @staticmethod
    def unpack_response_header(data: bytes) -> ResponseHeader: ...

    @staticmethod
    def decode_body(header: ResponseHeader, raw: bytes) -> bytes:
        """按需 zlib 解压"""
```

### 7.2 变长数值编码（VarintCodec）★

TDX 使用**变长编码**压缩价格和成交量，这是兼容性的关键点：

```python
class VarintCodec:
    """TDX 变长数值编解码

    价格编码结构：
      首字节高 2 位为标记位，决定后续字节数与小数位
        00: 1 字节，值 = b0
        01: 2 字节，值 = ((b0 & 0x3f) << 8) | b1，小数 2 位
        10: 4 字节，值 = ((b0 & 0x3f) << 24) | ...，小数 3–4 位
        11: 4 字节 float32

    成交量编码：类似结构，整数语义

    ⚠ 需通过抓包 + 自采集 golden 数据独立验证，
       spec 见 PROTOCOL_SPEC/wire_format.md
    """

    @staticmethod
    def decode_price(data: bytes, offset: int) -> tuple[float, int]:
        """返回 (价格, 消耗字节数)"""

    @staticmethod
    def decode_volume(data: bytes, offset: int) -> tuple[int, int]:
        """返回 (成交量, 消耗字节数)"""
```

### 7.3 主站列表与测速

```python
DEFAULT_HOSTS_7709 = [...]   # 43 台候选
DEFAULT_HOSTS_7727 = [...]   # 16 台候选
MAC_HOSTS_7709     = [...]   # 3 台专属

class ServerRanking:
    async def benchmark(self, top_n: int = 5, timeout: float = 3.0)
    def save(self)   # → ~/.tstdx/server_ranking.json
    def load(self)
```

### 7.4 连接池与 Slot 模型

```python
@dataclass
class SlotConfig:
    num_servers: int = 2
    connections_per_server: int = 4
    # 默认总 Slot = 8；可扩展到 20 × 8 = 160

    raw_buffer_max_mib: int = 256
    decoded_buffer_max_mib: int = 2048

class ConnectionPool:
    async def acquire(self) -> TdxConnection
    async def release(self, conn: TdxConnection, success: bool)
    # 健康评分：失败 ×0.5，成功 +0.2，上限 1.0
    # 降级阈值 < 0.3 移出可用列表；每 60s 探测恢复
```

### 7.5 心跳 / 编解码

```python
class Heartbeat:
    interval: float = 30.0
    command: int = 0x0004
    # 备用探测：0x0015 Ping

class Codec:
    @staticmethod
    def decode_text(data: bytes, encoding: str = "gbk") -> str:
        """GBK/GB18030/Big5/UTF-8（Rust 内核用 encoding_rs）"""
    @staticmethod
    def zlib_decompress(data: bytes) -> bytes
```

---

## 8. Client 客户端层

### 8.1 客户端矩阵

| Client | 端口 | 覆盖 | 说明 |
|---|---|---|---|
| `StandardClient` | 7709 | A股/基金/债券/指数/B股 | 标准协议 36 命令 |
| `ExtendedClient` | 7727 | 港股/美股 | 扩展市场协议 |
| `MacClient` | 7709(MAC服务器) | 板块/资金流向/统一行情 | MAC 协议 |
| `GoodsClient` | 7727 | 期货/期权/外汇/贵金属 | ★ 商品语义协议 |
| `F10Client` | 7615 | F10 资料 | TQLEX 网关 |
| `AsyncTdxClient` | — | 全协议 | 异步版本 |

### 8.2 统一接口

```python
class BaseClient:
    """客户端基类 —— 统一接口"""

    # === 会话 ===
    def login(self) -> LoginResult          # 0x000d + 0x0fdb 两步握手
    def heartbeat(self) -> bool             # 0x0004
    def ping(self) -> float                 # 0x0015，返回延迟
    def server_info(self) -> ServerInfo     # 0x0015

    # === 证券列表 ===
    def security_count(self, market) -> int            # 0x044e
    def security_list(self, market) -> list[Security]  # 0x044d
    def security_list_old(self, market) -> list        # 0x0450
    def security_feature(self, code) -> SecurityFeature  # 0x0452

    # === K 线（统一入口，自动适配周期/品种/复权/Profile）===
    def kline(self, code, *, period=Period.DAILY, count=800, start=0,
              adjust=AdjustMode.NONE, profile=None, output="dict") -> list

    # === 分时 / 图表 ===
    def minute(self, code, *, output="dict")                    # 0x0537
    def history_minute(self, code, date, *, output="dict")      # 0x0feb
    def chart_sampling(self, code, *, output="dict")            # 0x0fd1
    def volume_profile(self, code, *, output="dict")            # 0x051a

    # === 行情 ===
    def quotes(self, codes, *, output="dict") -> list           # 0x054c
    def quotes_detail(self, codes, *, output="dict")            # 0x053e
    def quotes_list(self, market, *, output="dict")             # 0x054b
    def quotes_encrypt(self, codes, *, output="dict")           # 0x0547 五档

    # === 逐笔 / 成交 ===
    def transaction(self, code, *, output="dict")               # 0x0fc5
    def history_transaction(self, code, date, *, output="dict") # 0x0fb5
    def history_orders(self, code, date, *, output="dict")      # 0x0fb4

    # === 分析 ===
    def index_info(self, code) -> IndexInfo          # 0x051d
    def index_momentum(self, code) -> list            # 0x051c
    def top_board(self, market) -> list               # 0x053f 涨停板
    def unusual(self, market) -> list                 # 0x0563 异动
    def auction(self, code) -> AuctionData            # 0x056a 竞价

    # === 基本面 ===
    def xdxr(self, code) -> list[XDXREvent]           # 0x000f 除权除息
    def finance(self, code) -> FinanceInfo            # 0x0010
    def company_category(self, code) -> list          # 0x02cf
    def company_content(self, code, category) -> str  # 0x02d0

    # === 文件 / 板块 ===
    def file_meta(self, filename) -> FileMeta         # 0x02c5
    def download_file(self, filename, *, offset=0) -> bytes  # 0x06b9
    def block_data(self, block_type) -> list          # 0x06b9

    # === 公告 ===
    def exchange_announcement(self) -> list           # 0x0002
    def announcement(self) -> list                    # 0x000a

    # === ★ 通用逃生舱：任意命令（三层覆盖入口）===
    def call_raw(self, msg_id: int, payload: bytes = b"") -> RawResult:
        """调用任意命令（含未注册命令）

        RawResult:
          - tier: "L1" | "L2" | "L3"   解析层级
          - data: 解析结果
          - confidence: float
          - raw: bytes                  原始响应
          - warnings: list[str]
        """
```

### 8.3 GoodsClient（期货/期权/外汇）

```python
class GoodsClient(BaseClient):
    """商品语义客户端

    覆盖：CFFEX / SHFE / DCE / CZCE / INE / GFEX / FX
    """

    def goods_count(self, category: str) -> int
    def goods_category_list(self) -> list[GoodsCategory]
    def goods_list(self, category: str) -> list[Goods]
    def goods_varieties(self) -> list[Variety]
    def goods_quote(self, code: str) -> GoodsQuote
    def goods_quotes(self, codes: list[str]) -> list[GoodsQuote]
    def goods_kline(self, code, *, period=Period.DAILY,
                    count=800) -> list        # 含持仓量/结算价
    def goods_tick_chart(self, code) -> list
    def goods_history_transaction(self, code, date) -> list
```

### 8.4 客户端工厂

```python
def create_client(
    code=None,
    *,
    market=None,
    instrument=None,
    region="cn",
    async_mode=False,
    slot_config=None,
    auto_detect=True,
) -> BaseClient:
    """智能工厂：根据代码/市场/品种自动选择客户端

    决策逻辑：
      code 以 6/9 开头 → SH
      code 以 0/2/3 开头 → SZ
      code 以 4/8 开头 → BJ
      code 含 .HK → ExtendedClient（港股）
      code 含 .US → ExtendedClient（美股）
      instrument ∈ {FUTURE, OPTION, FOREX, SPOT} → GoodsClient
      region ∈ {hk, us} → ExtendedClient
    """
```

### 8.5 重试与 failover

```python
class RetryPolicy:
    """重试：指数退避 0.1s → 0.5s → 1.0s → 2.0s，最多 4 次
    跨主站 failover：失败后切换到下一台主站
    全部主站失败 → TstdxConnectionError(E1004)
    """
```

---

## 9. Reader 本地文件解析层

### 9.1 Reader 矩阵

| Reader | 文件 | Profile 关键参数 |
|---|---|---|
| `DailyBarReader` | `.day` 日线 | `record_size=32`, `price_encoding=FIXED_INT` |
| `MinBarReader` | `.lc1` 1分钟 | `record_size=32`, `price_encoding=FLOAT32` |
| `LcMinBarReader` | `.lc5` 5分钟 | 同上 |
| `BlockReader` | `.dat` 板块 | 变长 |
| `FinancialReader` | `gpcw*.dat` 财务 | 字段元数据驱动 |
| `ProfessionalFinanceReader` | 专业财务 | ★ 字段元数据表驱动 |

### 9.2 .day 日线格式（32 字节，小端）

```
偏移  大小  类型     字段       说明
0     4     uint32   date      日期 YYYYMMDD
4     4     uint32   open_raw  开盘价 ÷ price_scale
8     4     uint32   high_raw  最高价
12    4     uint32   low_raw   最低价
16    4     uint32   close_raw 收盘价
20    4     float32  amount    成交额（amount_unit）
24    4     uint32   volume    成交量（volume_unit）
28    4     uint32   extra     上日收盘 / 期货持仓量

记录数 = filesize / 32（由 Profile 决定，不再硬编码）
```

### 9.3 .lc1/.lc5 分钟线格式（32 字节，小端）

```
偏移  大小  类型     字段
0     2     uint16   date_code       LC 编码日期
2     2     uint16   minute_offset   从 0 点起的分钟数
4     4     float32  open
8     4     float32  high
12    4     float32  low
16    4     float32  close
20    4     float32  amount
24    4     uint32   volume
28    4     —        保留

LC 日期解码：
  year  = floor(num / 2048) + 2004
  month = floor(mod(num, 2048) / 100)
  day   = mod(mod(num, 2048), 100)
```

### 9.4 文件路径规则

```
vipdoc/
├── sh/lday/sh600519.day          沪市日线
├── sh/minline/sh600519.lc1       沪市 1 分钟
├── sh/fzline/sh600519.lc5        沪市 5 分钟
├── sz/lday/sz000001.day          深市日线
└── bj/lday/bj430047.day          北交所日线
T0002/hq_cache/
├── block_gn.dat                  概念板块
├── block_zs.dat                  指数板块
├── block_fg.dat                  风格板块
└── gpcw20260831.dat              财务数据
```

### 9.5 专业财务数据（字段元数据驱动）

```python
class ProfessionalFinanceReader:
    """专业财务数据解析（字段元数据驱动，避免硬编码数百字段）"""

    def __init__(self, metadata_path: Path | None = None):
        self.metadata = load_metadata(metadata_path)

    def read(
        self,
        gpcw_file: Path,
        *,
        categories: list[str] | None = None,
        fields: list[str] | None = None,
        output="dict",
    ) -> list[dict]:
        """按类别/字段筛选读取"""
```

---

## 10. 实时数据 Streaming 子系统

> **硬性约束：支持获取实时数据。**
> 设计原则：**Streaming 是一等公民**，不是轮询的补丁。

### 10.1 架构

```
┌──────────────────────────────────────────────────────────┐
│                  Subscription Registry                    │
│   subscribe(codes, channels=[quote,tick,kline,auction])  │
└───────────────────────┬──────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌───────────────┐ ┌──────────────┐ ┌──────────────┐
│  PushChannel  │ │ PollChannel  │ │ HybridChannel│
│  0x0547 推送   │ │ 定时轮询      │ │ 推送+轮询兜底 │
│  五档增量      │ │ 054c/0fc5    │ │              │
└───────┬───────┘ └──────┬───────┘ └──────┬───────┘
        └────────────────┼────────────────┘
                         ▼
              ┌─────────────────────┐
              │   DeltaMerger       │  增量合并
              │  · 快照 + 增量       │  去重/乱序/回退
              │  · 版本号 / 时间戳   │
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │   GapFiller         │  断线补数
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │  BackpressureCtrl   │  背压控制
              └──────────┬──────────┘
                         ▼
            AsyncIterator / Callback / WebSocket
```

### 10.2 订阅管理

```python
@dataclass
class Subscription:
    codes: set[str]
    channels: set[Channel]        # QUOTE / TICK / KLINE / AUCTION
    profile: DataProfile | None
    mode: StreamMode              # PUSH / POLL / HYBRID
    poll_interval: float = 3.0
    queue_size: int = 1024
    on_event: Callable | None
    on_error: Callable | None

class SubscriptionRegistry:
    def subscribe(self, codes, *, channels=[Channel.QUOTE],
                  mode=StreamMode.HYBRID, profile=None,
                  on_event=None) -> Subscription
    def unsubscribe(self, codes, channels=None)
    def list_subscriptions(self) -> list[Subscription]
    async def stream(self) -> AsyncIterator[StreamEvent]
```

### 10.3 推送通道（0x0547 QuotesEncrypt）

```python
class PushChannel:
    """五档推送通道

    · 服务端主动推送增量帧，需维持长连接 + 心跳
    · 队列容量：1024 帧 / 64 MiB
    · 溢出策略：drop_oldest（默认） / drop_newest / block / sample
    · pin 机制：可固定订阅特定股票，防止被挤出

    帧结构：数量前缀 + N 条增量记录
            每条含：代码、五档价量、成交量变化、时间戳
    """

    queue_capacity: int = 1024
    queue_max_bytes: int = 64 * 1024 * 1024
    overflow_policy: str = "drop_oldest"

    async def run(self): ...
```

### 10.4 轮询通道

```python
class PollChannel:
    """轮询通道 —— PUSH 不可用时的兜底 / 无推送命令的实时化

    适用命令：0x054c 批量行情 / 0x0fc5 逐笔 / 0x053f 涨停板 / 0x056a 竞价

    策略：
      · 按 poll_interval 定时拉取（默认 3s）
      · 交易时段自适应：盘中 1–3s，盘后 10–60s
      · 批量合并：多只股票合并到单次请求（≤60 只/次）
      · 去重：与上次快照比对，仅投递变化部分
    """

    def _adaptive_interval(self) -> float: ...
```

### 10.5 增量合并（DeltaMerger）

```python
class DeltaMerger:
    """增量合并 —— 把增量帧合并为完整快照

    核心问题与解决：
      1. 乱序：维护 (version, timestamp, snapshot)
               乱序窗口 out_of_order_window = 0.5s，窗口内可重排
      2. 重复：只接受 version/timestamp 严格递增的帧
      3. 回退：主站间数据不一致时，后到帧时间戳更旧 → 丢弃并告警
      4. 缺失：version 跳跃 > 1 → 触发 GapFill

    返回：更新后的完整快照（无变化则返回 None）
    """

    async def merge(self, frame: PushFrame) -> Snapshot | None: ...
```

### 10.6 断线补数（GapFiller）

```python
class GapFiller:
    """断线补数 —— 保证数据完整性 100%

    触发：连接断开重连后 / 版本号跳跃 / 心跳超时恢复

    策略：
      1. 记录断线时刻 T0 与恢复时刻 T1
      2. 用快照命令（0x054c）拉取 T1 最新状态
      3. 用历史命令补齐 T0–T1 区间：
         · 逐笔：0x0fc5 当日成交明细（按 offset 翻页）
         · K线： 0x052d（1 分钟周期）
         · 分时：0x0537
      4. 与 T1 快照合并，去重后投递

    保证：
      · 补数在后台进行，不阻塞实时流
      · 补数完成发出 ResyncEvent 通知消费者
      · 补数失败重试，上限 3 次后告警（E7003）
    """

    async def fill(self, code, channel, gap_start, gap_end) -> list[StreamEvent]: ...
```

### 10.7 背压控制

```python
class BackpressureController:
    """背压控制 —— 生产者速度 > 消费者速度

    策略（可配）：
      · drop_oldest  丢弃最旧（默认，保实时性）
      · drop_newest  丢弃最新
      · block        阻塞生产者
      · sample       采样（每 N 保留 1）

    监控：队列水位（高 0.8 告警 / 低 0.5 解除）、丢弃计数、消费延迟 P50/P95/P99
    """
```

### 10.8 重连与容错

```python
class ReconnectPolicy:
    """· 检测：心跳超时（3 次无响应）/ 连接异常 / 读写错误
    · 退避：1s → 2s → 4s → 8s → 30s（上限）
    · 切换：连续失败 3 次 → 换下一台主站
    · 重连后：① 重新 login（0x000d+0x0fdb）
             ② 恢复订阅（re-subscribe all）
             ③ 触发 GapFill 补齐断线数据
             ④ 发出 ReconnectEvent
    """

    initial_backoff: float = 1.0
    max_backoff: float = 30.0
    failover_threshold: int = 3
```

### 10.9 事件模型

```python
@dataclass
class StreamEvent:
    type: EventType  # SNAPSHOT / DELTA / TICK / RESYNC / ERROR / HEARTBEAT
    channel: Channel  # QUOTE / TICK / KLINE / AUCTION
    code: str
    timestamp: datetime
    data: dict
    seq: int  # 客户端单调序号，用于去重
    server_version: int | None
    profile: DataProfile
    is_gap_filled: bool = False


@dataclass
class ResyncEvent(StreamEvent):
    gap_start: datetime
    gap_end: datetime
    filled_count: int
```

### 10.10 使用示例

```python
import asyncio
from tstdx import create_client, Channel, StreamMode, EventType


async def main():
    client = create_client(async_mode=True)

    # 方式 1：异步迭代器
    sub = client.streaming.subscribe(
        codes=["600519", "000001"],
        channels=[Channel.QUOTE, Channel.TICK],
        mode=StreamMode.HYBRID,
    )
    async for event in sub.stream():
        if event.type == EventType.DELTA:
            print(f"{event.code} {event.data['price']}")
        elif event.type == EventType.RESYNC:
            print(f"补数完成，补齐 {event.filled_count} 条")

    # 方式 2：回调
    client.streaming.subscribe(
        codes=["600519"], channels=[Channel.QUOTE], on_event=lambda e: print(e.code, e.data)
    )


asyncio.run(main())
```

### 10.11 WebSocket 网关

```
WS  ws://localhost:8000/ws/stream

订阅： {"action":"subscribe","codes":["600519"],"channels":["quote","tick"]}
       {"action":"unsubscribe","codes":["600519"]}      {"action":"list"}

推送： {"type":"snapshot","code":"600519","ts":"...","data":{...}}
       {"type":"delta",   "code":"600519","ts":"...","data":{...}}
       {"type":"tick",    "code":"600519","ts":"...","data":{...}}
       {"type":"resync",  "code":"600519","filled":42}
       {"type":"error",   "code":"600519","error":"E1004"}
```

### 10.12 性能指标

| 指标 | 目标值 |
|---|---|
| 五档推送端到端延迟 P95 | < 100ms |
| 逐笔吞吐 | ≥ 5,000 笔/秒 |
| 断线检测 | < 5s |
| 重连完成 | < 3s |
| 补数完整性 | 100%（GapFill 成功） |
| 事件队列丢弃率（正常负载） | < 0.1% |
| 内存占用（1000 代码订阅） | < 512 MiB |

---

## 11. 领域服务层

### 11.1 复权引擎

```python
class AdjustEngine:
    """复权计算引擎

    数据源：0x000f XDXR 除权除息事件

    模式：NONE / QFQ / HFQ / FIXED_QFQ / FIXED_HFQ

    公式（A 股标准）：
      f = (1 + 送转率) / (1 + 配股率 × 配股价/市价 + 现金分红/市价)
      adjusted_price = raw_price × f
      多事件累积：从基准点向目标方向累乘所有事件因子
    """

    def adjust(
        self, kline, code, *, mode=AdjustMode.QFQ, anchor_date=None, profile=None
    ) -> list: ...
```

**品种适配**：
| 品种 | 处理 |
|---|---|
| 股票 | 标准除权除息 |
| 基金 | 分红 + 份额拆分 |
| 指数 | **不复权**（指数本身已含调整） |
| 期货 | 主力合约拼接（换月处理），非复权 |
| 债券 | 应计利息调整 |

**MAC 协议特殊处理**：服务端可能返回负值的复权数据，需本地基于 XDXR 重算 QFQ。

### 11.2 缓存

```python
class Cache:
    """多级缓存：内存 LRU（256 MiB）+ 磁盘（1 GiB，~/.tstdx/cache）

    TTL 分级：
      静态数据（证券列表/Spec）  24h
      日线历史（已收盘）         永久（当日收盘后）
      实时行情                   0（不缓存）
      逐笔                       不缓存

    失效：按 key 模式 / 按市场 / 全局
    """
```

### 11.3 请求限流

```python
class RateLimiter:
    """交易时段自适应限流（令牌桶，支持突发）

    盘中 (09:25–15:00)  15 req/s
    盘前盘后             30 req/s
    休市                 60 req/s
    补数/批量下载         独立配额，不占用实时额度

    批量请求限制：单次 ≤ 60 只，超出自动分批
    """
```

### 11.4 下载器

```python
class Downloader:
    """· 多服务器分发（轮询/并行）
    · 自动翻页 / 增量更新 / 断点续传 / 进度回调
    · 限速，不占用实时请求配额
    """

    async def download_financial(self, *, date=None, dest=None, progress=None) -> Path: ...

    async def download_history(
        self, codes, *, period=Period.DAILY, start, end, concurrency=8
    ) -> AsyncIterator: ...
```

### 11.5 数据质量校验

```python
class DataValidator:
    """数据质量校验 —— 防止脏数据流入下游

    OHLC 自洽性（ERROR）：low <= open <= high，low <= close <= high，low <= high
    数值合理性（ERROR）：price > 0，volume >= 0，amount >= 0
    时间序列（WARN）：  时间戳单调递增、无重复、无异常间隔
    业务逻辑（WARN）：  涨跌幅在 ±10%/±20%/±30%（视板块）
                        成交量不异常放大（> 100 倍均值）
                        价格不异常跳变（> 50% 单日）
    完整性（WARN）：    无缺失交易日、无缺失分钟（盘中）

    输出：ValidationReport(issues, severity, sample)
    """

    def validate(self, data: list, profile: DataProfile) -> ValidationReport: ...
```

---

## 12. API 输出层

### 12.1 输出三态

```python
OutputFormat = Literal["dict", "tuple", "dataframe"]


def to_output(data: list[dict], output: OutputFormat = "dict"):
    """
    dict      → list[dict]        调试友好（默认）
    tuple     → list[tuple]       遍历快 40–60%
    dataframe → pandas.DataFrame  分析回测友好（pandas 为可选依赖）
    """
```

### 12.2 Helpers 高级组合封装

```python
class Helpers:
    def full_quotes(self, codes, *, output="dict") -> list:
        """完整行情 = 0x054c + 0x0452 + 0x000f"""

    def auction_data(self, code, *, output="dict") -> dict:
        """竞价数据 = 0x056a + 0x054c"""

    def stock_topics(self, code, *, output="dict") -> dict:
        """题材 = 板块 + 资金流向"""

    def shortline_indicators(self, code, *, output="dict") -> dict:
        """短线指标 = 0x052d + 0x0537 + 0x0fc5"""

    def realtime_rank(self, *, market=Market.SH, sort_by="change_pct", count=100) -> list:
        """涨幅排名 = 0x054b + 排序"""

    def limit_up_pool(self, *, market=Market.SH) -> list:
        """涨停板池 = 0x053f + 0x054c 补全"""

    def capital_flow_rank(self, *, top=50) -> list:
        """资金流向排名（MAC 协议）"""

    def stock_profile_table(self, code) -> dict:
        """个股概况 = F10 + 行情 + 除权除息"""

    def adjusted_kline(self, code, *, period=Period.DAILY, mode=AdjustMode.QFQ, count=800) -> list:
        """复权 K 线 = 0x052d + 0x000f + 复权引擎"""
```

---

## 13. 集成层

### 13.1 CLI

```bash
# 行情
tstdx quotes 600519 000001
tstdx kline 600519 --period daily --count 100 --adjust qfq --output dataframe
tstdx minute 600519
tstdx tick 600519

# 本地文件
tstdx read-day 600519 --vipdoc /path/to/vipdoc --profile a_share_equity_daily
tstdx read-min 600519 --period 1min

# 实时流
tstdx stream 600519 000001 --channels quote,tick
tstdx stream --top 100 --channels quote

# 协议工具
tstdx protocol coverage
tstdx protocol list
tstdx protocol unknown
tstdx protocol draft 0x1234
tstdx protocol probe --range 0x1000-0x1100 --rate-limit 1

# 主站 / 服务
tstdx benchmark --top 5
tstdx server-status
tstdx serve --port 8000
tstdx mcp
```

### 13.2 HTTP REST API

```
# 行情类
GET  /api/quote?codes=600519,000001
GET  /api/kline?code=600519&period=daily&count=100&adjust=qfq
GET  /api/minute?code=600519
GET  /api/tick?code=600519
GET  /api/transaction?code=600519&date=20260830
GET  /api/search?keyword=贵州
POST /api/batch-quote

# 分析类
GET  /api/index-info?code=000001
GET  /api/top-board?market=sh        涨停板
GET  /api/unusual?market=sh          异动
GET  /api/auction?code=600519        竞价
GET  /api/market-stats
GET  /api/workday

# 基本面
GET  /api/xdxr?code=600519           除权除息
GET  /api/finance?code=600519
GET  /api/f10/profile?code=600519
GET  /api/f10/finance?code=600519

# 商品（期货/期权/外汇）
GET  /api/goods/list?category=future
GET  /api/goods/quote?code=IF2609
GET  /api/goods/kline?code=IF2609&period=daily

# 板块
GET  /api/blocks?type=gn
GET  /api/block-stocks?block=xxx

# 系统
POST /api/tasks                      提交异步任务
GET  /api/tasks/{id}                 任务状态
GET  /api/server-status
GET  /api/health
GET  /api/protocol/coverage          协议覆盖度
GET  /api/metrics                    Prometheus 指标
```

### 13.3 WebSocket

```
WS /ws/stream
  subscribe / unsubscribe / list
  推送：snapshot / delta / tick / resync / error
```

### 13.4 MCP 工具服务

```python
MCP_TOOLS = [
    "tstdx_quotes",  # 实时行情（A股/港股/美股/期货）
    "tstdx_kline",  # K线（含复权）
    "tstdx_minute",  # 分时
    "tstdx_tick",  # 逐笔
    "tstdx_blocks",  # 板块
    "tstdx_capital_flow",  # 资金流向
    "tstdx_f10",  # F10 资料
    "tstdx_read_local",  # 本地文件解析
    "tstdx_subscribe_stream",  # ★ 实时订阅
    "tstdx_protocol_probe",  # ★ 协议探测
]
```

---

## 14. 可观测性

### 14.1 指标（Prometheus 格式）

```python
# 连接
tstdx_connection_active          Gauge
tstdx_connection_errors_total    Counter
tstdx_connection_latency_seconds Histogram

# 请求
tstdx_requests_total             Counter{command, market, status}
tstdx_request_duration_seconds   Histogram{command}
tstdx_request_errors_total       Counter{command, error_code}

# 协议（★ 覆盖度监控）
tstdx_protocol_parse_tier        Counter{tier}        # L1/L2/L3
tstdx_protocol_unknown_commands  Counter{msg_id}
tstdx_protocol_confidence        Histogram            # L2 置信度分布

# Streaming
tstdx_stream_events_total        Counter{channel, type}
tstdx_stream_queue_depth         Gauge
tstdx_stream_dropped_total       Counter{reason}
tstdx_stream_latency_seconds     Histogram            # 端到端延迟
tstdx_stream_gapfill_total       Counter{status}
tstdx_stream_reconnect_total     Counter

# Profile
tstdx_profile_detect_confidence  Histogram
tstdx_profile_detect_fallback    Counter

# 数据质量
tstdx_validation_issues_total    Counter{severity, rule}
```

### 14.2 结构化日志

```json
{
  "ts": "2026-08-31T10:15:32.123+08:00",
  "level": "WARNING",
  "logger": "tstdx.protocol",
  "trace_id": "a1b2c3d4",
  "event": "unknown_command",
  "msg_id": "0x1234",
  "host": "115.238.56.198:7709",
  "tier": "L2",
  "confidence": 0.62,
  "message": "msg_id 0x1234 未注册，使用通用解析器"
}
```

### 14.3 请求追踪

```python
@dataclass
class TraceContext:
    trace_id: str
    span_id: str
    command: int | None
    host: str | None
    start_time: float
    # 分段耗时：连接获取 / 发送 / 接收 / 解析 / 校验
```

**用途**：定位"慢请求"究竟慢在哪一环（网络 / 解析 / 校验）。

---

## 15. 数据模型与错误体系

### 15.1 核心数据模型

```python
class Security(TypedDict):
    code: str
    name: str
    market: Market
    instrument: InstrumentType


class Bar(TypedDict):
    """统一 K 线（兼容所有品种）"""

    datetime: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    # 品种特化字段按 profile.extra_fields 动态填充
    open_interest: int | None  # 期货持仓量
    settlement: float | None  # 期货结算价
    implied_vol: float | None  # 期权隐含波动率


class Quote(TypedDict):
    code: str
    name: str
    price: float
    pre_close: float
    open: float
    high: float
    low: float
    volume: int
    amount: float
    bid_p: tuple[float, ...]  # 买五档价
    bid_v: tuple[int, ...]
    ask_p: tuple[float, ...]  # 卖五档价
    ask_v: tuple[int, ...]


class Tick(TypedDict):
    """逐笔成交"""

    time: str
    price: float
    volume: int
    direction: int  # 0=买 1=卖 2=中性
    order_id: int | None


class XDXREvent(TypedDict):
    date: str
    category: int
    bonus_cash: float  # 现金分红
    bonus_share: float  # 送股
    transfer_share: float  # 转增
    rights_share: float  # 配股
    rights_price: float  # 配股价
```

### 15.2 统一错误码

| 范围 | 类别 | 代表码 |
|---|---|---|
| **E1xxx** | 连接错误 | E1001 超时 / E1002 被拒 / E1003 断开 / E1004 所有主站不可用 / E1005 心跳超时 |
| **E2xxx** | 协议错误 | E2001 响应头不匹配 / E2002 长度不足 / E2003 解析失败 / **E2004 命令未注册（降级，非致命）** / E2005 报文超长 |
| **E3xxx** | 数据错误 | E3001 代码不存在 / E3002 数量超限 / E3003 日期无效 / E3004 文件不存在 / E3005 格式错误 / E3006 校验失败 |
| **E4xxx** | 配置错误 | E4001 Profile 无效 / **E4002 Profile 探测失败** / E4003 Slot 配置无效 / E4004 参数组合不支持 |
| **E5xxx** | 限流错误 | E5001 频率超限 / E5002 配额耗尽 |
| **E6xxx** | 超时错误 | E6001 请求超时 / E6002 流式等待超时 |
| **E7xxx** | Streaming | E7001 订阅失败 / E7002 队列溢出 / E7003 补数失败 / E7004 重连失败 |
| **E8xxx** | 品种/市场 | E8001 市场不支持该命令 / E8002 品种不支持该周期 / E8003 该品种无此字段 |

---

## 16. 测试策略

### 16.1 测试金字塔

```
         ┌───────────┐
         │  E2E      │  真实环境 smoke（需网络+交易日）
        ┌┴───────────┴┐
        │ Integration  │  真实主站连接（需网络）
       ┌┴──────────────┴┐
       │  Compatibility  │ ★ 兼容性矩阵（市场×品种×周期）
      ┌┴─────────────────┴┐
      │    Parity          │ ★ 纯 Python vs Rust 一致性
     ┌┴────────────────────┴┐
     │   Protocol Replay     │  协议回放（离线，golden bytes）
    ┌┴───────────────────────┴┐
    │      Golden File         │  本地文件 golden（离线）
   ┌┴──────────────────────────┴┐
   │        Unit Tests           │  纯逻辑（离线）
   └─────────────────────────────┘
```

### 16.2 测试分类与目标

| 类型 | 依赖 | 数量目标 | 说明 |
|---|---|---|---|
| Unit | 无 | 300+ | 纯逻辑，无 IO |
| Golden File | 自抓数据 | 50+ | 真实 `.day/.lc1/.lc5/.dat` 验证 |
| Protocol Replay | 自抓 bytes | 80+ | 录制响应，离线回放 |
| **Parity** | Rust 内核 | 60+ | 纯 Python vs Rust 结果必须一致 |
| **Compatibility** | 网络 | 矩阵 | 市场 × 品种 × 周期组合 |
| Integration | 网络 | 30+ | 真实主站 |
| E2E Smoke | 网络+交易日 | 15+ | 端到端冒烟 |
| **Originality** | CI | — | 相似度 + License + Spec 覆盖 |
| Benchmark | 无 | 15+ | 性能基准 |

### 16.3 Golden 数据自采集规范

> **约束：golden 数据必须自行采集，不得使用参考项目的测试数据。**

```python
# tools/collect_golden.py


class GoldenCollector:
    """Golden 数据采集工具

    采集内容：
      1. 协议响应 bytes
         · 连接自有环境的 TDX 主站
         · 对每个命令发送典型请求
         · 保存 (request_bytes, response_bytes, context) 三元组
         → tests/golden/protocol/<msg_id>_<case>.bin + .json

      2. 本地文件样本
         · 从自有通达信客户端 vipdoc 目录复制
         · 每市场/每品种/每周期至少 1 个样本
         → tests/golden/day/sh600519.day 等

      3. 实时流样本
         · 录制一段真实推送帧序列
         → tests/golden/stream/<date>_<code>.bin

    元数据（.json 伴随文件）：
      {
        "collected_at": "2026-08-31T10:15:32+08:00",
        "host": "115.238.56.198:7709",
        "command": "0x052d",
        "request_params": {...},
        "expected_record_count": 800,
        "expected_first_record": {...},
        "collector_version": "1.0",
        "source": "self-captured"      ← 强制标注来源
      }
    """
```

**CI 校验**：扫描所有 golden 文件元数据，`source != "self-captured"` → 阻断。

### 16.4 Parity 测试（纯 Python vs Rust）

```python
# tests/parity/test_reader_parity.py


@pytest.fixture(params=["python", "native"])
def reader(request, vipdoc_path):
    if request.param == "native":
        pytest.importorskip("tstdx._native")
        return DailyBarReaderNative(vipdoc_path)
    return DailyBarReader(vipdoc_path)


@pytest.mark.parametrize("code,market", ALL_SAMPLE_CODES)
def test_daily_parity(reader, code, market):
    """纯 Python 与 Rust 内核结果必须逐字段一致"""
    bars = reader.read(code, market=market)
    assert len(bars) == EXPECTED[code]["count"]
    assert bars[0]["open"] == EXPECTED[code]["first_open"]
    assert bars[-1]["close"] == EXPECTED[code]["last_close"]


@pytest.mark.parametrize("msg_id", ALL_KNOWN_COMMANDS)
def test_protocol_parity(msg_id, golden_bytes):
    """协议解析两种实现结果一致"""
```

---

## 17. 分阶段实施路线图

### Phase 0：奠基（2 周）

```
□ 项目骨架（pyproject.toml / hatch / 目录结构）
□ 原创性工程规范落地
  □ PROTOCOL_SPEC/ 目录与 spec 模板
  □ ORIGINALITY/ 合规档案
  □ tools/check_originality.py
  □ CI 门禁（相似度 / License / Spec 覆盖）
□ 基础设施
  □ errors.py / types.py / constants.py
  □ observability（metrics / logging / tracing）
□ 报文层
  □ wire.py 报文头编解码（12B req / 16B resp）
  □ codec.py GBK/GB18030/Big5/zlib
  □ varint.py 变长价格/成交量编码
□ tools/collect_golden.py Golden 采集工具
```

### Phase 1：核心协议 + 全兼容底座（5 周）

```
□ Profile 系统
  □ DataProfile 定义 + 预设库（12 市场 × 10 品种）
  □ ProfileDetector 自动探测
  □ 兼容性矩阵测试框架

□ 协议层 L1（MVP 12 命令）
  □ 0x000d + 0x0fdb 两步登录
  □ 0x0004 心跳 / 0x0015 Ping
  □ 0x044d / 0x044e / 0x0450 证券列表
  □ 0x052d K线 / 0x0537 分时
  □ 0x053e / 0x054c / 0x054b 行情
  □ 0x0547 五档推送 / 0x0fc5 逐笔成交
  □ 0x000f 除权除息

□ 协议层 L2/L3 兜底
  □ GenericParser 通用启发式解析
  □ ProtocolSniffer 探针 + spec 草案生成

□ Transport（连接/测速/心跳/failover）
□ Client（StandardClient）
□ Reader（DailyBarReader / MinBarReader，Profile 驱动）
□ 测试：Unit 100+ / Golden 20+ / Replay 30+
```

### Phase 2：全协议覆盖 + 实时流（6 周）

```
□ 协议层 7709 补全（24 命令）
  □ 0x0002 / 0x000a 公告      □ 0x0010 财务
  □ 0x02cf / 0x02d0 公司信息  □ 0x02c5 / 0x06b9 文件与板块
  □ 0x0452 证券特征           □ 0x051a 量价分布
  □ 0x051c 指数动量           □ 0x051d 指数信息
  □ 0x0523 旧版K线            □ 0x053f 涨停板
  □ 0x0563 异动               □ 0x056a 竞价
  □ 0x0fb4 / 0x0fb5 / 0x0fc6  □ 0x0fd1 图表采样 / 0x0feb 历史分时

□ 扩展协议 7727（港股/美股 列表·K线·行情·分时 + ExMapping2562）
□ ★ 商品语义协议 GoodsClient（期货/期权/外汇/贵金属）
□ MAC 协议（板块/成分股/统一K线/统一行情）
□ F10 协议（18+ Entry）

□ ★ Streaming 子系统
  □ SubscriptionRegistry
  □ PushChannel / PollChannel / HybridChannel
  □ DeltaMerger / GapFiller / BackpressureController / ReconnectPolicy
  □ WebSocket 网关

□ Reader（BlockReader / FinancialReader / ProfessionalFinanceReader）
□ 领域服务（复权含品种适配 / 缓存 / 限流 / 下载器 / 数据校验）
□ 测试：Unit 300+ / Golden 50+ / Replay 80+ / Integration 20+
```

### Phase 3：Rust 内核 + 集成生态（5 周）

```
□ Rust 内核
  □ transport.rs / protocol.rs / reader.rs / streaming.rs
  □ PyO3 绑定
  □ 5 平台 wheel（Win x64 / manylinux x64+ARM64 / macOS x64+ARM64）
  □ Parity 测试（60+，强制一致性）
  □ Benchmark 对比报告

□ 集成层
  □ CLI 完整（含 protocol 子命令）
  □ HTTP REST（FastAPI，32+ 接口）
  □ WebSocket RPC
  □ MCP 工具服务（10 工具）

□ AsyncTdxClient（与同步版同形）
□ 可观测性完善（Prometheus 端点 / 结构化日志 / 请求追踪）
□ 文档（协议文档自动生成自 PROTOCOL_SPEC / 架构详解 / API Reference
        / 兼容性矩阵报告 / 原创性审计报告 / 迁移指南）
```

### Phase 4：协议补全 + 生态打磨（4 周）

```
□ 协议探测与补全
  □ ProtocolProber 主动探测（限速、非交易时段）
  □ 处理 Sniffer 累积的未知命令
  □ 补全 spec + 注册精确解析器

□ 边界场景
  □ 极端行情（熔断/停牌/涨跌停）
  □ 特殊品种（ST/*ST/退市/新股）
  □ 跨时区（港股/美股）
  □ 合约换月（期货主力连续）

□ 性能调优（Profile-guided 优化 / 零拷贝路径 / 批量请求合并）
□ 生态（迁移指南 / 示例集 15+ / 社区协同协议补全机制）
```

### 路线图总览

```
Phase 0  奠基            ████░░░░░░░░░░░░░░░░░░░░░░░░░░░░  W1-2
Phase 1  核心+兼容底座   ░░░░██████████░░░░░░░░░░░░░░░░░░░  W3-7
Phase 2  全协议+实时流   ░░░░░░░░░░░░░░████████████░░░░░░░░  W8-13
Phase 3  Rust+集成生态   ░░░░░░░░░░░░░░░░░░░░░░░██████████░  W14-18
Phase 4  补全+打磨       ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░████████  W19-22

总计约 22 周（5.5 个月），每阶段可独立交付
```

---

## 18. 参考项目能力对照表

| 能力维度 | mootdx | easy_tdx | tdx-api | tdxrs | eltdx | **tstdx v2.0** |
|---|---|---|---|---|---|---|
| 标准协议命令数 | 部分 | 部分 | 部分 | 部分 | 21 | **36+ 全量** |
| 未知命令兜底 | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ L2通用+L3透传** |
| 协议探针/主动探测 | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ Sniffer+Prober** |
| 扩展市场 7727 | 部分 | ✓ | 部分 | 部分 | ✗ | **✓** |
| **商品语义(期货/期权/外汇)** | ✗ | 部分 | ✗ | ✗ | ✗ | **✓ GoodsClient** |
| MAC 协议 | ✗ | ✓ | ✗ | ✗ | ✗ | **✓** |
| F10 资料 | ✗ | 部分 | 部分 | feature gate | 20+ | **✓ 18+** |
| 本地文件解析 | ✓ | 部分 | ✗ | ✓ | ✗ | **✓ 6 Reader** |
| 专业财务数据 | ✗ | 部分 | ✗ | ✗ | ✗ | **✓ 元数据驱动** |
| **全市场全品种兼容** | 部分 | 部分 | 部分 | 部分 | 部分 | **✓ Profile 系统** |
| **口径自动探测** | ✗ | ✗ | ✗ | 参数 | ✗ | **✓ 自动探测** |
| **实时推送** | ✗ | 部分 | ✗ | ✗ | 队列 | **✓ 完整子系统** |
| **断线补数** | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ GapFiller** |
| **增量合并** | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ DeltaMerger** |
| 复权 | ✗ | ✓ | ✗ | ✗ | ✗ | **✓ 4 模式+品种适配** |
| 异步 | ✗ | ✓ | ✗ | ✓ | ✗ | **✓** |
| 连接池/Slot | 多线程 | ✓ | ✗ | ✓ | ✓ | **✓ 可配置** |
| 健康评分 failover | ✗ | ✓ | ✗ | ✓ | ✓ | **✓** |
| 限流 | ✗ | ✗ | ✗ | ✓ | ✓ | **✓ 时段自适应** |
| 输出三态 | ✗ | JSON | JSON | ✓ | JSON | **✓** |
| CLI | ✓ | ✓ | WebUI | ✓ | ✗ | **✓ 含 protocol 子命令** |
| HTTP REST | ✗ | ✓ | ✓ | ✗ | ✓ | **✓ 32+ 接口** |
| WebSocket | ✗ | ✗ | ✗ | ✗ | ✓ | **✓ 流式网关** |
| MCP | ✗ | 适配 | ✗ | ✗ | ✓ | **✓ 10 工具** |
| Rust 内核 | ✗ | ✗ | ✗ | ✓ | ✓ | **✓ 可选 + Parity** |
| 可观测性 | ✗ | ✗ | 部分 | ✗ | 部分 | **✓ 指标+日志+追踪** |
| 数据质量校验 | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ Validator** |
| **原创性保障** | — | — | — | — | — | **✓ 洁净室 + CI 审计** |
| License | MIT | 学习研究 | MIT | MIT | **禁止商用** | **MIT** |

### tstdx v2.0 独有优势

1. **协议三层覆盖** — 已知精确解析 + 未知通用解析 + 原始透传，永不丢包；独有 Sniffer/Prober 持续补全协议
2. **商品语义协议** — 期货/期权/外汇/贵金属的 `Goods*` 协议族，多数库完全缺失
3. **Profile 数据规格档案** — 市场×品种×周期×口径×编码全维度参数化 + 自动探测，一处配置全库生效
4. **完整 Streaming 子系统** — 推送+轮询双模、增量合并、断线补数、背压控制、重连容错
5. **原创性工程规范** — 洁净室流程 + Spec 驱动 + AST 相似度审计 + License 白名单，合规可证明
6. **可观测性内建** — 指标/日志/追踪从第一天就有，含协议层级与置信度监控
7. **Parity 保障** — 纯 Python 与 Rust 内核结果强制一致，加速不牺牲正确性

---

## 19. 附录：License 与合规声明

### 19.1 License

**MIT License**

理由：mootdx / tdx-api / tdxrs 均使用 MIT，兼容性最佳；eltdx 明确禁止商用，**绝不可引入其代码**。

### 19.2 原创性声明

```
tstdx 全部代码为原创实现。

协议格式的获取途径：
  · 自有环境网络抓包（连接公开 TDX 主站）
  · 自有通达信客户端本地文件二进制分析
  · 通达信官方帮助中心公开文档
  · 公开技术标准（GBK 编码、zlib 规范等）

架构设计借鉴（仅为设计模式，非代码）：
  · 注册表模式管理协议解析器
  · 分层架构与输出格式多态
  · 连接池与健康评分机制
  · Rust + PyO3 混合加速架构

明确声明：
  · 未复制任何参考项目的源代码
  · 未使用任何参考项目的测试数据
  · 未引入许可证不明的第三方依赖
  · eltdx（禁止商用）仅作协议事实参考，无任何代码引入

审计方式：
  · CI 执行 AST 级相似度检测（阈值 0.70 阻断）
  · CI 执行 License 白名单扫描
  · CI 执行 Spec 覆盖率检查
  · Golden 数据来源校验（source == "self-captured"）
  · 审计报告见 ORIGINALITY/AUDIT_REPORT.md
```

### 19.3 数据版权声明

```
本库仅提供通达信行情数据的协议读取能力，不分发任何数据。

所有行情数据版权归通达信及相应数据商所有。
用户使用本库获取的数据仅限个人学习研究用途，
不得用于商业用途或转售。

开发者不对数据的准确性、完整性、及时性做任何担保。
用户自行承担数据使用的合规责任。
```

---

## v3.0 增量：链路贯通补齐

> **触发**：完成 v2.0 后发现主链路在 21 个点存在断链或半断链，集中表现为配置/错误/国际化/安全/异步/反馈/文档/治理/部署/互操作 10 个横切链缺失，导致"协议能写、用户跑不通"。
>
> **本部分总目标**：把从"协议/数据源"到"用户应用"再到"反馈回路"的 7 环节全部贯通，让 v2.0 的协议深度真正变成可发布的 SDK。
>
> **入口索引**：`AUDIT_AND_BRIDGES.md` 是高层审计索引（**24 断链点 + 10 贯通工程 + 24 项自动化验收**）。本文 §20–§33 是具体落地。

---

## 20. 链路贯通总图与协议闭环

### 20.1 主体链路 7 环节总图

```
┌────────────────┐   ┌────────────────┐   ┌────────────────┐   ┌────────────────┐
│ ①协议/数据源    │──▶│ ②协议捕获        │──▶│ ③Spec 沉淀      │──▶│ ④代码实现        │
│   主站集群      │   │   Sniffer/Prober│   │  PROTOCOL_SPEC │   │  洁净室 A/B 组   │
│   本地 vipdoc   │   │   capture.pcapng│   │  spec_id+v     │   │  parsers/*      │
│   F10/TQLEX    │   │   zstd + meta   │   │  评审+CI       │   │  spec→code      │
└───────┬────────┘   └───────┬────────┘   └───────┬────────┘   └───────┬────────┘
        │                    │                    │                    │
        ▼                    ▼                    ▼                    ▼
┌────────────────┐   ┌────────────────┐   ┌────────────────┐   ┌────────────────┐
│ ⑤测试验证        │──▶│ ⑥Library 分发   │──▶│ ⑦用户应用       │──▶│ ⑧反馈回路        │
│  golden/golden │   │  PyPI wheel    │   │  tstdx.toml    │   │  opt-in        │
│  fuzz/property │   │  conda-forge   │   │  user code     │   │  privacy-filtered│
│  bench/regress │   │  docker        │   │  SDK/HTTP/MCP  │   │  → git/issue   │
└────────────────┘   └────────────────┘   └────────────────┘   └────────────────┘
        │                    │                    │
        └──────▶ 数据流 ◀─────┘──────▶ 错误/指标 ◀─┘
```

### 20.2 协议闭环：Spec ↔ 实现 ↔ 测试的强一致性

**问题**：v2.0 §4 规定 Spec 必须先于代码，但实际怎么保证"代码改了 Spec 跟着改"、"Spec 改了代码自动同步"，没有自动化机制。

**贯通方案**：建立 4 件套契约。

#### 20.2.1 Spec 与代码版本绑定

```yaml
# PROTOCOL_SPEC/7709/052d_kline.yaml（不是 md，是 YAML，便于校验）
spec_id: 7709/0x052d/kline
version: "1.3.0"
tstdx_version_required: ">=0.6.0,<1.0"   # 这个 spec 要求 tstdx 至少 0.6.0
fields:
  - { offset: 0,  size: 4, type: uint32_date, name: date,        unit: YYYYMMDD }
  - { offset: 4,  size: 4, type: uint32,       name: open,        scale: 0.01, encoding: little }
  - { offset: 8,  size: 4, type: uint32,       name: high,        scale: 0.01, encoding: little }
  ...
known_hosts: ["main_hosts"]
```

#### 20.2.2 Spec → 实现生成的代码模板

```python
# src/tstdx/protocol/parsers/auto_generated/7709_052d_kline.py
# 这一行是机器生成的，不要手改（CI 会还原）
# @generated from PROTOCOL_SPEC/7709/052d_kline.yaml @ 2026-09-02T10:00:00Z
@register_parser(msg_id=0x052D, protocol="7709")
def parse_kline(raw: bytes, profile: DataProfile) -> ParsedResult:
    fields = {f.name: read(raw, f) * (f.scale or 1) for f in FIELDS}
    return ParsedResult(tier="L1", fields=fields, spec_id="7709/0x052d/kline@1.3.0")
```

```bash
# 重新生成所有解析器
$ python -m tstdx.tools.codegen --regen-all
# 校验所有生成代码与 spec 一致
$ python -m tstdx.tools.codegen --verify-all  # 阻断 CI
```

#### 20.2.3 实现 → Spec 的反向校验

```python
# tools/spec_audit.py
# 扫 src/tstdx/protocol/parsers/ 与 PROTOCOL_SPEC/
# 1. 每个 parser 文件首行的 @register_parser() 必须能在某 spec 中找到匹配
# 2. 每个 spec 必须有对应 parser；缺失则阻断 CI
# 3. spec 中 field 的 offset+size 必须能完全覆盖响应报文（不允许 parse 后还有未消费字节）
```

#### 20.2.4 实现 → 测试的覆盖矩阵

```python
# tests/protocol/test_spec_coverage.py
@pytest.mark.parametrize("spec_id,protocol,host", load_all_specs())
def test_spec_parse_roundtrip(spec_id, protocol, host):
    """任意 spec 的真实报文 parse 后必须能被 're-serialize' 回相同字节（或字段等价）"""
    raw = GoldenStore.fetch(spec_id, host)
    result = PARSERS[spec_id].parse(raw)
    assert result.tier == "L1"
    re_bytes = result.serialize_to_bytes()
    assert re_bytes == raw or fields_equal(parse(re_bytes), result.fields)
```

### 20.3 捕获闭环：捕获 → 归档 → 二次分析

```python
# CLI: tstdx protocol capture 0x052d sh600519 1d 30
# → 在 tests/golden/raw/7709/0x052d/sh600519_1d_<timestamp>.zst 存 zstd 压缩的原始报文
# → 在 tests/golden/meta/7709/0x052d/sh600519_1d_<timestamp>.yaml 存元数据
---
captured_at: "2026-09-02T10:00:00+08:00"
source: "self-captured"          # 关键字段，CI 会校验
host: "main_host_12"
duration_seconds: 30
trigger: "manual"
operator: "<hash>"               # 不暴露个人信息，只哈希
trading_session: "closed"        # 强制非交易时段抓取
zstd_dict: null
checksum_sha256: "..."
legal_basis: "see §22.3"
```

**归档后二次分析**：

```python
# CLI: tstdx protocol replay <file.zst>
# 输出与原始抓取完全一致的解析结果（双向校验）
$ tstdx protocol replay tests/golden/raw/7709/0x052d/*.zst
```

### 20.4 10 大贯通工程清单（详见 §21–§30 + §33）

| 编号 | 贯通工程 | 起始章节 |
|---|---|---|
| E1 | 协议 ↔ Spec ↔ 代码 ↔ 测试闭环 | §20.2（本文） |
| E2 | 捕获 → 归档 → 重解析 | §20.3（本文） |
| E3 | 配置 → 函数 → 默认值合并 | §21 |
| E4 | 异常 → 错误码 → 重试 → 上报 | §24 + §27 |
| E5 | 离线 → 在线 → 兜底源降级 | §26 |
| E6 | 同步 → 异步 → 回调 API 一致 | §25 |
| E7 | 订阅 → 重连 → 补数流闭环 | §10（v2.0 含） |
| E8 | 上报 → 灰度回灌 | §27 |
| E9 | wheel → 弃用 → 升级 | §29 |
| **E10** | **多源路由 → 口径归一 → 反爬韧性 → 兼容迁移（HTTP Web 源闭环）** ★ v3.1 | **§33** |

---

## 21. 配置中心

### 21.1 4 源配置 + 优先级合并

```
优先级（高 → 低）：
  ① 函数入参      client = TdxClient(config_overrides={"rate_limit": 50})
  ② 环境变量       TSTDX_RATE_LIMIT=50
  ③ 项目级配置     ./tstdx.toml （或 ./pyproject.toml [tool.tstdx]）
  ④ 用户级配置     ~/.config/tstdx/config.toml （XDG 规范）
  ⑤ 系统级配置     /etc/tstdx/config.toml （可选，存在需 root）
  ⑥ 内置默认       src/tstdx/_defaults.py
```

**合并语义**：deep-merge；列表拼接（不去重）；标量覆盖。

### 21.2 tstdx.toml Schema（pydantic v2）

```toml
# ~/.config/tstdx/config.toml
[network]
connect_timeout_ms = 5000
read_timeout_ms = 30000
keepalive_interval_s = 30
max_slots = 32                     # 服务器数 × 每台连接数
host_strategy = "adaptive"         # "adaptive" | "round_robin" | "manual"

[main_hosts]                       # 用户可自定义主站
override_urls = ["..."]
ranking_cache_path = "~/.cache/tstdx/server_ranking.json"

[rate_limit]
session_open   = 15                # 集合竞价 req/s
intraday       = 15
pre_close      = 30
closed         = 60

[memory]
raw_budget_mib    = 256
decoded_budget_mib = 2048
cleanup_threshold  = 0.85           # 占预算 85% 触发主动淘汰

[streaming]
push_queue_size  = 1024
push_queue_mib   = 64
delta_window_ms  = 500
gap_fill_on_reconnect = true

[i18n]
encoding         = "auto"          # "gbk" | "gb18030" | "big5" | "utf-8" | "auto"
timezone_output  = "Asia/Shanghai"
trading_calendar = "china_sse"

[observability]
metrics = { enabled = false, exporter = "prom" }    # prom | statsd | otel
logging = { level = "INFO", json = false }
tracing = { enabled = false, exporter = "otel" }

[security]
use_tls             = false        # TDX 协议本身非加密；默认 false
credential_backend  = "keyring"    # "keyring" | "env" | "file:~/..."
user_agent          = "tstdx/0.x"

# ────────────────────────────────────────────────────────────────
# ★ v3.1 新增：HTTP Web 行情源配置（对应 §33）
# ────────────────────────────────────────────────────────────────
[web]
enabled          = true            # 总开关；false 则完全不参与降级
enabled_sources  = ["sina", "tencent", "eastmoney"]  # 数组顺序 = 降级优先顺序
fallback_enabled = true            # TDX 主站全不可达时是否降级到 Web 源
timeout_ms       = 5000
max_retries      = 2
cross_validate   = false           # 多源交叉校验（开发期打开，生产关）

[web.headers]                      # 反爬必需（详见 §33.4）
user_agent      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
sina_referer    = "http://finance.sina.com.cn/"      # 不带 Referer → 403
tencent_referer = "https://gu.qq.com/"
eastmoney_referer = "https://quote.eastmoney.com/"

[web.rate_limit]                   # 按源限流（req/s），东财高频会封 IP 20h+
sina      = 8
tencent   = 8
eastmoney = 1                      # 必须串行
jsl       = 1
default   = 5

[web.normalize]                    # §33.5 全局契约：volume=股 / amount=元
volume_unit = "share"
amount_unit = "yuan"
strict      = true                 # 归一化后均价偏离 > 5% 抛 DataConsistencyError

[web.jsl]                          # 集思录需登录
cookie_backend = "keyring"         # "keyring" | "env" | "plain"（plain 不推荐）

[compatibility]
mootdx_layer        = false        # 启用时导入 mootdx 兼容垫片（§26）
easyquotation_layer = false        # ★ v3.1 启用时导入 easyquotation 兼容垫片（§33.7）
```

> **v3.1 说明**：`[web]` 段为 §33 HTTP Web 行情源的运行时配置。其**合并语义**沿用 §21.1 的 6 源优先级（函数入参 > env > 项目 > 用户 > 系统 > 默认），由 §21.5 既有的 12 个合并 case 覆盖；其**行为正确性**（源优先级排序生效、限流按源区分、归一化 strict 生效）由 §33.10 测试矩阵的 case 覆盖，计入验收 #23/#24。

### 21.3 配置加载与合并（实现要点）

```python
# src/tstdx/config.py
from pydantic import BaseModel
from pathlib import Path
import os, sys


class TstdxConfig(BaseModel):
    model_config = {"extra": "forbid"}  # 拼错字段名立刻报错


class Network(BaseModel): ...


# ... (其他子模型)


class ConfigLoader:
    PRIORITY = ["callable", "env", "project", "user", "system", "default"]

    def load(self, overrides: dict | None = None) -> TstdxConfig:
        merged = {}
        for src in reversed(self.PRIORITY):
            layer = self._load_layer(src)
            merged = deep_merge(merged, layer)
        if overrides:
            merged = deep_merge(merged, overrides)
        return TstdxConfig.model_validate(merged)  # 严格校验

    def _load_layer(self, name):
        return {
            "callable": self._callable_overrides,
            "env": {k: parse_env(v) for k, v in os.environ.items() if k.startswith("TSTDX_")},
            "project": self._read_toml("./tstdx.toml"),
            "user": self._read_toml("~/.config/tstdx/config.toml"),
            "system": self._read_toml("/etc/tstdx/config.toml"),
            "default": _defaults.DEFAULT_CONFIG_DICT,
        }[name]
```

**校验失败行为**：fail-fast（启动即报错，不允许运行时查字段）。

### 21.4 运行时配置变更

```python
# 启动后修改（仅运行时生效，不写盘）
with TdxClient() as c:
    c.config.rate_limit.intraday = 50  # 立即生效，限流器热更新
    c.config.streaming.push_queue_size = 2048

# 持久化（用户主动调用，不自动写）
c.config.save_to("~/.config/tstdx/config.toml")
```

### 21.5 测试矩阵（12 case）

```python
# tests/config/test_merge.py
@pytest.mark.parametrize("layers,expected", [
    ("env-over-default",      {"rate_limit": {"intraday": 50}}),  # 1
    ("project-over-user",     {"host_strategy": "manual"}),       # 2
    ("callable-wins-all",     {"rate_limit": {"intraday": 100}}), # 3
    ("list-concat",           {"main_hosts": ["a", "b", "c"]}),   # 4
    ("deep-merge-dicts",      {"network": {"timeout": 7, "keep": 30}}),  # 5
    ("unknown-field-error",   ValidationError),                   # 6
    ("missing-file-silent",   None),                              # 7
    ("env-typed-cast",        {"rate_limit": {"intraday": int}}),  # 8
    ("relative-path-resolve", Path("/abs/path/cache.json")),       # 9
    ("xdg-env-override",      {"user": "/custom/path/config"}),   # 10
    ("pydantic-version",      "2.x"),                             # 11
    ("concurrent-load-safety","thread-safe"),                     # 12
])
```

---

## 22. 安全与合规边界

### 22.1 凭据存储（TDX 协议本身无认证，但部署相关账号体系存在）

```python
# src/tstdx/security/credentials.py
class CredentialBackend(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...


class KeyringBackend:  # 优先：系统 keyring（macOS Keychain / Win Credential / Linux Secret Service）
    def get(self, key):
        return keyring.get_password("tstdx", key)

    def set(self, key, value):
        keyring.set_password("tstdx", key, value)


class EnvBackend:  # 回退：环境变量
    def get(self, key):
        return os.environ.get(f"TSTDX_CRED_{key.upper()}")


class FileBackend:  # 仅当显式启用 `file:/encrypted/path` 才用
    """AES-GCM 加密文件，凭据从 KMS 或用户输入派生"""


# 自动选择：系统 keyring → env → None（首次提示用户）
```

### 22.2 TLS 选项（默认不启用，TDX 协议本身明文）

```python
# 警告：启用 TLS 会增加网络栈一跳，无明显收益；建议仅在内网部署白盒环境用
class Transport:
    def __init__(self, *, use_tls: bool = False, ca_bundle: str | None = None):
        if use_tls:
            log_warning("TLS 启用会与 TDX 公共主站不兼容，仅内网代理/录制服务器使用")
```

### 22.3 抓包法律边界（强制）

```python
# tools/capture.py 启动时强制自检
def _legal_self_check():
    assert now_in_trading_session() is False, "禁止交易时段抓包，以免干扰生产主站"
    assert user_consent_signed(),            "用户必须签署 CLA（含法律边界条款）"
    assert only_own_traffic(),               # 三次握手中只抓发往 / 来自本地 socket 的字节
```

**CLA（贡献者许可协议）**模板在 `CONTRIBUTING.md`，要求：
- 不引入任何参考项目源码
- 不分享未公开的协议命令/字段
- 接受项目原创性审计流程

### 22.4 数据来源声明强制嵌入

```python
# 任何返回市场数据的 dataclass 自动带上 source 字段
@dataclass
class Bar:
    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    source: Literal["tdx_online", "tdx_local_vipdoc", "tdx_f10", "user_supplied"]
    fetched_at: datetime
    license: Literal["personal_study_only"]
    __copyright_notice__: ClassVar[str] = (
        "Data via TDX protocol. Copyright notice from upstream data provider required."
    )
```

**用户导出 CSV/Parquet 时**强制要求附带 `LICENSE_HEADER.txt`，提供工具 `tstdx data stamp` 注入。

---

## 23. 国际化 / 时区 / 交易日历

### 23.1 字符集处理

```python
# src/tstdx/i18n/encoding.py
ENCODING_TABLE = {
    "gbk": codec("gbk"),  # 默认
    "gb18030": codec("gb18030"),  # 中文扩展
    "big5": codec("big5"),  # 港股繁体
    "shift_jis": codec("shift_jis"),  # 日股（部分日股 TDX 镜像）
    "euc_kr": codec("euc-kr"),  # 韩股（部分韩股 TDX 镜像）
    "utf-8": codec("utf-8"),
}


def auto_detect(raw: bytes) -> str:
    """对短字符串做多编码试探取首胜；失败回退 GBK"""
    for enc in ["utf-8", "gb18030", "gbk", "big5"]:
        try:
            return enc
        except:
            continue
    return "gbk"


def decode(raw: bytes, *, prefer: str = "auto") -> str:
    enc = auto_detect(raw) if prefer == "auto" else prefer
    text = raw.decode(enc, errors="replace")  # 不抛，保底
    return _normalize_simplified(text)  # 可选：OpenCC 转简体
```

**配置项**：`[i18n] encoding = "auto"`；可指定 `prefer="gb18030"`；可选 `simplify=true` 用 OpenCC 转简体。

### 23.2 时区（UTC 内部 + 输出本地化）

```python
# src/tstdx/i18n/timezone.py
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class MarketTime:
    """统一内部表示：UTC + 输出可转本地"""

    utc: datetime  # tzinfo=ZoneInfo("UTC")
    local: datetime | None = None  # 派生，按 config.i18n.timezone_output

    @classmethod
    def from_bj(cls, naive_or_aware: datetime):
        # 接受 naive datetime，默认当北京时间
        if naive_or_aware.tzinfo is None:
            aware = naive_or_aware.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        else:
            aware = naive_or_aware
        return cls(utc=aware.astimezone(ZoneInfo("UTC")))

    def to(self, tz: str) -> datetime:
        return self.utc.astimezone(ZoneInfo(tz))
```

**对内**：所有 `MarketTime` 内部 `utc`。
**对外**：默认转 `Asia/Shanghai` 输出，用户配置后转 `Europe/London` 等。
**DTO 序列化**：默认 ISO 8601 带时区偏移（`2026-09-02T10:00:00+08:00`）；可选 RFC 3339 nano。

### 23.3 A 股交易日历

```python
# src/tstdx/i18n/calendar.py
class ChinaSseCalendar:
    """上交所 + 深交所 + 北交所合并交易日历（含节假日、调休、临时休市）"""
    HOLIDAYS_2024_2026 = [
        # 2024
        date(2024,1,1), date(2024,2,9), date(2024,2,12), date(2024,2,13), date(2024,2,14),
        date(2024,2,15), date(2024,2,16), date(2024,4,4), date(2024,4,5),
        ...
        date(2026,5,1), date(2026,5,4), date(2026,5,5),
        # 调休补班日视为交易日
    ]
    SPECIAL_HOLIDAYS_URL = "https://www.sse.com.cn/disclosure/dealinstru/.../calendar.json"
    # 启动时尝试联网下载最新节假日表；离线时 fallback 到内置

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.HOLIDAYS_2024_2026

    def is_trading_session(self, ts: datetime) -> bool:
        """上午 9:30-11:30 + 下午 13:00-15:00（北京时间），不含集合竞价与盘后"""
        ...

    def previous_trading_day(self, d: date, n: int = 1) -> date: ...

    def next_trading_day(self, d: date, n: int = 1) -> date: ...
```

**支持日历范围**：
- `china_sse` —— A 股（默认）
- `china_szse` —— A 股深市（同上但深市独有节假日）
- `china_bse` —— 北交所（部分节假日差异）
- `hongkong_hkex` —— 港股
- `us_nyse`, `us_nasdaq` —— 美股（含夏令时）
- `japan_tse`, `korea_krx` —— 亚股

**临时休市**自动检测（盘中遇服务器拒绝请求或公告字段 `trading_status="HALT"`）。

### 23.4 测试矩阵

| # | 测试 | 方法 |
|---|---|---|
| 01 | GBK 编码字段正确解码 | 给定 raw bytes → expected text |
| 02 | UTF-8 BOM 不破坏后续字段 | 含 BOM 头的混合流 |
| 03 | Big5 港股代码解码 | 给定 Big5 字节流 |
| 04 | UTC ↔ 北京时间转换无精度损失 | 1ms 边界 |
| 05 | 美股夏令时切换日无歧义 | 2026-03-08 / 2026-11-01 |
| 06 | A 股 2026-05-04 调休视为交易日 | 内置日历 |
| 07 | 2025-09-03 抗战胜利日停市 | 内置日历 |
| 08 | 临时休市检测 | mock 服务器返回 HALT |
| 09 | OpenCC 繁简转换 | "台灣積體電路" → "台湾积体电路" |
| 10 | 字符集自动探测 3 编码并发第一命中 | 投毒样本 |

---

## 24. 错误体系细化（扩展 §15.2）

### 24.1 错误分类树

```
TstdxError (基类，所有 tstdx 抛出的异常都继承自此，用户可统一捕获)
│
├── ConfigError                # 配置相关
│   ├── ConfigNotFound         # 未找到配置项（用 default 时不抛，显式 strict=True 才抛）
│   ├── ConfigInvalid          # pydantic 校验失败（含文件位置）
│   └── ConfigPermission       # 配置文件不可读
│
├── TransportError             # 网络传输层
│   ├── ConnectTimeout         # TCP 连接超时
│   ├── ReadTimeout            # 读响应超时
│   ├── ConnectionReset        # 对端 RST
│   ├── DNSResolveFailed       # DNS 解析失败
│   ├── TLSHandshakeFailed     # TLS 错误
│   └── AllHostsUnreachable    # 全部主站不可达
│
├── ProtocolError              # 协议层
│   ├── UnknownMsgId           # 收到未注册命令且 L2 兜底也失败（极罕见，落到 L3）
│   ├── ParseError             # 字段解码失败
│   ├── SpecMismatch           # 实际报文与 spec 不符
│   ├── CompressionError       # zlib 解压失败
│   ├── EncodingError          # GBK 等字符集失败
│   ├── VarintOverflow         # varint 编码异常
│   └── SnifferCaptureAborted  # Sniffer 中断
│
├── ProtocolTier               # 解析层级元数据
│   └── (为 result 附加，非抛)
│
├── RateLimitError             # 限流
│   ├── RateLimitExceeded      # 单次超限
│   └── RateLimitSessionFull   # 全局预算用尽（含 retry_after 字段）
│
├── MemoryError                # 内存预算
│   ├── MemoryBudgetExceeded   # 解码后超 2GiB
│   ├── RawQueueOverflow       # push 队列满（背压触发）
│   └── SlowConsumer           # 消费者太慢，回压无效
│
├── DataError                  # 业务数据层
│   ├── SymbolNotFound         # 代码表里查不到
│   ├── DataStale              # 数据陈旧（X 秒前）
│   ├── DataQualityFail        # 数据质量校验失败（OHLC 自洽等）
│   ├── ReplaySequenceGap      # 重连补数时无法补全
│   └── CalendarMismatch       # 日历与服务器日期不一致
│
├── StreamingError             # 流式订阅
│   ├── SubscribeRejected      # 服务端拒绝订阅
│   ├── ReconnectFailed        # 重连失败 N 次
│   ├── DeltaOutOfOrder        # 增量顺序错乱且窗口耗尽
│   └── GapNotFillable         # 断线期间的数据无法补
│
├── CompatibilityError         # 兼容层（门面桥接/二进制/市场门面的跨口径告警）
│   ├── MojoMissing            # mootdx 接口返回意料外
│   └── AdapterMismatch        # 适配器 schema 对不上
│
├── LicenseError               # 合规
│   ├── CommercialUseDenied    # 检测到商用场景（best-effort）
│   └── AttributionMissing     # 数据导出未带版权声明
│
├── UserError                  # 用户误用
│   ├── InvalidArgument        # 参数类型/范围错
│   ├── ContextManagerRequired # 必须用 with 块
│   └── AsyncInSyncContext     # 异步函数在同步上下文调用
│
└── InternalError              # 内部 bug（不应出现但兜底）
    └── (含 stack_trace / build_version / git_commit)
```

### 24.2 可重试性矩阵

```python
@dataclass
class RetryAdvice:
    retryable: bool
    backoff: float          # 建议退避秒数（0 = 立即重试）
    max_retries: int        # 建议最大重试次数
    switch_host: bool       # 建议切主站
    fallback_to_offline: bool  # 建议切换离线数据
    cause_hint: str         # 人类可读原因

RETRY_TABLE: dict[type, RetryAdvice] = {
    ConnectTimeout:   RetryAdvice(True,  1.0, 3, True,  False, "网络瞬时抖动"),
    ReadTimeout:      RetryAdvice(True,  2.0, 3, True,  False, "服务器负载高"),
    ConnectionReset:  RetryAdvice(True,  0.5, 5, True,  False, "RST 后重连"),
    AllHostsUnreach:  RetryAdvice(True,  5.0, 3, False, True,  "全部主站不可用"),
    RateLimitExceeded:RetryAdvice(True,  1.0, 10, False, False, "请降速"),
    RateLimitSessionFull: RetryAdvice(False, 0, 0, False, True,  "应转离线"),
    MemoryBudgetExceeded: RetryAdvice(False, 0, 0, False, False, "调小 batch"),
    UnknownMsgId:     RetryAdvice(False, 0, 0, False, False, "需上报 issue"),
    ParseError:       RetryAdvice(False, 0, 0, False, False, "需人工 review"),
    SpecMismatch:     RetryAdvice(False, 0, 0, False, False, "需升级 spec"),
    SymbolNotFound:   RetryAdvice(False, 0, 0, False, False, "代码无效"),
    DataStale:        RetryAdvice(True,  30.0, 2, False, True,  "应转离线"),
    ...
    UserError:        RetryAdvice(False, 0, 0, False, False, "代码 bug"),
    InternalError:    RetryAdvice(False, 0, 0, False, False, "上报 bug"),
}
```

**用户范式**（catch-all 后看建议表）：

```python
from tstdx import TstdxError, RetryAdvice

try:
    bars = client.get_bars("sh600519", period="1d", count=100)
except TstdxError as e:
    advice = e.retry_advice
    if advice.retryable:
        if e.within_retry_budget():
            time.sleep(advice.backoff)
            return retry()
        elif advice.fallback_to_offline:
            return read_from_local_vipdoc("sh600519")
    else:
        log.exception("不可重试: %s / 建议: %s", e, advice.cause_hint)
        if advice.cause_hint.startswith("需上报"):
            auto_open_issue(e)  # §27 反馈回路
        raise
```

### 24.3 错误码与 HTTP 状态码对应

| Tstdx 错误 | HTTP REST |
|---|---|
| UserError (4xx like) | 400 |
| SymbolNotFound | 404 |
| ConfigInvalid | 422 |
| RateLimitExceeded | 429 |
| AllHostsUnreachable | 502/503 |
| ProtocolError | 500 (含 retry_after) |
| InternalError | 500 |

### 24.4 测试矩阵

```python
# tests/errors/test_taxonomy.py
def test_every_exception_subclasses_tstdx_error(): ...
def test_retry_advice_table_complete(): ...  # 每种类型必须有 advice
def test_user_can_catch_all_with_TstdxError(): ...
def test_retry_after_includes_backoff_seconds(): ...
@pytest.mark.parametrize("exc,advice", RETRY_TABLE.items())
def test_retry_advice_consistency(exc, advice): ...
```

---

## 25. 异步 / 同步双轨桥

### 25.1 一套实现两种 API（避免双倍维护）

```python
# 用统一内部协程实现
class TdxClient:
    # 异步 API
    async def get_bars_async(self, symbol, period, count):
        return await self._get_bars_core(symbol, period, count)

    # 同步 API（默认推荐）
    def get_bars(self, symbol, period, count):
        return run_sync(self.get_bars_async(symbol, period, count))
```

### 25.2 同步桥实现

```python
# src/tstdx/_async_bridge.py
def run_sync(coro):
    """在独立守护线程中跑事件循环（同步上下文用）"""
    if threading.current_thread() is threading.main_thread():
        # 主线程：尝试拿到已存在的 loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 主线程已有 loop 在跑，新建 loop 跑守护线程
                return _run_in_worker_thread(coro)
        except RuntimeError:
            pass
        return asyncio.run(coro)
    else:
        return _run_in_worker_thread(coro)


def _run_in_worker_thread(coro):
    box = ResultBox()

    def target():
        try:
            box.value = asyncio.run(coro)
        except BaseException as e:
            box.error = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join()
    if box.error:
        raise box.error
    return box.value
```

### 25.3 并发使用（多任务同步）

```python
# 用户在同步代码里并发取多只股票
from concurrent.futures import ThreadPoolExecutor

with TdxClient() as c, ThreadPoolExecutor(max_workers=8) as ex:
    futs = [ex.submit(c.get_bars, s, "1d", 100) for s in symbols]
    results = [f.result() for f in futs]
```

**线程安全保证**：`TdxClient` 内部用独立 `Slot` per thread，主连接池共享；`StreamingChannel` 必须 one-per-thread 或 one-per-task；显式抛错禁止跨线程共享。

### 25.4 异步使用（高级用户）

```python
import asyncio


async def main():
    async with AsyncTdxClient() as c:
        bars = await c.get_bars("sh600519", "1d", 100)
        async for update in c.subscribe(["sh600519"], level=5):
            print(update)


asyncio.run(main())
```

### 25.5 测试矩阵

```python
# tests/async_bridge/test_sync_async.py
def test_sync_call_in_async_loop_raises_clear_error(): ...
def test_async_call_in_thread_uses_dedicated_loop(): ...
def test_concurrent_sync_calls_use_independent_slots(): ...
async def test_async_parity_with_sync():
    sync_result = client_sync.get_bars("sh600519", "1d", 100)
    async_result = await client_async.get_bars_async("sh600519", "1d", 100)
    assert sync_result.fields == async_result.fields


async def test_no_event_loop_leak_after_run_sync(): ...
```

---

## 26. 互操作与生态适配器

### 26.1 三类适配器

#### 26.1.1 兼容垫片：mootdx API（**已废弃，v1.4.0 决议删除**）

> **状态注记（v1.4.0，P13-D/G 文档漂移清理）**：mootdx 兼容垫片在 v1.4.0
> 决议中已**不实现**——设计时曾规划 `tstdx.compat.mootdx` 提供 mootdx 风格
> API（`Quotes.get_security_quotes` / `get_k_data` / `Reader` 等），实际工程
> 中未落地。§26 相关章节保留为**规划归档**，不作为当前实现承诺。
>
> 用户如需迁移 mootdx 代码：请按 ``UnifiedQuoteAPI`` / ``TdxClient`` 的原生
> 接口重写（详见 ``README.md`` 快速上手与 ``docs/quickstart.md``）；或
> 继续使用 mootdx 原库并通过数据管线对接 tstdx。

```python
# 历史规划（未实现，仅供参考）：
# src/tstdx/compat/mootdx.py
# 提供与 mootdx 相同的接口，让原有 mootdx 用户零成本切换
from tstdx import TdxClient as _Real
from tstdx.compat.mootdx import Reader, Quotes, Affair  # 兼容类


class Quotes:  # 类似 mootdx.Quotes
    def __init__(self, **kw):
        self._c = _Real(**kw)

    def get_security_quotes(self, codes):
        return self._c.get_quotes(codes)

    def get_k_data(self, code, ktype):
        return self._c.get_bars(code, ktype)


# 启用：pyproject 加 "compat-mo" extra 或 [compatibility] mootdx_layer = true
# —— 未实现，当前 pyproject 无 compat-mo extra
```

**局限声明**：兼容层只覆盖高频 API（≥80% 用例）；底层 API 不一致的部分抛 `CompatibilityWarning` 让用户知悉。

#### 26.1.2 互转桥：DataFrame / Parquet / DuckDB sink

```python
# src/tstdx/output/
class BarSink(Protocol):
    def write(self, bars: list[Bar]) -> None: ...


class DataFrameSink:  # 内存
    def __init__(self):
        self.df = pd.DataFrame()

    def write(self, bars):
        self.df = pd.concat([self.df, DataFrame(bars)])


class ParquetSink:  # 磁盘（按日分区）
    def __init__(self, path):
        self.path = path

    def write(self, bars):
        df = DataFrame(bars)
        date = bars[0].ts.date().isoformat()
        pq.write_to_dataset(df, self.path / f"date={date}")


class DuckDBSink:  # 内嵌分析
    def __init__(self, db=":memory:"):
        import duckdb

        self.con = duckdb.connect(db)
        self.con.execute("CREATE TABLE IF NOT EXISTS bars (...)")

    def write(self, bars):
        self.con.execute("INSERT INTO bars SELECT * FROM df", [DataFrame(bars)])


# 使用
with TdxClient() as c, ParquetSink("/data/tdx") as sink:
    c.stream_bars(["sh600519"]).sink_to(sink)
```

#### 26.1.3 数据源路由：多源合并（未来扩展锚点）

```python
# src/tstdx/sources/router.py
class DataSourceRouter:
    """定义 tstdx 在线源 + 可插拔第三方源（akshare/efinance/未来的新浪/腾讯）的统一路由"""

    def __init__(self, *, primary="tstdx", fallbacks=("akshare",), policy="failover"):
        self.primary = primary
        self.fallbacks = fallbacks
        self.policy = policy  # "failover" | "merge" | "race"

    async def get_bars(self, symbol, period, count):
        try:
            return await self.sources[self.primary].get_bars(symbol, period, count)
        except (TransportError, AllHostsUnreachable):
            for fb in self.fallbacks:
                try:
                    return await self.sources[fb].get_bars(symbol, period, count)
                except:
                    continue
            raise
```

### 26.2 数据质量校验（OHLC 自洽 + 异常跳变）

```python
# src/tstdx/services/validator.py
class DataQualityValidator:
    def check_bar(self, bar: Bar) -> list[QualityIssue]:
        issues = []
        if not (bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high):
            issues.append(QualityIssue("OHLC自洽失败", severity="error"))
        if bar.high - bar.low > 0 and (bar.high - bar.low) / max(bar.open, 1) > 0.20:
            issues.append(QualityIssue("单日振幅>20%", severity="warning"))
        if bar.volume < 0 or bar.amount < 0:
            issues.append(QualityIssue("量价负值", severity="error"))
        # 与上一根的连续性
        if self._prev_close and abs(bar.close / self._prev_close - 1) > 0.10:
            issues.append(QualityIssue("单日涨幅>10%（涨停可能）", severity="info"))
        return issues
```

### 26.3 Fallback 路径（6 种降级）

| 场景 | 主路径 | 降级路径 |
|---|---|---|
| 1 | 在线主站 | 同主站的备份连接 |
| 2 | 在线主站 | 其他主站（failover） |
| 3 | 在线 TDX | 本地 vipdoc（最近一天） |
| 4 | 本地 vipdoc | 用户缓存（Parquet/DuckDB） |
| 5 | 在线/本地 | akshare/efinance（route 配置后） |
| 6 | 全部失败 | 抛 `AllSourcesExhausted` + 详细 trace |

---

## 27. 反馈回路

### 27.1 用户协议差异上报（opt-in）

```python
# 用户在自己代码里发现 tstdx 没解析某字段
client.report_protocol_observation(
    spec_id="7709/0x052d",
    observed_field={"offset": 100, "size": 8, "interpretation": "可能是新的成交笔数字段"},
    captured_raw=bytes,  # 自动附加 source="self-captured"
)
# → 写入 ~/.local/share/tstdx/observations/<hash>.json
# → 用户后续运行 `tstdx feedback submit` 时匿名上传
```

### 27.2 错误指标自动上报（默认 opt-out）

```python
# [observability]
# telemetry = { enabled = false, endpoint = null, include_stack = false }
# 启用后上传字段：
#   - tstdx_version
#   - protocol / parse_tier 命中分布
#   - 错误码 + 去敏感化 hash
#   - 不含：用户数据、symbol、time、amount


class TelemetryReporter:
    def __init__(self, config):
        if not config.telemetry.enabled:
            self._noop = True

    def report(self, event):
        if self._noop:
            return
        payload = self._scrub(event)
        # HTTPS POST 到 tstdx.io/v1/telemetry，失败也吞，不影响本地功能
```

### 27.3 用户调优建议回收（opt-in）

```python
# 本地汇总用户的实际使用模式，帮助优化默认参数
$ tstdx feedback stats
# 显示：
#   - 你过去 30 天用了 4.2 TB 流量（建议把 raw_budget 调到 1GiB）
#   - 70% 请求是 1d K 线（建议默认 cache TTL 改为 24h）
#   - 你命中了 12 次 AllHostsUnreachable（建议启动时启用 failover）
# → 用户点击 "apply all" 自动改 tstdx.toml
```

### 27.4 隐私边界（强制）

- 上报前 7 步脱敏：去掉 IP / 主机名 / 用户名 / 路径前缀 / 内网段 / 数据样本 / stack trace 中的变量名
- 上报文件可被用户在 `~/.local/share/tstdx/observations/` 完全审视
- 上报通道支持本地导出 `.jsonl`，用户自决是否外发

---

## 28. 文档体系

### 28.1 文档矩阵

| 文档 | 位置 | 维护方式 | 测试保障 |
|---|---|---|---|
| README.md | 根 | 手写 + 章节顺序锁定 | `test_readme_links.py` |
| Quickstart | docs/quickstart.md | 手写 | `test_quickstart_snippets.py`（执行所有代码片段） |
| Tutorial（深度） | docs/tutorial/*.ipynb | 手写 + nbmake 测试 | `pytest --nbmake docs/tutorial` |
| API Reference | docs/api/ | sphinx-autodoc 自动 | `test_api_docs_complete.py`（每个 public 函数必须有 docstring 段落） |
| Cookbook（按问题索引） | docs/cookbook/*.md | 手写 | `test_cookbook_examples.py` |
| Migration Guides | docs/migration/from-*.md | 手写（mootdx/easy_tdx/eltdx 用户迁移） | 链接测试 |
| FAQ | docs/FAQ.md | 社区 PR 维护 | — |
| Troubleshooting | docs/troubleshooting.md | 按错误码索引 | — |
| ADR（架构决策记录） | docs/adr/NNNN-*.md | 决策后写 | `test_adr_format.py` |
| Changelog | CHANGELOG.md | git-cliff 自动 | `test_changelog_updated.py` |
| Spec | PROTOCOL_SPEC/ | 自动校验 | §20.2 |
| Compliance | COMPLIANCE.md | 手写 | `test_compliance.py` |
| Security Policy | SECURITY.md | 手写 | — |
| Contributing | CONTRIBUTING.md | 手写 | — |
| Code of Conduct | CODE_OF_CONDUCT.md | Contributor Covenant v2.1 | — |

### 28.2 Cookbook 选题（首批 22 题，★ 为 v3.1 新增）

1. 如何在 Notebook 里画当天分时图？
2. 如何把所有 A 股代码导出为 CSV？
3. 如何订阅"自选股"列表的实时五档？
4. 如何做断点续传下载全市场 K 线？
5. 如何计算某只股票的所有除权除息日的前复权价？
6. 如何异步并发拉 1000 只股票的最新价？
7. 如何用 DuckDB 把流式行情落到本地查询？
8. 如何在 WebSocket 服务里推 K 线给前端？
9. 如何处理交易日历外的历史回放？
10. 如何用 Rust 内核加速大规模日线回补？
11. 如何把 tstdx 嵌入到一个已有的 Flask 服务？
12. 如何用 MCP 工具让 AI Agent 查行情？
13. 如何检测到抓回来的 K 线数据异常？
14. 如何同时连 3 个主站做负载分担？
15. 如何在一台机器内复现"主站全挂"的故障演练？
16. 如何把 tstdx 数据对接到 backtrader？
17. 如何用 Plotly 画一个跨标的的对比图？
18. 如何处理分钟线和日线之间的复权差异？
19. 如何对收到的协议命令做手动调试？
20. 如何贡献一个新协议的解析器给上游？
21. ★ 如何在 TDX 主站全不可达时自动降级到新浪/腾讯 HTTP 源？（§33.6）
22. ★ 如何从 easyquotation 零成本迁移到 tstdx？（§33.7）

### 28.3 ADR 模板（每次架构决策都写）

```markdown
# ADR-0012: 协议解析器用 spec 驱动而非反射注册

## 状态
Proposed | Accepted | Superseded by ADR-0020

## 上下文
...（背景 + 备选 + 评估）

## 决策
...（选了哪个 + 为什么）

## 后果
...（正面 + 负面 + 取舍）
```

---

## 29. 部署与分发

### 29.1 PyPI wheel 矩阵（12 个组合）

```yaml
# .github/workflows/wheels.yml
strategy:
  matrix:
    python: ["3.10", "3.11", "3.12", "3.13"]
    os: [windows-latest, macos-latest, ubuntu-latest]
include:
  - { python: "3.13", os: "ubuntu-latest", arch: "arm64" }   # 补充 ARM64
  - { python: "3.13", os: "macos-latest", arch: "arm64" }
  - { python: "3.13", os: "windows-latest", arch: "amd64" }

# 每个组合：build wheel → smoke test install → 验证 import → 上传 PyPI
```

### 29.2 包依赖分层

```toml
# pyproject.toml
[project]
name = "tstdx"
dependencies = [                   # 核心零依赖
    "pydantic>=2.5",
]
[project.optional-dependencies]
api = ["fastapi>=0.110", "uvicorn[standard]>=0.27"]   # HTTP REST/WebSocket
streaming = ["websockets>=12"]                         # WS RPC
mcp = ["mcp>=0.1"]                                     # MCP 工具
observability = ["prometheus-client>=0.20", "opentelemetry-api>=1.24"]
i18n = ["opencc-python-reimplemented>=0.1"]            # 繁简转换
perf = ["polars>=0.20"]                                # DataFrame 后端
mo = ["mootdx>=0.6"]                                   # mootdx 兼容垫片
all = ["tstdx[api,streaming,mcp,observability,i18n,perf,mo]"]
```

### 29.3 签名与校验

```bash
# 维护者本地
$ python -m build --wheel
$ gpg --detach-sign --armor dist/tstdx-0.6.0-py3-none-any.whl
$ twine upload dist/tstdx-0.6.0* dist/tstdx-0.6.0-py3-none-any.whl.asc

# 用户安装可校验（CI 强制）
$ pip install tstdx==0.6.0 \
    --require-hashes \
    --trusted-host pypi.org
```

### 29.4 Docker 镜像（轻量）

```dockerfile
# docker/Dockerfile.python
FROM python:3.13-slim
RUN pip install --no-cache-dir tstdx[api,streaming,observability]
EXPOSE 8000
ENTRYPOINT ["tstdx", "serve", "--host", "0.0.0.0"]
# → docker run -p 8000:8000 tstdx:0.6.0
```

镜像 weekly 重建 + 自动重新跑冒烟测试。

### 29.5 SemVer + 弃用策略

```
版本号格式：MAJOR.MINOR.PATCH
  · MAJOR: 不兼容 API（错误码变更、协议格式解析结果顺序变化）
  · MINOR: 新增向后兼容功能
  · PATCH: 修复 bug

弃用周期：
  · 0.x 版本：不承诺（开发期）
  · ≥1.0：弃用标记 → 至少 2 个 minor 后删除
  · 弃用警告：每次调用 DeprecationWarning 一次（不刷屏）
```

### 29.6 Changelog 自动化

```bash
$ git-cliff --tag 0.6.0 --output CHANGELOG.md
# 自动按 conventional commits 分组：feat/fix/perf/breaking/docs
# spec 变更单列一组
# 依赖变更单列一组
```

---

## 30. 治理与社区

### 30.1 GOVERNANCE.md（维护者/审稿人/贡献者三级）

```
维护者（Maintainer，2-3 人）
  · 合并 PR、发布版本、维护路线图
  · 决策权重：每人一票，平票时 maintainer lead 决定

审稿人（Reviewer，3-5 人）
  · 评审 PR、给出 review
  · 1 个审稿人 approve 才能合并

贡献者（Contributor）
  · 提 PR、提 issue、参与讨论
  · 5 个 merged PR 后邀请成为 Reviewer
```

### 30.2 CoC（Contributor Covenant v2.1）

引入标准 CoC，提供举报邮箱 + 48 小时响应承诺。

### 30.3 Issue 模板

| 类型 | 模板字段 |
|---|---|
| Bug Report | tstdx 版本 / OS / Python / 复现代码 / 实际 vs 期望 / 抓包 hex |
| Feature Request | 动机 / 设计建议 / 备选方案 / 影响面 |
| Spec Gap（协议新增） | 抓包文件、Spec 草案、影响哪些客户端 |
| Question | 简短问题 |

**Bug Report 中的抓包字段**强制引导用户：
- 自动识别并提示"请用 `tstdx protocol capture <cmd>` 抓包"
- 校验上传附件的金色签名

### 30.4 PR 模板（强制要求）

```markdown
- [ ] 关联 spec：`PROTOCOL_SPEC/7709/0x052d/kline.md` 已更新（或无需）
- [ ] 抓包来源：链接到 `tests/golden/raw/.../*.zst`
- [ ] 原创性自查：未复制任何参考项目源码，未引入许可证不明依赖
- [ ] 测试覆盖：单元 / Parity / Spec Contract
- [ ] 文档：README / API Reference / Cookbook（任一有变更）
- [ ] Changelog：用 `git-cliff` 自动生成
```

### 30.5 SECURITY.md

- 受支持版本矩阵（每个 minor 最新 1 个 patch）
- 报告流程：私密 issue / 安全邮件（24h 内首响，72h 内评估）
- 披露时间表：修复后 7 天公开
- 历史 CVE 表

---

## 31. 路线图修订（v3.0：28 周 5 阶段 + v3.1 W14b 增量）

### Phase 0：奠基（2 周）—— 同 v2.0
产出 `PROTOCOL_SPEC` 体系、Golden 采集工具、CodeGen 模板、tstdx.toml schema。

### Phase 1：核心协议 + 贯通底座（5 周）—— 在 v2.0 基础上增加配置中心、错误体系、国际化、Codec

| 周 | 模块 | 产出 |
|---|---|---|
| W3 | Wire Codec | 把 §7.2 varint、§7.1 报文都变成有 spec、有 contract test 的实现 |
| W4 | Spec/Codegen 闭环 | §20.2 全部跑通 |
| W5 | tstdx.toml + 多源合并 + 12 case 测试 | §21 完整 |
| W6 | 错误分类树 + retry advice + 用户范式 | §24 完整 |
| W7 | i18n codec + 时区 + A 股交易日历 | §23 完整 |

### Phase 2：全协议覆盖 + 跨域贯通（8 周）

| 周 | 模块 | 产出 |
|---|---|---|
| W8 | 7709 全部 36 命令 spec + 实现 | §5 |
| W9 | 7727 扩展 + GoodsClient（期货期权外汇） | §5 + §8 |
| W10 | MAC 0x120F–0x2562 协议 | §5 |
| W11 | F10 资料 + TQLEX 网关 | §5 |
| W12 | Streaming 全特性（push 队列、增量合并、断线补数、背压） | §10 |
| W13 | 异步/同步双轨 + 桥 + 12 case | §25 |
| W14 | 互操作层（mootdx 兼容垫片 + Parquet/DuckDB sink）+ **HTTP Web 源 7 Adapter（W14b，可并行组 F）** | §26 + **§33** |
| W15 | 反馈回路（4 类上报通道 opt-in） | §27 |

### Phase 3：Rust 内核 + 集成生态（7 周）

| 周 | 模块 | 产出 |
|---|---|---|
| W16 | Rust 核心移植（Reader 层 + Parser 层） | 加 perf extra |
| W17 | Parity 测试 100% | §16 |
| W18 | HTTP REST 42 接口 + OpenAPI 文档 | §13 |
| W19 | WebSocket RPC + MCP 10 工具 | §13 |
| W20 | 文档体系（cookbook 20 + migration 6 + ADR 起 10 篇） | §28 |
| W21 | 治理文件全套 + Issue/PR 模板 | §30 |
| W22 | Docker 镜像 + CI 完整化 | §29 |

### Phase 4：协议补全 + 生态打磨 + 贯通验证（6 周）

| 周 | 模块 | 产出 |
|---|---|---|
| W23 | 主动探测（ProtocolProber）安全运行 | §5 |
| W24 | Profile 自动探测（DataProfile）收敛 + 性能基准 | §6 |
| W25 | 安全与合规边界（CLA + telemetry + 脱敏） | §22 + §27 |
| W26 | 文档完善 + 真实环境冒烟 30 日 | §28 |
| W27 | 24 项贯通验收 + `make audit-bridges` 全过 | §32 |
| W28 | v1.0 正式发布 | PyPI stable + Docker Hub |

### 路线图总览

```
Phase 0 (W1-2)    Phase 1 (W3-7)    Phase 2 (W8-15)   Phase 3 (W16-22)   Phase 4 (W23-28)
 奠基                贯通底座            全协议+跨域         Rust 集成         补全+打磨+验证
 ●                  ●●●●●             ●●●●●●●●           ●●●●●●●          ●●●●●●
                                  ↘                          ↘                  ↓
                                  ↘ 协议/Spec 双轨            ↘ 集成         ↘ 24 项
                                  ↘                          ↘                ↘ 验收
                                  ↘                          ↘                ↘ v1.0
                                  ↘                          ↘                  发布
```

| 维度 | Phase 0 | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|---|
| 协议覆盖 | L1 占位 | L1+L2+L3 全栈 | Goods + MAC + F10 | 全部 | Prober 自动补 |
| 贯通 | 规范 | 配置/错误/i18n | 异步桥/互操作/反馈 | 集成/部署 | 验收闭环 |
| 文档 | SPEC | ADR 起 | Cookbook | Migration | Changelog 稳定 |
| 治理 | CONTRIBUTING | CLA | CoC | Issue 模板 | SECURITY |
| 分发 | — | — | 基础 | wheel 12 组合 | Docker + 签名 |
| v3.1 验收 | — | 5 项 | 12 项 | 19+2 项 | **24 项** |

---

## 32. 贯通验收清单（24 项 + `make audit-bridges`）

> **v3.1 变更**：22 项 → **24 项**，新增 #23（HTTP Web 源 7 Adapter + 12 case 测试矩阵）、#24（volume/amount 归一化契约）。

### 32.1 验收项（详见 AUDIT_AND_BRIDGES.md §4，本处仅给出 §对应索引）

| # | 项 | 见 § |
|---|---|---|
| 01-03 | 协议覆盖 + spec 100% + **24 断链全闭环** | §20 + **§33** |
| 04 | tstdx.toml 12 case 合并测试 | §21.5 |
| 05 | 错误分类 100% + 可重试性 | §24.4 |
| 06 | 同步/异步一致 | §25.5 |
| 07 | 离线/在线/兜底 6 降级 | §26.3 |
| 08 | 流式订阅-重连-补数 | §10 |
| 09 | 字符集 4 编码探测 | §23.4 |
| 10 | A 股日历节假日/停牌/调休 | §23.4 |
| 11 | 时区 UTC 内部 + 本地输出 | §23.4 |
| 12 | Golden 数据来源审计 | §4 + §16 |
| 13 | AST 相似度门禁 | §4 |
| 14 | License 白名单 | §4 |
| 15 | wheel 12 组合 | §29 |
| 16 | 弃用策略 2 minor | §29.5 |
| 17 | HTTP API 42 接口 | §13 |
| 18 | MCP 10 工具 schema | §13 |
| 19 | 可观测性 zero-dep + 3 exporter | §14 + §20 |
| 20 | DataFrame/Parquet/DuckDB sink | §26 |
| 21 | 文档矩阵 | §28 |
| 22 | 治理文件 | §30 |
| **23** | **HTTP Web 源 7 Adapter + 12 case 测试矩阵 + easyquotation 兼容垫片** ★ v3.1 | **§33.10** |
| **24** | **volume/amount 归一化契约（新浪 ×1 / 腾讯 ×100+×10000 / 东财 ×100）** ★ v3.1 | **§33.5** |

### 32.2 一键贯通验证脚本

```makefile
# Makefile
audit-bridges:
    @echo ">>> 1/24 协议三层覆盖"      && pytest -q tests/test_protocol_tiers.py
    @echo ">>> 2/24 spec 全覆盖"        && pytest -q tests/test_spec_coverage.py
    @echo ">>> 3/24 24 断链闭环"        && pytest -q tests/test_bridges.py
    @echo ">>> 4/24 配置 12 case"       && pytest -q tests/test_config_merge.py
    @echo ">>> 5/24 错误体系"           && pytest -q tests/test_errors.py
    @echo ">>> 6/24 同步异步"           && pytest -q tests/test_sync_async_parity.py
    @echo ">>> 7/24 降级路径"           && pytest -q tests/test_fallbacks.py
    @echo ">>> 8/24 流式重连补数"       && pytest -q tests/test_stream_resilience.py
    @echo ">>> 9/24 字符集"             && pytest -q tests/test_encoding.py
    @echo ">>> 10/24 日历"              && pytest -q tests/test_calendar.py
    @echo ">>> 11/24 时区"              && pytest -q tests/test_tz.py
    @echo ">>> 12/24 Golden 来源审计"   && pytest -q tests/test_golden_source.py
    @echo ">>> 13/24 原创性 AST"        && pytest -q tests/test_originality.py
    @echo ">>> 14/24 License 扫描"     && pytest -q tests/test_licenses.py
    @echo ">>> 15/24 wheel smoke"       && bash scripts/wheel_smoke.sh
    @echo ">>> 16/24 弃用测试"          && pytest -q tests/test_deprecation.py
    @echo ">>> 17/24 HTTP API 契约"     && pytest -q tests/test_http_api.py
    @echo ">>> 18/24 MCP 契约"          && pytest -q tests/test_mcp_contracts.py
    @echo ">>> 19/24 可观测性导出"      && pytest -q tests/test_observability.py
    @echo ">>> 20/24 sink 三实现"       && pytest -q tests/test_sinks.py
    @echo ">>> 21/24 文档存在"          && pytest -q tests/test_docs_exist.py
    @echo ">>> 22/24 治理文件"          && pytest -q tests/test_governance.py
    @echo ">>> 23/24 HTTP Web 源矩阵"   && pytest -q tests/web/test_web_sources.py
    @echo ">>> 24/24 归一化契约"        && pytest -q tests/web/test_normalize.py
    @echo ""
    @echo "OK 24/24 audit-bridges passed"

audit-report:
    python -m tstdx.tools.compliance_report --output ORIGINALITY/AUDIT_REPORT.md
```

### 32.3 发版硬门槛

```
v1.0 发版条件（缺一不可）：
  · 24 项贯通验收全过
  · Golden 数据 ≥ 500 案例（覆盖所有已知 spec）
  · 真实环境冒烟 30 天无 P0 bug
  · 治理文件齐备 + 至少 2 个 Maintainer
  · LICENSE_HEADER 在数据导出路径下确认可用
  · README + Quickstart + Cookbook 6 个 chapter 已写
  · Migration Guide from mootdx 验证通过

v0.x 预发版（每个 minor）：
  · 24 项贯通验收全过
  · 真实环境冒烟 7 天
  · 至少 1 个 Maintainer approve
```

---

## 33. HTTP Web 行情源子系统（easyquotation 兼容层）★ v3.1 新增

> **触发**：用户要求参考 `https://github.com/shidenggui/easyquotation` 项目，开发类似功能并加入 tstdx。
>
> **调研结论**：easyquotation 是基于 HTTP 网页行情接口的轻量库（MIT），与 tstdx 的 TDX 二进制协议是**完全不同的协议族**，但获取的数据类型高度重叠（实时报价、五档、K 线）。二者形成天然互补：
> - **TDX 协议**：二进制 TCP、低延迟、高吞吐、主站可 failover → **主路径**
> - **HTTP Web 源**：零门槛、无认证、但有反爬/限流/不稳定 → **fallback 降级路径**
>
> **定位**：HTTP Web 源不是 tstdx 的替代主路径，而是 §26 `DataSourceRouter` 中预留的 HTTP fallback 适配器，当 TDX 主站全不可达时自动降级。同时提供独立使用的 `WebQuoteClient`，让只需要简单网页行情、不想连 TDX 主站的用户也能用。

### 33.1 架构定位

```
┌──────────────────────────────────────────────────────────────────┐
│                    DataSourceRouter (§26.1.3)                      │
│                                                                   │
│  ┌──────────────┐  failover  ┌──────────────────────────────────┐ │
│  │ TDX 主路径    │ ─────────▶ │ HTTP Web Fallback (§33)          │ │
│  │ StandardClient│           │  ├ SinaAdapter                   │ │
│  │ (7709/7727)   │           │  ├ TencentAdapter               │ │
│  │ ExtendedClient│           │  ├ EastmoneyAdapter ★            │ │
│  │ MacClient     │           │  ├ JslAdapter                    │ │
│  │ GoodsClient   │           │  ├ HkquoteAdapter                │ │
│  └──────────────┘           │  ├ DayKlineAdapter                │ │
│                              │  └ BocAdapter                    │ │
│                              └──────────────────────────────────┘ │
│                                                                       │
│  独立入口：WebQuoteClient(source="sina")  ← 不走 TDX，直接 HTTP      │
└──────────────────────────────────────────────────────────────────┘
```

### 33.2 数据源清单与字段映射

#### 33.2.1 新浪财经（SinaAdapter）

| 项 | 值 |
|---|---|
| API 端点 | `http://hq.sinajs.cn/rn={timestamp}&list={codes}` |
| 编码 | GBK（需 `Referer: http://finance.sina.com.cn/` 否则 403） |
| 批量上限 | 800 只/次 |
| 响应格式 | 文本，正则解析 `code="name,open,close,now,high,low,buy,sell,turnover,volume,bid1..bid5,ask1..ask5,date,time"` |
| 特殊 | 2024+ 必须带 Referer 头（否则 403）；部分接口已迁移 HTTPS |

**字段映射**（33 字段）：

| # | 字段名 | 类型 | 说明 | tstdx 统一字段 |
|---|---|---|---|---|
| 1 | name | str | 股票名 | symbol_name |
| 2 | open | float | 开盘价 | open |
| 3 | close | float | 昨收价 | prev_close |
| 4 | now | float | 现价 | last |
| 5 | high | float | 最高 | high |
| 6 | low | float | 最低 | low |
| 7 | buy | float | 竞买价 | bid |
| 8 | sell | float | 竞卖价 | ask |
| 9 | turnover | int | 成交股数 | volume（**股**） |
| 10 | volume | float | 成交金额 | amount（**元**） |
| 11-20 | bid1-5 / bid1-5_volume | float/int | 五档买 | bids[0-4] |
| 21-30 | ask1-5 / ask1-5_volume | float/int | 五档卖 | asks[0-4] |
| 31 | date | str | 日期 | date |
| 32 | time | str | 时间 | time |

> **口径注意**：新浪 `turnover` = 成交股数（股），`volume` = 成交金额（元）——**与 tstdx 统一契约中 `volume`=股、`amount`=元 一致**，无需转换。

#### 33.2.2 腾讯财经（TencentAdapter）

| 项 | 值 |
|---|---|
| API 端点 | `http://qt.gtimg.cn/q={codes}` |
| 编码 | GBK |
| 批量上限 | 60 只/次 |
| 响应格式 | 文本，`~` 分隔，按索引取值 |
| 特殊 | 字段数 > 50（含 PE/PB/市值/涨跌停/量比/委差/均价/市盈动/市盈静） |

**字段映射**（索引 → tstdx）：

| 索引 | 字段 | 类型 | 说明 | tstdx 统一字段 |
|---|---|---|---|---|
| 1 | name | str | 股票名 | symbol_name |
| 2 | code | str | 代码 | symbol |
| 3 | now | float | 现价 | last |
| 4 | close | float | 昨收 | prev_close |
| 5 | open | float | 今开 | open |
| 6 | volume | float | 成交量（手）→ ×100 → 股 | volume（**需 ×100 归一化**） |
| 7 | bid_volume | int | 买总量（手）→ ×100 | — |
| 8 | ask_volume | float | 卖总量（手）→ ×100 | — |
| 9-28 | bid1-5/ask1-5 + volumes | float/int | 五档（量 ×100） | bids/asks |
| 29 | 最近逐笔 | str | 逐笔成交 | — |
| 30 | datetime | str→datetime | `YYYYMMDDHHMMSS` | ts |
| 31 | 涨跌 | float | 涨跌额 | change |
| 32 | 涨跌(%) | float | 涨跌幅 | pct_change |
| 33 | high | float | 最高 | high |
| 34 | low | float | 最低 | low |
| 36 | 成交量(手) | int→×100 | | volume（**需 ×100**） |
| 37 | 成交额(万) | float→×10000 | | amount（**需 ×10000 → 元**） |
| 38 | turnover | float | 换手率 | turnover_rate |
| 39 | PE | float | 市盈率(动) | pe_ratio |
| 44 | 流通市值 | float | | circ_market_cap |
| 45 | 总市值 | float | | total_market_cap |
| 46 | PB | float | 市净率 | pb_ratio |
| 47 | 涨停价 | float | | limit_up |
| 48 | 跌停价 | float | | limit_down |
| 49 | 量比 | float | | volume_ratio |
| 50 | 委差 | float | | — |
| 51 | 均价 | float | | avg_price |
| 52 | 市盈(动) | float | | pe_dynamic |
| 53 | 市盈(静) | float | | pe_static |

> **口径注意**：腾讯的 volume/amount 都是"手"或"万元"单位，必须做 ×100 / ×10000 归一化到 tstdx 契约（**volume=股、amount=元**）。这与 niuniu 项目中腾讯源已有的 `_norm_volume` 逻辑一致。

#### 33.2.3 东方财富（EastmoneyAdapter）★ v3.1 新增

| 项 | 值 |
|---|---|
| API 端点 | `https://push2.eastmoney.com/api/qt/stock/get?secid={market}.{code}&fields=...` |
| 编码 | UTF-8（JSON） |
| 批量上限 | 1 只/次（个股），`/clist/get` 可批量 |
| 响应格式 | JSON |
| 特殊 | 需 UA + Referer；高频会被 IP 级封禁 20h+；串行 + 1s 间隔 |

**字段映射**（JSON key → tstdx）：

| JSON key | tstdx 字段 | 说明 |
|---|---|---|
| `f43` | last | 最新价（需 ×100 → 元） |
| `f44` | high | 最高（×100） |
| `f45` | low | 最低（×100） |
| `f46` | open | 今开（×100） |
| `f47` | volume | 成交量（手 → ×100 → 股） |
| `f48` | amount | 成交额（元） |
| `f50` | pe_ratio | 市盈率 |
| `f57` | symbol | 代码 |
| `f58` | symbol_name | 名称 |
| `f60` | prev_close | 昨收（×100） |
| `f116` | total_market_cap | 总市值 |
| `f117` | circ_market_cap | 流通市值 |
| `f162` | pe_dynamic | 市盈(动) |
| `f167` | pb_ratio | 市净率 |

> 东财的价格字段是 ×100 的整数，需要 `/100` 转浮点。这是东财特有口径。

#### 33.2.4 集思录（JslAdapter）

| 项 | 值 |
|---|---|
| 端点 | 多个（ETF/QDII/可转债各自不同） |
| 认证 | Cookie（可选，不登录可获取基础数据） |
| 方法 | `etfindex()` / `qdii()` / `cb()` |
| 用途 | ETF 折溢价/QDII 净值/可转债数据 |

#### 33.2.5 港股行情（HkquoteAdapter）

| 项 | 值 |
|---|---|
| 端点 | 腾讯港股 `http://qt.gtimg.cn/q=hk{code}` |
| 字段 | 与腾讯 A 股类似但字段位置有差异 |
| 用途 | 港股实时五档 |

#### 33.2.6 日 K 线（DayKlineAdapter）

| 项 | 值 |
|---|---|
| 端点 | 腾讯 `http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},,,{count},qfq` |
| 用途 | HTTP K 线（前复权 qfq / 后复权 hfq） |
| 限制 | 1 只/次；连续 5000+ 次返回空（限流不封 IP） |

#### 33.2.7 中行汇率（BocAdapter）

| 项 | 值 |
|---|---|
| 端点 | 中国银行外汇牌价页面 |
| 用途 | 人民币/外币汇率 |
| 输出 | 现汇买入/现钞买入/现汇卖出/现钞卖出/中行折算价 |

### 33.3 统一接口设计

```python
# tstdx/web/base.py
class WebQuoteSource(Protocol):
    """所有 HTTP Web 行情源的统一接口"""

    name: str  # "sina" | "tencent" | "eastmoney" | ...
    max_per_request: int  # 批量上限
    encoding: str  # "gbk" | "utf-8"
    requires_referer: bool  # 是否需要 Referer 头

    def fetch_quotes(self, codes: list[str]) -> dict[str, Quote]:
        """获取实时报价（五档）"""
        ...

    def fetch_market_snapshot(self, *, prefix: bool = False) -> dict[str, Quote]:
        """全市场快照（仅 sina/tencent 支持）"""
        ...

    def fetch_kline(self, code: str, count: int = 100, *, adjust: str = "qfq") -> list[Bar]:
        """HTTP K 线（仅 daykline/eastmoney/tencent 支持）"""
        ...


class WebQuoteClient:
    """独立入口：不走 TDX，直接 HTTP"""

    def __init__(self, source: str = "sina", *, config: TstdxConfig | None = None):
        self._source = self._create_source(source)

    def real(self, codes: str | list[str]) -> dict[str, Quote]:
        """获取实时行情（兼容 easyquotation.real() 语义）"""
        ...

    def market_snapshot(self, *, prefix: bool = False) -> dict[str, Quote]:
        """全市场快照（兼容 easyquotation.market_snapshot()）"""
        ...

    def kline(self, code: str, count: int = 100, *, adjust: str = "qfq") -> list[Bar]:
        """HTTP K 线"""
        ...
```

### 33.4 反爬与稳定性对策

| 数据源 | 风险 | 对策 |
|---|---|---|
| 新浪 | 403（无 Referer）| 强制注入 `Referer: http://finance.sina.com.cn/` + UA |
| 新浪 | 部分接口迁移 HTTPS | 自动 `http→https` 升级 + 证书校验 |
| 腾讯 | GBK 解码 | encoding_rs / `response.encoding = "gbk"` |
| 腾讯 | 连续 5000+ 次返回空 | 限流 3 req/s + 检测空响应降级 |
| 东财 | IP 级封禁 20h+ | **串行 + 1s 间隔 + 随机抖动** + Keep-Alive + UA+Referer |
| 东财 | 不同子域不同 WAF | push2 被封 ≠ datacenter 被封，按子域独立限流 |
| 集思录 | Cookie 过期 | `set_cookie()` 方法 + 检测登录页跳转 |
| 通用 | DNS 劫持 | 可选 DoH（DNS over HTTPS） |

**限流策略**（自动适配交易时段）：

```python
# tstdx/web/ratelimit.py
WEB_RATE_LIMITS = {
    "sina": {"closed": 60, "intraday": 15, "pre_close": 30},
    "tencent": {"closed": 60, "intraday": 15, "pre_close": 30},
    "eastmoney": {"closed": 2, "intraday": 1, "pre_close": 1},  # 极保守
    "jsl": {"closed": 30, "intraday": 10, "pre_close": 20},
}
```

### 33.5 volume 归一化契约

> **核心原则**：tstdx 全局契约 `volume=股`、`amount=元`，所有 HTTP 源的原始数据必须归一化。

| 数据源 | 原始 volume 单位 | 归一化系数 | 原始 amount 单位 | 归一化系数 |
|---|---|---|---|---|
| 新浪 | 股（turnover 字段） | ×1（已一致） | 元（volume 字段） | ×1 |
| 腾讯 | 手（索引 6/36） | ×100 → 股 | 万元（索引 37） | ×10000 → 元 |
| 东财 | 手（f47） | ×100 → 股 | 元（f48） | ×1 |
| 腾讯港股 | 股 | ×1 | 港元 | ×1（标注 currency=HKD） |

**实现**：每个 Adapter 在 `format_response_data` 后强制调用 `_normalize_volume()`。

```python
# tstdx/web/normalize.py
def normalize_volume(raw_volume: int, source: str, *, is_amount: bool = False) -> int | float:
    """归一化到 tstdx 全局契约：volume=股、amount=元"""
    COEFF = {
        "sina": {"volume": 1, "amount": 1},
        "tencent": {"volume": 100, "amount": 10000},
        "eastmoney": {"volume": 100, "amount": 1},
        "hkquote": {"volume": 1, "amount": 1},
    }
    key = "amount" if is_amount else "volume"
    return raw_volume * COEFF[source][key]
```

### 33.6 与 TDX 协议层的降级路径

```
场景：TDX 全主站不可达（AllHostsUnreachable）

1. DataSourceRouter 捕获 AllHostsUnreachable
2. RetryAdvice.fallback_to_offline = True
3. 尝试本地 vipdoc Reader（最近一天缓存）
4. 本地无缓存 → 降级到 HTTP Web 源（受 `[web].fallback_enabled` 开关控制）
5. 按 `[web].enabled_sources` 数组顺序依次尝试
     默认 ["sina", "tencent", "eastmoney"]，用户可改顺序或裁剪
6. 单源失败 → 按 `[web].max_retries` 重试 → 仍失败则切下一源
7. 全部源失败 → 抛 AllSourcesExhausted
```

> **配置绑定**：降级顺序**不硬编码**，由 §21.2 `[web].enabled_sources` 驱动；限流速率由 `[web.rate_limit]` 按源区分（东财必须 1 req/s 串行）；Referer/UA 由 `[web.headers]` 提供。运行时可通过 `config_overrides={"web": {"enabled_sources": ["tencent"]}}` 单次覆盖。

**降级时的数据差异标注**：

```python
@dataclass
class Quote:
    symbol: str
    last: float
    ...
    source: Literal[
        "tdx_online",
        "tdx_local_vipdoc",
        "web_sina",
        "web_tencent",
        "web_eastmoney",
        "user_supplied",
    ]
    fidelity: Literal["full", "reduced"]  # HTTP 源可能缺字段
```

### 33.7 easyquotation 兼容垫片

```python
# tstdx/compat/easyquotation.py
"""提供与 easyquotation 相同的 API，让原用户零成本切换"""

from tstdx.web import WebQuoteClient


def use(source: str):
    """兼容 easyquotation.use() 工厂"""
    return WebQuoteClient(source=source)


# 原有方法签名保持一致：
#   quotation.real('000001')          → WebQuoteClient.real()
#   quotation.market_snapshot(prefix=True) → WebQuoteClient.market_snapshot()
#   quotation.stocks(['000001', '162411']) → WebQuoteClient.real() (别名)
```

**启用**：`pip install tstdx[easyquotation]` 或 `[compatibility] easyquotation_layer = true`

### 33.8 限定用途声明

```
HTTP Web 行情源为非官方公开接口，本库仅提供协议读取能力：
  · 数据版权归相应数据商所有
  · 接口可能随时变更或下线，不保证长期可用
  · 存在反爬/限流/封 IP 风险，生产环境应优先使用 TDX 协议
  · 用户自行承担使用风险与合规责任
  · 东财 push2 系列高频调用会导致 IP 级封禁（20h+），必须遵守限流策略
```

### 33.9 目录结构新增

```
tstdx/
├── web/                           ★ v3.1 新增
│   ├── base.py                    # WebQuoteSource Protocol + WebQuoteClient
│   ├── sina.py                    # SinaAdapter
│   ├── tencent.py                 # TencentAdapter
│   ├── eastmoney.py               # EastmoneyAdapter
│   ├── jsl.py                     # JslAdapter
│   ├── hkquote.py                 # HkquoteAdapter
│   ├── daykline.py                 # DayKlineAdapter
│   ├── boc.py                     # BocAdapter
│   ├── normalize.py               # volume/amount 归一化
│   ├── ratelimit.py               # HTTP 源限流（按源+时段）
│   └── headers.py                 # UA/Referer/Cookie 管理
│
├── compat/
│   ├── mootdx.py                  # v2.0 已有
│   └── easyquotation.py           ★ v3.1 新增
```

### 33.10 测试矩阵

| # | 测试项 | 方法 |
|---|---|---|
| 01 | 新浪正则解析 33 字段 | 给定 raw text → expected dict |
| 02 | 腾讯 ~ 分割 53 字段 | 给定 raw text → expected dict |
| 03 | 东财 JSON 解析 | 给定 JSON → expected dict |
| 04 | volume 归一化（新浪 ×1 / 腾讯 ×100 / 东财 ×100） | 归一化前后对比 |
| 05 | amount 归一化（新浪 ×1 / 腾讯 ×10000 / 东财 ×1） | 归一化前后对比 |
| 06 | 新浪无 Referer → 403 | mock 服务器 |
| 07 | 东财串行限流 1s 间隔 | 计时器断言 |
| 08 | 腾讯连续 5000 次空响应 → 降级 | mock |
| 09 | 全市场快照 800 只分批 | mock |
| 10 | TDX→HTTP 降级路径 | 模拟 AllHostsUnreachable → WebQuoteClient |
| 11 | easyquotation.use() 兼容 | API 签名一致性 |
| 12 | Quote.source 字段标注正确 | 降级后 source="web_sina" |

### 33.11 路线图更新

在 §31 Phase 2 的 W14 中新增 HTTP 源适配器批次：

| 周 | 任务 | 产出 |
|---|---|---|
| W14a | mootdx 兼容垫片 + 3 种 sink（已有） | §26 |
| **W14b** ★ | **HTTP Web 源适配器**（sina/tencent/eastmoney/jsl/hkquote/daykline/boc 7 个 Adapter + normalize + ratelimit + 兼容垫片） | **§33** |
| W14c | DataSourceRouter + 6 种降级路径（含 HTTP fallback） | §26 |

**Phase 2 退出条件新增**：
- [ ] 7 个 HTTP Web 源 Adapter 全部实现 + 字段映射测试通过
- [ ] volume/amount 归一化契约 4 case 通过
- [ ] TDX→HTTP 降级路径测试通过
- [ ] easyquotation.use() 兼容垫片可用

---

> **下一步（v3.1）**：确认方向后，Phase 0（Spec 体系 + Golden 采集 + Codegen 模板）开始执行。第 1 周首先建立 `PROTOCOL_SPEC/INDEX.yaml` 占据 36 个已知命令的 spec 编号，再走 §20.2 的 Spec/Codegen/Contract 闭环。
>
> **并行提示**：W14b HTTP Web 源适配器**不依赖 TDX 协议层**（纯 HTTP，无二进制协议前置），可从 Phase 1（W5 起）提前并行启动，由集成工程师独立推进，不占关键路径。

---

## 版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-08-31 | 初版，21 命令，四阶段 16 周（已归档至 `docs/archive/DESIGN_v1.0.md`） |
| v2.0 | 2026-08-31 | 重大升级：协议三层覆盖（36 命令 + 通用兜底 + 探针/主动探测）、原创性洁净室工程规范、Profile 全数据兼容系统、完整 Streaming 实时子系统、可观测性、商品语义协议（GoodsClient），路线图 22 周四阶段 |
| v3.0 | 2026-08-31 | 补齐断链贯通：链路 7 环节 21 断点审计 → 9 大贯通工程；新增 §20–§32 共 13 章：链路贯通总图 + Spec/Codegen/Contract 闭环 + 配置中心 + 安全/合规 + 国际化/时区/日历 + 错误体系 + 异步同步桥 + 互操作/生态适配器 + 反馈回路 + 文档体系 + 部署/分发 + 治理 + 路线图扩展至 28 周 5 阶段 + 22 项贯通验收清单 + `make audit-bridges` 一键脚本。审计索引见 `AUDIT_AND_BRIDGES.md` |
| **v3.1** | **2026-08-31** | **新增 §33：HTTP Web 行情源子系统（easyquotation 兼容层）。将新浪/腾讯/东财/集思录/港股/中行 7 类 HTTP 源作为 TDX 协议层的 HTTP fallback 适配器纳入 `DataSourceRouter` 统一路由；新增 7 Adapter 字段映射规范（§33.2）、6 类反爬对策（§33.4）、volume/amount 归一化契约（§33.5）、TDX 降级路径（§33.6）、easyquotation 兼容垫片（§33.7）、限定用途声明（§33.8）、12 case 测试矩阵（§33.10）。审计增量：断链点 21→**24**（+A5 口径归一 / +A6 反爬韧性 / +A7 兼容迁移）、贯通工程 9→**10**（+E10）、验收项 22→**24**（+#23 HTTP 源矩阵 / +#24 归一化契约）。开发计划：Phase 2 新增 W14b 批次（`P2-W14-08 ~ W14-16`），列为可并行组 F** |
