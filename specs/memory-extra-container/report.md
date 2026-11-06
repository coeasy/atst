# Bar / Quote 驻留内存改造 — 完成报告

## 1. 结论：五项硬约束全部达标

| # | 约束 | 结论 | 证据 |
|---|---|---|---|
| 1 | 序列化形状不变（字段名/层级/缺省/顺序、空 extra 表现） | 达标 | `tests/domain/test_models_extra_contract.py::TestSerializationGolden` 7 例 golden，值与 `list(keys)` 顺序同时断言 |
| 2 | `extra` 读写语义不变 | 达标 | 同文件 `TestExtraMappingSemantics`：写入/迭代/`in`/缺失/`pop`/`update`/`clear`/赋值/自定义键/`==`/`copy` |
| 3 | 热路径不回退 ≤ +5% | 达标 | parse_quotes 0.295s（-9.0%）、parse_kline 0.429s（+3.8%）、serialize 0.0141s（-7.8%） |
| 4 | 内存 KiB/条至少降 30% | 达标 | market -41.7%、kline -31.0%、vipdoc -35.0% |
| 5 | 同步测试与文档、不删测试/不放宽断言、不 commit/push | 达标 | 仅新增测试；`ruff`/`mypy`/`gen_docs --check`/全量 pytest 通过；工作区未提交 |

## 2. 改动清单

### 2.1 `atst/domain/models.py`（改造本体）

| 机制 | 做法 |
|---|---|
| `extra` 存储 | 空 → `None`（不再分配空 dict）；非空 → 扁平 `tuple`（`k1,v1,k2,v2…`），天然保持插入顺序 |
| `extra` 对外 | `_ExtraView(MutableMapping)` 代理视图；`[]`/`get`/`in`/`len`/迭代/`items`/`pop`/`setdefault`/`update`/`clear`/`copy`/`==` 全部可用；写入时重建 tuple 写回 owner |
| `Bar` / `Quote` | 由 `@dataclass(slots=True)` 改为手写 `__slots__` 类（slots 无法让字段名同时是 property）；构造签名、默认值、位置参数顺序不变；手写 `__eq__` / `__repr__` |
| `to_dict` | 直读 `_extra` 槽位，不经视图；先契约字段再按 extra 插入顺序追加；空 extra 不追加 |
| `datetime` 去重 | `_canonical_datetime()` 有界去重表（上限 4096，饱和后停止新增且停止查表） |
| 盘口紧凑化（追加范围 D6） | `Quote._bid_raw/_ask_raw` 改 `array('d')`，价格/量交错；`bid`/`ask` 改 property，首次访问才物化 `list[Level]` 并留在槽位复用；setter 重打包并失效缓存；`to_dict` 空盘口走 `[]` 快速路径；`_pack_levels` 兼容 `Level` / 行式 `Mapping`（`client._row_to_quote` 传的就是 row 内 dict） |

### 2.2 `atst/reader/formats.py`（解析热路径）

- `_day_datetime(y, m, d)`：`lru_cache(maxsize=8192)`，合法 `YYYYMMDD` 与 LC16 分支复用；畸形日期仍走原位域回退 f-string，不登记。
- `_decode_record_at`：`extra` 直接产出扁平 tuple（`("open_interest", n)` / `("prev_close", x)`），省掉逐行构造 dict 再拆开。

### 2.3 测试与归档

- 新增 `tests/domain/test_models_extra_contract.py`（22 例）：golden、extra 语义、`TestLazyOrderBook`（打包未物化 / 物化后复用 / 空盘口可 `append` / setter 重打包 / 行式 dict 入参）。
- 新增归档：`benches/results/memory_after_extra.json`、`benches/results/time_after_extra.json`；baseline 沿用上一轮 `*_v2.json`。
- 同步 `specs/memory-extra-container/spec.md`（D6）与 `tasks.md`（T6.5）。

## 3. 数据（前后对比）

### 3.1 内存（`bench_memory.py --all`，tracemalloc 峰值）

| 场景 | 条目 | baseline KiB/条 | after KiB/条 | 变化 |
|---|---:|---:|---:|---:|
| market | 5 400 | 2.364 | **1.378** | **-41.7%** |
| kline | 320 000 | 0.3624 | **0.250** | **-31.0%** |
| vipdoc | 80 000 | 0.5158 | **0.336** | **-35.0%** |

峰值：market 7.27 MiB、kline 78.19 MiB、vipdoc 26.21 MiB。

### 3.2 耗时（`bench_time.py --all`，连续 5 次稳定区间；单次归档值）

| 场景 | 条目 | baseline s | after s | 变化 |
|---|---:|---:|---:|---:|
| parse_quotes | 1 000 | 0.3242 | 0.295 | **-9.0%** |
| parse_kline | 80 000 | 0.4132 | 0.429 | **+3.8%** |
| serialize | 5 400 | 0.0153 | 0.0141 | **-7.8%** |

时间口径说明：`--scenario parse_kline` 单跑会把 `atst` 包首次导入与临时 `.day` 文件写入算进计时（实测 0.44~0.47s），与归档口径（`--all`，导入成本已由 parse_quotes 承担）不可比，故一律用 `--all`。

## 4. 契约等价性论证

1. **序列化**：`to_dict` 的输出键集合、顺序、缺省值由 golden 逐例锁死 —— 空 `Bar`（7 键）、无 extra 的 `Bar`、`extra={"open_interest":5,"x":1}`、空 `Quote`（`bid`/`ask` 均为 `[]`）、带 `Level` + 下划线内部键的 `Quote`（内部键不进公开 dict）、`to_dicts`、`to_tuples`。HTTP/WS/MCP/CLI 四面均经 `to_dict` / `jsonable` 落到同一份 dict。
2. **`extra` 语义**：对外仍是 `MutableMapping`；写入重建扁平 tuple 保证插入顺序与 dict 一致；`bar.extra == {"prev_close": 10.48}`、`"k" not in bar.extra`、缺失键 `KeyError`、`dict(bar.extra)`、`for k, v in bar.extra.items()` 全部照旧；`extra = None` / `extra = {}` / 任意 Mapping 赋值等价。
3. **对象语义**：`Bar`/`Quote` 不再是 dataclass —— 已核查仓库内无 `asdict` / `isinstance(..., dataclass)` 依赖，序列化均优先 `to_dict`；`__eq__` 逐契约字段 + `extra` 比较，`__repr__` 仍是 dataclass 风格。
4. **盘口语义边界（D6，明示）**：`quote.bid` 首次访问返回的是**等值新 list**，不再是构造入参 list 的本尊；构造后继续原地改写原 list 不会反映到 Quote。仓库内唯一相关用法是百度适配器的 `q.bid.append(...)`（经 property 取列表，正常生效），无别名依赖。`len`/下标/迭代/`== [Level(...)]` 语义不变；`to_dict` 对空盘口不物化，因此纯序列化路径不产生 `Level` 分配。
5. **`datetime` 去重**：返回的仍是等值 `str`（类型与值不变），仅让重复串共享同一对象；缓存有上限，不会随进程寿命无界增长。
6. **去重开销**：同一进程内 `DayBarReader.read` 的 min-of-6 对照 —— 去重 0.3804s vs 恒等 0.3725s，约 **+2.1%**，落在预算内；饱和后只剩字典查找。

## 5. 未做 / 风险项

- **未做**：`Bar.datetime` 的进一步驻留（kline 已达标，收益需改 `formats` 之外的分支）；market 场景中 `code` 字符串驻留（占比小）。均超出本轮范围。
- **风险：非 dataclass**。若外部下游直接 `dataclasses.asdict(bar)` 会失败；本仓库内无此用法，但这是对外可见的类型性质变化，未写入 `CHANGELOG`（`tests/compatibility/test_release_history_contract.py` 锁死 Unreleased「暂无。」）。
- **风险：视图瞬时开销**。每次 `bar.extra` 访问新建一个 ~40 B 的 `_ExtraView`；热路径（`to_dict`、`turnover_rate` 内的 `get`）已直读槽位或只读一次，批量序列化不受影响。
- **风险：盘口 list 身份变化**（见 §4.4）。极端用法（构造后持原 list 原地改）行为不同，属未定义用法。
- **风险：耗时基准受机器负载影响**。本机同一场景重复测量波动可达 ±10%，报告采用 `--all` 多次运行的稳定区间；`+3.8%` 是区间中值，非单次样本。
- 未 commit / push（按要求）。
