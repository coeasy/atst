# tstdx 主体功能梳理与优化改进计划（v3 路线版）

> **文档性质**：历史路线图快照；当前稳定发布基线为 [v1.0.0](../../releases/v1.0.0.md)。
> **生成日期**：2026-09-02（v1.2.0 修复波进行中快照）
> **数据来源**：本会话实测勘察（LOC/命令/端点/测试计数均为脚本实测，非文档转录）+ 六域修复代理回报
> **文档谱系**：v1 `docs/archive/OPTIMIZATION_PLAN.md`（A-E 批次，已完成）→ v2 `docs/INDUSTRIAL_OPTIMIZATION_PLAN.md`（缺陷审计 F-H 批次，执行中，进度见其附录 D）→ **本文档**（功能地图 + v1.2.0 之后的前进路线 I/J 批次）→ v3 `docs/POTENTIAL_ISSUES_AND_PLAN.md`（修复波后的健康评估 + K/L/M 批次）
> **与 v2 的分工**：v2 回答「哪里坏了、怎么修」；本文回答「项目是什么、修完之后往哪走」；v3 回答「现在还剩什么问题、下一程怎么排」。

---

## §1 主体功能地图（实测）

### 1.1 规模快照

| 维度 | 实测值 | 维度 | 实测值 |
|---|---|---|---|
| 总代码量 | ≈37.7k 行（tstdx/ 20 子包+顶层） | HTTP 端点 | 42（V 域重构中） |
| 协议命令账本 | 85 命令 / 5 协议族 | CLI 子命令 | 19（含新增 probe/feedback） |
| L1 精确解析器 | 61 注册项 | 门面公开方法 | 46（UnifiedQuoteAPI） |
| Web 源 | 17 模块 / 45 Source 类 | 测试函数 | ≈997（61 文件，修复波后 >1100） |
| golden 语料 | 530 payload（real/synthetic 分级） | CI 门禁 job | 9 |

最大子包 **web（8.8k）** > protocol（5.0k）> tools（3.9k）> transport（2.8k）> facade（2.1k）> observability/integration（1.9k）。

### 1.2 九层架构与职责

```
 L8 工具链     tools/{capture,codegen,spec_audit,golden_audit,golden_expand,
               check_originality} ── 协议研究闭环 + 洁净室/语料门禁（进程入口）
 L7 服务面     integration/{http(42端点),ws,mcp} + observability(指标三导出器)
               + feedback(三件套) ── 对外部署形态
 L6 存储离线   reader(vipdoc 本地文件) + profile(帧/文件双探测器) + sinks
               (DataFrame/Parquet/DuckDB) + i18n ── 无网络兜底与分析出口
 L5 多源降级   sources(5 级路由 TDX→Web→本地→缓存→合成) + web(17 模块 45 源)
 L4 门面       facade: UnifiedQuoteAPI(46 方法,auto/tdx/web/local 路由+熔断)
               + async 门面(实例桥接) + response(ApiResponse) + 三兼容门面
 L3 域模型     domain: symbol(单一事实源) / models / calendar / adjust(复权)
 L2 客户端     client.py: TdxClient/AsyncTdxClient + get_client 工厂
               (std/goods/ex/mac/f10) + quotes_concurrent
 L1 传输       transport: base(sync RLock 租约) / async_ / pool(4 槽+心跳+退避)
               / ratelimit(时段档) / speedtest / hosts / sniff
 L0 协议       protocol: commands(85 账本) / registry(L1 精确→L2 启发→L3 原始
               三层 dispatch, 边界异常收口) / parsers(6 族 61 注册) / generic /
               handshake / prober + codec(framing/primitive/zlib)
 横切          errors(E1-E8 分类树+RetryAdvice) / config(6 源合并+toml) /
               deprecation / security(三级凭据)
```

### 1.3 主干数据流（四链）

1. **请求链**：`facade/api → client → pool.acquire(4 槽租约) → transport.base.request
   （锁内：构帧→发送→recv→zlib→seq 校验）→ registry.dispatch(L1/L2/L3) → rows →
   domain 归一 → ApiResponse/.df`
2. **降级链**：`sources.DataSourceRouter：TDX(协议) → Web(45 源按能力匹配) →
   vipdoc(本地文件) → 缓存 → 合成`，每级带错误语义与可用性熔断（A 域新增路由冷却）。
3. **流式链**：`streaming.QuoteStream：订阅表(裸码归一) → 轮询 0x0530 →
   diff → 回调`；异步镜像同构（S 域修复后线程/协程均具备顶层兜底不死）。
4. **协议研究闭环**（工具链）：`capture(合规门禁) → PROTOCOL_SPEC YAML →
   codegen 骨架 → @register_parser → golden_audit 三旗标 → spec_audit
   双向漂移检查`——v2 审计后已修：expect 篡改拦截、spec_id 校验、tzdata 标记。

### 1.4 能力面矩阵（用户视角「能拿到什么」）

| 域 | 能力 | 通道 |
|---|---|---|
| A 股行情 | 报价/K线(12 类别)/分时/逐笔/快照 80 只批量/财务/除权/F10/代码表 | TDX 0x053x 族 + Web 双通路 |
| 板块/资金 | 行业/概念板块、DDE、资金流、龙虎榜、异动、人气榜、涨停池、北向 | Web 东财/新浪系 + MAC 0x1xxx |
| 扩展市场 | 期货/期权/外汇/港股/美股（7727 族）+ 商品（goods 族） | TDX ex/goods 客户端 |
| 指数 | 指数列表/K线（4 字节尾涨跌家数，v1.2.0 起 ctx 实装）/MAC 统一 | 0x052D index=True |
| 离线 | vipdoc 日/分钟/板块/财务文件直读 + 帧档案探测 | reader/profile |
| 复权 | 前/后/密 四因子除权复权引擎 | domain.adjust（E4：v1.2.0 已接线 bars(adjust=) 走 Python 路径） |
| 日历/时区 | 交易日历（懒加载+节假日）、时段判定、盘门禁 | domain.calendar |
| 服务部署 | HTTP 42 端点(白名单派发)、WS 推送(轮询循环实装)、MCP 10+ 工具 | integration |
| 可观测 | 指标注册表→Prometheus pull/push、StatsD、OTLP；transport/client 埋点 | observability |
| 出口 | DataFrame/Parquet/DuckDB + CSV 原子写 | sinks |

### 1.5 质量基建（v1.2.0 新增，长期资产）

`make gates` 六步（lint→format→全量→**对抗矩阵**→**golden 三旗标**→**可达性**）+ CI 9 job
+ `tests/seams/` 跨域契约缝钉 ×8 + 对抗 canary 自证 + 10.4k 请求并发压测 +
处置白名单（孤儿必须登记理由）。

---

## §2 v1.2.0 修复波状态快照（2026-09-02 14:40）

| 域 | 状态 | 核心交付 |
|---|---|---|
| F0 编排者 | ✅ | async 门面实例桥接 / HttpxClient.post / config env 三修 / credentials 三缺陷 |
| S 流式客户端 | ✅ 已验证 | C1 裸码归一 + C3 错误局部化 + C4 线程兜底 + P1a index ctx 端到端 + push 三连 + 32 回归；**新发现修复** async `Event.wait(timeout)` 必崩 |
| A 门面 | ✅ 已验证 | W11 route_errors+熔断 / W12 逐方法分类（双通路×2+显式拒绝×15）/ W13 口径根因修复（**契约变更**：bars 默认原始价）+ 53 回归 |
| P 协议 | ✅ 已验证 | P1b 边界收口（**实测修正：逃逸面 610 组合 0 穿透，审计「7 类」为低估+旧形态**）+ T3 钳制 + P1d golden 逐字节裁决 + P1e 15B 对齐 + 50 回归 |
| T 传输 | ✅ 已验证 | C2 连接级 RLock 租约 / C5+C6 async 锁+magic 收口 / T4 部分数据保留 / 10.4k 并发 0 串线 / 40+ 回归；另实锤 3.10/3.11 asyncio 超时跨版本缺陷 |
| W Web 源 | ✅ 已验证 | W1-W14 全组（327 绿+62 新增；复核反驳 6 条；含 hk/us 1min 误落日线数据缺陷修复） |
| V 服务可观测 | ✅ 已验证 | V1-V7 全组（276 绿+66 新增；42 端点实测清点；真回环 socket 端到端；复核反驳 3 条含 ws SHA-1 无调用点） |
| G 存储配置 | 🔄 **唯一在途** | probe/feedback CLI 已接线（可达性孤儿闭合在望）；symbol 白名单已落地三方交叉验证 |

**审计执行期被修正/新增的发现**（收口时并入 v2 附录 B 追加节）：
- ↩ `market._option_symbol`、goods `_tdx_gbk`、负 count、`_HEURISTIC_MAX_BARS`、mcp route/force 与 `_write_loop`、ws SHA-1 调用点、`build_security_quote_body`（0x0530「真缺陷」，编排者实测裁决：真实链走 `quote_request_market` golden 逐字节自洽）——共 8+ 条为旧形态/幻影引用/在途快照误读，**均未虚构修复**。
- ⬆ 逃逸面真实规模 610 组合（9 payload×61 注册命令），审计「7 类」为低估。

**契约变更汇总（进 release notes）**：env `"1"/"0"`→int；bars 默认口径原始价；`_collect` 内部参数；credentials 互斥写；prober 盘中默认不抛；zlib 默认 strict；async 门面构造/生命周期。

---

## §3 优化改进计划（v1.2.0 → v1.3+ 路线）

### 批次 H（承接 v2，质量债，~1 月）
| # | 项 | 出口标准 |
|---|---|---|
| H1 | mypy 清 `type: ignore`（web 域 100+/124 优先） | 每 PR ignore 数单调不增（CI 计数门禁） |
| H2 | 服务面 socket 级集成冒烟（HTTP/WS/MCP 真实 loopback）+ WS 并发写压测 | 三服务面各 ≥5 端到端用例进 gates |
| H3 | 存储原子写统一（tmp+rename 收敛为单一 helper，sinks/hosts/speedtest 共用） | 无裸 `open(w)` 落盘点 |
| H4 | check_originality tokenize 化 + `--fix` 许可守卫 | 归一化后指纹可复现 |
| H5 | 静默降级全面 warnings 化（declared vs parsed lint、单位标注 lint） | 每条降级链有可观测痕迹 |
| H6 | P2 长尾残余（以 v2 附录 D 收口时未勾项为准） | 清零或显式豁免登记 |

### 批次 I（功能纵深，新提案）
- **I1 真机定标周（前置优先级最高）**：一次合规非交易时段采集专列，裁决全部 ⚠️ 悬案——0x0530 反转族建表、0x0547 bj 市场字节、0FC5 记录布局（15B vs 74B 矛盾）、0x1300 差分基准、std7727 base、指数 4 字节尾多类别覆盖。golden 530→≥700，**P 域 0x0530 新发现一并裁决**。⚠️ 项清零是「帧合法但内容错误」风险的根治手段。
- **I2 复权能力完整化（E2+E4 合并）**：除权因子库（本地缓存+capital_changes 供给）+ 分红再投口径 + 与 reader 历史段拼接回归。
- **I3 流式能力裁决落地（M1 决断已执行，v1.4.0 用户拍板「接线」）**：`QuoteStream`/`AsyncQuoteStream` 内核改由 `StreamEngine` 组件构成（DeltaMerger/BackpressureQueue/ReconnectPolicy），E6 错误类（`GapUnfilledError` 等）兑现真实抛点；断线补数仍不自动执行（0x0530 仅回当前快照，缺口以 `GapUnfilledError` 可观测）。
- **I4 native 接入决策**：三场景基准（10k 串行请求 / 全语料解析 / 300 只快照）Python vs native 对拍，采用、挂开关或删包（-500 行）出数据结论。
- **I5 可观测开箱**：config `observability.exporter` 段自动装配（消灭「配了不用」）、`tstdx serve --metrics`、随包 Grafana dashboard JSON 样例。
- **I6 性能基线回归**：轻基准套件（ASV 可选）入 CI 非门禁跟踪趋势——1.1.0→1.2.0 的锁/钳制开销需量化可见。

### 批次 J（生态与交付）
- **J1 文档站**：mkdocs + API 参考自动生成 + cookbook 补 4 篇（复权/服务面部署/可观测装配/协议贡献工作流——把 §1.3 研究闭环写成人人能走的路）。
- **J2 发布工程**：1.2.0 release（tag+sdist/wheel+GH release notes 从 CHANGELOG 生成）；yank 流程与版本策略文档化；docker 镜像 CI 化。
- **J3 依赖面治理**：pandas/pyarrow/duckdb 可选矩阵回归（无 pandas 时 `.df` 降级行为）、最低依赖版本策略与 CI 版本矩阵对齐（当前 3.10-3.13 已覆盖）。
- **J4 社区协议贡献**：spec PR 模板 + codegen CI（提交 YAML → 自动跑 L2 骨架与 spec_audit）、good-first-issue 标定。

### 里程碑与依赖
```
v1.2.0 发布（修复波收口）─→ H1/H2/H5（1 月）─→ I1 真机定标周 ─┬→ I2 复权完整化
                                    └→ H3/H4/H6、I4 native、I3 流式 ─→ v1.3.0
                                    └→ I5/I6、J1/J2（并行）→ v1.3 文档/发布同步
```
关键依赖：**I1 先行**（六个 ⚠️ 悬案挡着 I2/I3 的口径正确性与一切「数据正确」声明）；J2 发布工程应在 gates 稳定后立即做（当前正是窗口）。

---

## §4 门禁演进承诺

每批次出口必须保持：`make gates` 六步绿 + 缝钉全绿 + 附录 D 白名单不新增无决议条目；新增公共 API 必附「三件套」（单测 + cookbook 段落 + docs/api 表行），破坏性变更必上 CHANGELOG「Changed」+ DeprecationWarning ≥1 版本。
