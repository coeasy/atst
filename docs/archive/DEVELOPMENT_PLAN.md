# atst 开发计划

> **基线方案**：`DESIGN.md` v3.0（32 章，28 周 5 阶段）
> **审计索引**：`AUDIT_AND_BRIDGES.md`（24 断链点 → 10 贯通工程 → 24 项验收）
> **创建日期**：2026-08-31
> **计划周期**：W1（2026-09-07）→ W28（2027-03-26）
> **版本节奏**：0.1.0 (W2) → 0.2.0 (W7) → 0.4.0 (W15) → 0.7.0 (W22) → 1.0.0 (W28)

---

## 目录

1. [计划总则](#1-计划总则)
2. [里程碑总览](#2-里程碑总览)
3. [Phase 0：奠基（W1-W2）](#3-phase-0奠基w1-w2)
4. [Phase 1：核心协议 + 贯通底座（W3-W7）](#4-phase-1核心协议--贯通底座w3-w7)
5. [Phase 2：全协议覆盖 + 跨域贯通（W8-W15）](#5-phase-2全协议覆盖--跨域贯通w8-w15)
6. [Phase 3：Rust 内核 + 集成生态（W16-W22）](#6-phase-3rust-内核--集成生态w16-w22)
7. [Phase 4：协议补全 + 贯通验证 + v1.0 发布（W23-W28）](#7-phase-4协议补全--贯通验证--v10-发布w23-w28)
8. [依赖关系矩阵](#8-依赖关系矩阵)
9. [风险登记册](#9-风险登记册)
10. [资源与角色](#10-资源与角色)
11. [质量门禁](#11-质量门禁)
12. [周会节奏](#12-周会节奏)

---

## 1. 计划总则

### 1.1 开发节奏

| 项 | 规则 |
|---|---|
| 冲刺周期 | 1 周一个 Sprint（周一始） |
| 每周交付 | 周五前产出可运行版本 + Sprint Demo |
| 版本号 | `0.{phase}.{sprint}`，如 Phase 1 W3 = `0.1.3-dev`，周五 tag = `0.1.3` |
| 分支策略 | `main`（稳定） / `dev`（集成） / `feature/*` / `spec/*` / `fix/*` |
| 合并门槛 | 1 个 Reviewer approve + CI 全绿 + Spec 覆盖检查通过 |
| 冻结期 | 每阶段最后 2 天冻结新 feature，只修 bug + 补文档 |

### 1.2 优先级规则

```
P0-Blocker  → 阻断当前 Sprint 发版，必须本 Sprint 解决
P1-High     → 本 Sprint 必须完成或明确降级
P2-Medium   → 本阶段内完成即可
P3-Low      → 可推迟到下一阶段
```

### 1.3 产出物分类

| 类型 | 说明 | 示例 |
|---|---|---|
| **SPEC** | 协议规格说明书（Markdown + YAML） | `PROTOCOL_SPEC/7709/052d_kline.yaml` |
| **CODE** | 生产代码（含单元测试） | `atst/protocol/quotation/kline.py` |
| **TEST** | 集成/e2e/parity/贯通测试 | `tests/test_protocol_tiers.py` |
| **TOOL** | 开发工具 / CI 脚本 | `tools/check_originality.py` |
| **DOC** | 文档（README/Cookbook/ADR/Changelog） | `docs/cookbook/01_notebook_kline.md` |
| **GOV** | 治理文件 | `GOVERNANCE.md` / `SECURITY.md` |

---

## 2. 里程碑总览

```
  W1-W2          W3-W7           W8-W15          W16-W22         W23-W28
  ┌─────┐       ┌──────────┐    ┌──────────────┐  ┌────────────┐  ┌──────────────┐
  │Phase 0│      │  Phase 1  │   │   Phase 2    │  │  Phase 3   │  │   Phase 4    │
  │ 奠基  │      │ 贯通底座  │   │ 全协议+跨域  │  │Rust+集成   │  │ 补全+验证    │
  └──┬──┘       └────┬─────┘    └──────┬───────┘  └─────┬──────┘  └──────┬───────┘
     │               │                 │                │                │
   v0.1.0         v0.2.0           v0.4.0           v0.7.0           v1.0.0
   Spec体系        贯通底座          全协议覆盖        Rust+HTTP+WS     24项验收+发布
   Codegen模板     配置/错误/i18n    Streaming+异步    MCP+文档+治理     冒烟30天+PyPI
   Golden采集      8命令MVP          GoodsClient      Docker+wheel      Docker Hub
```

| 里程碑 | 周 | 版本 | 核心交付 | 验收锚点（§32） |
|---|---|---|---|---|
| **M0** | W2 | 0.1.0 | Spec 体系 + Codegen + Golden 工具 + atst.toml schema | 12, 13, 14（基础） |
| **M1** | W7 | 0.2.0 | 8 命令 MVP + 配置 + 错误 + i18n + 报文层 | 04, 05, 09, 10, 11 |
| **M2** | W15 | 0.4.0 | 36 命令全 + 7727 + MAC + F10 + Streaming + 异步桥 + 互操作 + **HTTP Web 源 7 Adapter + 归一化** + 反馈 | 01, 02, 06, 07, 08, 20, **23, 24** |
| **M3** | W22 | 0.7.0 | Rust 内核 + HTTP 32 接口 + WS + MCP + 文档 + 治理 + Docker | 15, 17, 18, 19, 21, 22 |
| **M4** | W28 | 1.0.0 | Prober + Profile 自动探测 + 安全合规 + 30 天冒烟 + 24 项贯通全过 | **24/24** |

---

## 3. Phase 0：奠基（W1-W2）

> **目标**：建立"禁复制"约束能被执行的前提——Spec 体系、Codegen 模板、Golden 采集工具。没有这个地基，后面写多少代码都不好证明清白。
>
> **版本目标**：`0.1.0`
> **验收锚点**：§32 #12（Golden 来源审计）、#13（AST 相似度门禁）、#14（License 白名单）基础版

### W1：Spec 体系 + 原创性工程

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P0-W1-01 | 初始化项目骨架 | CODE | P0 | `pyproject.toml` / `atst/__init__.py` / `atst/errors.py` / `atst/types.py` / `atst/constants.py` | `pip install -e .` 成功 + `import atst` 可用 | — |
| P0-W1-02 | 创建 `PROTOCOL_SPEC/` 目录结构 | SPEC | P0 | `PROTOCOL_SPEC/wire_format.md` / `INDEX.yaml`（36 命令占位） | 36 个 spec_id 全部占位（status=draft） | 01 |
| P0-W1-03 | 编写报文格式 Spec | SPEC | P0 | `PROTOCOL_SPEC/wire_format.md` | 请求头 12B + 响应头 16B 字段表完整 + 示例 hex | 02 |
| P0-W1-04 | 创建 `ORIGINALITY/` 合规档案 | GOV | P0 | `LICENSE_ALLOWLIST.md` / `CLEANROOM_PROCESS.md` | 白名单含 pyo3/maturin/encoding_rs/bytes/thiserror/tokio + 禁止清单含 eltdx | 01 |
| P0-W1-05 | 实现原创性检查工具 | TOOL | P0 | `tools/check_originality.py` | AST 相似度 > 0.70 阻断 + License 白名单扫描 + 可 `pre-commit` 执行 | 04 |
| P0-W1-06 | 配置 CI 流水线 | TOOL | P0 | `.github/workflows/ci.yml` | lint(ruff) + type-check(mypy) + test(pytest) + originality 检查 4 个 job 全过 | 05 |
| P0-W1-07 | 编写 3 个核心 Spec（首批） | SPEC | P0 | `PROTOCOL_SPEC/7709/000d_login.md` / `052d_kline.md` / `054c_batch_quote.md` | 每份含：基本信息/请求报文/响应报文/口径说明/验证数据/观测来源 | 03 |

### W2：Codegen + Golden 采集 + 配置 Schema

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P0-W2-01 | 实现 Spec → Codegen 模板引擎 | TOOL | P0 | `atst/tools/codegen.py` | `python -m atst.tools.codegen --regen-all` 能从 YAML spec 生成 parser 骨架代码 + `--verify-all` 校验一致 | P0-W1-07 |
| P0-W2-02 | 实现 Golden 数据采集工具 | TOOL | P0 | `tools/collect_golden.py` + `atst` CLI `protocol capture` 子命令 | 能在非交易时段连接公共主站、抓取指定命令的原始报文、zstd 压缩存储 + 元数据 YAML 含 `source: self-captured` | 06 |
| P0-W2-03 | 采集首批 Golden 数据（8 命令） | TEST | P0 | `tests/golden/raw/7709/{000d,052d,054c,0fdb,0fc5,0fc6,000f,0010}/*.zst` | 每命令 ≥ 3 个样本，含不同市场/品种，元数据完整 | 02 |
| P0-W2-04 | 设计 atst.toml Schema | CODE | P0 | `atst/config.py`（pydantic v2 模型） | 所有 §21.2 配置项建模 + strict 校验 + 未知字段报错 | P0-W1-01 |
| P0-W2-05 | 实现 6 源配置加载器 | CODE | P0 | `atst/config.py` `ConfigLoader` 类 | 函数入参 > env > project > user > system > default 优先级合并 + deep-merge + 12 case 测试 | 04 |
| P0-W2-06 | Spec ↔ Codegen ↔ Contract 闭环验证 | TEST | P0 | `tests/test_spec_coverage.py` | 所有 spec 有对应 parser + 所有 parser 有对应 spec + spec 字段偏移完全覆盖报文 | P0-W2-01 |
| P0-W2-07 | 编写 README + CONTRIBUTING | DOC | P1 | `README.md` / `CONTRIBUTING.md` | README 含安装/快速开始/5 行代码示例 + CONTRIBUTING 含洁净室流程说明 | 04 |
| P0-W2-08 | Tag v0.1.0 | GOV | P0 | git tag `v0.1.0` | CI 全绿 + 8 个 Golden 样本可 replay + Codegen 闭环通过 | 全部 |

**Phase 0 退出条件**：
- [ ] 36 个 spec_id 全部占位（≥ 3 个已完整编写）
- [ ] Codegen `--regen-all` + `--verify-all` 通过
- [ ] Golden 采集工具可在非交易时段工作，产出 ≥ 8 命令的样本
- [ ] `atst.toml` 6 源合并 12 case 测试通过
- [ ] `tools/check_originality.py` 可执行
- [ ] CI 4 job 全绿

---

## 4. Phase 1：核心协议 + 贯通底座（W3-W7）

> **目标**：8 命令 MVP + 贯通底座（配置/错误/i18n/报文层），让"协议能写、用户能跑"。
>
> **版本目标**：`0.2.0`
> **验收锚点**：§32 #04（配置 12 case）、#05（错误体系）、#09（字符集）、#10（日历）、#11（时区）

### W3：报文层 + Wire Codec

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P1-W3-01 | 实现报文头编解码 | CODE | P0 | `atst/protocol/wire.py` | 请求头 12B 编码/解码 + 响应头 16B 编码/解码 + 与 Golden 数据 round-trip 一致 | P0-W2-01 |
| P2-W3-02 | 实现 VarintCodec | CODE | P0 | `atst/protocol/varint.py` | TDX 变长价格/成交量编码 + 解码 + 边界测试（0 / 最大值 / 截断报文） | 01 |
| P1-W3-03 | 实现 BaseParser + 注册表 | CODE | P0 | `atst/protocol/base.py` / `registry.py` | `@register_parser(msg_id, protocol)` 装饰器 + 注册表查询 + BaseParser 接口定义 | P0-W1-01 |
| P1-W3-04 | 报文层 Contract 测试 | TEST | P0 | `tests/protocol/test_wire_format.py` | 12B/16B 头编解码 round-trip + varint 边界 + 报文分片重组 | 01, 02 |
| P1-W3-05 | 实现 GBK/zlib 编解码 | CODE | P0 | `atst/transport/codec.py` | GBK 解码 + GB18030 兼容 + zlib 压缩/解压 + 与 Golden 数据一致 | 01 |

### W4：Spec/Codegen 闭环全量跑通

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P1-W4-01 | 补全 8 核心命令 Spec | SPEC | P0 | `PROTOCOL_SPEC/7709/{000d,0004,052d,054c,0fdb,0fc5,0fc6,000f}.yaml` | 8 份 YAML spec 全部 status=accepted | P0-W1-07 |
| P1-W4-02 | Codegen 生成 8 个 parser 骨架 | CODE | P0 | `atst/protocol/quotation/{login,heartbeat,kline,batch_quote,history_minute,trade_detail,history_trade,gbbq}.py` | `--regen-all` + `--verify-all` 全过 | P1-W3-03 + P1-W4-01 |
| P1-W4-03 | 实现 8 个 L1 精确解析器 | CODE | P0 | 同上 8 个 `.py` 文件（完整实现） | 每个解析器与 Golden 数据 round-trip 一致 + spec 字段 100% 覆盖 | 02 |
| P1-W4-04 | Spec ↔ 实现反向校验脚本 | TOOL | P0 | `tools/spec_audit.py` | 扫描所有 parser 与 spec：1) 每个 parser 有 spec 2) 每个 spec 有 parser 3) 字段偏移全覆盖报文 | 03 |
| P1-W4-05 | L2 通用解析器骨架 | CODE | P1 | `atst/protocol/generic.py` | 记录数前缀识别 + 定长推测 + 字段类型打分 + 返回带置信度的 `ParsedResult` | P1-W3-03 |
| P1-W4-06 | L3 原始透传 | CODE | P1 | `atst/protocol/generic.py` `RawPassthrough` 类 | L2 推测失败也返回 raw bytes + `ParsedResult(tier="L3")` | 05 |

### W5：配置中心完整落地

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P1-W5-01 | 完善配置 pydantic 模型 | CODE | P0 | `atst/config.py` 全部子模型（Network/RateLimit/Memory/Streaming/I18n/Observability/Security/Compatibility） | strict 校验 + 未知字段报错 + 环境变量自动类型转换 | P0-W2-04 |
| P1-W5-02 | 运行时配置热更新 | CODE | P1 | `atst/config.py` `TstdxConfig.hot_update()` | 限流器/内存预算/push 队列大小可运行时修改 + 立即生效 | 01 |
| P1-W5-03 | 配置持久化 | CODE | P2 | `atst/config.py` `save_to()` | 用户主动调用写盘 + 不自动写 + 原子写入（tmp + rename） | 01 |
| P1-W5-04 | 12 case 合并测试 | TEST | P0 | `tests/config/test_merge.py` | §21.5 全部 12 case 通过 | 01 |
| P1-W5-05 | XDG 路径规范 | CODE | P1 | `atst/config.py` 路径解析 | Linux: `~/.config/atst/` / macOS: `~/Library/Application Support/atst/` / Windows: `%APPDATA%/atst/` | 01 |

### W6：错误体系 + 重试 failover

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P1-W6-01 | 实现 24 类异常分类树 | CODE | P0 | `atst/errors.py` | §24.1 全部 24 个异常类 + 所有继承自 `TstdxError` + 每类有 `retry_advice` 属性 | P0-W1-01 |
| P1-W6-02 | 实现 RetryAdvice 表 | CODE | P0 | `atst/errors.py` `RETRY_TABLE` | §24.2 所有异常类型映射到 retryable/backoff/max_retries/switch_host/fallback_to_offline | 01 |
| P1-W6-03 | 实现重试 failover 引擎 | CODE | P0 | `atst/services/retry.py` | 指数退避 + 切主站 + 重试预算 + 用户可 catch `TstdxError` 统一处理 | 02 |
| P1-W6-04 | 错误体系测试 | TEST | P0 | `tests/errors/test_taxonomy.py` | §24.4 全部子测试通过（异常继承/retry_advice 完整/catch-all/退避秒数） | 01, 02 |
| P1-W6-05 | Transport 层错误映射 | CODE | P1 | `atst/transport/connection.py` | TCP 超时→ConnectTimeout / RST→ConnectionReset / 全主站不可达→AllHostsUnreachable | P1-W3-01 |

### W7：i18n + 时区 + 交易日历 + MVP 集成

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P1-W7-01 | 实现字符集编解码 | CODE | P0 | `atst/i18n/encoding.py` | GBK/GB18030/Big5/Shift-JIS/EUC-KR/UTF-8 + auto_detect + errors=replace 不抛 | P1-W3-05 |
| P1-W7-02 | 实现时区处理 | CODE | P0 | `atst/i18n/timezone.py` `MarketTime` 类 | UTC 内部 + 本地输出 + ISO 8601 带偏移序列化 + 美股夏令时无歧义 | 01 |
| P1-W7-03 | 实现 A 股交易日历 | CODE | P0 | `atst/i18n/calendar.py` `ChinaSseCalendar` | 2024-2026 节假日表 + 调休 + `is_trading_day` / `is_trading_session` / `previous/next_trading_day` | 02 |
| P1-W7-04 | 交易日历联网更新 | CODE | P2 | `atst/i18n/calendar.py` `update_from_url()` | 启动时尝试下载上交所最新节假日 JSON + 失败 fallback 内置 | 03 |
| P1-W7-05 | i18n 测试矩阵 | TEST | P0 | `tests/i18n/test_encoding.py` / `test_tz.py` / `test_calendar.py` | §23.4 全部 10 case 通过 | 01, 02, 03 |
| P1-W7-06 | 8 命令 MVP 集成测试 | TEST | P0 | `tests/integration/test_mvp.py` | 登录→心跳→K线→批量行情→历史分时→成交明细 全链路跑通 + 输出 dict/tuple | P1-W4-03 |
| P1-W7-07 | 编写 Quickstart 文档 | DOC | P1 | `docs/quickstart.md` | 安装 + 5 行代码取 K 线 + 输出示例 | 06 |
| P1-W7-08 | Tag v0.2.0 | GOV | P0 | git tag `v0.2.0` | CI 全绿 + 8 命令 MVP 可跑 + 配置/错误/i18n 测试全过 | 全部 |

**Phase 1 退出条件**：
- [ ] 8 个核心命令 L1 精确解析器全部实现 + Golden round-trip 一致
- [ ] L2 通用解析器 + L3 原始透传可用
- [ ] atst.toml 6 源合并 12 case 全过
- [ ] 24 类异常 + RetryAdvice 表完整
- [ ] 字符集 4 编码探测 + 时区 + A 股日历 10 case 全过
- [ ] 8 命令 MVP 集成测试通过

---

## 5. Phase 2：全协议覆盖 + 跨域贯通（W8-W15）

> **目标**：36 命令全覆盖 + 7727/MAC/F10 + Streaming + 异步桥 + 互操作 + 反馈回路。
>
> **版本目标**：`0.4.0`
> **验收锚点**：§32 #01（三层覆盖）、#02（spec 全覆盖）、#03（24 断链闭环）、#06（同步异步）、#07（降级）、#08（流重连）、#20（sink 三实现）

### W8：7709 全部 36 命令 Spec + 实现

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P2-W8-01 | 补全剩余 28 命令 Spec | SPEC | P0 | `PROTOCOL_SPEC/7709/*.yaml`（28 份新增） | 36 份 YAML spec 全部 status=accepted + 每份含完整字段表 | P1-W4-01 |
| P2-W8-02 | Codegen + 实现 28 个 parser | CODE | P0 | `atst/protocol/quotation/*.py`（28 个新增） | `--regen-all` + `--verify-all` 全过 + 每个与 Golden round-trip 一致 | 01 |
| P2-W8-03 | 补充 Golden 数据（28 命令） | TEST | P0 | `tests/golden/raw/7709/*.zst`（28 组） | 每命令 ≥ 2 个样本 + 元数据含 `source: self-captured` | 02 |
| P2-W8-04 | Spec 覆盖率检查 | TEST | P0 | `tools/spec_audit.py` + `tests/test_spec_coverage.py` | 36 个 spec 全有 parser + 全有 Golden + 字段全覆盖 | 02, 03 |
| P2-W8-05 | 三层覆盖测试 | TEST | P0 | `tests/test_protocol_tiers.py` | L1 精确 / L2 通用（给未知命令） / L3 透传 全部有测试 + 永不丢包断言 | P1-W4-05, 06 |

### W9：7727 扩展市场 + GoodsClient（期货/期权/外汇）

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P2-W9-01 | 7727 协议 Spec | SPEC | P0 | `PROTOCOL_SPEC/7727/*.yaml`（17+ 份） | 扩展市场全命令 spec 完整 + 含港股/美股/期货字段差异 | P0-W1-02 |
| P2-W9-02 | 7727 解析器实现 | CODE | P0 | `atst/protocol/ex_quotation/*.py` | 17+ 个 L1 解析器 + Golden round-trip | 01 |
| P2-W9-03 | 商品语义协议 Spec | SPEC | P0 | `PROTOCOL_SPEC/goods/*.yaml`（11+ 份） | GoodsCount/CategoryList/Varieties/Quote/Quotes/KLine/TickChart/ChartSampling/HistoryTransaction 全覆盖 | P0-W1-02 |
| P2-W9-04 | GoodsClient 实现 | CODE | P0 | `atst/client/goods.py` | CFFEX/SHFE/DCE/CZCE/INE/GFEX 期货 + 期权 + 外汇 接口可用 | 03 |
| P2-W9-05 | ExtendedClient 实现 | CODE | P0 | `atst/client/extended.py` | 港股/美股/期货扩展市场行情可取 | 02 |

### W10：MAC 专属协议

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P2-W10-01 | MAC 协议 Spec | SPEC | P0 | `PROTOCOL_SPEC/mac/*.yaml`（16+ 份） | 0x120F–0x2562 全命令 spec + 含板块/资金流向/统一K线/统一报价等 | P0-W1-02 |
| P2-W10-02 | MAC 解析器实现 | CODE | P0 | `atst/protocol/mac_quotation/*.py` | 16+ 个 L1 解析器 + Golden round-trip | 01 |
| P2-W10-03 | MacClient 实现 | CODE | P0 | `atst/client/mac.py` | 板块列表/成分股/资金流向/主力监控 可用 + 本地 QFQ 重算 | 02 |
| P2-W10-04 | MAC Golden 数据采集 | TEST | P0 | `tests/golden/raw/mac/*.zst` | MAC 主站少（3 台），非交易时段采集 ≥ 16 命令各 1 样本 | 02 |

### W11：F10 资料协议 + TQLEX 网关

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P2-W11-01 | F10 Entry Spec | SPEC | P0 | `PROTOCOL_SPEC/f10/*.yaml`（18+ 份） | 20+ Entry 全覆盖（公司概况/主营构成/股东增减持/分红融资/财务报表/财务诊断/个股总评/盈利预测/热点题材/沪深股通/资讯研报 等） | P0-W1-02 |
| P2-W11-02 | F10 解析器实现 | CODE | P0 | `atst/protocol/f10/*.py` | 18+ 个 L1 解析器 + Golden round-trip | 01 |
| P2-W11-03 | F10Client 实现 | CODE | P0 | `atst/client/f10.py`（或 `atst/client/standard.py` 扩展） | `client.f10.company_overview("sh600519")` 可用 + 返回结构化 dict | 02 |
| P2-W11-04 | TQLEX 网关适配 | CODE | P1 | `atst/protocol/f10/tqlex.py` | 7615 端口 TQLEX 网关连接 + Entry 请求/响应格式 | 02 |

### W12：Streaming 全特性

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P2-W12-01 | 订阅管理器 | CODE | P0 | `atst/streaming/subscription.py` `SubscriptionRegistry` | 订阅/取消订阅/重订阅 + 级别（五档/逐笔/全量） + 60 只/批限制 | P1-W4-03 |
| P2-W12-02 | PushChannel（0x0547） | CODE | P0 | `atst/streaming/push.py` | 加密推送队列 + 1024 帧/64MiB 预算 + 实时解码五档 | P1-W4-03 |
| P2-W12-03 | PollChannel + HybridChannel | CODE | P1 | `atst/streaming/poll.py` / `hybrid.py` | 轮询兜底 + 推送优先混合 + 自动切换策略 | 02 |
| P2-W12-04 | DeltaMerger 增量合并 | CODE | P0 | `atst/streaming/merger.py` | 乱序窗口 0.5s + 去重 + 版本跳跃检测 + 合并后输出完整快照 | 02 |
| P2-W12-05 | GapFiller 断线补数 | CODE | P0 | `atst/streaming/gapfill.py` | 断线期间数据 100% 补全 + 重连后自动补 + 补数失败抛 `GapNotFillable` | 04 |
| P2-W12-06 | BackpressureController | CODE | P1 | `atst/streaming/backpressure.py` | 消费者慢 → 丢旧帧/降采样/通知用户 + raw 预算 85% 触发淘汰 | 02 |
| P2-W12-07 | ReconnectPolicy | CODE | P0 | `atst/streaming/reconnect.py` | 1→30s 指数退避 + 3 次失败换主站 + 重连后恢复订阅 + 自动补数 | 05 |
| P2-W12-08 | Streaming 测试 | TEST | P0 | `tests/test_stream_resilience.py` | 断网 30s 后流 0 丢失 + 重连 < 3s + 增量合并正确 | 04, 05, 07 |

### W13：异步/同步双轨桥

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P2-W13-01 | 实现 run_sync 桥 | CODE | P0 | `atst/_async_bridge.py` | 主线程已有 loop 检测 + 守护线程独立 loop + 线程安全 + 不泄漏 | P1-W4-03 |
| P2-W13-02 | AsyncTdxClient | CODE | P0 | `atst/client/async_client.py` | 异步 API 签名与同步一致 + `async with` + `async for` 流式 | 01 |
| P2-W13-03 | 线程安全保证 | CODE | P0 | `atst/client/base.py` Slot 管理 | per-thread Slot + Streaming one-per-thread + 跨线程显式抛错 | 01 |
| P2-W13-04 | 同步/异步 Parity 测试 | TEST | P0 | `tests/test_sync_async_parity.py` | §25.5 全部 case 通过：sync_in_async_raises / async_in_thread / concurrent_sync / result_parity / no_loop_leak | 01, 02 |

### W14：互操作层 + HTTP Web 行情源

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P2-W14-01 | mootdx 兼容垫片 | CODE | P1 | `atst/compat/mootdx.py` `Reader`/`Quotes`/`Affair` | 覆盖 ≥ 80% mootdx 高频 API + 不一致部分抛 `CompatibilityWarning` | P2-W8-02 |
| P2-W14-02 | DataFrameSink | CODE | P0 | `atst/sinks/dataframe.py` | list[Bar] → DataFrame + 内存 | P1-W4-03 |
| P2-W14-03 | ParquetSink | CODE | P0 | `atst/sinks/parquet.py` | 按日分区 + zstd 压缩 + 可续写 | 02 |
| P2-W14-04 | DuckDBSink | CODE | P1 | `atst/sinks/duckdb.py` | 内嵌分析 + SQL 查询 + 流式 INSERT | 02 |
| **P2-W14-08** | **HTTP Web 源基类 + 统一接口** | CODE | **P0** | `atst/web/base.py` `WebQuoteSource` Protocol + `WebQuoteClient` | `WebQuoteClient(source="sina")` 可用 + `real()` / `market_snapshot()` / `kline()` 接口定义 | P1-W7-01 |
| **P2-W14-09** | **SinaAdapter 实现** | CODE | **P0** | `atst/web/sina.py` | 33 字段正则解析 + GBK 解码 + Referer 注入 + 800 只/批 + Golden round-trip | 08 |
| **P2-W14-10** | **TencentAdapter 实现** | CODE | **P0** | `atst/web/tencent.py` | 53 字段 ~ 分割解析 + GBK + 60 只/批 + volume ×100 归一化 + amount ×10000 归一化 | 08 |
| **P2-W14-11** | **EastmoneyAdapter 实现** | CODE | **P1** | `atst/web/eastmoney.py` | JSON 解析 + 串行 1s 限流 + UA+Referer + push2 端点 + 价格 ×100→浮点 | 08 |
| **P2-W14-12** | **JslAdapter + HkquoteAdapter + DayKlineAdapter + BocAdapter** | CODE | **P2** | `atst/web/{jsl,hkquote,daykline,boc}.py` | 4 个专用源各自实现 + Cookie/港股/K线/汇率 | 08 |
| **P2-W14-13** | **volume/amount 归一化模块** | CODE | **P0** | `atst/web/normalize.py` | 新浪 ×1 / 腾讯 ×100+×10000 / 东财 ×100+×1 全部归一化到 `volume=股、amount=元` + 4 case 测试 | 09, 10, 11 |
| **P2-W14-14** | **HTTP 源限流** | CODE | **P0** | `atst/web/ratelimit.py` | 按源×时段限流（东财 1 req/s 串行）+ 空响应检测降级 | 09, 10, 11 |
| **P2-W14-15** | **easyquotation 兼容垫片** | CODE | **P1** | `atst/compat/easyquotation.py` `use()` 工厂 | `easyquotation.use('sina')` 签名兼容 + `real()` / `market_snapshot()` 行为一致 | 08 |
| P2-W14-05 | DataSourceRouter | CODE | P1 | `atst/sources/router.py` | 多源路由（failover/merge/race） + 6 种降级路径（含 HTTP Web fallback） | 01, 08 |
| P2-W14-06 | 降级路径测试 | TEST | P0 | `tests/test_fallbacks.py` | 6 种降级场景全过（含 TDX→HTTP Web 降级） + `AllSourcesExhausted` 正确抛出 | 05, 13, 14 |
| P2-W14-07 | Sink 测试 | TEST | P0 | `tests/test_sinks.py` | DataFrame/Parquet/DuckDB 三种 sink 写入 + 读回 + 一致性校验 | 02, 03, 04 |
| **P2-W14-16** | **HTTP Web 源测试矩阵** | TEST | **P0** | `tests/web/test_web_sources.py` | §33.10 全 12 case 通过（正则/~ 分割/JSON/归一化/Referer/限流/降级/兼容） | 09-15 |

### W15：反馈回路 + Phase 2 收尾

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P2-W15-01 | 协议差异上报通道 | CODE | P1 | `atst/feedback/reporter.py` `report_protocol_observation()` | 写入本地 JSON + opt-in 上传 + `source: self-captured` 校验 | P2-W8-04 |
| P2-W15-02 | Telemetry 上报 | CODE | P1 | `atst/feedback/telemetry.py` | 默认 opt-out + 7 步脱敏 + 不含用户数据 + 失败吞 | 01 |
| P2-W15-03 | 调优建议回收 | CODE | P2 | `atst/feedback/stats.py` + CLI `atst feedback stats` | 本地汇总 + apply all 改配置 | 02 |
| P2-W15-04 | 24 断链闭环测试 | TEST | P0 | `tests/test_bridges.py` | §AUDIT_AND_BRIDGES 24 个断链点（21 基线 + A5/A6/A7 增量）全部有对应处理 + 测试覆盖 | 全 Phase 2 |
| P2-W15-05 | Phase 2 集成测试 | TEST | P0 | `tests/integration/test_phase2.py` | 36 命令 + 7727 + MAC + F10 + Goods + Streaming + 异步 + sink 全链路 | 全 Phase 2 |
| P2-W15-06 | 编写 6 篇 Cookbook | DOC | P1 | `docs/cookbook/01-06.md` | §28.2 前 6 个选题 | 全 Phase 2 |
| P2-W15-07 | Tag v0.4.0 | GOV | P0 | git tag `v0.4.0` | CI 全绿 + 12 项验收通过（§32 #01-03, #06-08, #20） | 全部 |

**Phase 2 退出条件**：
- [ ] 7709 全 36 命令 L1 + 7727 17+ + MAC 16+ + F10 18+ + Goods 11+ 全部实现
- [ ] L2 通用 + L3 透传 + 永不丢包测试通过
- [ ] Streaming push + 轮询 + 增量合并 + 断线补数 + 背压 + 重连全过
- [ ] 异步/同步双轨 Parity 测试全过
- [ ] mootdx 兼容垫片 + 3 种 sink + 6 种降级路径全过
- [ ] **HTTP Web 源 7 个 Adapter（sina/tencent/eastmoney/jsl/hkquote/daykline/boc）全实现**
- [ ] **volume/amount 归一化契约 4 case 通过（新浪 ×1 / 腾讯 ×100+×10000 / 东财 ×100+×1）**
- [ ] **TDX→HTTP Web 降级路径测试通过（AllHostsUnreachable → WebQuoteClient）**
- [ ] **easyquotation.use() 兼容垫片可用**
- [ ] 反馈回路 3 通道（opt-in）可用
- [ ] 24 断链闭环测试通过（含 A5 口径归一 / A6 反爬韧性 / A7 兼容迁移）

---

## 6. Phase 3：Rust 内核 + 集成生态（W16-W22）

> **目标**：Rust 加速内核 + HTTP REST 32 接口 + WebSocket RPC + MCP 10 工具 + 文档体系 + 治理 + Docker。
>
> **版本目标**：`0.7.0`
> **验收锚点**：§32 #15（wheel 12 组合）、#17（HTTP API）、#18（MCP 工具）、#19（可观测性）、#21（文档）、#22（治理）

### W16：Rust 核心移植（Reader + Parser）

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P3-W16-01 | Rust crate 初始化 | CODE | P0 | `atst_native/Cargo.toml` / `src/lib.rs` | pyo3 + maturin 初始化 + `maturin develop` 可 import | P2-W15-07 |
| P3-W16-02 | Reader 层 Rust 移植 | CODE | P0 | `atst_native/src/reader.rs` | DailyBarReader / MinBarReader / LcMinBarReader / BlockReader / FinancialReader | P2-W8-02 |
| P3-W16-03 | Parser 层 Rust 移植 | CODE | P0 | `atst_native/src/protocol.rs` | 36 命令 L1 解析器 Rust 版 + 编解码 | P2-W8-02 |
| P3-W16-04 | PyO3 绑定 | CODE | P0 | `atst_native/src/python.rs` | Python 可调 Rust 解析器 + 结果与纯 Python 一致 | 02, 03 |
| P3-W16-05 | perf extra | CODE | P1 | `pyproject.toml` `[project.optional-dependencies] perf` | `pip install atst[perf]` 可用 + 无 perf 时 fallback 纯 Python | 04 |

### W17：Parity 测试 100%

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P3-W17-01 | Reader Parity 测试 | TEST | P0 | `tests/parity/test_reader_parity.py` | 纯 Python vs Rust：日线 1000 条 / 分钟线 / 板块 / 财务 全部结果一致 | P3-W16-02 |
| P3-W17-02 | Parser Parity 测试 | TEST | P0 | `tests/parity/test_parser_parity.py` | 36 命令 × 2 实现 全部结果一致 + 差异归零 | P3-W16-03 |
| P3-W17-03 | 性能基准 | TEST | P1 | `benches/bench_reader.py` / `bench_parser.py` | 日线 ≥ 200K 条/秒（Rust） + 与 v1.0 基准回归不退化 | 01, 02 |
| P3-W17-04 | 性能回归 CI | TOOL | P1 | `.github/workflows/bench.yml` | codspeed / pytest-benchmark + 性能退化 > 10% 阻断 | 03 |

### W18：HTTP REST API（32 接口）

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P3-W18-01 | FastAPI 服务骨架 | CODE | P0 | `atst/integration/http_server.py` | uvicorn + 路由 + 中间件 + 健康检查 + OpenAPI 自动文档 | P2-W15-07 |
| P3-W18-02 | 行情类接口（8） | CODE | P0 | `/api/quote` / `/api/kline` / `/api/minute` / `/api/trade` / `/api/batch-quote` / `/api/kline-all` / `/api/index` / `/api/market-stats` | 8 接口全部可用 + 输出三态（dict/JSON/DataFrame→JSON） | 01 |
| P3-W18-03 | 基本面接口（6） | CODE | P0 | `/api/workday` / `/api/income` / `/api/f10/*` / `/api/gbbq` | F10 + 财务 + 除权除息 + 工作日 | 01 |
| P3-W18-04 | 商品接口（4） | CODE | P0 | `/api/goods/quote` / `/api/goods/kline` / `/api/goods/variety` / `/api/goods/list` | 期货/期权/外汇行情 | 01 |
| P3-W18-05 | 板块接口（4） | CODE | P1 | `/api/block/list` / `/api/block/members` / `/api/block/flow` / `/api/block/rank` | MAC 协议板块相关 | 01 |
| P3-W18-06 | 系统接口（4） | CODE | P1 | `/api/server-status` / `/api/health` / `/api/tasks/*` / `/api/search` | 主站状态 + 异步任务 + 搜索 | 01 |
| P3-W18-07 | 异步任务 | CODE | P1 | `atst/integration/http_server.py` task 模块 | 下载/批量回补异步执行 + 进度查询 | 01 |
| P3-W18-08 | HTTP 契约测试 | TEST | P0 | `tests/test_http_api.py` | 32 接口全部契约测试 + 错误码 ↔ HTTP 状态映射 | 全部 |

### W19：WebSocket RPC + MCP 工具

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P3-W19-01 | WebSocket 网关 | CODE | P0 | `atst/integration/ws_server.py` | 流式 K 线/五档/逐笔推送 + 订阅/取消 JSON 协议 + 多客户端广播 | P2-W12-02 |
| P3-W19-02 | MCP stdio 服务 | CODE | P0 | `atst/integration/mcp_server.py` | stdio JSON-RPC + 10 工具 schema 注册 | P2-W15-07 |
| P3-W19-03 | MCP 10 工具实现 | CODE | P0 | `atst/integration/mcp_server.py` 工具定义 | get_quote / get_kline / get_minute / search_stock / get_f10 / get_gbbq / get_block / subscribe / get_calendar / get_server_status | 02 |
| P3-W19-04 | MCP 契约测试 | TEST | P0 | `tests/test_mcp_contracts.py` | 10 工具 schema 与实现一致 + 输入/输出验证 | 03 |

### W20：文档体系

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P3-W20-01 | API Reference 自动生成 | DOC | P0 | `docs/api/` sphinx-autodoc | 每个 public 函数/类有 docstring 段落 + `test_api_docs_complete.py` 通过 | P2-W15-07 |
| P3-W20-02 | Cookbook 20 篇 | DOC | P0 | `docs/cookbook/01-20.md` | §28.2 全部 20 选题 + 每篇代码片段可执行 | P2-W15-06 |
| P3-W20-03 | Migration Guide 3 份 | DOC | P1 | `docs/migration/from-mootdx.md` / `from-easy_tdx.md` / `from-eltdx.md` | 每份含 API 对照表 + 代码迁移示例 | 01 |
| P3-W20-04 | ADR 起 10 篇 | DOC | P1 | `docs/adr/0001-*.md` ~ `0010-*.md` | §28.3 模板 + 每篇含状态/上下文/决策/后果 | 全项目 |
| P3-W20-05 | FAQ + Troubleshooting | DOC | P2 | `docs/FAQ.md` / `docs/troubleshooting.md` | 按错误码索引 + 常见问题 20+ | P1-W6-01 |
| P3-W20-06 | 文档存在测试 | TEST | P1 | `tests/test_docs_exist.py` | §28.1 14 类文档全部存在 | 全部 |

### W21：治理文件 + Issue/PR 模板

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P3-W21-01 | GOVERNANCE.md | GOV | P1 | `GOVERNANCE.md` | 3 级角色 + 决策权重 + 升级路径 | — |
| P3-W21-02 | Code of Conduct | GOV | P1 | `CODE_OF_CONDUCT.md` | Contributor Covenant v2.1 + 举报邮箱 | — |
| P3-W21-03 | SECURITY.md | GOV | P1 | `SECURITY.md` | 版本矩阵 + 报告流程 + 披露时间表 | — |
| P3-W21-04 | Issue 模板 | GOV | P1 | `.github/ISSUE_TEMPLATE/*.yml` | Bug/Feature/Spec Gap/Question 4 模板 | — |
| P3-W21-05 | PR 模板 | GOV | P1 | `.github/PULL_REQUEST_TEMPLATE.md` | spec 链接 + 抓包来源 + 原创性自查 + 测试 + 文档 + Changelog 6 项 checklist | — |
| P3-W21-06 | 治理文件测试 | TEST | P1 | `tests/test_governance.py` | GOVERNANCE/CoC/SECURITY/PR/Issue 模板全部存在 | 全部 |

### W22：Docker + CI 完整化 + wheel 矩阵

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P3-W22-01 | Docker 镜像 | CODE | P1 | `docker/Dockerfile.python` | `docker run -p 8000:8000 atst:0.7.0` 可用 + weekly 重建 | P3-W18-01 |
| P3-W22-02 | wheel 矩阵 CI | TOOL | P0 | `.github/workflows/wheels.yml` | 4 Python × 3 OS + 2 ARM64 = 12 组合 + smoke test install | P3-W16-04 |
| P3-W22-03 | wheel smoke 测试 | TEST | P0 | `scripts/wheel_smoke.sh` | 12 组合 wheel 均能 install + import + 8 命令 MVP smoke | 02 |
| P3-W22-04 | 可观测性 zero-dep | CODE | P1 | `atst/observability/` + 3 exporter | prometheus/statsd/opentelemetry + 不安装则 no-op | P2-W15-07 |
| P3-W22-05 | 可观测性测试 | TEST | P0 | `tests/test_observability.py` | zero-dep 可启用 + 3 exporter 全部可输出 | 04 |
| P3-W22-06 | Changelog 自动化 | TOOL | P1 | `git-cliff.toml` | `git-cliff --tag 0.7.0` 生成 changelog + spec 变更单列 | — |
| P3-W22-07 | Tag v0.7.0 | GOV | P0 | git tag `v0.7.0` | CI 全绿 + 19 项验收通过 | 全部 |

**Phase 3 退出条件**：
- [ ] Rust 内核 Reader + Parser 完整 + Parity 100%
- [ ] HTTP 32 接口契约测试全过
- [ ] WebSocket RPC + MCP 10 工具契约全过
- [ ] 文档 14 类齐备 + Cookbook 20 篇可执行
- [ ] 治理文件全套 + Issue/PR 模板
- [ ] wheel 12 组合 smoke 全过
- [ ] 可观测性 zero-dep + 3 exporter
- [ ] Docker 镜像可用

---

## 7. Phase 4：协议补全 + 贯通验证 + v1.0 发布（W23-W28）

> **目标**：ProtocolProber + Profile 自动探测 + 安全合规 + 30 天冒烟 + 24 项贯通全过 + v1.0 发布。
>
> **版本目标**：`1.0.0`
> **验收锚点**：**24/24 全过**

### W23：ProtocolProber + Spec 补全

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P4-W23-01 | ProtocolProber 实现 | CODE | P1 | `atst/protocol/prober.py` | 限速 1 req/s + 仅非交易时段 + 单次上限 100 + 自动归档未知命令 | P2-W8-05 |
| P4-W23-02 | ProtocolSniffer 完善 | CODE | P1 | `atst/transport/sniff.py` | 运行时自动归档未知命令 + 生成 spec 草案 + 写入 `PROTOCOL_SPEC/UNKNOWN/` | P1-W4-06 |
| P4-W23-03 | Spec 补全（基于 Prober 产出） | SPEC | P2 | `PROTOCOL_SPEC/7709/` 补充 | 所有 Prober 发现的新命令升级为 L1 spec + status=accepted | 01, 02 |

### W24：Profile 自动探测 + 性能基准

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P4-W24-01 | ProfileDetector 六步探测 | CODE | P0 | `atst/profile/detect.py` | 记录长度→价格缩放→编码类型→成交量单位→时间编码→字符编码 + 置信度分级 | P2-W8-02 |
| P4-W24-02 | 预设 Profile 库 | CODE | P0 | `atst/profile/presets.py` | A 股/港股/美股/期货/期权/外汇/指数/基金/债券/北交所 预设全 | 01 |
| P4-W24-03 | 兼容性矩阵测试 | TEST | P0 | `tests/compatibility/test_matrix.py` | 12 市场 × 10 品种 × 12 周期 + 自动探测准确率 ≥ 99% | 01, 02 |
| P4-W24-04 | 性能基准回归 | TEST | P1 | `benches/` 全量 | Rust 日线 ≥ 200K 条/秒 + 网络编解码 < 1ms + 与 v0.7 基准不退化 | P3-W17-03 |

### W25：安全与合规边界

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P4-W25-01 | 凭据存储后端 | CODE | P1 | `atst/security/credentials.py` | system keyring → env → encrypted file 三级 + 自动选择 | P2-W15-07 |
| P4-W25-02 | 抓包法律自检 | CODE | P0 | `tools/capture.py` `_legal_self_check()` | 交易时段阻断 + CLA 签署 + 只抓自有流量 | P0-W2-02 |
| P4-W25-03 | 数据来源声明嵌入 | CODE | P1 | `atst/types.py` Bar/Quote dataclass | 所有市场数据 model 自动带 `source` + `license` + `__copyright_notice__` | — |
| P4-W25-04 | 数据导出版权注入 | TOOL | P1 | `atst data stamp` CLI | 导出 CSV/Parquet 自动附带 `LICENSE_HEADER.txt` | 03 |
| P4-W25-05 | Telemetry 脱敏 | CODE | P0 | `atst/feedback/telemetry.py` 7 步脱敏 | IP/主机名/用户名/路径/内网段/数据样本/stack 变量名 全脱 | P2-W15-02 |

### W26：文档完善 + 真实环境冒烟

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P4-W26-01 | 文档终审 | DOC | P0 | 全部文档矩阵 | 14 类文档齐备 + 无断链 + 链接全部有效 | P3-W20-06 |
| P4-W26-02 | 真实环境冒烟启动 | TEST | P0 | 冒烟脚本 + 30 天定时 | 每天自动跑 8 命令 MVP + Streaming + HTTP + MCP + 结果记录 | P3-W22-07 |
| P4-W26-03 | 冒烟监控仪表盘 | TOOL | P1 | Grafana / 简易 HTML | 30 天成功率/延迟/错误分布 可视化 | 02 |
| P4-W26-04 | Bug 收尾 | CODE | P0 | — | 冒烟期间发现的 P0/P1 bug 全部修复 | 02 |

### W27：24 项贯通验收

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P4-W27-01 | `make audit-bridges` 脚本 | TOOL | P0 | `Makefile` audit-bridges target | §32.2 全 24 项测试脚本存在 + 可执行 | 全项目 |
| P4-W27-02 | 贯通验收执行 | TEST | P0 | `make audit-bridges` 输出 | **24/24 全过** | 01 |
| P4-W27-03 | Golden 数据 ≥ 500 案例 | TEST | P0 | `tests/golden/raw/` | 覆盖所有已知 spec + 多市场/品种/周期 | P2-W8-03 |
| P4-W27-04 | 弃用策略测试 | TEST | P1 | `tests/test_deprecation.py` | 弃用标记 → 2 minor 后删除 + DeprecationWarning 一次 | P3-W22-06 |
| P4-W27-05 | 合规审计报告 | GOV | P0 | `ORIGINALITY/AUDIT_REPORT.md` | AST 相似度 + License + Spec 覆盖 + Golden 来源 全部通过 | 02 |

### W28：v1.0 正式发布

| ID | 任务 | 类型 | 优先级 | 产出物 | 验收标准 | 依赖 |
|---|---|---|---|---|---|---|
| P4-W28-01 | 发版硬门槛检查 | GOV | P0 | 发版 checklist | §32.3 全 7 项满足：24 项验收 / Golden ≥ 500 / 冒烟 30 天 / 治理 / LICENSE_HEADER / README+Quickstart+Cookbook 6 / Migration mootdx | P4-W27-02 |
| P4-W28-02 | PyPI stable 发布 | CODE | P0 | `twine upload atst-1.0.0-*` | 12 组合 wheel + GPG 签名 + `--require-hashes` 可校验 | 01 |
| P4-W28-03 | Docker Hub 发布 | CODE | P0 | `docker push atst:1.0.0` | 多标签（1.0/1.0.0/latest） + README 自动同步 | 02 |
| P4-W28-04 | 发布公告 | DOC | P0 | GitHub Release + Blog | Changelog + 迁移指南 + 亮点 + 已知限制 | 02, 03 |
| P4-W28-05 | Tag v1.0.0 | GOV | P0 | git tag `v1.0.0` | — | 全部 |

**Phase 4 退出条件**：
- [ ] ProtocolProber 安全运行（限速 + 非交易时段）
- [ ] ProfileDetector 六步探测 + 兼容矩阵 ≥ 99%
- [ ] 安全合规：凭据存储 + 抓包自检 + 来源嵌入 + 7 步脱敏
- [ ] 真实环境冒烟 30 天无 P0 bug
- [ ] **24/24 贯通验收全过**
- [ ] Golden ≥ 500 案例
- [ ] PyPI + Docker Hub 发布

---

## 8. 依赖关系矩阵

### 8.1 关键路径

```
P0-W1-01(骨架) → P0-W1-02(Spec目录) → P0-W1-03(报文Spec) → P0-W2-01(Codegen)
     → P1-W3-01(报文编解码) → P1-W3-03(BaseParser) → P1-W4-03(8命令L1)
     → P2-W8-02(36命令) → P2-W12-02(PushChannel) → P2-W13-01(run_sync)
     → P3-W16-03(Rust Parser) → P3-W17-02(Parity) → P3-W18-02(HTTP)
     → P4-W27-02(24项验收) → P4-W28-02(PyPI) → v1.0.0
```

### 8.2 跨阶段依赖

| 下游任务 | 依赖上游 | 说明 |
|---|---|---|
| P1-W4-03（8 命令 L1） | P0-W2-03（Golden 数据） | 无 Golden 无法验证 round-trip |
| P2-W8-02（36 命令） | P1-W4-05/06（L2/L3） | 36 命令中可能有未知命令走 L2 |
| P2-W12-05（GapFiller） | P2-W12-04（DeltaMerger） | 补数依赖增量合并的版本号 |
| P3-W16-03（Rust Parser） | P2-W8-02（Python 36 命令） | Rust 移植前提是 Python 版稳定 |
| P3-W18-02（HTTP 8 接口） | P2-W15-05（Phase 2 集成） | HTTP 依赖底层全协议 |
| P4-W27-02（24 项验收） | 全部 | 贯通验收依赖全链路完成 |
| P4-W28-02（PyPI） | P4-W27-02 + P3-W22-03（wheel） | 发版依赖验收 + wheel |

### 8.3 可并行任务

| 可并行组 | 任务 | 说明 |
|---|---|---|
| A | P0-W2-04（配置 Schema）+ P0-W2-05（配置加载） | 与 Spec/Codegen 并行 |
| B | P1-W6-01（错误树）+ P1-W7-01（i18n）+ P1-W7-03（日历） | 3 个横切可并行 |
| C | P2-W9（7727）+ P2-W10（MAC）+ P2-W11（F10） | 3 套协议可并行（不同人/不同主站） |
| D | P3-W20（文档）+ P3-W21（治理）+ P3-W22（Docker） | 非代码可并行 |
| E | P4-W23（Prober）+ P4-W24（Profile）+ P4-W25（安全） | 3 个独立方向并行 |
| **F** | **P2-W14-08 ~ W14-16（HTTP Web 源 7 Adapter + 归一化 + 限流 + 兼容垫片 + 测试）** | **纯 HTTP，不依赖 TDX 二进制协议层，可由集成工程师从 W5 起提前并行，不占关键路径** |

---

## 9. 风险登记册

| ID | 风险 | 概率 | 影响 | 缓解措施 | 触发条件 |
|---|---|---|---|---|---|
| R1 | MAC 主站仅 3 台，Golden 采集困难 | 中 | 高 | 非交易时段多次采集 + 跨日采集 + 容许 1 样本/命令 | W10 采集 < 10 命令 |
| R2 | 未知命令 L2 通用解析准确率不足 | 低 | 中 | 置信度 < 0.5 报错 + 不静默吞 + 上报通道反馈 | W8 测试 L2 准确率 < 80% |
| R3 | Rust 内核 Parity 无法 100% 一致 | 低 | 高 | 差异字段标注 + 逐字段排查 + 不一致字段单独 spec | W17 Parity 测试有 fail |
| R4 | 公共主站限流/封 IP | 中 | 高 | 限流器 + 多主站 failover + 冒烟降速 | W26 冒烟连续 3 天被封 |
| R5 | spec 字段口径分歧无法收敛 | 中 | 中 | coefficient 可配置 + Golden 多市场验证 + 标注口径来源 | W8 兼容矩阵 < 99% |
| R6 | 28 周工期不足 | 中 | 高 | P3/P4 低优先级可裁剪 + Phase 4 W23-W24 可压缩 | W15 延迟 > 1 周 |
| R7 | eltdx License 纠纷 | 低 | 高 | 绝不引入 eltdx 代码 + 仅作协议事实参考 + CI 扫描 | 收到 eltdx 投诉 |
| R8 | PyO3/maturin 多平台 wheel 编译失败 | 中 | 中 | cibuildwheel + ARM64 交叉编译 + 失败降级纯 Python | W22 wheel < 12 组合 |
| R9 | 交易日历更新滞后（临时休市未覆盖） | 中 | 低 | 启动联网更新 + 内置 fallback + 临时休市检测 | 冒烟期间遇临时休市 |
| R10 | Streaming push 队列内存溢出 | 低 | 高 | raw 预算 256MiB + 85% 触发淘汰 + BackpressureController | W12 压测溢出 |
| **R11** | **HTTP Web 源接口变更/下线（新浪 Referer、东财 push2 改版）** | **高** | **中** | 7 源多活互为备份 + 契约测试每日 CI 巡检 + 失败自动切源 + `source` 字段降级不静默 | W14b 后任一源契约测试连续 3 天失败 |
| **R12** | **HTTP Web 源反爬封 IP（东财高频 20h+ 封禁）** | **中** | **高** | 按源限流（东财串行 1 req/s）+ 空响应检测降级 + 可选 DoH + 默认 TDX 主路径优先 | W14b 压测触发封禁 |
| **R13** | **volume/amount 口径漂移（手 vs 股、万元 vs 元）** | **中** | **高** | `normalize.py` 集中归一化 + 4 case 契约测试常驻 CI + 用 `amount/volume` 反推均价校验 | W14b 归一化测试 fail 或均价偏离 > 5% |

---

## 10. 资源与角色

### 10.1 角色矩阵

| 角色 | 人数 | 职责 | 所需技能 |
|---|---|---|---|
| **维护者 / Lead** | 1 | 路线图决策、PR 合并、版本发布 | Python / Rust / TDX 协议 / PyO3 |
| **A 组 Spec 分析师** | 1 | 抓包 + 文件分析 + 编写 PROTOCOL_SPEC | 二进制分析 / 网络抓包 / GBK |
| **B 组 协议实现工程师** | 1-2 | 从 Spec 独立编写 parser / Reader / Client | Python / 结构化编程 / 测试 |
| **Streaming 工程师** | 1 | push/轮询/增量合并/断线补数/背压/重连 | asyncio / WebSocket / 背压 |
| **Rust 工程师** | 1 | Rust 内核 + PyO3 绑定 + Parity | Rust / pyo3 / maturin / cibuildwheel |
| **集成工程师** | 1 | HTTP / WebSocket / MCP / Docker / CI | FastAPI / uvicorn / MCP / Docker |
| **测试工程师** | 1 | Golden / Fuzz / Parity / 贯通验收 / 冒烟 | pytest / hypothesis / pytest-benchmark |
| **文档 / 治理** | 0.5 | Cookbook / Migration / ADR / 治理文件 | 技术写作 / sphinx / git-cliff |

> **最小可行团队**：3 人（Lead 兼 A 组 + B 组 1 人 + 测试 1 人），Phase 3 起加 Rust 工程师。

### 10.2 环境清单

| 项 | 要求 |
|---|---|
| Python | 3.10 / 3.11 / 3.12 / 3.13（cibuildwheel 矩阵） |
| Rust | 1.83+（Phase 3 起） |
| OS | Windows x64 / macOS x64+ARM64 / Linux x64+ARM64 |
| 网络 | 非交易时段可连公共 TDX 主站 |
| 本地通达信 | 安装版，有 `vipdoc/` 目录（Golden 采集用） |
| CI | GitHub Actions（Linux/Windows/macOS runner） |
| PyPI / Docker Hub | 账号 + GPG 签名密钥 |

---

## 11. 质量门禁

### 11.1 每次提交（pre-commit + CI）

| 门禁 | 工具 | 阻断阈值 |
|---|---|---|
| 代码风格 | ruff | 任何违规 |
| 类型检查 | mypy --strict | 任何错误 |
| 单元测试 | pytest | 覆盖率 < 90% 阻断 |
| 原创性 AST | `tools/check_originality.py` | 相似度 > 0.70 阻断 |
| License 扫描 | `tools/check_originality.py` | 非 --allowlist 依赖阻断 |
| Spec 覆盖 | `tools/spec_audit.py` | 缺 spec 的 parser 阻断 |

### 11.2 每周发版（Sprint Demo 前）

| 门禁 | 要求 |
|---|---|
| CI 4 job 全绿 | lint + type + test + originality |
| 新功能 Golden round-trip | 新增 parser 必须有 Golden 验证 |
| 集成测试 | 本 Sprint 新增功能的集成测试全过 |
| Sprint Demo | 可运行 demo + 截图/录屏 |

### 11.3 每阶段发版

| 门禁 | 要求 |
|---|---|
| 对应阶段验收锚点 | §32 对应项全过 |
| Parity 测试（Phase 3 起） | 纯 Python vs Rust 100% 一致 |
| 性能基准（Phase 3 起） | 与上一版本不退化 > 10% |
| 文档更新 | 本阶段新功能文档齐备 |

### 11.4 v1.0 发版硬门槛

参见 §32.3，7 项缺一不可。

---

## 12. 周会节奏

| 会议 | 频率 | 时长 | 议题 |
|---|---|---|---|
| 日站会 | 每天 | 15min | 昨日完成 / 今日计划 / 阻塞 |
| 周中检查 | 周三 | 30min | 进度 vs 计划 / 风险更新 |
| Sprint Demo | 周五 | 60min | 本周可运行 demo + 评审 |
| Sprint 回顾 | 周五 | 30min | 做得好 / 做得不好 / 改进 |
| 阶段评审 | 阶段末 | 120min | 验收锚点检查 / 下阶段启动 |

---

## 附录：版本号映射

| 周 | 开发版本 | 发版标签 |
|---|---|---|
| W1 | 0.1.0-dev | — |
| W2 | 0.1.0 | **v0.1.0** |
| W3-W6 | 0.2.0-dev ~ 0.2.4-dev | — |
| W7 | 0.2.0 | **v0.2.0** |
| W8-W14 | 0.3.0-dev ~ 0.3.7-dev | — |
| W15 | 0.4.0 | **v0.4.0** |
| W16-W21 | 0.5.0-dev ~ 0.6.1-dev | — |
| W22 | 0.7.0 | **v0.7.0** |
| W23-W26 | 0.8.0-dev ~ 0.9.3-dev | — |
| W27 | 0.9.0 (RC) | **v0.9.0-rc** |
| W28 | 1.0.0 | **v1.0.0** |

---

> **下一步**：确认本计划后，从 W1 第一个任务 `P0-W1-01 初始化项目骨架` 开始执行。
