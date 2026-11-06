# Spec：Bar / Quote 的 extra 低开销容器（驻留内存改造）

## 1. 背景与目标

`atst.domain.models.Bar` / `Quote` 每个对象都带一个 `extra: dict[str, Any]`（默认
`default_factory=dict`）。CPython 下空 dict 占 64 B，1~2 键 dict 占 184 B，在
全市场行情（5400 条）与长 K 线（数十万条）驻留场景中是稳定的常数开销。

目标：把 `extra` 改成低开销容器，显著降低 `benches/bench_memory.py` 三项场景的
KiB/条，同时**完全不改变对外序列化与读写语义**。

硬约束（来自用户）：

1. `to_dict` / JSON / HTTP / WS / MCP / CLI 的字段名、层级、缺省值、顺序语义不变；
   `extra` 内容等价，空 `extra` 的序列化表现与改造前一致。用改造前 golden 断言锁死。
2. `extra` 读写语义不变：任意 key 写入、迭代、`in`、缺失访问、无 schema 自定义键。
3. 热路径不回退：`bench_time.py` 解析/序列化耗时相对上一轮 after 不得超过 +5%
   （确有超出需说明原因）。
4. `bench_memory.py` 合成数据 market / kline / vipdoc 的 KiB/条至少降 30%。
5. 同步测试与文档；不删测试、不放宽断言；不 commit / push。

## 2. 现状测量（改造前，本机 3.12）

对象尺寸（`sys.getsizeof`）：`Bar` 96 B、`Quote` 128 B、空 dict 64 B、
1~2 键 dict 184 B。

`benches/results/memory_after_v2.json`（上一轮 after 归档，作为本轮 baseline）：

| 场景 | 条目数 | peak KiB | KiB/条 |
|---|---:|---:|---:|
| market | 5400 | 12767.78 | 2.365 |
| kline | 320000 | 115978.66 | 0.3624 |
| vipdoc | 80000 | 41262.81 | 0.5158 |

`benches/results/time_after_v2.json`：

| 场景 | 条目数 | 耗时 s |
|---|---:|---:|
| parse_quotes | 1000 | 0.3242 |
| parse_kline | 80000 | 0.4132 |
| serialize | 5400 | 0.0153 |

tracemalloc 归因（market 12951 KiB）：

- `bench_memory.py` 的 bid/ask 两个 Level 列表推导 ≈ 7375 KiB（约 57%）；
- `Quote` 对象本身 ≈ 675 KiB；`code` 字符串 ≈ 248 KiB；`datetime` ≈ 316 KiB；
- 2 键 `extra` dict ≈ 993 KiB（约 7.7%）。
- kline 场景每根 Bar 371 B 中，空 dict 64 B（≈17%）；`datetime` 字符串 ≈59 B（≈16%）。
- vipdoc 每条 528 B 中，1 键 `extra` dict 184 B（≈35%）；`datetime` 字符串 ≈61 B（≈12%）。

## 3. 设计决策

### D1. `extra` 存储形态

- 内部槽位改为 `_extra`，两种状态：
  - `None`：没有额外字段（**不再分配空 dict**）；
  - `tuple`：扁平键值序列 `(k1, v1, k2, v2, ...)`，保持 dict 的插入顺序；
    1 键 = tuple2（56 B），2 键 = tuple4（72 B）。
- 公开属性 `extra` 改为 property：
  - 非空时返回 `_ExtraView(owner)`（`collections.abc.MutableMapping` 实现）；
  - 空时同样返回视图（零底层存储），读写都不会分配 dict；
  - setter 接受 `dict` / `None` / 任何 `Mapping`，转成扁平 tuple 存回。
- 视图需完整实现 dict 行为：`[]`、`get`、`in`、`len`、迭代、`items/keys/values`、
  `pop/popitem/clear/update/setdefault/copy`、`==`/`!=`、`repr`；写入/删除时重建
  扁平等价 tuple 并写回 owner（条数 ≤ ~10，O(n) 重建成可接受）。
- 语义保持要点：`bar.extra == {"prev_close": 10.48}`、`"k" not in bar.extra`、
  `bar.extra["x"] = 1`、`bar.extra.pop("_baidu_time", None)`、`dict(bar.extra)`、
  `for k, v in bar.extra.items()` 全部照旧可用。

### D2. `Bar` / `Quote` 由 dataclass 改为手工 `__slots__` 类

`@dataclass(slots=True)` 无法让字段名 `extra` 同时是 property（slots 会覆盖
descriptor），因此两个类改为手写 `__slots__` 类：

- 保留**构造签名**与默认值（含关键字 `extra=None`），位置参数顺序不变；
- 保留 `to_dict` / `to_tuple` / `from_dict` / `change` / `pct_change` /
  `turnover_rate` 等全部方法；
- 手写 `__eq__`（逐契约字段 + `extra` 映射比较）与 `__repr__`（dataclass 风格，
  `extra` 显示为 dict），保持对象行为一致；
- `isinstance(Bar, dataclass)` 与 `dataclasses.asdict(Bar)` 不再成立——仓库内已核查：
  无测试/生产代码依赖 `Bar`/`Quote` 的 dataclass 性质；序列化路径
  （`runtime.executor._canonical_rows`、`domain.to_dicts`、HTTP/MCP/CLI）都优先走
  `to_dict`，`server.serialization.jsonable` 会落到 `to_dict` 分支，输出与本契约一致。

### D3. 序列化等价

`Bar.to_dict` / `Quote.to_dict` 直接读 `_extra` 扁平 tuple，避免为序列化构造视图：

- 先按 `BAR_FIELDS` / `QUOTE_FIELDS` 顺序写契约字段，再按 `extra` 原插入顺序追加；
- `Quote` 仍跳过 `_` 前缀内部键；
- 空 `extra` 时不追加任何键，输出与改造前逐字节一致。

改造前 golden（已在本机实测并写入新测试）：

```json
{"datetime": "", "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "volume": 0, "amount": 0.0}
{"datetime": "2026-01-02", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10, "amount": 15.0}
{"datetime": "2026-01-02", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10, "amount": 15.0, "open_interest": 5, "x": 1}
{"code": "", "datetime": null, "price": 0.0, "last_close": 0.0, "open": 0.0, "high": 0.0, "low": 0.0, "volume": 0, "amount": 0.0, "bid": [], "ask": []}
{"code": "600000", ..., "bid": [{"price": 9.99, "volume": 1000}], "ask": [{"price": 10.01, "volume": 2000}], "float_shares": 1000000000, "turnover": 1.23}
[{"datetime": "d", ... , "a": 1}, {"code": "c", ...}]
[('d', 0.0, 0.0, 0.0, 0.0, 0, 0.0)]
```

### D4. 构造 / 解析热路径

1. `atst/reader/formats.py`：`DayBarReader._decode_datetime` 的 `YYYYMMDD` / `LC16`
   分支改走模块级有界缓存 `_day_datetime(y, mo, d)`（`lru_cache(maxsize=8192)`）。
   vipdoc 200 文件 × 400 行只有 ~400 个不同日期，字符串驻留大幅下降；字符串不可变，
   语义不变。
2. `Bar.__init__` / `Quote.__init__` 对 `datetime` 做有界规范化缓存（普通 dict 实现，
   `_DT_CACHE` 上限 4096 条，超出即停止新增）：同一批多标的 K 线的日期/时刻高度重复
   （bench kline 320k 根只有 320 个不同日期），驻留字符串数量从 N 条降到唯一值条数。
   空串直接跳过；`Bar.datetime` 值与类型完全不变。
3. 上述缓存均有上限，不会随进程寿命无界增长。

### D5. 降幅预期与 market 缺口（需在批准时确认）

按上面设计逐项估算（以 after_v2 为 baseline）：

| 场景 | 主要节省项 | 预计 KiB/条降幅 |
|---|---|---:|
| kline | 空 dict -64 B、datetime 去重 -59 B、tuple 槽位 0 | ~-32% |
| vipdoc | 1 键 dict 184→56 B、datetime 去重 -61 B | ~-35% |
| market | 2 键 dict 184→72 B、datetime 去重 -2.4% | ~-7% |

market 的 30% **无法仅靠 extra 容器达成**：该场景 57% 的内存是 `Quote.bid/ask`
的 10 个 `Level` 对象与两个 list；`extra` 只占 7.7%。要达标必须把 bid/ask 改成
紧凑存储 + 惰性物化，这会改变 `Quote.bid/ask` 的公开对象契约（文档承诺
`list[Level]`），超出本次「extra 容器」范围。

**本 spec 的默认决定**：不扩到 bid/ask；kline / vipdoc 达标，market 如实报告
-7% 左右并给出归因。若要求 market 也 ≥30%，请在批准时明确追加「bid/ask 紧凑化」
范围（将另做等价性论证与 golden）。

**实际决定**：用户已批准追加 bid/ask 紧凑化，见 D6。追加后 market 亦达标。

### D6. bid/ask 紧凑化（追加范围）

market 场景扣掉 extra 后仍有约 57% 驻留在 `Quote.bid/ask` 的 `Level` 实例与 list
上，仅靠 extra 容器到不了 -30%。经批准追加：

- 存储：`_bid_raw` / `_ask_raw` 为 `array('d') | None`，交错保存 `price`、`volume`
  （`volume` 在出口转回 `int`）；空盘口为 `None`，不分配空数组。
- 惰性物化：`bid` / `ask` 由字段改为 property。首次访问时把紧凑数组重建成
  `list[Level]`，写入 `_bid_list` / `_ask_list` 缓存；之后访问返回同一 list。
- setter：`quote.bid = [...]` 重新打包并清空对应缓存。
- `to_dict`：空盘口走 `[]` 快速路径，不触发物化；非空物化后按 `QUOTE_FIELDS`
  顺序输出 `{"price": ..., "volume": ...}`，与改造前一致。
- 输入兼容：`_pack_levels` 接受 `Level`、行式 `Mapping`（`{"price","volume"}`，
  `atst/client/core._row_to_quote` 传的就是 row 里的 dict）以及带 `price`/`volume`
  属性的对象。
- **语义边界（明示）**：改造前 `Quote(bid=lst)` 后 `quote.bid` 就是 `lst` 本尊；
  现在首次访问返回等值新 list。若调用方在构造后继续持有 `lst` 并原地改写，
  不会反映到 Quote（本轮改造前也是未定义用法）。仓库已核查：百度适配器走
  `q.bid.append(...)`（经 property 取列表，正常生效）；无代码依赖入参 list 的别名。
  对象层面的 `quote.bid == [Level(...)]`、`len`、下标、`for x in quote.bid`
  语义完全不变。

追加后内存 KiB/条：market 2.201→1.378（对 baseline -41.7%）。

## 4. 文件改动清单

| 文件 | 改动 |
|---|---|
| `atst/domain/models.py` | 新增 `_ExtraView`；`Bar` / `Quote` 改手写 slots 类 + `extra` property；`to_dict` 直读 `_extra`；新增有界 datetime 规范化缓存；`Quote.bid/ask` 改 `array('d')` 紧凑存储 + 惰性物化（D6） |
| `atst/reader/formats.py` | `DayBarReader._decode_datetime` 改走 `_day_datetime` 有界缓存 |
| `tests/domain/test_models_extra_contract.py`（新增） | golden 序列化断言 + extra 读写语义 + 空 extra 不分配 + 对象尺寸回归 |
| `tests/reader/test_formats.py` | 如受缓存影响则补日期缓存复用断言（不放宽原断言） |
| `CHANGELOG.md` / 文档 | 仅当实际行为要对外说明时改；`tests/compatibility/test_release_history_contract.py` 锁死 Unreleased「暂无。」，不擅自改 |
| `benches/results/memory_after_extra.json`、`time_after_extra.json`（新增归档） | 本轮 after 数据 |

不改：`docs/` 中 `bar.extra["raw_volume"]` 等既有语义描述（容器化后仍成立）。

## 5. 验收与验证

1. 定向：
   - `pytest tests/domain/test_models_extra_contract.py tests/reader/test_formats.py tests/domain/test_adjust.py tests/sink/test_local_day.py tests/sources/test_baidu.py tests/runtime/test_canonical_rows.py -q`
   - `python benches/bench_time.py --all --json benches/results/time_after_extra.json`
     与 `time_after_v2.json` 对比，parse_quotes / parse_kline ≤ +5%。
2. 内存：
   - `python benches/bench_memory.py --all --json benches/results/memory_after_extra.json`
     与控制台 KiB/条对比 baseline，写回报告。
3. 全量门禁：
   - `pytest -m "not network"`（当前基线 4818 passed / 5 skipped）
   - `ruff check .`、`ruff format --check .`
   - `mypy atst/`
   - `python scripts/gen_docs.py --check`
4. 不 commit / push。

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| `bar.extra` 不再是 `dict`，有代码 `isinstance(extra, dict)` 或 `json.dumps(extra)` | 全仓已 grep：无 `isinstance(..., dict)` 依赖；序列化均走 `to_dict`；`_ExtraView.copy()` 返回真 dict |
| `Bar`/`Quote` 不再是 dataclass，`asdict` 路径变化 | 全仓核查仅 `executor` / `server.serialization` 会触及，且都优先 `to_dict`；新增 golden 覆盖这四个面 |
| 代理每次访问分配小对象（32~40 B，瞬时） | 热路径 `to_dict` / `to_turnover` 直读 `_extra`；代理仅在调用方主动读写 `extra` 时创建 |
| 有界缓存未命中时停止增长后仍有重复字符串 | 只影响内存收益上限，不影响正确性；上限 4096/8192 防止泄漏 |
| market 目标不达标 | 报告明示归因；如需达标按 D5 追加范围 |
| `Quote.bid` 不再与构造入参 list 同一对象 | 仅在调用方构造后继续原地改写入参 list 时可见；已核查无此类用法（D6 语义边界） |
