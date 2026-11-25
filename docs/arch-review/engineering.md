# 工程化与基础设施审查（slug = engineering）

审查范围：`atst/config/`、`atst/observability/`、`atst/feedback/`、`atst/tools/`、`Makefile`、`pyproject.toml`、`Dockerfile`、`docker-compose.yml`、`.github/workflows/`、`.pre-commit-config.yaml`、`tests/`（architecture/ 与 compatibility/ 契约测试）、`docs/` 与 `README` 一致性、仓库卫生。
方法：一手代码/配置/工作流为准，本地 `coverage.xml`（2026-11-22 生成，223 文件）实测覆盖率分布；契约测试读其断言而非注释。

## 1. 结论摘要

整体置信：**高置信**（核心结论均有 文件:行号 一手证据，覆盖率数字来自本仓 `coverage.xml` 实测）。

1. **配置 6 段确实被全读，无死键**：`core/hosts/rate_limit/security` 经 `pool_settings_from_config` 单点翻译进传输层；`routing` 由内核 planner 读取；`web` 由 legacy `WebQuoteClient` 读取——与 schema「配置面即执行面契约」的声明一致（该契约把 `WebQuoteClient` 列为合法读者）。
2. **77% 覆盖率门禁是均值门禁，盲区真实存在**：全包 line 86.0% / branch 75.6%，但有 19 个文件 <70%、8 个 <50%、2 个 `__main__.py` 为 0，session 族适配器与 observability 导出器是重灾区。
3. **契约测试不是「改文档才能改代码」的枷锁**：主体是 AST 派生判据（代码为真值源）；反向存在 3 处把文档标题/表格结构钉死的锚点，改文档措辞会红，属双向对账成本而非方向颠倒。
4. **`make gates` 与 CI 口径一致**（13 目标 ↔ 13 作业 1:1 映射，测试矩阵 7 格现算钉死）；唯一实操口径差是 `test-bridges` 的 fastapi 依赖只在工作流里补装。
5. **零依赖主包成立**：`atst/` 无顶层第三方 import，全部惰性导入；根目录散落物（`.coverage`、`coverage.xml`、`dist/`、`release-dist/`、`*.log`）全部被 `.gitignore` 覆盖且未跟踪——**不该进版本库，现状正确**。
6. **docs/README 一致性有一处实锤矛盾**：README 三处宣称「三级路由降级」，与 README 自身「无聚合降级路由」及 ARCHITECTURE「五级降级路由已物理删除」冲突，全仓代码 0 命中该说法。

## 2. 发现清单

| 编号 | 严重度 | 类别 | 位置 | 问题 | 改法 |
|---|---|---|---|---|---|
| E1 | 中 | 架构（文档一致性） | `README.md:27` | 「三级路由降级」卖点与代码事实、README 自身、ARCHITECTURE 三方矛盾 | 改为 RankingStore/测速排序的真实表述 |
| E2 | 中 | 架构（测试策略） | `pyproject.toml:180` | 77% 全包均值门禁允许整模块不测 | 对 <70% 模块设 per-file 下限或纳入 diff-cover |
| E3 | 中 | 细节（CI/本地口径） | `Makefile:52` | `test-bridges` 依赖 `.[server]` extra 但本地不检查不安装 | 前置 import 探测并提示安装 |
| E4 | 中 | 细节（依赖管理） | `pyproject.toml:66` | `all` extra 手工复制全部依赖，历史上已混入死依赖 | 改自引用 extras `atst[xxx]` |
| E5 | 中 | 细节（仓库卫生） | `CHANGELOG.md` | 343KB/3952 行手写账本与 `git-cliff.toml` 双工具并存，无同步判据 | 二选一，或按版本拆 `docs/releases/` |
| E6 | 低 | 细节（并发） | `atst/config/loader.py:300` | `get_config()` 惰性单例无锁 check-then-act | 加 `threading.Lock` |
| E7 | 低 | 细节（配置 UX） | `atst/config/loader.py:168` | `ATST_RATE_LIMIT_STRICT=1` 被解析成 int，bool 字段 fail closed | 按 schema 字段类型解析 env |
| E8 | 低 | 细节（可观测性盲区） | `pyproject.toml:181` | `skip_covered=true` 藏 100% 文件；CI 7 格 coverage 不聚合 | 报告改 `skip_covered=false` 或输出 json 聚合 |
| E9 | 低 | 细节（构建） | `Dockerfile:19` | builder 每次镜像构建跑全量离线测试，`COPY . .` 连 1847 个 golden 文件进上下文 | 可接受；如嫌重可拆测试出构建 |
| E10 | 低 | 细节（契约锚点） | `tests/architecture/test_pool_knob_reachability.py:29` | 3 处文档结构锚，改文档标题/表格即红 | 表格对账迁移到 gen_docs 投影 |
| E11 | 低 | 细节（测试矩阵） | `.github/workflows/ci.yml:57` | test 矩阵无 macOS（仅 wheels 冒烟装包）；lint/type-check 只在 3.11 | 如 macOS 是支持面则补一格 |

## 3. 详细发现与证据

### E1【架构·中】README「三级路由降级」是陈旧卖点，与代码、自身、ARCHITECTURE 三方矛盾

- `README.md:27`：「主站池治理 + 三级路由降级 + 后台测速排序 + 社区候选注入」；`README.md:107` 与 `README.md:498` 再次出现「驱动三级路由降级」。
- 同一文件 `README.md:43` 与 `README.md:86` 却写「无聚合降级路由、无隐式缓存层」；`README.md:484` 写「没有"多级降级 + 熔断 + 合成数据"这一类兜底路由」。
- `docs/ARCHITECTURE.md:7-9`：「早期方案里的 L1/L2 缓存、……五级降级路由……均已物理删除，不再是事实」。
- 反向检索：`grep 三级路由降级 atst/ docs/` 仅命中 README 三处与 `docs/releases/v1.3.0.md:9`（后者指 universe 的「代码表→新浪节点→tdx 段表」三级降级，语义完全不同）。代码里真实存在的是 `RankingStore`（`atst/transport/hosts.py:59` 起）与 `rank_hosts`/`speedtest`（`atst/transport/__init__.py:25`），即「测速排序」，不是三级路由降级。
- 证据等级：一手代码 + 官方架构文档，高置信。
- 改法：README 三处改为「主站池 + RankingStore 测速排序 + 社区候选注入」，与 ARCHITECTURE 对齐。

### E2【架构·中】77% 覆盖率门禁的盲区：均值达标，整模块裸奔

- 门禁唯一真值：`pyproject.toml:180` `fail_under = 77`，`branch = true`；Makefile/CI 均不传 `--cov-fail-under`（`tests/compatibility/test_local_gate_contract.py:33-38` 钉死这一口径）。
- 本仓 `coverage.xml`（223 文件）实测：整体 line-rate **0.8597**、branch-rate **0.7561**——门禁轻松通过。但分文件看：
  - **0 覆盖**：`atst/__main__.py`、`atst/server/mcp/__main__.py`；
  - **<0.50**：`sources/_session_holders.py` 0.19、`sources/_session_sector_macro.py` 0.31、`sources/_session_esg_chip.py` 0.34、`sources/_session_research.py` 0.38、`sources/cninfo/adapters.py` 0.45、`observability/otel_exporter.py` 0.46；
  - **<0.70 共 19 个文件**：另含 `observability/statsd_exporter.py` 0.62、`tools/codegen.py` 0.64、`observability/prometheus_exporter.py` 0.67、`protocol/prober.py` 0.62 等。
- 结构性盲区：coverage 只统计 `-m "not network"` 的离线路径（`Makefile:44-48`、`ci.yml` test 作业同款），`tests/live/` 与全部网络分支零覆盖；CI 7 个矩阵格各自产出 `coverage.xml` 仅上传不聚合，无跨格对比。
- 证据等级：一手配置 + 本仓 coverage.xml 实测数值，高置信。
- 改法：对 line-rate <0.70 的模块设 per-module 最低线（或引入 diff-cover 对新增代码设 90%）；`_session_*` 族与 `observability/` 三个导出器补离线单测（它们都是纯标准库实现，可测性并不差）。

### E3【细节·中】`make gates` 与 CI 的唯一实操口径差：bridges 的 fastapi 依赖

- CI `bridges` 作业专门补装 `.[server]` 并自述原因：`fastapi` 是惰性可选依赖，只装 `.[dev]` 时 `tests/test_bridges.py` 以 ModuleNotFoundError 失败（`.github/workflows/ci.yml:115-135`）。
- 本地 `make gates` → `test-bridges` 只执行 `pytest tests/test_bridges.py`（`Makefile:52-54`），不安装、不检查任何东西；`make install` 装 `.[all,dev]`（`Makefile:31-32`）才带上 fastapi。贡献者若按 lint/type-check 作业同款只装 `.[dev]`（`ci.yml:33`、`ci.yml:47`），本地 `make gates` 在 bridges 一步必红，且报错不指向缺 extra。
- 其余 12 个目标与 CI 作业 1:1 对应，由 `tests/support/gate_inventory.py:21` 的 `GATES_TO_CI_JOBS` 映射表 + `test_local_gate_contract.py:139-160` 双向钉死，**口径本身一致**。
- 证据等级：一手 Makefile/workflow/测试，高置信。
- 改法：`test-bridges` 目标前加一行 `python -c "import fastapi"` 探测，失败时提示 `pip install -e ".[server]"`；或把该检查写进 `tests/compatibility/test_local_gate_contract.py`。

### E4【细节·中】`all` extra 手工复制依赖清单，是已经咬过人的形状

- `pyproject.toml:66-79`：`all` 逐条手抄 dataframe/parquet/duckdb/web/metrics/server/tools 六个 extra 的依赖。文件内注释自述：「过去这里多出的 `pydantic` / `mcp` 全仓 0 处读取，已经随 extra 一起删掉」——即这种形状已经产生过两次死依赖。
- 新增 extra 必须同步改 `all`，没有任何判据钉住「`all` == 各 extra 并集」（`tests/` 里也只验 wheel 安装后能 import 这些包，`wheels.yml:214-222`）。
- 证据等级：一手配置 + 注释自述，高置信。
- 改法：改用自引用 extras：`all = ["atst[dataframe,parquet,duckdb,web,metrics,server,tools]"]`（hatchling 支持），删掉手抄清单。

### E5【细节·中】CHANGELOG 343KB 双工具并存；散落物卫生核实

- `CHANGELOG.md` 343,123 字节、3,952 行，被跟踪并随 sdist 发布（`pyproject.toml` `[tool.hatch.build.targets.sdist] include` 含 `CHANGELOG.md`）。同时仓里存在 `git-cliff.toml`（完整 cliff 配置），但 CHANGELOG 是 Keep-a-Changelog 手写体，两者无同步判据——第二套工具是死配置或在等切换。
- 仓库卫生**核实结论（正面）**：任务清单点名的 `.coverage`、`coverage.xml`、`.pytest_ci_ubuntu.log`、`dist/`、`release-dist/` 均为本地未跟踪产物，`.gitignore:10-11,35-38` 与 `*.log` 规则全部覆盖，`git status` 干净——**它们不该进版本库，现状正确**。`tests/` 占 2,168/2,593 个跟踪文件（84%），其中 `tests/golden` 1,847 个文件共 1.9MB，属「样本即契约」的刻意设计，可接受。
- 证据等级：一手文件系统 + git ls-files 实测，高置信。
- 改法：git-cliff 要么接管生成（并在 `make gates` 加生成一致性检查），要么删掉 `git-cliff.toml`；CHANGELOG 可按大版本拆入已存在的 `docs/releases/`。

### E6【细节·低】`get_config()` 惰性单例无锁

- `atst/config/loader.py:65` `_global_config: Config | None = None`；`loader.py:300-306` `get_config` 直接 `if _global_config is None: _global_config = load_config()`，无锁。两个线程并发首调会各自完整加载一次（结果等价，属良性竞态），但与本仓大量自称「线程安全」的口径（`tests/architecture/test_declared_knobs.py:189-215` 专判此事，只是其尺子只认 `_SHARED_*` 命名）不一致。
- 改法：模块级 `threading.Lock` 包住 check-then-act。

### E7【细节·低】布尔环境变量传 `1`/`0` 会 fail closed

- `atst/config/loader.py:168-196` `parse_env_value` 把 `1`/`0` 按数值解析（注释明说是为了救 `ATST_CORE_MAX_RETRIES=1`），于是 `ATST_RATE_LIMIT_STRICT=1` 得到 int `1`，`RateLimitConfig.validate`（`atst/config/schema.py:166` `_check_bool`）当场抛「必须是 bool」。用户照 "0/1 开关" 的直觉设置即崩，错误信息不提示 true/false。
- 改法：解析时已知目标字段类型（`_require_env_field` 已拿到 section/field），bool 字段额外接受 `1/0`。

### E8【细节·低】覆盖率报告自身遮蔽 + 不聚合

- `pyproject.toml:181` `skip_covered = true`：100% 文件从 term-missing 报告里消失，正好把「盲区在哪」以外的信息藏掉，与 E2 的定位需求相逆。
- CI `test` 作业 7 格各自 `--cov-report=xml:coverage.xml` 并上传（`ci.yml:73-82`），无聚合作业、无与 77% 之外的趋势对比。
- 改法：`skip_covered = false`；或加一步合并 coverage 数据并输出单口径数字。

### E9【细节·低】Docker 构建把全量测试搬进 builder

- `Dockerfile:19` builder 阶段跑 `pytest tests/ -m "not network"`（全套离线测试，含 architecture/compatibility 契约测试），`Dockerfile:11` `COPY . .` 把包括 1,847 个 golden 文件在内的整个仓库送进构建上下文。换来的是「镜像里的 wheel 一定过过全量门禁」，属于用构建时长换正确性，方向正确但重；`.dockerignore` 已排除本地数据与产物（`.dockerignore:1-46`），无泄漏。
- 改法：可接受；如需提速可把测试移到独立 CI 步骤、builder 只构建。

### E10【细节·低】契约测试的方向与三处反向锚点

- **方向核实**：`tests/architecture/` 主体判据是派生而非手抄——`test_declared_knobs.py:105-125` 用 AST 现算「文档承诺的入参函数体必须读」；`test_generated_docs.py:31-38` 只验「生成物 == 重新生成结果」，真值在运行时注册表；`test_pool_knob_reachability.py:47-52` 用 `pool_settings_from_config(Config())` 现算配置可达集。改代码不跑 `make docs-gen` 会红——这是「改代码必须同步文档」，不是「改文档才能改代码」。
- **反向锚点（改文档措辞即红）**：
  - `tests/architecture/test_pool_knob_reachability.py:29` 硬编码小节标题 `"### 传输池参数：哪些**不**经配置面"`，注释自认「文档改这个标题会红——有意」；
  - `tests/architecture/test_offline_capability_honesty.py:421-445,533-539` 直接解析 `docs/api/interfaces.md` 方法表与 `docs/providers/tdx.md` 能力块的 Markdown 表格形状；
  - `tests/architecture/test_error_promises.py:47` 扫 `README.md`/`SECURITY.md` 里点名的错误类名。
- 评估：这是双向对账的显式成本，锚点均在测试内声明，不构成「枷锁」；但 Markdown 表格解析是最脆的一类，建议逐步把这类对账迁到 `scripts/gen_docs.py` 投影（仓库已有唯一写者机制）。
- 证据等级：一手测试代码，高置信。

### E11【细节·低】CI 矩阵缺口：macOS 与静态作业的版本面

- `ci.yml:57-66` test 矩阵 = ubuntu × (3.10–3.14) + windows × (3.11, 3.12) 共 7 格；macOS 只出现在 `wheels.yml` 的 `smoke-install`/`extras-install`（装包 + import 冒烟，不跑测试）。`lint`/`type-check`/其余静态作业只在 3.11 单版本。`pyproject.toml` classifiers 宣称 OS Independent、3.10–3.14 全支持。
- 改法：若 macOS 是宣称支持面，test 矩阵补 1 格 macos-latest + 3.12 即可由 `test_local_gate_contract.py:151` 的现算判据自动收编。

### 核实结论（正面，非缺陷）

- **配置 6 段全被读**：`core.timeout/heartbeat_interval/max_retries`、`hosts.slots_per_host`、`rate_limit.*`、`security.use_tls` → `atst/transport/pool.py:103-115` `pool_settings_from_config` 单点翻译；`hosts.servers` → `atst/runtime/kernel.py:74-76`；`routing.auto_providers/auto_health_ttl` → `kernel.py:66-71`；`web.*` → `atst/sources/__init__.py:395-402`（legacy `WebQuoteClient`）。`rate_limiter` 在同步池请求路径真实被 acquire（`atst/transport/pool.py:629-631,819-821,946-947`）。schema 契约文本（`atst/config/schema.py:8-12`）把 `WebQuoteClient` 列为合法读者，无「声明了却没人读」的死键。
- **零依赖主包成立**：`pyproject.toml:33` `dependencies = []`；`grep "^(import|from) (pandas|httpx|fastapi|...)"` 在 `atst/` 顶层 0 命中，全部第三方导入位于函数体内（如 `atst/domain/models.py:549`、`atst/output/__init__.py:95`、`atst/server/runtime_http.py:27`、`atst/tools/capture.py:521`）。运行时镜像 `--no-deps` 裸装 wheel（`Dockerfile:38`）。
- **pre-commit 与 CI 同口径**：`.pre-commit-config.yaml` 的 ruff/mypy/originality/spec-audit/doc-links 与 ci.yml 对应作业命令一致，mypy 旗标逐字相同。
- **`atst/tools/` 四件套是真实实现**：`capture.py`（863 行，自采集 + 法律边界 + 归档）、`check_originality.py`（626 行，AST 许可/指纹审计）、`codegen.py`（spec→stub 生成，草稿区不进包）、`golden_audit.py`（origin 分级 + L1 门禁 + 克隆普查），均为零依赖标准库实现，且都在 `make gates`/CI 里被实际调用。

## 4. 待证项

| 线索 | 现状 | 建议查法 |
|---|---|---|
| README 规模数字（85 命令 / 61 解析器 / 217 capability / 304 绑定 / CLI 33 子命令 / 14 Provider） | 未逐一核对 | 对照 `atst/protocol/commands.py`、`atst/runtime/bindings.py`、`atst/providers/` 现算，`tests/architecture/test_ledger_public_surface.py` 已有现成判据 |
| `otel_exporter.py` 的 OTLP JSON 与官方 proto3 JSON 映射的兼容性 | 覆盖率仅 0.46，未审语义 | 对照 OpenTelemetry proto3 JSON 规范抽查字段名/包装类型 |
| `docs/configuration.md` 全部键与 schema 字段的双向一致性 | 抽查一致（`[web]`/`[routing]`/`[security]` 均对上），未全量比对 | 用 `config_diff` 思路写一次性脚本比对 `DEFAULT_CONFIG.to_dict()` 与文档表格 |
| `docker-compose.yml` | 未发现与 CI/Makefile 口径冲突，未深查 | 低优先 |

## 5. 建议的下一步

1. 修 README 三处「三级路由降级」（E1，半天内可完成，防新用户按已删除的架构理解系统）。
2. 给覆盖率门禁加 per-module 下限并补 `_session_*` / `observability` 离线单测（E2，重构方案里应作为质量底线项）。
3. `test-bridges` 加 extra 探测 + `all` extra 改自引用（E3/E4，各一行级改动）。
4. CHANGELOG 与 git-cliff 二选一（E5）。
