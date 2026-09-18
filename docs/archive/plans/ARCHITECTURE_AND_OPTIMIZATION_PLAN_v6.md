# tstdx 架构综述与优化改进方案 v6

> **范围**：项目全量（25 个顶层模块 / 116 个 `.py` / client.py 1823 行 / web 层 10516 行 / 107 个测试文件 / 530 组 golden 样本）。
> **版本轴**：v1（协议正确性）→ v2（功能扩展）→ v3（能力扩展）→ v4（文档一致性）→ v5（分页与完整性）→ **v6（本文件：架构全景 + 工程治理）**。
> **审计方法**：4 路并行只读审计（核心协议栈 / Web 层 / 横向能力层 / 工程治理）+ 人工逐条复核，复核推翻 2 条误判（见附录 A）。
> **日期**：2026-09-05

---

## 0. 总体结论

| 维度 | 评价 |
|---|---|
| **协议覆盖** | 优秀。5 个协议族 85 条命令、约 80 个解析器、三级兜底派发（L1 精确 / L2 通用 / L3 原始透传），永不丢包 |
| **数据契约** | 良好。`models.py` 统一「价格=元 / 成交量=股 / 成交额=元」，`as_format` 三态输出 |
| **数据源广度** | 优秀。TDX 主站 + 7 个上游家族 30 个 Web 源 + 本地 vipdoc + golden 回放 |
| **工程治理** | 良好。CI 含 lint/typecheck/multi-version/originality/spec-coverage/bridges/golden-gate/adversarial/benchmark |
| **主要短板** | ① 同步/异步双端全量复制（漂移风险）② 两套降级引擎并存 ③ Web 层容错旁路与静默截断 ④ 无覆盖率门禁与规格漂移门禁 |

**一句话**：协议正确性与数据源广度已达工业级，短板集中在**代码结构的重复度**与**质量门禁的强制性**，而非功能缺失。

---

## 1. 全景架构

```
                         ┌─────────────────────────────────────────────┐
                         │              调用入口层                        │
                         │  cli.py(28 子命令)  integration/              │
                         │  (FastAPI 43 端点 / WS / MCP 23 工具)         │
                         └───────────────┬─────────────────────────────┘
                                         │
        ┌────────────────────────────────┼──────────────────────────────┐
        ▼                                ▼                              ▼
┌───────────────────┐      ┌──────────────────────────┐   ┌────────────────────────┐
│  facade/ 门面层    │      │  sources/DataSourceRouter │   │ streaming/ 实时层       │
│ UnifiedQuoteAPI   │      │  5 级降级：                │   │ QuoteStream(轮询+diff) │
│ 路由 local/tdx/web│      │  tdx→web→reader→          │   │ PushChannel(0x0547)    │
│ + 熔断(3次/30s)   │      │  cache→synthetic          │   │ 注：PushChannel 未接线  │
└─────────┬─────────┘      └──────────┬───────────────┘   └───────────┬────────────┘
          │                          │                                │
          ▼                          ▼                                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                        client.py 客户端层（1823 行）                          │
│  TdxClient(33 方法) + GoodsClient / MacClient / F10Client                    │
│  AsyncTdxClient + 4 个 Async 子类   ← 同步/异步全量镜像复制                    │
│  统一：请求体构造 → dispatch 派发 → _row_to_bar → as_format 输出              │
└───────────────┬──────────────────────────────┬──────────────────────────────┘
                │                              │
                ▼                              ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────────┐
│  protocol/ 协议层                  │  │  web/ Web 层（22 文件 / 10516 行）    │
│  commands.py  5 族 85 命令账本     │  │  sources.py  KNOWN_SOURCES(30 源)    │
│  registry.py  @register_parser    │  │  base.py  HttpClient/TokenBucket/    │
│           三级派发 dispatch()      │  │           RateLimiter/BaseWebSource  │
│  parsers/  ~80 解析器(530 golden) │  │  adapters*/history/corporate/boards  │
│  codec/     帧编解码                │  │  facade.py WebQuoteSession(45 方法)  │
└───────────────┬──────────────────┘  └───────────────┬──────────────────────┘
                ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────────┐
│  transport/ 传输层                 │  │  normalize.py VolumeNormalizer        │
│  pool.py  hosts×4槽 / 轮换 / 重试 │  │  （量纲收敛：手→股 / 万元→元）          │
│  base.py  字节收发 / seq 校验      │  └──────────────────────────────────────┘
│  心跳 30s / 空闲 300s / 无熔断     │
└──────────────────────────────────┘

横向支撑：domain/(模型契约) reader/(vipdoc) sink/sinks/(落盘) observability/(仅 metrics)
          config/(6 源合并) security/(凭据未接线) i18n/(仅编码探测) trade/(纸面模拟器)
          tools/(10 个开发工具) native.py(原生加速已废)
```

### 1.1 模块矩阵

| 层 | 模块 | .py | 职责 | 关键类 |
|---|---|---|---|---|
| 入口 | `cli.py` | 1 | 28 子命令 CLI | `build_parser` / `main` |
| 入口 | `integration/` | 4 | FastAPI 43 端点 / WS JSON-RPC / MCP 23 工具 | `create_app` |
| 门面 | `facade/` | 7 | 三通路统一 API、异步桥接、统一响应 | `UnifiedQuoteAPI` / `ApiResponse` |
| 降级 | `sources/` | 1 | 5 级数据源降级路由 | `DataSourceRouter` |
| 实时 | `streaming/` | 3 | 轮询增量引擎、0x0547 推送通道 | `QuoteStream` / `PushChannel` |
| 客户端 | `client.py` | 1 | 8 个客户端类，协议命令封装 | `TdxClient` / `AsyncTdxClient` |
| 协议 | `protocol/` | 13 | 命令账本、解析器注册、三级派发 | `register_parser` / `dispatch` |
| 传输 | `transport/` | 8 | 连接池、槽位、轮换、重试、心跳 | `ConnectionPool` |
| Web | `web/` | 22 | 7 家族 30 源 HTTP 数据源 | `BaseWebSource` / `WebQuoteSession` |
| 领域 | `domain/` | 6 | 数据契约、符号解析、日历 | `Bar` / `Quote` / `Symbol` |
| 落盘 | `reader/` `sink/` `sinks/` | 6 | 本地 vipdoc 读、csv/parquet/duckdb 写 | `LocalDaySink` |
| 交易 | `trade/` | 7 | 交易帧编解码 + 内存模拟器 | `TradeClient` / `SimTransport` |
| 工具 | `tools/` | 10 | 样本采集、spec codegen、审计 | `golden_audit` / `spec_audit` |
| 支撑 | `observability/` `config/` `security/` `i18n/` `feedback/` `profile/` | 16 | 指标、配置、凭据、编码、反馈 | `Metrics` / `load_config` |

### 1.2 协议族与命令账本

| 协议族 | 命令数 | 说明 |
|---|---|---|
| STANDARD (7709) | 39 | 行情 / K 线 / 分时 / 代码表 / 财务 / 文件下载 |
| EXTENDED (7709 扩展) | 17 | 板块、量价分布、资金流向等 |
| MAC (7727) | 16 | 期货品种映射 |
| GOODS (7711) | 11 | 商品行情 |
| F10 | 2 | 资料目录与文件 |
| **合计** | **85** | 另有 TRADE 族（仅帧定义，未实盘） |

**派发三级兜底**（`protocol/registry.py`）：`@register_parser(msg_id, family)` 写入 `PARSERS[(family, msg_id)]` → `dispatch()` 命中走 L1 精确解析 → `LowConfidenceParse` 或非致命 `TdxError` 走 L2 `parse_generic` → 仍不足则 L3 原始透传。**任何命令都不丢包**，调用方经 `ParseResult.tier/confidence` 可感知降级（但 `client.request()` 目前丢弃该元数据，见 P0-3）。

---

## 2. 核心实现细节

### 2.1 连接池（`transport/pool.py`）

| 维度 | 现状 |
|---|---|
| 槽位模型 | `hosts × slots_per_host(默认 4)`，每槽持一条 `TcpConnection` |
| 轮换 | `_ordered_slots`：轮询起点 + 按 `host.score` 稳定排序；`_rotate_away` 换主站 |
| 重试 | `max_attempts = max(max_retries+1, 去重主机数)`，指数退避 × 抖动 |
| 超时 | `timeout=3.0` / `connect_timeout=2.0` / `heartbeat=30s` / `idle_timeout=300s` |
| 失败处理 | `_mark_failure` 累加 `host.failures` / `biz_failures`，丢弃连接并降级 score |
| 多帧 | `request_multi` 同连接拼帧（首帧必须与后续帧同连接，故固定取首槽） |
| 热更新 | `update_hosts` 测速后热替换，复用既有槽位 |

**关键观察**：池有**失败降级与轮询回避**，但**无熔断状态机**——`biz_failures` 被统计却不用于开路，故障主站在 score 尚未归零前仍会被反复探测（每次 3s 超时）。

### 2.2 数据契约（`domain/models.py`）

- 全局单位：**价格 = 元(float)、成交量 = 股(int)、成交额 = 元(float)**
- 核心模型：`Bar`（OHLCV + amount + extra）、`Quote`（含 `bid[]`/`ask[]` 五档 `Level`）、`Tick`、`MinutePoint`、`CapitalChange`、`AuctionSnapshot`
- 价格精度：`price_scale=100` 由调用方经 `ctx` 传入解析器（`client.py:581`）——**隐式契约**，漏传即精度错误
- 输出三态：`dict` / `tuple` / `dataframe`，统一经 `to_dicts/to_tuples/to_dataframe`

### 2.3 Web 层（30 源 / 7 上游家族）

| 上游 | 域名示例 | 覆盖数据 |
|---|---|---|
| Sina | hq.sinajs.cn / vip.stock.finance / quotes.sina.cn | 行情、全市场分页、港股、历史 K 线、板块、新闻、联想、资金流 |
| Tencent | qt.gtimg.cn / ifzq.gtimg.cn / proxy.finance.qq.com | 行情、日 K、分钟 K、分时、逐笔、外盘、大盘、港股、美股 |
| Eastmoney | push2 / push2his / push2ex / datacenter-web / fund / emappdata / kamt | 行情、K 线、资金流、板块、龙虎榜、基本面、指数成分、人气、异动 |
| Baidu | pae.baidu.com | K 线、分时、逐笔、快照 |
| Wencai | iwencai.com | 自然语言选股 |
| JSL | jisilu.cn | 可转债、ETF |
| BOC | bankofchina.com | 外汇牌价 |

**量纲口径差异**（`sources.py` 声明，`normalize.py` 收敛）：Sina 量=股；Tencent 量=手、额=万元；Eastmoney 价=×100 整数、量=手。`VolumeNormalizer` 仅对 sina/tencent/eastmoney/rank/market_stat 做真实缩放，其余 25+ 源为 identity（解析层已内联缩放）——**三处维护**（`SourceSpec.scales` / `normalize.py` / 解析层内联），存在漂移风险。

**限速与重试**：`TokenBucket` + 按 `source_name` 建桶 + **进程级 `shared_bucket` 跨实例共享配额**；退避 `0.5×2ⁿ×抖动`，封顶 8s，重试上限 5，acquire 超时 30s 防死等；403/429/5xx 可重试，400/404 确定性错误不重试；`StdlibPooledClient.MAX_POOL=16`；东财按 host 池 failover 并写 TTL=600s 黑名单。

**接口下线判定**：连续传输失败 **20** 次或解析失败 **40** 次 → `SourceDeprecated`，成功后双桶清零。

### 2.4 两套降级引擎并存（重要架构观察）

| | `facade.UnifiedQuoteAPI` | `sources.DataSourceRouter` |
|---|---|---|
| 路由集合 | `local` / `tdx` / `web` | `tdx` / `web` / `reader` / `cache` / `synthetic` |
| 熔断 | 有（连败 ≥3 进 30s 冷却） | 无 |
| 口径守卫 | 有（拒绝非法路由组合） | 无 |
| 调用入口 | CLI 在线默认路径、Web/复权类 | 仅 CLI `--source router` |

两者语义重叠（facade 的 `local` ≈ router 的 `reader`），**命名与口径不一致**，是维护双份降级逻辑的隐性成本。

### 2.5 集成与可观测

- **HTTP**：`create_app` 共 **43 端点**，含 `/query` 白名单 `SAFE_CLIENT_METHODS`、body/符号数护栏、`TaskStore`；全部为同步 `def`（FastAPI 自动跑线程池）以便离线测试
- **可观测**：**仅 metrics 落地**（`Metrics` 单例 + Counter/Gauge/Histogram/Summary + prometheus/statsd/otel 三导出器）；**tracing 是 stub**——`ObservabilityConfig.tracing.enabled` 默认 `False`，全库无 `Tracer` 实现；logging 仅标准库
- **配置**：`load_config` 六源合并 + strict 校验，但**无热更新**（无文件监视，运行期改配置需重新调用）
- **交易**：`trade/` 实现了 login/query/send_order/cancel 帧编解码与 `SimTransport` 内存账本，`SocketTransport.connect` **直接抛 `TradingUnavailable`**（`trade/client.py:221`）——纯纸面/模拟器，与行情链路完全隔离（不 import `client`/`facade`），帧布局为洁净室推断

### 2.6 工程治理

| 维度 | 现状 |
|---|---|
| 打包 | hatchling，**零硬依赖**，全部 optional extras（`config`/`dataframe`/`parquet`/`duckdb`/`web`/`tools`/`metrics`/`server`/`mcp`/`all`） |
| Python | `>=3.10`，classifiers 3.10–3.13；主包纯 Python，编译加速由独立 Rust `tstdx_native` 承担 |
| CI | lint(ruff) + type-check(mypy) + test(3.10–3.13 + win3.12) + originality + spec-coverage + bridges + golden-gate + adversarial-matrix + reachability + benchmark-smoke |
| 测试 | 107 个 `test_*.py`，按 unit/client/web/streaming/sinks/domain/protocol/facade/transport/... 切分 |
| Golden | `tests/golden/quotation/<0x命令_名称_代码>/<时间戳>/`，530 组 `payload.bin`（配 `.json` + `.yaml`），`golden_audit` 三旗标门禁 |
| 规格 | `PROTOCOL_SPEC/` 44 yaml + 2 md，7 协议族；`codegen` 生成 parser 骨架 + 命令账本 + CLI，**仅写入草稿**待人工合入 |
| 文档 | README / DESIGN / docs 30 篇（quickstart、troubleshooting、FAQ、FEATURE_MAP、OPTIMIZATION_PLAN v1–v5、adr、api、cookbook、migration、archive） |
| 治理 | GOVERNANCE / CONTRIBUTING / CODE_OF_CONDUCT / SECURITY / LICENSE(MIT) / git-cliff.toml(CHANGELOG 自动生成) |

---

## 3. 问题清单（分级）

### P0 —— 正确性 / 契约破损（必须修）

| # | 问题 | 位置 | 后果 |
|---|---|---|---|
| **A1** | `TdxClient.request()` 声明 `as_format` 参数却**直接 `return result.rows` 忽略**，传 `tuple`/`dataframe` 静默失效 | `client.py:738-749` | 通用命令入口的输出契约假承诺；调用方拿到与预期不符的结构且无告警 |
| **A2** | `request()` **丢弃 `ParseResult.tier/confidence/warnings`**，调用方无法感知 L2/L3 降级与解析告警 | `client.py:749` | 三级兜底的安全网在客户端层被切断，脏数据静默 |
| **A3** | 自定义 `fetch_*` **绕过基类容错**：`history.py`/`corporate.py`/`boards.py`/`global_market.py`/`fundflow.py` 直接 `self.client.get(...)`，仅手动 `_check_deprecated` + `acquire`，**无重试退避、无失败计数** | web 层多处 | `SourceDeprecated` 在这些路径永不触发；东财历史 K 线另写一套裸 host 循环未复用 `_EastmoneyJson._get_json` |
| **A4** | 腾讯 `fetch_all` **单批失败 `continue` 静默丢数据** | `web/adapters.py:469` | 全市场快照偶发残缺且无任何告警 |
| **A5** | 腾讯日 K / 分钟 K **`amount` 恒为 0**，不告警 | `web/adapters.py:828` | 下游用成交额会误判为「零成交」 |
| **A6** | CI **无覆盖率门禁**（未设 `--cov-fail-under`） | `pyproject.toml` / `ci.yml` / `Makefile` | 覆盖率下滑不阻断，`integration`（HTTP 43 端点 / MCP 23 工具）各仅 1 个测试文件、端点级覆盖浅 |
| **A7** | `network` 标记测试**无 skipif/deselect**，默认 CI 直连外网 | `tests/web/*_live.py` / `ci.yml` | CI 非幂等，外网抖动即红 |
| **A8** | `spec_audit` 未加 `--strict`，codegen 产出未自动合入 | `ci.yml` spec-coverage job | 规格与实现漂移可过 CI |

### P1 —— 结构债务 / 静默风险（应修）

| # | 问题 | 位置 |
|---|---|---|
| **B1** | **同步/异步双端全量复制**：`TdxClient`（222–968，747 行）与 `AsyncTdxClient`（969–1468，499 行）逐方法镜像；再加 3 个同步子类 + 4 个异步子类，一处改另一处易漏（v5 改造即因此需 6 处同步修改） | `client.py` |
| **B2** | **两套降级引擎并存**（见 §2.4），命名/口径不一致，facade 与 router 各写一份 | `facade/api.py:258` / `sources/__init__.py:161` |
| **B3** | **命令号硬编码为字面量**（`0x052D`/`0x0530`/`0x044D`/`0x054C` 等散落约 20 处），与 `commands.py` 账本**无引用关系**，可漂离 | `client.py:464,476,573,757…` |
| **B4** | 连接池**无熔断状态机**：`biz_failures` 统计了但不用于开路，故障主站仍被反复探测（每次 3s 超时） | `transport/pool.py` |
| **B5** | **7 条 `STATUS_OFFLINE` 命令缺 fail-fast**：账本已标 offline，但 client 层仍走完整超时重试链；仅 `quotes_snapshot` 有 0x054C→逐只 0x0530 回退 | `protocol/commands.py` / `client.py:904` |
| **B6** | Web facade **god-class**：`WebQuoteSession` 45+ 方法，每次调用新建 `HttpClient` 并关闭，高并发下连接池开销大 | `web/facade.py` |
| **B7** | **辅助函数泛滥**：`_f`/`_i` 在 web 层 **12 个文件各自定义**（adapters / adapters_ext / adapters_baidu / boards / corporate / fundflow / global_market / history / longhu / hot_rank / market_stats / adapters_index） | web 层 |
| **B8** | 缩放口径**三处维护**：`SourceSpec.scales` vs `normalize.py` vs 解析层内联，可漂移 | web 层 |
| **B9** | **共享桶注入脆弱**：`base.py:694` 直接写私有属性 `self.rate_limiter._buckets[spec.name]`，若 `source_name ≠ spec.name` 会双桶并存 | `web/base.py:694` |
| **B10** | `CredentialStore`（三级凭据存储）**无任何调用方**，仅定义与导出 | `security/credentials.py:177` |
| **B11** | Docker 镜像打包 `tests/` + `PROTOCOL_SPEC/`（含 530 个 `.bin`），体积与攻击面膨胀 | `Dockerfile:13-15` |
| **B12** | wheels 非 manylinux 合规（ubuntu 上 `python -m build --wheel`），旧 glibc 环境可能失效 | `.github/workflows/wheels.yml` |
| **B13** | 文档**未入 CI**：无构建校验、无链接检查；5 份 OPTIMIZATION_PLAN + archive 易漂移 | docs/ |
| **B14** | golden 530 组按真实时间戳命名，人工 capture + 扩张成本随版本上升 | `tests/golden/` |

### P2 —— 轻微 / 命名与整洁

| # | 问题 | 位置 |
|---|---|---|
| C1 | CLI docstring 写「32 端点」，`http_server.py` 自述 43，文档漂移 | `cli.py:14,340` vs `http_server.py:4` |
| C2 | `i18n` 名不副实：仅字符集探测（`encoding.py`），无任何用户字符串本地化 | `tstdx/i18n/` |
| C3 | `native.py` 原生加速已废（`NATIVE_AVAILABLE = native is not None`，扩展源码已移除），仍携带完整 parity 自检与 DeprecationWarning | `native.py` |
| C4 | `_f` 对 NaN/Inf 处理不一：`history.py:66` 用 `math.isfinite`，`adapters.py:77` 用普通 `float()`，JSON `NaN` 可伪装合法值 | web 层 |
| C5 | `KlineCache` 仅覆盖 K 线，行情/分时无缓存，重复全量拉取 | `cache.py` |
| C6 | `request_multi` / `iter_frames` 固定取首槽，多帧请求不享受多槽并发（注释说明为同连接约束，属已知取舍） | `transport/pool.py:486` |
| C7 | mypy `ignore_missing_imports=true` 宽松，可选依赖类型错误漏检 | `pyproject.toml` |
| C8 | `AsyncUnifiedQuoteAPI` / `BinaryClient` / `BridgeClient` / `HqClient` 等兼容门面与 `UnifiedQuoteAPI` 能力重叠且无 CLI 入口 | `facade/` |
| C9 | 腾讯 K 线 `count>800` 仅 `logger.warning`，不抛错不补分页（v5 已标 PG8 未实现） | `web/adapters.py:801` |

---

## 4. 优化改进方案

> **原则**：先堵正确性（P0）→ 再收敛结构（P1）→ 最后整洁（P2）。每项含**方案**与**可跑验收标准**。
> **依赖顺序**：G1/G2 是后续重构的安全网前置；A1–A2 与 B1 应同批（都动 `client.request`）。

### 第一批 · 正确性封堵（P0，可离线验收）

#### G1 · 覆盖率门禁（对应 A6）
- **方案**：`pyproject.toml` 加 `[tool.coverage]` + `fail_under`；CI test job 加 `--cov=tstdx --cov-report=term-missing`。**分两阶段**：先设 60% 基线并记录 `integration`/`facade` 缺口，稳定后逐季提至 75%。为 `integration/http_server.py` 的 43 端点补**端点级参数化单测**（注入 fake client），为 MCP 23 工具补工具级用例。
- **验收**：`pytest --cov-fail-under=60` 通过；新增端点测试使 `integration/` 覆盖率从当前单文件级升至 ≥70%；故意删一个端点 → CI 红。

#### G2 · 网络测试隔离（对应 A7）
- **方案**：给 live 测试加 `pytest.mark.network` + `skipif(NO_NETWORK)`；CI 默认 `pytest -m "not network"`；新增独立 **scheduled live job**（每日/每周跑 `-m network`），失败仅告警不阻断主干。
- **验收**：断网环境下 `pytest -m "not network"` 全绿；scheduled job 能独立触发并产出报告。

#### G3 · 规格漂移门禁（对应 A8）
- **方案**：CI `spec-coverage` 加 `--strict`；`Makefile gates` 纳入 `audit-spec`。codegen 产物继续走人工合入（保持可控），但**账本 ↔ 实现**的一致性由 `--strict` 强制。
- **验收**：故意让 spec 与实现不一致 → CI 红。

#### A1+A2 · `request()` 契约修复
- **方案**：`request()` 真正消费 `as_format`（走 `_emit`）；新增 `request_result()` 返回 `ParseResult`（含 `tier`/`confidence`/`warnings`），`request()` 保持向后兼容只返回行。三级兜底的元数据由此对调用方可见。
- **验收**：`request(0x052D, body, as_format="tuple")` 返回 tuple；`request_result()` 在低置信样本上 `tier>=2` 且有 `warnings`。

#### A3 · Web 容错旁路收口
- **方案**：把 `history.py`/`corporate.py`/`boards.py`/`global_market.py`/`fundflow.py` 中直接 `self.client.get(...)` 的自定义 `fetch_*` 全部改为走 `_request_text` / `_get_json`（基类已含重试退避 + 失败计数 + 黑名单 failover）；`EastmoneyHistoryKlineSource` 改为继承 `_EastmoneyJson` 复用 `_get_json`，删除其裸 host 循环。
- **验收**：注入持续失败 → 这些路径能累积失败计数并触发 `SourceDeprecated`；429 场景下自动退避重试。

#### A4 · 腾讯 fetch_all 失败页不静默
- **方案**：单批失败改为「记录 `failed_symbols` + 单次退避重试 + 结束时 `UserWarning`」，不得静默 `continue` 丢数据。
- **验收**：注入某批失败 → 结果缺失部分可见（`failed_symbols` 非空）且有告警。

#### A5 · 腾讯 amount 缺失显式告警
- **方案**：腾讯日 K/分钟 K 解析时若 `amount` 全为 0，在响应 `extra` 标记 `amount_unavailable=True` 并一次性告警（非逐行）。
- **验收**：跨 tdx/web 路由取同一 K 线，`amount` 口径差异在 `extra` 中可判定，不再误判为「零成交」。

### 第二批 · 结构收敛（P1，中批量）

#### B1 · 同步/异步镜像消除
- **方案**：抽 `ClientBase`（协议构造、`_emit`、参数校验、分页封装共用），`TdxClient`/`AsyncTdxClient` 仅实现 `_request`/`_request_async` 两个传输 seam；子类继承后只加业务方法。**分两步**：先抽公共 mixin（零行为变更），再逐步合并重复方法体。
- **验收**：`client.py` 行数下降 ≥25%；同步/异步同名方法的行为一致性测试（对拍 `bars/quotes/minute/security_list` 的 body 字节完全相同）。

#### B2 · 降级引擎统一
- **方案**：以 `DataSourceRouter` 为唯一降级引擎（它已具备 5 级链与口径记录），`UnifiedQuoteAPI` 收窄为**路由选择 + 熔断 + 口径守卫**，其 `local` 路由内部委托 router 的 `reader` 层。消除双份降级逻辑与命名歧义。
- **验收**：facade 与 router 对同一 (symbol, period, count) 返回条数与 datetime 口径一致；`route="local"` 与 `--source router` 结果等价。

#### B3 · 命令号单一事实源
- **方案**：client.py 中约 20 处字面量命令号改为 `commands.COMMANDS` 的枚举常量引用（如 `CMD.security_bars()`），让账本成为唯一来源。
- **验收**：`grep -c "0x05" tstdx/client.py` 显著下降；改 spec 中某命令号 → 实现编译期/测试期即报错。

#### B4 · 熔断状态机
- **方案**：在 `ConnectionPool` 引入基于**连续失败率 + 连续失败次数**的熔断状态（`HEALTHY → DEGRADED → OPEN → HALF_OPEN`），`OPEN` 期间该 host 直接跳过（不走 3s 超时），`HALF_OPEN` 用单次探测恢复。`biz_failures` 纳入判定。
- **验收**：注入单主站持续失败 → 熔断后请求延迟从「N×3s 超时」降至「跳过即命中健康 host」；恢复后自动半开放行。

#### B5 · offline 命令 fail-fast
- **方案**：client 层在发请求前查账本 `status`，对 7 条 `STATUS_OFFLINE` 命令中**无回退路径**者直接抛 `CommandOffline`（含替代方案文案），不再走完整超时链；`quotes_snapshot` 保留 0x054C→逐只 0x0530 回退。
- **验收**：调用 offline 命令 → 立即抛明确异常（<10ms），文案指向替代命令；`quotes_snapshot` 仍正常返回。

#### B6 · Web facade 拆分 + 连接复用
- **方案**：`WebQuoteSession`（45+ 方法 god-class）拆为 `QuoteSession` / `KlineSession` / `FundFlowSession` / `BoardSession` / `CorporateSession` 等子会话，共享一个**持久 `HttpClient`**（生命周期由调用方或 session 持有），消除「每次新建 + 关闭」的连接开销。保持原 45 方法作为聚合门面委托子会话（向后兼容）。
- **验收**：高频循环调用下 TCP 连接数从「每次新建」降至「池化复用」；原 45 方法签名与返回值不变。

#### B7 · Web 辅助函数上提
- **方案**：`_f`/`_i` 及近似工具上提到 `web/base.py`（统一 NaN/Inf 处理为 `math.isfinite` 校验，对齐 C4），12 个文件删除本地副本改为导入。
- **验收**：`grep -rn "^def _f" tstdx/web/` 仅剩 `base.py` 1 处；JSON `NaN` 输入不再伪装合法值。

#### B8 · 缩放口径单一化
- **方案**：以 `normalize.py` 为唯一缩放事实源，`SourceSpec.scales` 仅声明元数据（供文档与校验），解析层内联缩放逐步迁移到 `VolumeNormalizer`。
- **验收**：三处口径一致性测试（每源的量纲在 spec / normalizer / 解析层三者相符）。

#### B9 · 共享桶注入修复
- **方案**：给 `RateLimiter` 增 `set_bucket(name, bucket)` 公共方法，替换对 `_buckets` 私有属性的直接写入。
- **验收**：`source_name ≠ spec.name` 场景下不产生双桶（单测断言桶数量）。

#### B10 · 凭据能力接线或废弃
- **方案**：为 `CredentialStore` 提供 `config` 入口 + CLI `--credential-source` 选项，接入 `trade/` 模拟器与 Web cookie 管理；若判定短期无用则明确标记 `@deprecated` 并写 ADR 说明废弃计划。
- **验收**：要么有实际调用方与测试，要么有 ADR + deprecation 标记（不留「死能力」）。

#### B11 · Docker 多阶段
- **方案**：构建阶段装 dev 依赖跑测试，运行镜像仅含 `tstdx/` + 运行 extras，剔除 `tests/`、`PROTOCOL_SPEC/`、`docs/`。
- **验收**：镜像体积显著下降；`docker run` 内 `ls tests/` 不存在。

#### B12 · manylinux wheels
- **方案**：`wheels.yml` 引入 `cibuildwheel` + manylinux 矩阵（Python 3.10–3.13 × x86_64/aarch64），产物上传 PyPI。
- **验收**：在旧 glibc 容器内 `pip install` wheel 成功。

#### B13 · 文档入 CI
- **方案**：加 mkdocs 构建 + markdown 链接检查 job；OPTIMIZATION_PLAN v1–v5 加「状态标记」（已实施/部分/未实施），未实施项移入 `docs/archive/`。
- **验收**：断链或文档构建失败 → CI 红。

### 第三批 · 整洁与命名（P2，低成本）

| 项 | 方案 | 验收 |
|---|---|---|
| C1 | 统一端点数表述为 43，CLI docstring 与 `http_server.py` 对齐（或直接改为「动态统计」） | `grep 端点` 无矛盾数字 |
| C2 | `tstdx/i18n` 更名 `charset`（或补齐真正 l10n）；若更名需同步导出与文档 | 包名与内容语义一致 |
| C3 | `native.py` 按 deprecation 计划删除或降为 1 页说明；Rust 包独立发布 | 主包无废代码 |
| C4 | 随 B7 一并统一 NaN 处理 | JSON `NaN` 被拒绝 |
| C5 | 缓存层扩展：`QuoteCache` / `MinuteCache`（带 TTL），或明确文档说明缓存仅覆盖 K 线 | 缓存覆盖面与文档一致 |
| C6 | `request_multi` 文档化「同连接约束」取舍；如需并发，改为多连接分段 + 显式合并 | 取舍有书面说明 |
| C7 | 拆分 mypy 配置：核心包严格、可选依赖单独 override | 可选依赖类型错误可检出 |
| C8 | 兼容门面（`BinaryClient`/`BridgeClient`/`HqClient`）标注 deprecated 与替代方案，或补 CLI 入口 | 无「无入口的重复门面」 |
| C9 | 腾讯 K 线超限自动分段取全量（v5 PG8 落地） | 请求 2000 根返回 2000 根 |

### 4.4 实施节奏建议

```
第一批（正确性，可离线验收）        第二批（结构收敛，中批量）           第三批（整洁）
├─ G1 覆盖率门禁 ─┐               ├─ B1 镜像消除 ─┐                    C1-C9
├─ G2 网络隔离    │  先建安全网    ├─ B2 降级统一  │  依赖 B1 的公共抽象 │
├─ G3 规格门禁    │               ├─ B3 命令号    │                    │
├─ A1/A2 request │               ├─ B4 熔断      │  ← 与 B3 同源改动   │
├─ A3 Web 容错    │               ├─ B5 fail-fast │                    │
├─ A4 腾讯丢数据  │               ├─ B6 拆分复用  │                    │
└─ A5 amount 告警 ┘               ├─ B7/B8 收口   │                    │
                                   └─ B9-B14 工程 │                    │
```

**推荐顺序**：G1→G2→G3（先建门禁，否则后续重构无保护）→ A1/A2（同文件，一次改完）→ A3→A4/A5（Web 层）→ B1（最大结构收益，需最谨慎）→ B2/B3（同源）→ B4/B5 → B6 → B7/B8/B9 → B10–B14 → C 系列。

---

## 5. 明确不做（避免过度工程）

| 不做 | 理由 |
|---|---|
| 不引入 pyo3/cython 到主包 | 现有 Rust `tstdx_native` 独立发布 + parity 对拍已是更干净的解耦；主包零硬依赖是分发优势 |
| 不实现真实实盘交易 | `trade/` 红线明确（`SocketTransport` 抛 `TradingUnavailable`），实盘涉及合规与资金风险，与行情库定位分离 |
| 不把 codegen 产物自动应用 | 当前「草稿 + 人工合入」保留了审核点，自动应用会让协议变更失去人工校验 |
| 不重写 golden 体系 | 530 组真实样本是三旗标门禁的基础，按时间戳组织虽膨胀但**真实性优先**；改为「按命令号索引」需重写门禁 |
| 不为 tracing 补全实现 | 当前明确标注 stub + 默认关闭，比「半实现」更诚实；若确有需求再按 otel 标准一次性补 |

---

## 6. 附录 A：审计中经复核推翻的误判

| 误判 | 复核结论 |
|---|---|
| 「`quotes_snapshot`/`auction_snapshot`/`volume_price_dist` 是死接口，必抛 `AllHostsUnreachable`」 | **部分推翻**：`quotes_snapshot` 有显式 0x054C→逐只 0x0530 回退（`client.py:904`），实测返回 3 条真实快照。真实问题是「7 条 offline 命令缺 client 层 fail-fast」（B5），而非「接口已死」 |
| 「`streaming/__init__.py` 有 return 之后的死代码」 | **推翻**：经缩进复核，该段是外层函数体内嵌函数之后的正常续行，非死代码 |

## 7. 附录 B：数据依据

| 指标 | 数值 | 来源 |
|---|---|---|
| 顶层模块数 | 25 | `ls tstdx/` |
| `tstdx/` 内 `.py` | 116 | `find tstdx -name "*.py"` |
| `client.py` 行数 | 1823 | `wc -l` |
| `web/` 行数 | 10516（22 文件） | 审计统计 |
| 协议族 / 命令 / 解析器 | 5 族 / 85 命令 / ~80 解析器 | `commands.py` / `registry.py` |
| Web 源 | 7 家族 / 30 源名 | `web/sources.py` `KNOWN_SOURCES` |
| Web facade 方法 | 45+ | `web/facade.py` |
| HTTP 端点 / MCP 工具 | 43 / 23 | `integration/http_server.py` / `mcp_server.py` |
| 测试文件 / golden 组 | 107 / 530 | `find tests -name "test_*.py"` / `tests/golden/` |
| PROTOCOL_SPEC | 44 yaml + 2 md（7 族） | `PROTOCOL_SPEC/` |
| 文档 | docs 30 篇 | `ls docs/` |
| Python / 依赖 | >=3.10 / 零硬依赖 | `pyproject.toml` |

---

## 附录：修改记录

| 日期 | 记录 |
|---|---|
| 2026-09-05 | v6 初版：4 路并行只读审计（核心协议栈 / Web 层 / 横向能力层 / 工程治理）+ 人工逐条复核（推翻 2 条误判），产出架构全景 + 30 项问题清单（P0×8 / P1×14 / P2×9）+ 三批实施方案。本文件为**架构与工程治理**专题，与 v1（协议正确性）/v2（功能扩展）/v3（能力扩展）/v4（文档一致性）/v5（分页与完整性）互不覆盖 |
