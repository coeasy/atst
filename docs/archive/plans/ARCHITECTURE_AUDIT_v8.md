# tstdx 架构审计与重构报告 v8（2026-09）

> 回答三个核心问题：**功能/架构/实现有哪些不合理？核心功能是否全部实现？主体链路是否全部贯通？**
> 配套执行记录见 [REFACTOR_PLAN_v8.md](REFACTOR_PLAN_v8.md)；本文档同时给出审计结论与后续（v9）路线。

## 一、主体功能与架构现状（梳理结论）

### 1.1 五层主链路（全部贯通 ✅）

```
 协议层          传输层           客户端层           门面层            服务面
 protocol/  →  transport/  →  client/(sync/async) →  facade/api  →  integration/
 codec/        pool/ratelimit   5 族子客户端          四路由+熔断      http/ws/mcp
                                     ↓                                    ↑
                               web/（50 Source 类，HTTP 兜底）─────────────┘
                                     ↓
                          sources/（5 级降级：tdx→web→reader→cache→synthetic，数据面）
```

- **数据面主链路**：TDX 主站 → Web 源（45+ HTTP 源类）→ 本地 vipdoc → golden 回放 → 合成数据，全部有测试且经对抗矩阵（9 payload × 85 命令，逃逸=0）。
- **服务面链路**：HTTP REST（42 端点白名单）/ WS JSON-RPC / MCP stdio 12→23 工具，全部接线 `UnifiedQuoteAPI` 门面（`integration/http_server.py:106`、`ws_server.py:257`），并有离线 fake-client 测试覆盖。
- **交互面链路**：CLI 19 子命令 → `TdxClient`/`UnifiedQuoteAPI`/`WebQuoteSession`，golden 缓存回放支持离线调试。

### 1.2 v8 重构已落地的结构治理（行为零变化，全量测试绿）

| 项 | 前 | 后 |
|---|---|---|
| `tstdx/cli.py` 1210 行单文件 | ~40 个 `_cmd_*` + 210 行 argparse | `cli/` 包：`_common`/`cmds_market`/`cmds_web`/`cmds_hosts`/`parser` |
| `tstdx/client.py` 1715 行 9 类 | 单文件 sync/async/工厂混杂 | `client/` 包：`sync.py`(1035)/`async_.py`(681)/`factory.py`(66)，37 个模块级符号逐一 re-export 对齐，monkeypatch 语义等价（`dispatch` 经包属性延迟解析） |
| `tstdx/web/facade.py` 1336 行 63 方法 | 单类堆全部 Web 域能力 | `facade.py`(135) + 3 个 Mixin 文件（market 586/info 486/baidu 265），61 方法 AST 级 diff 为空 |
| `facade/api.py` 21 段资源模板 | 「构造→try→close」复制粘贴 | 统一 `_with` 帮助方法，-85 行 |
| `tstdx/i18n` 废弃 shim | 与 `charset` 双包并存 | 已删除，3 测试/文档/白名单同步迁移 |

## 二、架构与实现的不合理之处（按严重度）

### A 级（结构性，需 v9 决策）

1. **同步/异步客户端逐方法镜像、零共享抽象**
   `client/sync.py`(1035 行) 与 `client/async_.py`(681 行) 约 25 组方法一一镜像，仅 `_req` 传输 seam 不同；`test_sync_async_parity.py` 奇偶门禁在兜底，但每加一个方法要写两遍。
   **建议**：抽 `_ClientMixin`（方法骨架 + 传输回调），重载密集（`bars` 5 个 @overload）需渐进迁移；v9 分批做，先迁 3-4 个简单方法验证模式。
2. **双路由链并存，返回口径未对拍**
   `facade/api.py`（local→tdx→web + 熔断，交互面）与 `sources/DataSourceRouter`（5 级链 + 缓存，数据面）两套实现；`facade/api.py:186-198` 已书面承认 (symbol, period, count) 三元组口径**未逐项对拍**。
   **建议**：先补对拍测试锁定差异，再决定是否让 facade 路由改调 router（合并）或维持分工（交互面/数据面）。本 v8 未动（风险中高，需独立批次）。
3. **AsyncUnifiedQuoteAPI 仅 10 核心方法 + `arun()` 泛化**（见三.3）

### B 级（局部，低成本可改）

4. **web Source 层残余重复**：`adapters_ext.MinuteKlineSource` 与 `adapters.KlineSource` 的分页拉取逻辑雷同（`adapters_ext.py:124-203` vs `adapters.py:825-932`）；`corporate.py` 的 Notice/Research 两源退回 `BaseWebSource` 手写取数，应归入 `_EastmoneyJson`（主机池 failover 复用）。
5. **`sink/` 与 `sinks/` 包名易混淆**：职责确实不同（输出 Sink vs vipdoc `.day` 写回），README 已补注；长期可考虑 `sinks/` 更名 `output/`（有 import 面成本，本期不做）。
6. **web/__init__.py 导入面大**：18 个子模块、50 Source 全量 re-export；`import tstdx.web` 首次加载偏重，可评估惰性 `__getattr__`（对齐顶层 `tstdx/__init__` 的 `_LAZY` 模式）。
7. **pytest-cov 5.0.0 与 pytest 9.1.1 组合吞掉终端 summary 行**（既有环境问题，测试本身正常；日志需 `-p no:cov` 获取汇总）。

## 三、核心功能完整性核查（宣称 vs 实现）

| # | 宣称/预期 | 实际状态 | 结论 |
|---|---|---|---|
| 1 | 5 协议族 85 命令、61 L1 解析器、三级分派 | 实现且有对抗矩阵与 golden 门禁（530 payload） | ✅ 完整 |
| 2 | 同步/异步客户端双 API | `TdxClient` 与 `AsyncTdxClient` 公开方法集合**完全一致**（dir() diff 为空），奇偶门禁守护 | ✅ 完整 |
| 3 | 异步统一门面 | `AsyncUnifiedQuoteAPI` **有意紧凑**：10 核心方法 + `arun("method")` 泛化任意方法 + `aquery()` 永不抛边界（`facade/async_api.py:17` 明示设计决策） | ⚠️ 部分镜像（设计使然，非缺失；长尾场景用 `arun` 已可覆盖） |
| 4 | 复权 K 线 | `adjusted_bars` 仅 `route="local"` 提供原始 K 线 + 在线 0x000F 事件（`facade/api.py:717-753`）；period≠day 或无 vipdoc 需调用方自取 events | ⚠️ 能力受限但口径守卫明确（W13），文档已声明 |
| 5 | 全市场快照 | 仅 A 股节点；港/美股整市场枚举**显式 ValueError**（N7 能力边界），按代码的 hk/us quotes 有 | ⚠️ 有意收窄，非缺陷 |
| 6 | 流式订阅 | QuoteStream（轮询+diff）可用；`streaming/push.py`（推送通道）与 `engine.py` 平行实现**未接入主链路**，登记白名单待 G2 决策 | ⚠️ 半成品未接线 |
| 7 | 交易面 | `tstdx.trade/*`（1535 行）为洁净室推断**模拟器**：`SocketTransport.connect` 显式抛 `TradingUnavailable`，不连真实券商；主链路零引用（可达性孤儿） | ⚠️ 有意独立（范围红线写入 docstring），v8 已登记白名单并在 README 标注定位 |
| 8 | 可观测性 | 指标注册表 + 三导出器 + `start_exporter` 均实现并有测试；属「装配式 API」，需使用方显式开启，非自动接线 | ✅（手动 API 定位） |
| 9 | 凭据/反馈/安全 | `security/credentials`、`feedback`（reporter/telemetry/stats）均实现、白名单登记为手动公开 API，CLI 有 `feedback` 子命令接线 | ✅ |
| 10 | Rust 加速层 | `tstdx/native` 自测对拍、非热路径、白名单登记，批次 H 待决策 | ⚠️ 实验性，保留 |

**总评**：README 宣称的数据/门面/服务面能力**全部落地**；「不完整」项均为**有意收窄并文档声明的能力边界**（复权 local-only、全市场不含港美、交易面模拟器、异步门面紧凑设计），未发现「宣称了但悄悄没实现」的情况。

## 四、主体链路贯通性核查

- ✅ **贯通**：行情（quotes/bars/minute/trades）三通路互备；K 线分页/复权/板块/F10/扩展市场；Web 50 源注册表三方一致性门禁（`_ADAPTERS` + Normalizer + `CAPABILITY_FACADE`）；HTTP/WS/MCP 服务面；CLI 全部 19 子命令有语义测试。
- ✅ **降级链**：tdx→web→reader→cache→synthetic 五级在 `sources/` 接线并有集成测试（离线 fake）。
- ⚠️ **未贯通（有意/待决策）**：`streaming/push`、`streaming/engine`（平行实现）、`trade/*`、`native/*` —— 全部已在 `scripts/_reach_allow.txt` 登记并附处置理由，`audit_reachability.py --strict` 通过（孤儿=0）。

## 五、v9 后续路线（✅ 决策已确认，方案见 [REFACTOR_PLAN_v9.md](REFACTOR_PLAN_v9.md)）

所有者已拍板：**双路由链 v9 直接合并**（Q1，先对拍后合并）、**sync/async 一次性全量迁移**共享骨架（Q2）、**异步门面保持 10 核心 + arun**（Q3）、低优先级四项（web Source 收尾 / web 惰性导入 / 半成品收敛决策 / sinks 改名）全部纳入（Q4）。执行顺序与门禁详见 v9 方案。

> 原 v9 草案（待决策版）已被 REFACTOR_PLAN_v9.md 取代。

## 六、验证记录（v8 收口）

- 全量 `pytest tests/ -q`：通过（exit 0，0 FAILED；.test_tmp/full_final.log）
- 对抗矩阵 / golden 门禁 / 严格可达性门禁：通过（孤儿=0）
- P4 方法集合 AST diff：空；P5 模块级符号 37 项 hasattr 全对齐
