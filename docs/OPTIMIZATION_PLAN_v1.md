# tstdx 功能梳理与优化改进方案（v1，2026-09-02）

> **基线**：v1.0.0 收口（24/24 验收项通过）、728+ 测试全绿、Rust 内核已对齐（K 线解析 \~8.4×、.day 读取 \~5.8×）、
> TDX 主站候选修复（默认 8 台候选 + 故障转移覆盖全池）、三源独立调用（TDX / 新浪 / 腾讯）实测可用。
> 本文档基于对 `tstdx/` 全部模块源码的梳理，给出下一阶段的优化改进路线。

***

## 1. 项目主要功能与逻辑实现梳理

### 1.1 架构总览（六层）

```
┌────────────────────────────────────────────────────────────┐
│ 集成层  cli / http_server / ws_server / mcp_server          │
├────────────────────────────────────────────────────────────┤
│ 兼容层  compat.easy_tdx / eltdx / mootdx + web.easyquotation │
├────────────────────────────────────────────────────────────┤
│ 门面层  sources.DataSourceRouter（tdx→web→reader→cache 降级）│
├───────────────────────┬────────────────────────────────────┤
│ 在线：TDX 原生协议     │ 离线+HTTP：reader(vipdoc) / web(7源) │
│  client.TdxClient     │  reader.formats + web.adapters      │
│  transport.pool/hosts │  streaming.engine / sinks           │
├───────────────────────┴────────────────────────────────────┤
│ 协议层  codec.framing/primitive → protocol.parsers（61 处）  │
├────────────────────────────────────────────────────────────┤
│ 支撑层  domain(模型/复权/日历) config errors i18n observability│
│        feedback security profile tools native(Rust 加速)    │
└────────────────────────────────────────────────────────────┘
```

### 1.2 各模块核心逻辑

| 模块                                                                  | 主要功能       | 关键实现手法                                                                                                                    |
| ------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------- |
| [codec/framing.py](../tstdx/codec/framing.py)                       | 请求/响应分帧    | `FrameSpec` 可配置帧布局（兼容非标主站）；12 字节请求头 + zlib 解压                                                                             |
| [codec/primitive.py](../tstdx/codec/primitive.py)                   | 解码原语       | LEB128 有符号变长价格差分、TDX 4 字节自定义浮点（logpoint/hleax）、6-bit 变长整数；`BinaryReader` 统一读取入口                                           |
| [protocol/parsers/std7709.py](../tstdx/protocol/parsers/std7709.py) | 核心命令解析     | `SecurityBarsParser`（0x052D）：datetime + 4×LEB128 差分价格 + tdx\_float 量/额，基准还原 + 单位归一 + 指数涨跌家数；注册表分发（61 处解析器，85 条命令账本）       |
| [transport/pool.py](../tstdx/transport/pool.py)                     | 连接池/故障转移   | Slot = hosts × slots\_per\_host；健康分（rtt + 失败指数惩罚）+ 轮询选槽；`RetryAdvice` 驱动换槽/换主站；尝试次数至少覆盖池内每台主站（2026-09-01 修复）              |
| [transport/hosts.py](../tstdx/transport/hosts.py)                   | 主站候选/排名    | 用户配置 > 排名文件（`~/.tstdx/server_ranking.json`）> 内置池（默认取 8 台）；失败衰减不删除                                                         |
| [client/](../tstdx/client/)                                         | 同步/异步客户端   | 语义化方法（`bars/quotes/finance_info…`）封装「命令号+请求体+解析上下文」；三态输出 dict/tuple/dataframe；异步镜像签名一致                                    |
| [reader/formats.py](../tstdx/reader/formats.py)                     | 本地 vipdoc  | .day（32B/条：u32 日期 + 4×u32 价格×scale + f32 额 + u32 量）/ .lc1/.lc5（lc16 日期编码）；`DataProfile` + `ProfileDetector` 自动探测规格，不硬编码假设 |
| [web/base.py](../tstdx/web/base.py)                                 | HTTP 源基础设施 | 零依赖 urllib / 可选 httpx；进程级共享令牌桶（跨实例共享防封配额）；传输/解析双失败桶（20/40 次）触发 `SourceDeprecated` 熔断；指数退避+抖动封顶 8s                         |
| [web/adapters.py](../tstdx/web/adapters.py)                         | 7 源适配器     | 新浪（Referer 必须）、腾讯（手/万元→股/元归一）、东财（×100 整数价格、3 主机 failover）、集思录/港股/K线/中行外汇；`fetch_all` 分页拉全市场                               |
| [web/normalize.py](../tstdx/web/normalize.py)                       | 口径归一       | 源级注册 `VolumeNormalizer`，统一到全局契约「元/股/元」                                                                                    |
| ~~web/easyquotation.py~~（未实施，无对应文件）                      | 竞品兼容垫片     | `use('sina'/'tencent'/...)` 独立调用；`real/stocks/all/get_klines/get_stock_market/get_index` 对齐原库 API                         |
| [sources/__init__.py](../tstdx/sources/__init__.py)                 | 多源路由       | `DataSourceRouter`：按配置顺序 `tdx → web → reader → cache → synthetic`，单源失败自动降级不中断                                             |
| [streaming/engine.py](../tstdx/streaming/engine.py)                 | 流式订阅       | 后台线程轮询 + 订阅管理 + Gap 检测（`GapUnfilledError`）+ 背压（`BackpressureOverflow`）                                                    |
| [native.py](../tstdx/native.py)                                     | Rust 内核加载  | 导入 + 能力自检 + 回退门控：自检通过自动启用（0x052D K 线、.day 读取），失败透明回退纯 Python                                                              |
| [observability/metrics.py](../tstdx/observability/metrics.py)       | 可观测        | Prometheus / statsd / OTel 导出；连接池 stats（requests/failures/host\_switches）                                                 |
| [config/schema.py](../tstdx/config/schema.py)                       | 配置         | dataclass schema + 校验（范围钳制）；高/低优先级合并                                                                                      |

### 1.3 已知遗留（非代码问题）

- PyPI / Docker Hub 发布：构建链路已验证，**待凭据**。

- 30 天真实环境冒烟：`ops/smoke_30d.py` 就绪，**待长期运行**。

- ARM64 wheels：CI 矩阵已配 cibuildwheel + 失败降级，待真机验证。

***

## 2. 优化改进方案

按「性能与内存 → 可靠性 → 易用性 → 功能覆盖 → 工程质量」五个主题组织，每项标注优先级（P0 高收益短期 / P1 重要中期 / P2 长期）与验证方式。工作量以低/中/高表示。

### 2.1 性能与内存优化（用户核心关注）

| #  | 优化项                         | 现状与问题                                                                                       | 方案                                                                                                                                                 | 优先级    | 工作量 |
| -- | --------------------------- | ------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ------ | --- |
| M1 | **Rust 内核热路径扩展**            | native 已覆盖 0x052D K 线（8.4×）与 .day 读取（5.8×）；0x0530 实时行情、0x0537 分时、0x0FC5/0x0FC6 逐笔仍走纯 Python | 按 golden 对拍流程逐命令移植：优先 0x0537 分时（数据量大、字段规整），其次 0x0530；沿用 `native.py` 自检门控模式                                                                         | **P0** | 中   |
| M2 | **Bar/Quote 内存布局**          | domain 模型逐行构造 dataclass 对象；`as_format="dict"` 时再逐行 `asdict()` 产生第二份拷贝                       | ① 确认模型已用 `__slots__`（无则加上，省 \~40% 实例内存）；② `as_format="dict"` 改为生成器/惰性转换避免中间 list；③ 大批量接口提供 `as_format="dataframe"` 直达（用数组列构造而非逐行 DataFrame append） | **P0** | 低   |
| M3 | **`fetch_all`** **全市场分页并发** | 新浪 `getHQNodeData` 串行逐页拉取（80 条/页 × \~70 页），限速 8 req/s 下全市场一轮约 9s+                           | 在限流约束内做 2–3 路并发分页（先取第 1 页解析 total，再分片并发）；令牌桶天然控速不会超配额                                                                                              | P1     | 低   |
| M4 | **`quotes()`** **批量行情吞吐**   | 0x0530 服务端约束一次一只，当前**同步串行**逐只请求；拉 500 只 = 500 RTT                                           | 用连接池多槽位（slots\_per\_host=4 × 8 主机）做受控并发（ThreadPool 4–8 并发），吞吐提升 4–8×；失败隔离逻辑保持不变                                                                    | **P0** | 中   |
| M5 | **urllib 后端连接复用**           | 零依赖 `UrllibClient` 每请求新建 TCP（无 keep-alive 生效路径）                                             | 基于 `http.client.HTTPConnection` 实现带连接复用的零依赖后端；装了 httpx 的用户不受影响                                                                                     | P2     | 低   |
| M6 | **连接池资源占用**                 | 修复后默认 8 主机 × 4 槽 = 32 潜在连接 + 30s 心跳                                                         | 惰性建连已有；增加「空闲槽位回收」（如 5 分钟未用即关连接），高峰自动重建                                                                                                             | P1     | 低   |
| M7 | **响应体解码零拷贝**                | `HttpResponse.text()` 每次全量 decode；gzip 解压产生 raw+decoded 两份 bytes                            | 大响应路径（全市场、K 线）提供 `resp.json()` 直接 `json.loads(bytes)`（Python 自动处理 BOM/编码），跳过中间 str                                                                 | P2     | 低   |

### 2.2 连接可靠性（用户核心关注）

| #  | 优化项             | 现状与问题                                                                                  | 方案                                                             | 优先级    | 工作量 |
| -- | --------------- | -------------------------------------------------------------------------------------- | -------------------------------------------------------------- | ------ | --- |
| R1 | **启动后台测速**      | `auto_speedtest=True` 只控制「读排名文件」，**并不会**在冷启动时实际测速；首日用户体验 = 内置池顺序（前 5 台可能全部不可达，靠故障转移硬扛） | 首次建连失败达到阈值（如 3 次）后，异步触发一次 `speedtest` 并写排名文件；后续启动直接按真实 RTT 排序  | **P0** | 中   |
| R2 | **心跳自适应**       | 固定 30s 心跳；部分主站 60s+ 才断空闲连接，导致心跳成功但业务帧随机失败                                              | 记录每主机「业务帧失败率」，失败率高的主机主动降权（已有 score 机制，补业务级信号）                  | P1     | 低   |
| R3 | **东财主机池健康度持久化** | `_EastmoneyJson.HOSTS` 3 主机 failover 但无记忆，每次调用都从 push2 开始试                             | 失败主机记入进程级黑名单（TTL 10 分钟），期间直接从备站开始                              | P1     | 低   |
| R4 | **流式引擎断线恢复**    | Gap 检测有（`GapUnfilledError`），但重连后补洞依赖下一轮轮询                                              | 断线重连成功后立即触发一次定向补拉（针对订阅标的的缺口区间），而不是等下个周期                        | P1     | 中   |
| R5 | **30 天冒烟落地**    | 脚本就绪未运行                                                                                | 配置每日定时执行 `ops/smoke_30d.py`，结果写 `ops/smoke_results.jsonl`，异常告警 | **P0** | 低   |

### 2.3 易用性 / UX（用户核心关注：简化安装与配置）

| #  | 优化项                    | 现状与问题                                                                                  | 方案                                                                                                                                        | 优先级    | 工作量 |
| -- | ---------------------- | -------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------ | --- |
| U1 | **开箱即用首体验**            | `pip install tstdx` 后 `TdxClient()` 依赖内置池顺序，首请求可能经历 5 次失败重试（每次 connect 超时 3–5s）才命中可用主站 | ① 把实测可达的 7 台主站**前置**到内置池排序（把 2026-08-31 连通性测试结果固化进 `DEFAULT_HOST_POOL` 顺序）；② connect\_timeout 与 timeout 分离并默认调小 connect 超时（如 2s），失败快速跳下一台 | **P0** | 低   |
| U2 | **错误信息可操作性**           | `AllHostsUnreachable` 已列出尝试过的主机，但未给用户下一步建议                                             | 错误消息附「下一步」：提示 `tstdx hosts scan` 命令与配置 `hosts.servers` 示例（一条可复制的配置片段）                                                                     | P1     | 低   |
| U3 | **配置即用**               | 配置体系完整但文档分散                                                                            | 在 quickstart 增加「三行配置解决 90% 场景」示例（主站列表 + 限速 + 缓存目录），并支持环境变量 `TSTDX_HOSTS` 快速注入                                                             | P1     | 低   |
| U4 | **本地缓存层默认开启**          | router 支持 `cache` 源，但默认未启用，重复拉同一标的 K 线每次全量请求                                           | 提供轻量 SQLite/Parquet K 线缓存（增量更新：只补末根之后），默认可一键开启；同时解决内存中重复持有问题                                                                              | P1     | 中   |
| U5 | **easyquotation 迁移体验** | 垫片已对齐 API，但 `all()` 仅新浪支持，腾讯源调用时报 `CompatibilityError`                                 | 补腾讯全市场能力（`qt.gtimg.cn` 批量代码接口按页拉取），或错误消息中直接给出等价替代调用代码                                                                                     | P2     | 中   |

### 2.4 功能覆盖（对标竞品）

| #  | 优化项                  | 现状与问题                                                      | 方案                                                                             | 优先级 | 工作量 |
| -- | -------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------ | --- | --- |
| F1 | **除权除息数据**           | 域模型有 `CapitalChange`，但在线获取除权除息（gpcw 文件 + 在线 0x\_\*）链路未全部打通 | 打通 `reader/FinanceReader`（gpcw\*.dat）解析 + 在线财务 0x0010 的组合，支撑 `adjust` 前后复权完整可用 | P1  | 中   |
| F2 | **板块数据在线化**          | 板块仅本地 block\_\*.dat；竞品（pytdx）有在线板块列表/成分                    | 复用协议嗅探（`protocol/sniff.py`）确认板块命令号，golden 采集后接入解析器注册表                          | P2  | 高   |
| F3 | **WS 服务行情推送语义**      | ws\_server 已有，但订阅协议与 streaming 引擎的背压/Gap 语义对用户不透明          | 文档化订阅协议 + 增补「断线补洞」消息类型（对齐 R4）                                                  | P2  | 低   |
| F4 | **Level-2 / 扩展市场深度** | 7727 扩展族（期货/期权）客户端已注册，解析覆盖有限                               | 按 golden 扩容流程（`tools/golden_expand.py`）逐步补 7727 命令                             | P2  | 高   |

### 2.5 工程质量

| #  | 优化项           | 现状与问题                                                                                    | 方案                                                                                               | 优先级    | 工作量 |
| -- | ------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | ------ | --- |
| Q1 | **传输层测试盲区**   | `tests/` 中没有 ConnectionPool/hosts 的单元测试（现有 client 测试全部注入 FakePool），本次 max\_hosts 修复无测试保护 | 新增 `tests/transport/`：fake TcpConnection 驱动的故障转移测试（换主站顺序、尝试次数覆盖全池、失败衰减）+ `resolve_hosts` 排序/合并测试 | **P0** | 中   |
| Q2 | **基准测试纳入 CI** | `benches/bench_parser.py`、`bench_reader.py` 手动运行                                         | CI 增加 benchmark 冒烟（只验证可运行 + 输出格式），性能回归用单独 nightly job                                            | P2     | 低   |
| Q3 | **发布流水线落地**   | 构建验证通过，凭据待配置                                                                             | 配置 PyPI trusted publishing + Docker Hub GitHub Actions，打 v1.0.1 tag 触发                           | **P0** | 低   |
| Q4 | **内存基准文档化**   | 有性能基准（加速比），无内存峰值基准                                                                       | 为「全市场行情 / 1000 标的 K 线 / vipdoc 全目录扫描」三类场景建立内存峰值基线（`tracemalloc`），纳入 benches 输出                   | P1     | 中   |

***

## 3. 实施路线建议

**第一批（P0，立即）**：U1（内置池可达排序 + 快速 connect 失败）→ R1（后台测速）→ M4（quotes 并发）→ M2（模型内存布局）→ Q1（传输层测试）→ Q3/R5（发布与冒烟落地）。

**第二批（P1）**：M1（Rust 扩展分时/行情）→ M3/M6（分页并发 + 空闲回收）→ R2/R3/R4 → U2/U3/U4 → F1 → Q4。

**第三批（P2）**：M5/M7、U5、F2/F3/F4、Q2，视社区反馈排期。

### 验证方式（每批通用）

1. 全量 pytest（当前基线 728 passed / 5 skipped，不得回退）；
2. 新增项各自带单元/集成测试（transport 层用 fake connection，不依赖真实主站）；
3. 实机冒烟：三源独立调用（TDX/新浪/腾讯）+ `fetch_all` 全市场 + streaming 订阅 10 分钟；
4. 性能/内存项用 `benches/` 前后对比，结果记入本文档附录。

***

## 附录：修改记录

| 日期         | 记录                          |
| ---------- | --------------------------- |
| 2026-09-02 | 初版：基于全模块源码梳理形成五大主题 24 项优化方案 |
| 2026-09-03 | **M5/M7 完成**：`web/base.py` 新增 `StdlibPooledClient`（http.client 连接池：空闲超时 + 懒回收 + 线程安全）与 `HttpResponse.json()` 零拷贝解码；`tests/web/test_pooled_client.py` 覆盖复用/清理/零拷贝。**U5 完成**：`TencentSource.fetch_all()` 单源两步走（`getBoardRankList` 枚举代码 + `qt.gtimg.cn` 批量行情，单批失败不拖垮全市场）；`WebQuoteSession.all_market()` 与 `UnifiedQuoteAPI.all_market()` 支持腾讯源；新增 `TestTencentFetchAll` 9 用例；有界实网冒烟通过（量/额/盘口全量归一）。**F3 完成**：`ws_server.py` 文档化订阅/推送协议（`quote_update` 周期推送 + `quote_snapshot` 断线补洞）——`JsonRpcHandler.on_message()` 上报新增订阅，`push_snapshot()` 订阅/重连即推全量快照；`serve_ws` 传输层在新增订阅时立即推快照；协议语义（背压、Gap）写入模块 docstring；离线测试覆盖补洞消息类型与订阅集独立。 |
| 2026-09-03 | **F2 完成**：板块数据在线化——`MacClient.block_list`（0x120F）/`block_members`（0x1210）同步+异步方法；合成载荷布局锁定测试（`TestMacBlockLayout`：板块列表 `<H count>+<8s name><H id>`、成分股 `<6s code>`、板块行情）。**F4 完成**：7727 扩展市场覆盖——`ExMarketClient.ex_market_count`（0x0100）/`ex_market_list`（0x0101）/`ex_instrument_count`（0x0102）/`ex_instrument_list`（0x0103）与 `GoodsClient.goods_count`（0x0200）/`goods_list`（0x0201）同步+异步；`tests/protocol/test_7727_goods_mac_coverage.py` 锁定 EXTENDED 0x0100–0x010E、GOODS 0x0200–0x020A、MAC 板块三族解析器布局（P1c per-record absolute vs 0x0202 running base 显式区分）。**golden structure 文档**：`PROTOCOL_SPEC/` 新增 7727（15）/GOODS（11）/MAC（3）共 29 份 `status: inferred` spec YAML（字段布局 + 合成载荷锁定参考 + 待真机样本标注），`spec_audit` 覆盖率 75%→94.6%。 |

