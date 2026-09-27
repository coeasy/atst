# 配置（configuration）

> 本文描述**当前生效**的配置面。口径只有一条：**配置面即执行面契约**——
> 这里存在的每一个键都必须被单一内核读取并改变行为；写一个不改变任何行为的键，
> 比不写更糟。因此本 Schema 只覆盖执行参数，且拼错的键立即报错。
> 对应债务清偿记录：V17 Phase 6（F-13/F-16），决策见
> [ADR-016](adr/ADR-016-config-surface-covers-execution-only.md)。

## 1. 配置如何进入执行链

```
tstdx.toml / 环境变量 / 入参
        │  load_config()：6 源合并 → Config.validate()
        ▼
tstdx.get_config()（进程级单例，首次访问时惰性解析一次）
        ▼
UnifiedRuntime ──► QueryPlanner(default_provider)
        │           DirectProviderExecutor(timeout / hosts / vipdoc_root / config)
        │                └─► tstdx.transport.pool.pool_settings_from_config(cfg)
        │                     —— 配置面到传输面的**唯一**翻译点 ——
        │                     └─► TdxClient → ConnectionPool（槽位/心跳/重试/限流/TLS）
        ▼
WebQuoteClient（独立读取 web.* 段：源清单/超时/重试/按源限流）
```

`Client()` / `AsyncClient()` 无参构造即走这条链；显式入参优先于配置：

```python
from tstdx import Client

with Client() as c:  # 读 tstdx.toml + 环境变量
    ...

with Client(timeout=12.0, hosts=["119.147.212.81:443"]) as c:  # 入参覆盖配置
    ...

with Client(config=my_config) as c:  # 整份 Config 注入（跳过进程级单例）
    ...
```

## 2. 六个来源与优先级

优先级高 → 低（`tstdx.config.load_config`）：

| # | 来源 | 形态 |
|---|---|---|
| 1 | 函数入参 | `load_config(overrides={"core": {"timeout": 5}})`、`tstdx.configure(core={"timeout": 5})` |
| 2 | 环境变量 | `TSTDX_CORE_TIMEOUT=5`、`TSTDX_WEB_ENABLED_SOURCES=tencent,sina` |
| 3 | 项目配置 | `./tstdx.toml` 或 `./.tstdx.toml`（自 CWD 向上最多 5 层，只取最近一层） |
| 4 | 用户配置 | `~/.tstdx/config.toml` |
| 5 | 系统配置 | `/etc/tstdx/config.toml`；Windows `%PROGRAMDATA%\tstdx\config.toml` |
| 6 | 内置默认 | `tstdx.config.DEFAULT_CONFIG` |

`TSTDX_CONFIG_FILE=<path>` 显式指定文件源路径，是文件源内的最高优先级；指向不存在
的路径或目录时**立即抛 `ConfigError`**，不静默回退到其余文件源。

段内嵌套字典（如 `web.rate_limit`）按**深合并**：高优先级层的键胜出，未覆盖的键保留。

## 3. 键清单（全部，共 5 段）

### `[core]` — Provider 选择与执行预算

| 键 | 默认 | 取值范围 | 读取方 / 效果 |
|---|---|---|---|
| `default_provider` | `"tdx"` | 必须是 `PROVIDERS` 注册表已登记 id | `QueryPlanner`：查询未显式传 provider 时选谁 |
| `timeout` | `5.0` | 0.1 – 300 | `DirectProviderExecutor` → `TdxClient` 请求超时（秒）。它是**一次读写的墙钟**，不是"两次收包之间的空档"：同步池与异步池在同一份声明上口径一致（第 31 轮之前同步面只有逐次空档超时，滴流对端能量不到它） |
| `heartbeat_interval` | `30` | 0 – 3600（`0` = 不启心跳） | 心跳 + 空闲槽位回收那一条线程的节奏（秒）。同步 `ConnectionPool` 在构造器里武装；异步 `AsyncConnectionPool` 在第一次握住真 socket 时武装（`_get_conn_locked`），构造与 `open()` 都不武装 |
| `max_retries` | `3` | 0 – 20 | **同一 Provider 内** host/endpoint 的重试预算，不是切换 Provider 的次数 |
| `vipdoc_root` | `None` | 非空字符串或 `None` | `local_vipdoc` Provider 与 `adjusted_bars`/`sync_daily` 的本地数据根目录；缺失时相关 capability 直接抛 `ValidationError` |

### `[hosts]` — TDX 主站与连接槽位

| 键 | 默认 | 取值范围 | 读取方 / 效果 |
|---|---|---|---|
| `servers` | `[]` | `[["host", port], …]`，每项须能被 `tstdx.transport.hosts.parse_server` 解析 | 空 = 使用内置候选池；非空 = 只用这些主站 |
| `slots_per_host` | `4` | 1 – 64 | 每台主站的 TCP 连接数（池大小 = hosts × slots_per_host） |

### `[rate_limit]` — 本地请求限流（req/s，按交易状态分档）

字段名与 `tstdx.transport.ratelimit.SessionState` 一一对应。

| 键 | 默认 | 交易状态 |
|---|---|---|
| `call_auction` | `80` | 集合竞价 |
| `continuous` | `120` | 连续竞价 |
| `noon_break` | `25` | 午休 |
| `closed` | `15` | 休市 |
| `strict` | `false` | `true`：令牌不足立即抛 `RateLimitedLocal`；`false`：阻塞等待 |

取值范围均为 1 – 1000。

### `[web]` — legacy `WebQuoteClient` 段

| 键 | 默认 | 读取方 / 效果 |
|---|---|---|
| `enabled_sources` | `["tencent", "sina", "eastmoney"]` | `WebQuoteClient` 缺省源顺序。每项须属于 `tstdx.web.sources.KNOWN_SOURCES` |
| `timeout` | `5.0` | 0.5 – 120，HTTP 超时 |
| `max_retries` | `2` | 0 – 10 |
| `rate_limit` | `{}` | 按源名区分的 req/s；键须属于 `KNOWN_SOURCES`，值 1 – 1000；未列出的源沿用各适配器默认 |

> `enabled_sources` **不是** TDX 失败后的 fallback 顺序。单一内核经 `QueryPlanner`
> 直接选择 `tencent/sina/eastmoney/...` 的 Provider channel；跨源只允许显式
> `FallbackPolicy`（见 `docs/ARCHITECTURE.md` §2）。

### `[security]` — 传输加密

| 键 | 默认 | 读取方 / 效果 |
|---|---|---|
| `use_tls` | `false` | `ConnectionPool`：标准族 TCP 是否包裹 TLS。主流 TDX 站点明文服务，故默认关闭 |

### 传输池参数：哪些**不**经配置面

`ConnectionPool` 的构造参数共 17 个，配置面只翻译其中 6 个（`slots_per_host`、`timeout`、
`heartbeat_interval`、`max_retries`、`rate_limit`（→ `rate_limiter`）、`use_tls`）。下表这 11 个
**没有配置键**，写进 TOML 也不会生效——它们要么由调用方按次决定，要么是刻意留给手工建池的调优口。
名单（下表第一列）由 `tests/architecture/test_pool_knob_reachability.py` 与本表双向核对：签名里
有、表里没有 ⇒ 红，表里有、签名里没有 ⇒ 红。"由谁给值"这一列只有 `family` 一行被现读核对（判据
从 `TdxClient.__init__` 的默认值读它），其余格子是写给人看的理由，没有尺子指向。

| 参数 | 由谁给值 | 口径 |
|---|---|---|
| `hosts` | 调用方 | 主站清单来自 `[hosts] servers`（空则内置候选池），但作为位置参数按次传入，不是池自己读配置 |
| `family` | 公开 API | `get_client(family=...)` / `TdxClient(family=...)` 按次选择协议族 |
| `connect_timeout` | 手工建池 | 建连超时与读写超时分离（默认 2s，故障转移时快速跳下一台） |
| `heartbeat_cmd` | 手工建池 | 心跳探测用的命令号，缺省 `DEFAULT_HEARTBEAT_CMD = 0x0002`。该码**不在** 7709 账本内，而账本里的 `0x0004 HEARTBEAT` 本包没有默认发送方（只有 `tstdx probe 0x0004` 会显式发出）。2026-09-26 真机实测两条都答（0x0002 回 50 字节、0x0004 回 10 字节无结构载荷），探活只判通畅、不解析响应，故刻意保持 0x0002 不动（台账 F-20 已清偿；字节证据见 `PROTOCOL_SPEC/7709/0x0004_HEARTBEAT.yaml` 的 `measured` 块，对账判据见 `tests/protocol/test_heartbeat_claim_evidence.py`） |
| `spec` | 手工建池 | 帧头长度规格；缺省由 `codec/framing.py` 的单一 `FrameSpec` 推断 |
| `keepalive` | 手工建池 | 是否发送 TCP keepalive |
| `handshake` | 手工建池 | `None` 时按协议族推断（标准族/MAC 需要握手，扩展市场不需要） |
| `handshake_strict` | 手工建池 | 握手失败是否让连接失败，而不是宽容放行 |
| `on_host_down` | 手工建池 | 主站判死后的回调钩子（可观察性用，库不替宿主做事） |
| `speedtest_threshold` | 手工建池 | 多少次失败后触发重测速 |
| `idle_timeout` | 手工建池 | 空闲槽位回收秒数（默认 300s），下次使用惰性重建 |

想改这 11 个参数，走 `TdxClient(...)` / `ConnectionPool(...)` 显式构造；把它们接进 `Config`
是一次对外契约扩张（会新增段与键、并让 `docs/ARCHITECTURE.md` §2 的"配置面即执行面契约"
覆盖面变大），本方案不擅自做，登记为待决项。

## 4. 环境变量规则

命名：`TSTDX_<SECTION>_<KEY>`，全大写。段名按最长前缀切分，因此
`TSTDX_RATE_LIMIT_CONTINUOUS` 指向 `rate_limit.continuous` 而不是不存在的 `rate` 段。

值解析顺序：JSON（`[`/`{` 开头）→ bool（`true/false/yes/no/on/off`，不区分大小写）
→ int → float → 逗号分隔列表 → 字符串。`"1"`/`"0"` 解析为**整数**而非 bool，
否则 `TSTDX_CORE_MAX_RETRIES=1` 会撞.bool 校验。

专用 runtime 变量**不属于** schema 命名空间，不参与 strict 扫描：

| 变量 | 消费者 | 作用 |
|---|---|---|
| `TSTDX_CONFIG_FILE` | `find_config_files` | 显式配置文件路径 |
| `TSTDX_HOSTS` | `tstdx.transport.hosts` | 直接给主站列表（`host:port,host:port`） |
| `TSTDX_FEEDBACK` / `TSTDX_FEEDBACK_ENDPOINT` / `TSTDX_FEEDBACK_STORE_DIR` | `tstdx.feedback` | 反馈数据的传输开关与落点 |
| `TSTDX_WENCAI_COOKIE` | `tstdx.web.wencai` | i问财 `hexin-v` cookie（值写成 `v=<token>` 头）；本库不存储它 |

`TSTDX_` 前缀是**保留命名空间**：库内代码从环境读取的每一个非 schema 变量都必须登记在
`tstdx/config/loader.py` 的 `_RUNTIME_ENV_KEYS`，否则用户一旦设置它，strict 扫描就把整条
配置加载判成拼写错误而 fail closed。`TSTDX_WENCAI_COOKIE` 曾漏登记：问财缺 cookie 时的
错误消息让用户"设置 `TSTDX_WENCAI_COOKIE`"，而照做之后 `Client()` 直接抛
`ConfigError`——按自己的指引修自己修不好的错（V17 第 43 步，F-69）。测试与工具用的开关
因此**不得**占用该前缀。

## 5. fail-closed：不认识的键一律报错

| 场景 | 结果 |
|---|---|
| 未知配置段（`[cache]`） | `ValidationError: config 含未知配置段: ['cache']；可选: ['core', 'hosts', 'rate_limit', 'web', 'security']` |
| 段内未知字段 | `ValidationError`，消息给出该段可选字段 |
| 未知 `TSTDX_*` 变量 | `ConfigError: 无法识别环境变量 …` |
| bool 位置写 `1`/`0` | `ValidationError: … 必须是 bool`（数值不算 bool） |
| 整数位置写 `1.5` | `ValidationError: … 必须是整数` |
| `timeout=nan/inf` | `ValidationError: … 必须是有限数值` |
| `default_provider` 不在注册表 | `ValidationError: 未知 provider …` |
| 配置文件语法错误 / 是目录 | `ConfigError`，不降级为默认值 |

已删除、**写了会立即报错**的段：`cache`、`output`、`profile`、`sources`、
`observability`、`compatibility`、`feedback`。理由：内核数据请求零缓存、
零跨 Provider 静默降级，输出格式由调用点决定，可观测性由 `tstdx.observability`
与 `tstdx.feedback` 的显式 API 驱动——这些键放在配置面里不会改变任何行为。

## 6. 编程接口

```python
import tstdx
from tstdx.config import Config, get_config, load_config, reset_config, set_config

cfg = load_config(verbose=True)  # 打印各层来源（调试）
print(cfg.core.timeout, cfg.hosts.slots_per_host)

tstdx.configure(core={"timeout": 8})  # 覆盖并写回进程级单例
get_config().core.timeout              # 8.0

set_config(Config())                   # 注入整份配置（会先 validate）
reset_config()                         # 清空单例：下一次 get_config() 重新读全部源
```

`tstdx.configure()` 的返回值就是合并后的 `Config`，同时已写回单例——
后续 `Client()` 与 `WebQuoteClient()` 都会读到它，显式构造参数仍然优先。

## 7. 回归锁定

| 事实 | 测试 |
|---|---|
| 本文 §3 的键清单/默认值/取值范围、§4 的环境变量命名与专用变量表、§5 的报错消息，逐项对上 schema 与 loader | `tests/architecture/test_config_doc_contract.py` |
| 写 `./tstdx.toml` ⇒ `Client()`/内核/传输层参数与文件一致 | `tests/runtime/test_kernel_config_wiring.py` |
| `Config` 只有 5 段、阈值数字单源 | `tests/config/test_merge.py`、`tests/compatibility/test_local_gate_contract.py` |
| 配置 → 池构造参数只有一个翻译点 | `tests/transport/test_pool_settings_from_config_contract.py` |
| 限流字段名与 `SessionState` 对齐（幻影键名报错） | `tests/transport/test_ratelimit_contract.py` |
| strict 校验语义 | `tests/config/test_schema_semantics.py`、`tests/config/test_schema_strict_fields.py` |
