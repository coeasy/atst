# tstdx 潜在问题清单与优化改进方案

> 当前稳定发布基线：**v1.0.0（2026-09-09）**；本文正文承接历史 **v1.4.0 修复波**
> 的健康评估内容，正文中的 v1.4.0 均为历史批次标识，不代表当前包版本。
> 前身 v1.2.0 快照已于 2026-09-06 归档至本文附录「§6 v1.2.0 快照」，
> 现正文反映 v1.4.0 现状。
> 功能深度地图见 [FEATURE_MAP_AND_ROADMAP.md](FEATURE_MAP_AND_ROADMAP.md)；本文聚焦**现存潜在问题**与**改进批次规划**。
> 生成日期：2026-09-06（v1.4.0 更新）。证据标注：`文件:行号` 均经实际核查。

---

## 1. 项目主要功能全景

### 1.1 架构分层（自底向上，v1.4.0）

```
┌─ 服务面：HTTP REST(42 端点) / WebSocket JSON-RPC / MCP stdio(12 工具)
├─ 门面层：UnifiedQuoteAPI(46+ 方法·四路由·熔断·P13-A 兜底) / ApiResponse
├─ 数据源层：TDX 主站(传输+协议) → Web 45 源(17 模块) → vipdoc 本地 → golden 回放 → 合成
├─ 协议层：85 命令账本 → 三级分派(L1 精确 61 解析器 → L2 启发 → L3 透传)
├─ 传输层：4 槽连接池 / RLock 租约 / 限流令牌桶 / 测速 / 主站嗅探
├─ 领域层：symbol 单一事实源 / adjust 复权 / calendar 交易日历
├─ 流式层：QuoteStream + AsyncQuoteStream（P13 内核由 engine 组件构成：
│          DeltaMerger/BackpressureQueue/ReconnectPolicy，E6 三类错误具备真实抛点）
├─ 存储层：DataFrame / Parquet / DuckDB 三 Sink（原子写）
└─ 支撑层：config(6 源合并) / errors(E1-E9 44 类·v1.4.0 新增 SourceUnavailable E7050) /
           observability / feedback / security / i18n
```

### 1.2 功能矩阵（v1.4.0）

| 子系统 | 核心能力 | 规模 | 状态 |
|---|---|---|---|
| 协议编解码 | 帧解析 / LEB128 / tdx_float / zlib strict / count 钳制 | 85 命令 / 61 L1 解析器 | ✅ 稳定 |
| 传输 | 连接池+心跳+退避 / 时段限流 / 主站测速 | 4 槽 / 7 模块 | ✅ 稳定 |
| 客户端 | 同步+异步 / 并发批量 / 5 族客户端工厂 | 2 主类+5 族 | ✅ 稳定 |
| 门面 | 四路由(tdx/web/local/auto) / 熔断 / adjust 守卫 / **P13-A 兜底路径** | 46+ 方法 | ✅ 稳定 |
| Web 数据源 | 行情/K线/资金/板块/龙虎/热榜/问财 等 | 45 源类 / 17 模块 | ✅ 稳定 |
| 流式 | 轮询+diff 订阅 / 裸码归一 / 线程兜底 / **engine 内核** | 2 Stream + 6 engine 组件 | ✅ 稳定（M1 已接线） |
| 服务面 | HTTP / WS / MCP 三服务 | 42+7+12 接口 | ✅（socket 冒烟缺⚠️） |
| 存储/配置 | 三 Sink 原子写 / 6 源配置合并 / 凭据三级存储 | 3+2+1 | ✅ 稳定 |
| 工具链 | capture 合规采集 / codegen / spec_audit / golden_audit / check_originality / probe | 6+ 工具 | ✅ 稳定 |
| 可观测 | Metrics 注册表 + Prometheus/StatsD/OTLP 导出 | 3 导出器 | ✅ 稳定 |

### 1.3 质量基线现状（v1.4.0 实测）

- 测试：76 文件 / **2040+ 用例全绿**（P13-A 后 facade 15 新增回归，覆盖率 76.21%）
- Golden：530 个真实 payload 三旗标门禁（markets + kline-categories 0,4,9 + payloads）
- 对抗矩阵：9 恶意 payload × 85 命令，原生逃逸 = 0
- 可达性：孤儿模块 = 0（strict）
- 缝钉：8 个跨域契约缝钉 + 4 对抗金丝雀
- CI：9 jobs（lint / type-check / test×4 版本 / originality / spec-coverage / bridges / golden / adversarial / reachability）
- mypy：0 错误（117 文件，P13 已清 39 错，基线清零）
- ruff check：0 错误；ruff format：0 漂移

---

## 2. 潜在问题清单（v1.4.0 现状）

### 2.1 系统级问题（发布工程 / 门禁完整性）

| # | 严重度 | 问题 | 证据 | 影响 |
|---|---|---|---|---|
| S1 | — | **↩ 复核反驳**：CI test job 执行 `pytest tests/` 全量，seams/adversarial/slow 均被执行 | pytest addopts=pyproject:88 | 无影响 |
| S2 | **中** | **CI 不验证 Windows 全矩阵**：9 个 job 中 Windows 仅单版本冒烟（3.12）；Windows 编码问题已有两次真实案例 | `ci.yml` | Windows 专属回归上线前不可见 |
| S3 | 低 | **format 门禁不含 scripts/**：`ruff format --check` 只查 `tstdx/ tests/` | `ci.yml:28` | scripts/ 格式漂移不被 CI 捕获 |
| S4 | — | **✅ 已闭环（v1.4.0）**：版本已落笔到 1.4.0；CHANGELOG 完整记录 v8-v13 全部批次 | `pyproject.toml:7` | 无影响 |
| S5 | — | **✅ 已闭环（P13）**：mypy 98→0 错误（39 错修复：pool 返回类型注解、可选依赖 override、facade._client Any 化） | `mypy tstdx/ --ignore-missing-imports` exit 0 | 无影响 |
| S6 | — | **✅ 已闭环（P13-D/G）**：文档漂移清理——DESIGN.md §26.1.1 mootdx 垫片已加「已废弃」注记；DESIGN.md 32→42 接口；Makefile golden target 更新 | DESIGN.md / Makefile | 无影响 |
| S7 | **中** | **真机定标周未执行（I1）**：5 悬案（0x0530 反转族 / 0x0547 bj / 0xFC5 15B vs 74B / 0x1300 差分 / std7727 base）仍需真机样本定标 | docs/archive/OPTIMIZATION_PLAN.md | 数据正确性悬案未清；golden 530 → 目标 ≥700 |

### 2.2 架构悬置项（第三态决断）

| # | 严重度 | 问题 | 证据 | 影响 |
|---|---|---|---|---|
| A1 | — | **✅ M1 已接线（v1.4.0）**：QuoteStream/AsyncQuoteStream 内核由 engine 组件构成，E6 三类错误具备真实抛点 | `streaming/__init__.py` + `streaming/engine.py` | 无影响 |
| A2 | **中** | **native 加速实质放弃但未决断（M1b 用户已决断废弃）**：v1.4.0 起 `tstdx.native` 导入发 DeprecationWarning；v1.6.0 按契约删除。当前仍有 `_LAZY` 导出与测试守护 | `tstdx/native.py`、`CHANGELOG.md` | 死承诺面逐步收敛；CHANGELOG 缺 v1.5.0/v1.6.0 时间线（**P13-F 已补**） |
| A3 | 低 | **性能无基线回归**：benches 已删，基线数据仅存归档文档 E10：异动池 33.7 万 rec/s | docs/archive/OPTIMIZATION_PLAN.md:103 | 性能退化不可检测 |

### 2.3 真机定标悬案（已知 ⚠️，非新发现）

> 以下为"帧结构合法但内容错误"风险类，**必须真机采集定标**（I1 真机定标周），代码侧已用 IntegrityViolation fatal 语义守住降级红线：

1. 0x0530 反转族语义（P1d 已 golden 裁决，但真机复核待做）
2. 0x0547 北交所 K 线类别映射
3. 0xFC5 记录长 15B 变体
4. std7727 / mac 族 diff base 确认
5. 指数 K 线 4 字节尾多类别覆盖

### 2.4 v1.4.0 新识别项（P13 批次）

| # | 严重度 | 问题 | 状态 |
|---|---|---|---|
| P13-A | 高 | 6 个「仅 tdx 路由」方法在对应命令 offline/环境失效时**无 web 兜底路径**，用户拿到 `AllHostsUnreachable` 或读超时——`block_quotes`/`auction`/`volume_price`/`f10_catalog`/`ex_market_list`/`ex_instruments` | ✅ **已修复**：`block_quotes` 增 web 近似兜底（`board_rank`）；其余 5 个 auto 路由下转 `SourceUnavailable`（E7050），context 附 `alternatives` |
| P13-D1 | 中 | `streaming/__init__.py:370-376` 死代码嵌套函数（`_dispatch_error` 缩进在 `_resolve_payload` 函数体内，return 之后，引用不存在的 `self`）——复制粘贴遗留 | ✅ **已删除**，ruff + pytest 全绿 |
| P13-D/G | 低 | 文档漂移：DESIGN.md:2866 mootdx 垫片指向已删除、DESIGN.md 32→42 接口、Makefile target 500→530、tdx_status.md 未含 P13-A 后状态 | ✅ **已修复** |
| P13-B | 中 | 主站池是静态常量，7727/MAC 候选长期未更新，缺自动化巡检 | ⏳ 待办 |
| P13-C | 中 | 5 悬案未裁决（真机定标周未执行） | ⏳ 待办 |
| P13-E | 低-中 | CI Windows 只加单版本冒烟；性能基线未建；覆盖率 76.21% 距目标 80 有 3.79 点余量 | ⏳ 待办 |
| P13-F | 低 | Native 删除时间线未在 CHANGELOG 明确节点 | ✅ **已补**（见 CHANGELOG §Unreleased） |

---

## 3. 改进批次规划（P14 起）

> P13 已闭环（2026-09-06 交付）；P14 承接 P13 遗留项与真机定标周执行。
> P14-D/E/F 已在本轮（2026-09-07）闭环，见下方状态表；P14-A/B/C 待外部
> 资源（新主站发现源、真机窗口、CI 计费）配合。

### P14 批次（v1.5.0）

| # | 项 | 内容 | 状态 | 出口标准 |
|---|---|---|---|---|
| P14-A | **主站池巡检自动化** | cron 探测 5 族（含 7727/MAC 新候选），发现可用主机自动落 `ranking_file`；引入 Cloudflare Radar / MTR 探测新候选入口；社区贡献主站入口 | 🔧 进行中（`scripts/audit_hosts.py` + `host-audit` CI job 已上线，新候选发现源待定） | cron 探测 5 族，新主站自动入库 |
| P14-B | **真机定标周（I1）** | 收盘后采集 5 悬案族真实样本 → golden 固化 → 消除 ⚠️ 标记 | ⏳ 待真机窗口 | golden ≥700，⚠️ 清零 |
| P14-C | **CI Windows 全矩阵** | 从冒烟扩到 3.11+3.12 双版本；性能基线重建（codspeed 或 ASV）；覆盖率 76→80 | ⏳ 待 codspeed 引入 | 3 门禁全绿 |
| P14-D | **Native 删除执行** | v1.5.0 强告警（DeprecationWarning → UserWarning + logger 双通道）；v1.6.0 按契约删除 `tstdx/native.py` + `_LAZY` 导出 + 测试 | ✅ v1.5.0 强告警已落地（`tstdx/native.py` 313-332 行）；v1.6.0 删除窗口期在 CHANGELOG Deprecation Timeline 锁定 | v1.5.0 警告、v1.6.0 删除 |
| P14-E | **异步门面 P13-A 扩展** | `AsyncUnifiedQuoteAPI` 同步 `SourceUnavailable` 转换语义 | ✅ 契约锁定：异步门面经 `asyncio.to_thread` 桥接同步门面实例，SourceUnavailable 转换**自动继承**（`tests/facade/test_w11_w12_w13.py::TestP14EAsyncFacadeSourceUnavailable` 3 个测试 + docstring 声明） | 异步门面 6 方法同语义 |
| P14-F | **异步 engine 组件接线** | `AsyncQuoteStream` 改为 engine 组件内核 | ✅ 已在 M1 阶段完成接线（`ReconnectPolicy` 指数+抖动+上限、`BackpressureQueue` 丢弃最旧、共享 `_resolve_payload` 处理 GapUnfilledError、`Subscription._merger` DeltaMerger）；本轮验证 tests/streaming 41 passed | 异步流具备 DeltaMerger/BackpressureQueue/ReconnectPolicy |

### P15 批次（v1.6.0，规划）

| # | 项 | 内容 | 出口标准 | 预估 |
|---|---|---|---|---|
| P15-A | **Native 删除执行** | 按契约删除 `tstdx/native.py`、`_LAZY` 导出、相关测试与文档；导入即 ImportError | v1.6.0 死承诺面清零 | 1 天 |
| P15-B | **性能基线固化** | 引入 codspeed 或 ASV；benches/ 结果入 CI artifact；阈值告警 | 版本间回归可检测 | 3 天 |
| P15-C | **社区主站贡献入口** | PR 模板 + `scripts/audit_hosts.py --discover`；社区贡献主站自动落 ranking_file | 新候选自动入库 | 2 天 |
| P15-D | **integration/facade 端点级单测补齐** | 覆盖率 77→80（P13-E 已提到 77，当前实测 76.21%） | 3 门禁全绿 + 覆盖率 ≥80 | 3 天 |

### 依赖关系

```
P14-B（真机定标周） ──→ 消 5 悬案 → 数据正确性声明可信
P14-A（主站池巡检） ──→ 恢复 7727/MAC → 扩展市场/Goods/MAC 族可用
P14-C（CI 深化）   ──→ 性能基线 → 版本间回归可检测（依赖 P15-B 引入 codspeed）
P14-D（native 强告警）→ P15-A（v1.6.0 删除执行）
P14-E/F（异步对齐）──→ 已闭环
```

### 4. 门禁演进承诺

每批次出口必须保持：`make gates` 六步绿 + 缝钉全绿 + 附录白名单不新增无决议条目；新增公共 API 必附「三件套」（单测 + cookbook 段落 + docs/api 表行），破坏性变更必上 CHANGELOG「Changed」+ DeprecationWarning ≥1 版本。

---

## 附录：§6 v1.2.0 快照（历史归档）

> 以下为 2026-09-03 原始评估内容，保留作历史参考。v1.3-v1.4 期间 S4/S5/S6 已闭环，
> P13 批次已识别并修复 A1/A2 部分项（见 §2.2 与 §2.4）。

（略——历史快照，请勿引用其状态作当前事实）

---

## 1. 项目主要功能全景

### 1.1 架构分层（自底向上）

```
┌─ 服务面：HTTP REST(42 端点) / WebSocket JSON-RPC / MCP stdio(12 工具)
├─ 门面层：UnifiedQuoteAPI(46 方法·四路由·熔断) / ApiResponse / 三兼容门面
├─ 数据源层：TDX 主站(传输+协议) → Web 45 源(17 模块) → vipdoc 本地 → golden 回放 → 合成
├─ 协议层：85 命令账本 → 三级分派(L1 精确 61 解析器 → L2 启发 → L3 透传)
├─ 传输层：4 槽连接池 / RLock 租约 / 限流令牌桶 / 测速 / 主站嗅探
├─ 领域层：symbol 单一事实源 / adjust 复权 / calendar 交易日历
├─ 流式层：QuoteStream + AsyncQuoteStream（轮询+diff）
├─ 存储层：DataFrame / Parquet / DuckDB 三 Sink（原子写）
└─ 支撑层：config(6 源合并) / errors(E1-E9) / observability / feedback / security / i18n
```

### 1.2 功能矩阵

| 子系统 | 核心能力 | 规模 | 状态 |
|---|---|---|---|
| 协议编解码 | 帧解析 / LEB128 / tdx_float / zlib strict / count 钳制 | 85 命令 / 61 L1 解析器 | ✅ 稳定 |
| 传输 | 连接池+心跳+退避 / 时段限流 / 主站测速 | 4 槽 / 7 模块 | ✅ 稳定 |
| 客户端 | 同步+异步 / 并发批量 / 5 族客户端工厂 | 2 主类+5 族 | ✅ 稳定 |
| 门面 | 四路由(tdx/web/local/auto) / 熔断 / adjust 守卫 | 46 方法 | ✅ 稳定 |
| Web 数据源 | 行情/K线/资金/板块/龙虎/热榜/问财 等 | 45 源类 / 17 模块 | ✅ 稳定 |
| 流式 | 轮询+diff 订阅 / 裸码归一 / 线程兜底 | 2 Stream | ✅（engine 未接线⚠️） |
| 服务面 | HTTP / WS / MCP 三服务 | 42+7+12 接口 | ✅（socket 冒烟缺⚠️） |
| 存储/配置 | 三 Sink 原子写 / 6 源配置合并 / 凭据三级存储 | 3+2+1 | ✅ 稳定 |
| 工具链 | capture 合规采集 / codegen / spec_audit / golden_audit / check_originality / probe | 6+ 工具 | ✅ 稳定 |
| 可观测 | Metrics 注册表 + Prometheus/StatsD/OTLP 导出 | 3 导出器 | ✅ 稳定 |

### 1.3 质量基线现状（实测）

- 测试：76 文件 / **1100+ 用例全绿**（修复波后含 294 新增回归）
- Golden：530 个真实 payload 三旗标门禁（markets + kline-categories 0,4,9 + payloads）
- 对抗矩阵：9 恶意 payload × 85 命令，原生逃逸 = 0
- 可达性：孤儿模块 = 0（strict）
- 缝钉：8 个跨域契约缝钉 + 4 对抗金丝雀
- CI：9 jobs（lint / type-check / test×4 版本 / originality / spec-coverage / bridges / golden / adversarial / reachability）

---

## 2. 潜在问题清单

### 2.1 系统级问题（发布工程 / 门禁完整性）

| # | 严重度 | 问题 | 证据 | 影响 |
|---|---|---|---|---|
| S1 | — | **↩ 复核反驳（原判「CI 缺缝钉/压测 job」不成立）**：CI test job 执行 `pytest tests/` **全量**（ci.yml:60），seams 缝钉、adversarial 矩阵、slow 压测（全库仅 1 处 `@pytest.mark.slow`，tests/transport/test_pool_concurrency.py:47，且 addopts 无 `-m "not slow"` 过滤）均被执行；adversarial 另有专门 job 强化。本地 gates 与 CI 的实际差异为零 | 复核记录：pytest addopts=pyproject:88 | 无影响——记录以防重复误判 |
| S2 | **中** | **CI 不验证 Windows**：项目声明 `OS Independent`（pyproject.toml:22），但 9 个 job 全部 `ubuntu-latest`。Windows 编码问题已有两次真实案例（`audit_reachability.py` GBK `✓` 崩溃、build 隔离环境 GBK 解码崩溃） | `ci.yml:17-132` 全 ubuntu | Windows 专属回归（编码/路径/事件循环策略）上线前不可见 |
| S3 | 低 | **format 门禁不含 scripts/**：`ruff check` 含 scripts/ 但 `ruff format --check` 只查 `tstdx/ tests/`（ci.yml:26 vs :28；Makefile:139 同构） | `ci.yml:28` | scripts/ 格式漂移不被 CI 捕获 |
| S4 | **中** | **版本落笔悬置**：pyproject 仍是 `1.1.0`，而 CHANGELOG 已写完整 1.2.0 章节、修复波已全量合入 | `pyproject.toml:7` vs `CHANGELOG.md` | 发布工程断链：构建脚本产出 1.1.0 包但内容已是 1.2.0 |
| S5 | **高** | **mypy 基线红（修复波类型债爆发）**：`mypy tstdx/` 报 **98 错误/26 文件**（attr-defined×26、index×15、arg-type×12、union-attr×11、assignment×9、dict-item×7、override×7、return-value×5、其他×9）。最大根因：`get_client() -> TdxClient`（client.py:1121）注解过窄——实际按 kind 返回 5 种客户端，cli.py 的族方法调用全部误报为 attr-defined。**CI type-check job 当前必然失败**（ci.yml:30-41 基线即红） | 实测 2026-09-03：`mypy tstdx/ --ignore-missing-imports` exit 1 | CI 类型门禁失效；类型注解与实现漂移持续扩大 |
| S6 | 低 | **文档漂移小项**：`DESIGN.md:2866`"兼容层（mootdx 等）"注释指向已删除的垫片；`Makefile [17] "32 接口"`（实际 42）、`[12] "target 500"`（实际 530） | DESIGN.md:2866 / Makefile | 轻微，影响新读者 |

### 2.2 架构悬置项（第三态决断）

| # | 严重度 | 问题 | 证据 | 影响 |
|---|---|---|---|---|
| A1 | **中** | **streaming.engine 未接线**：StreamEngine/QuoteChannel 已实现但零生产接线（G2 决议维持轮询语义）；E6 四类错误（SubscriptionError/GapUnfilledError/BackpressureOverflow）库内零抛出点，作为占位保留 | `errors.py:363-368` 处置注释、`streaming/engine.py` | **✅ M1 接线落地（v1.4.0 用户决断）**：QuoteStream/AsyncQuoteStream 内核改由 engine 组件构成（DeltaMerger/BackpressureQueue/ReconnectPolicy），E6 三类错误具备真实抛点（不可解析符号 / 连续 3 轮缺失 / 溢出丢最旧） |
| A2 | **中** | **native 加速承诺实质放弃但未决断**：`tstdx_native/` 扩展源码（空壳）已删，`tstdx/native.py` 加载层**永远走纯 Python 回退**；回退语义有测试守护（test_native.py / test_native_semantics.py），但"原生加速"已无实现载体，native.py 成为永久 fallback 层（归档文档所记 native.yml workflow 实际不存在，已核实 workflows 仅 ci.yml + wheels.yml） | `tstdx/native.py`（ImportError → fallback）、`.github/workflows/`（无 native job） | 死承诺面：文档/类型/测试仍在为不存在的能力付费 |
| A3 | 低 | **性能无基线回归**：I6 性能基线套件未建（benches 已删，基线数据仅存归档文档 E10：异动池 33.7 万 rec/s） | docs/archive/OPTIMIZATION_PLAN.md:103 | 性能退化不可检测 |

### 2.3 真机定标悬案（已知 ⚠️，非新发现）

> 以下为"帧结构合法但内容错误"风险类，**必须真机采集定标**（I1 真机定标周），代码侧已用 IntegrityViolation fatal 语义守住降级红线：

1. 0x0530 反转族语义（P1d 已 golden 裁决，但真机复核待做）
2. 0x0547 北交所 K 线类别映射
3. 0xFC5 记录长 15B 变体
4. std7727 / mac 族 diff base 确认
5. 指数 K 线 4 字节尾多类别覆盖

### 2.4 域级深审发现（4 路并行代码审查）

> 审查范围：协议层 / 传输·客户端·流式 / Web·门面 / 服务面·可观测·存储。
> 产出 60 项发现（高 6 / 中 33 / 低 21）+ 61 项复核反驳（凭文件:行号证据）。
> 以下为高危项与本轮（v1.2.0 L1 首波）修复状态；中级项按域分批并入后续小版本。

| # | 域 | 发现 | 等级 | 状态 |
|---|---|---|---|---|
| W#1 | 门面 | `facade/api.py` local 路由 4 重缺陷：str.market AttributeError / 幽灵 `resolve_path` / `DayBarReader(root=)` TypeError / count+start 被忽略且违反 list[Bar] 契约 | 高 | ✅ 已修复（重写 `_local()`，`tests/facade/test_bars_local_route.py` 5 用例） |
| W#2 | 门面 | sources 降级链 tdx→web 时 klines 未传 `adjust=""`，原始价静默切为前复权（除权日价差可达数十元） | 高 | ✅ 已修复（显式 `adjust=""` + `test_sources.py::TestKlineAdjustConsistency`） |
| T#1 | 传输 | `BackpressureQueue.put` 持锁调 `on_drop` → 回调内 `qsize()` 抢同一把不可重入 Lock，队列首次溢出即 100% 死锁 | 高 | ✅ 已修复（锁外回调 + `tests/streaming/test_engine_backpressure.py`） |
| T#2 | 传输 | async `read_frame` 无锁（公开 API 原子性缺失）；`iter_frames` 读循环在锁外（帧间隙心跳/请求可插帧）；心跳对 iter_frames 流量失明（last_used 不更新）；消费方弃读后残留帧污染连接 | 高 | ✅ 已修复（`_read_frame_locked` 拆分收口 4 处持锁调用点；sync/async 整体租约 + 逐帧 touch last_used + GeneratorExit 弃连） |
| S#1 | 服务面 | HTTP 8 端点签名断裂：`/file_download` 缺 symbol、`/download` 调不存在的 `TdxClient.download`、goods/ex/mac 5 端点透传 `market=`（族客户端无此形参）→ 500；测试 fake 反写端点签名掩盖断裂 | 高 | ✅ 已修复（`_mkt_sym` 合成前缀 symbol + `/download` 委托 file_download；fake 对齐真实签名 + 契约锁测试） |
| P#1 | 协议 | `ProtocolSniffer.archive` 零调用点——三处文档承诺的「未知/低置信样本自动归档」从未发生 | 高 | ✅ 已修复（`_generic_or_raw` 接线，旁路 try/except；`tests/protocol/test_sniffer_loop.py` 7 用例） |
| P#8 | 协议 | sniffer 归档秒级时间戳直写，同秒样本静默覆盖；DRAFT.yaml 并发竞写 | 高 | ✅ 已修复（毫秒+序号探测 + 模块锁 + `open("x")` 原子创建） |
| S7 | 服务面 | mypy 基线红：98 错误 / 26 文件（`get_client() -> TdxClient` 注解过窄为最大模式；cli 26 / metrics 9 / registry 8 / capture 7） | 高 | ✅ 已清零（L1b：@overload 体系 bars/quotes/quotes_concurrent/get_client、Self 返回类型、TypeVar 泛型 register、Literal 收窄；`mypy tstdx/` → Success 100 files） |

中级发现（33 项，全部处理完毕；✅ = 已修复，ⓘ = 有据裁决保留现行为）：✅ request_multi `merged.payload = b""` 清空矛盾（pool.py——保留部分数据对齐 async，前缀有效不再丢弃）；✅ async 限流 3 缺口（async_.py——`_acquire_rate()` 收口：request 忽略二次 try_acquire 结果 + request_multi/iter_frames 完全绕过）；✅ QuoteStream.stop 不关 client/pool + 孤儿双跑（stop 关闭 _client；join 超时保留线程引用防 start 双跑）；✅ QuoteChannel.tick 裸码查找（engine.py——裸码/带前缀双向索引与查找）；✅ engine 退避恒 0（poll_delay 按 ReconnectPolicy 指数退避，成功时 0）；✅ quotes_snapshot dispatch 穿透（L3 透传行不再强转 Quote——同步/异步均回退逐只 0x0530）；✅ PushChannel.read 吞异常（断线 warning + `last_error` 属性；数据损坏不再伪装「无帧」）；✅ 4MB 护栏 chunked 绕过（无 Content-Length 且 chunked → 413）；✅ Registry.snapshot Histogram 口径（按序列输出各自 bucket/count/sum，statsd 消费端同步适配）；✅ TaskStore cancel 回写竞态（cancelled 后完成不回写 done）；✅ GET /tasks 响应放大（列表只回摘要，不含 result/error 全文）；✅ config 层间 dict 吞键（merge_config 改用 _deep_merge，与 with_overrides 同语义）；ⓘ statsd 重启假尖峰（首次推送推累计值——F4 测试契约锚定的已知设计，已文档化）；✅ profile hint 绕过阈值（原始 conf < 阈值 60% 时 +0.1 加权不生效，hint 不能救活数据不匹配候选）；✅ web 桶首建者胜（rate 不同即 set_rate 显式更新）；✅ 12 处裸 get/post 绕过 `_check_deprecated`（boards×3/fundflow×2/corporate×2/global×2/history×2/hot_rank×1 全部前置下线检测）；✅ fundflow period 静默回退（未知周期显式 ValueError；bridge._resolve_period 属兼容层有警告——保留）；✅ sina 丢 adjust（sina 源显式拒复权 + facade 复权请求自动路由东财）；✅ 市值单位跳变（腾讯亿→元、新浪万元→元，统一与东财同口径）；✅ SuggestSource 无 urlencode（quote 后拼接）；✅ 东财单候选早退（空结果先换 host 复核，全部主机皆空才定论）；✅ FileDownloadParser 死分支（不可达 uint16 分支清除 + docstring 对齐）；✅ 4 处 price_scale 丢弃（float32 无需折算的死语句清除）；✅ tier/confidence 账实不符（0x0010/0x053E 显式 tier=L2，不再缺省自报 L1 满置信）；✅ golden 回放 zlib.error 裸抛（decode_response_body 统一转 DecompressError）；✅ min_confidence 未实现（显式阈值低于时抛 LowConfidenceParse，默认 0.0 行为不变）；✅ parse_f10_text NUL 截断（decode_gbk 增 strip_nul="tail"，正文中间 NUL 不再截断）；✅ build_request pkg_len 溢出（uint16 超限抛 FramingError 而非 struct.error）；✅ iter_frames 尾部残流静默（随 T#2 已修）；✅ ctx["_meta"] 通道失效（可变对象模式 `ctx["_meta"]["k"]=v`，与 _state 同机制）；✅ Prober archive body_hex 失真（ProbeResult.request_body 记录实际发送字节）；✅ Prober 限速无锁 + TypeError 探测误吞真 bug + 无头鸭子类型误剥 16B + raw 未定义 NameError（prober.py 四合一修复）；✅ Sniffer 秒级时间戳（随 P#8 已修）。
低级发现（21 项，具名 14 项全部处理完毕；✅ = 已修复，ⓘ = 有据裁决保留）：✅ 账本注释漂移（commands.py 36→39 条）；✅ port 映射缺 F10（显式声明 F10→7709，不再靠 .get 回退）；✅ MacNews 截断无告警（warn_ctx 留痕）；ⓘ _looks_like_uint16_date 死代码（零调用点但为 L2 探测候选启发，裁决保留 + 标注）；✅ deflate zlib 包装盲区（zip==unzip 声明未压缩但长度矛盾时尝试解压兜底）；✅ minute_klines 港美 adjust 口径（随 M9 统一路由东财）；✅ golden 回放 glob 丢 market（ids 含 case 层：命令+标的+市场段+时间戳）；✅ KlineNormalizer 文档×100 冲突（对齐解析层内联缩放为 identity，防双重缩放）；✅ credentials list_keys/keyring 遮蔽（docstring 补遮蔽警示 + get_source 诊断路径）；✅ symbol 白名单冲突（000010/000016 移出——深市有同名个股美丽生态/深康佳A，违反白名单自身准入规则；5 位裸码归港为既有设计，文档已注）；✅ reporter 过度脱敏（股票代码脱敏步骤移除——公开市场标识非 PII，脱敏使报告失去诊断价值）；✅ to_csv 非原子（mkstemp + os.replace 对齐 to_parquet）；✅ WS 每连接独立连接池（模板层惰性创建唯一 client，N 连接不再 N 倍资源放大）；✅ /quotes 逗号列表无上限（100 上限 422，quotes/snapshot/auction_snapshot 共用）。未具名余项并入 v1.4.0 批次（原始报告细节已随会话归档，按本表具名清单执行）。

---

## 3. 优化改进方案

### 批次 K —— v1.2.1（patch，1-2 天）：发布工程收口

| 项 | 内容 | 验收 |
|---|---|---|
| K1 | **版本原子落笔**：pyproject `1.1.0→1.2.0` + `tstdx/__init__.py` 同步 + CHANGELOG 日期 | `make build` 产出 1.2.0 包 + 冒烟通过 |
| K2 | ~~CI 补缝钉 job~~ **取消（复核反驳）**：S1 不成立——CI test job 全量 pytest 已覆盖 seams/adversarial/slow | 无需动作 |
| K3 | **CI 补 format 范围**：`ruff format --check` 加 `scripts/` | CI 绿 |
| K4 | **native 面止损**：`tstdx/native.py` docstring 标注"扩展载体已移除，当前恒为纯 Python 回退"（防止新读者误信加速承诺），行为不变 | 文档复核 |
| K5 | **文档漂移清理**：DESIGN.md:2866 注释、Makefile [17]/[12] 数字校正 | grep 复核 |

### 批次 L —— v1.3.0（minor，1-2 周）：域级修复 + 真机定标

| 项 | 内容 | 验收 |
|---|---|---|
| L1 | **域级问题修复**（2.4 节高/中级项，待深审结果定稿清单） | 每项带行号回归测试 |
| L1b | **mypy 基线清零（S7）**：先修模式根因（`get_client` 按 kind 做 `@overload` 字面量重载），再逐文件清零 98 错误（cli 26 → observability 9 → registry 8 → capture 7 → 其余）；完成后 CI mypy job 恢复绿并加 `--warn-unused-ignores` 递减门禁 | `mypy tstdx/` exit 0 |
| L2 | **I1 真机定标周**：收盘后采集 5 悬案族真实样本 → golden 固化 → 消除 ⚠️ 标记 | golden_audit 新增 5 族 payload 三旗标绿 |
| L3 | **CI Windows 矩阵**：test job 增加 `windows-latest`（仅 3.12 单版本控制成本），验证编码/路径面 | Windows job 绿 |
| L4 | ✅ **mypy 递减门禁**：`mypy --warn-unused-ignores` 进 CI（L1b 清零时顺带清掉 12 处失效 ignore，门禁即绿） | CI type-check job 绿 |
| L5 | ✅ **H2 socket 级冒烟**：HTTP 真实 loopback + WS 并发推送 + MCP stdio 往返 各 1 条集成测试（`tests/integration/test_l5_smoke.py`） | 3 用例全绿 |

### 批次 M —— v1.4.0（minor，规划）：架构决断 + 性能

| 项 | 内容 | 决断点 |
|---|---|---|
| M1 | **streaming engine 决断**：要么接线（QuoteChannel 替换 QuoteStream 内核 + E6 错误类兑现抛点），要么删除 engine 并把 E6 占位注释升级为永久决议 | **✅ 已决断（用户拍板）：接线**——QuoteStream/AsyncQuoteStream 内核由 engine 组件构成，E6 兑现抛点，专测 69 例全绿 |
| M1b | **native 决断**：要么用 maturin 重建 `tstdx_native/`（基线数据见归档 E10），要么废弃 `tstdx/native.py`（走 §29 deprecation 策略 + 删除回退面） | **✅ 已决断（用户拍板）：废弃**——v1.4.0 起 import 发 DeprecationWarning，§29 窗口 2 个 minor 后 v1.6.0 删除；窗口期行为不变 |
| M2 | **I6 性能基线**：重建 benches（基线数据在归档 E10），解析热路径 ≥ 基线 80% 为门禁 | bench 进 CI nightly |
| M3 | **I5 可观测深化**：结构化日志面 + OTLP 采样策略 | 按需 |
| M4 | J 批次路线（复权因子在线化 / F10 扩展 / 7727 扩展市场）按 FEATURE_MAP §3 滚动 | — |

### 优先级依赖

```
K1(版本) ──┐
K2/K3(K 收口) ──→ L 批次（L1 依赖 K 的干净基线）
A2(native.yml) ──→ K4
A1(engine) / A3(性能) ──→ M 批次
L2(真机定标) 与 L1/L3/L4/L5 并行
```

---

## 附：证据与验证命令

```bash
make gates                                          # 六步门禁全绿基线
python scripts/build_package.py --smoke             # 一键构建+冒烟（产出当前版本包）
python -m pytest tests/seams tests/adversarial -q   # 缝钉+金丝雀（12 项）
python -m tstdx.tools.golden_audit --gate --require-markets --require-kline-categories 0,4,9 --require-payloads
python scripts/audit_reachability.py --strict
```
