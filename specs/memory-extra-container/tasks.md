# Tasks：Bar / Quote extra 低开销容器

> 依 `spec.md` 执行。每完成一个块先跑该块测试，收口后再进入下一块。不 commit / push。

## T1. 容器与类改造（`atst/domain/models.py`）
- [x] `_ExtraView(MutableMapping)`：tuple 扁平存储、读写重建、`==`/`repr`/`copy`。
- [x] `Bar` / `Quote` 改为手工 `__slots__` 类，保留构造签名/默认值/方法；
      `extra` 为 property（`_extra: tuple | None`）。
- [x] 手写 `__eq__` / `__repr__`，与 dataclass 语义对齐。
- [x] `to_dict` 直读 `_extra`，保持字段顺序与空 extra 行为。
- [x] 有界 datetime 规范化去重（上限 16384，饱和后停止查表）。
- [x] 验证：`pytest tests/runtime/test_canonical_rows.py tests/domain/test_adjust.py -q`。

## T2. golden 与语义契约测试
- [x] `tests/domain/test_models_extra_contract.py`：改造前 `to_dict` golden
      （Bar/Quote/空 extra/自定义键/下划线键/bid·ask/`to_dicts`/`to_tuples`/`from_dict`）。
- [x] 读写语义：任意写入、迭代、`in`、缺失 KeyError、`pop`、`update`、
      `dict(extra)`、`extra = None` / `extra = {...}` 赋值、`turnover_rate`。
- [x] 空 extra 不分配底层 dict（`_extra is None`）。
- [x] 验证：`pytest tests/domain/test_models_extra_contract.py -q`（17 passed）。

## T3. 热路径时间基准
- [x] `python benches/bench_time.py --all --json benches/results/time_after_extra.json`
- [x] 与 `time_after_v2.json` 对比（`--all` 连续 5 次取稳定区间）：
      parse_quotes 0.295（-9.0%）、parse_kline 0.429（+3.8%）、serialize 0.0141（-7.8%），
      均在 +5% 预算内（单次 `--scenario parse_kline` 会被包导入/临时文件写入抬高，不作对比口径）。

## T4. 解析构造路径（`atst/reader/formats.py`）
- [x] `_decode_datetime` 的 YYYYMMDD 合法分支 / LC16 分支改走 `_day_datetime`
      有界驻留（畸形日期回退路径不登记，避免拖慢热路径）。
- [x] `_decode_record_at` 直接给出压平 tuple 形式的 extra，省掉逐行 dict 构造/再拆开。
- [x] 验证：`pytest tests/reader tests/sink/test_local_day.py ... -q`（117 passed）；
      重跑 parse_kline 未回退。

## T5. 内存基准与归档
- [x] `python benches/bench_memory.py --all --json benches/results/memory_after_extra.json`
- [x] KiB/条 before→after：market 2.364→2.201（-6.9%）、kline 0.3624→0.2502（-31.0%）、
      vipdoc 0.5158→0.3355（-35.0%）。
- [x] kline / vipdoc ≥ -30%；market 按 D5 归因说明（结构 57% 为 bid/ask）。

## T6.5. bid/ask 紧凑化（追加范围，见 spec D6）
- [x] `Quote.bid/ask` 内部改 `array('d')` 交错存价格/量，空盘口为 `None`；
      属性改为 property + setter，首次访问惰性物化为 `list[Level]` 并缓存。
- [x] `_pack_levels` 兼容 `Level` / 行式 `Mapping`（`_row_to_quote` 传入 row 里的 dict）。
- [x] `to_dict` 空盘口快速路径（`[]`），不触发物化。
- [x] 测试：`TestLazyOrderBook` 五项（打包、物化复用、空盘口 append、setter 重打包、行式 dict）。
- [x] 验证：`pytest tests/domain/test_models_extra_contract.py tests/sources tests/client tests/sink/test_local_day.py -q`（exit 0）。
- [x] market KiB/条 2.201→1.378（对 baseline -41.7%），三项全部 ≥ -30%。

## T8. 兼容层（见 spec D7）
- [x] `Bar` / `Quote` 恢复 `@dataclass(init=False, eq=False, repr=False)` + 手写
      `__slots__`（无 `__dict__`）；`is_dataclass` / `fields` / `asdict` 可用。
- [x] `extra` 三态存储（`None` / 扁平 tuple / 物化 dict）+ property：
      `isinstance(extra, dict)` 恒真；空态零 dict 分配，首次当 dict 用才物化。
- [x] `to_dict` / `turnover_rate` 直读槽位，不因兼容层触发物化。
- [x] `server/serialization.jsonable` 改为先判 `to_dict`，锁住扁平化出口。
- [x] 百度适配器 `q.extra = {...}` → `q.extra.update({...})`。
- [x] 测试：`TestDataclassCompat`（is_dataclass / fields 顺序 / asdict / 无 `__dict__`）、
      `TestDictExtraMaterialization`（isinstance / 空态零分配 / 物化后与 dict 一致 / 重赋值）。
- [x] 基准：`memory_after_compat.json`（1.383 / 0.250 / 0.336）、
      `time_after_compat.json`（0.290 / 0.4174 / 0.0139）。

## T6. 文档与门禁
- [x] `docs/`/`CHANGELOG` 无需同步（Unreleased 保持「暂无。」；`gen_docs --check` 通过）。
- [x] `pytest -m "not network"`（exit 0，无 FAILED）
- [x] `ruff check .` && `ruff format --check .`（All checks passed / 548 files already formatted）
- [x] `mypy atst/`（Success: no issues found in 220 source files）
- [x] `python scripts/gen_docs.py --check`（exit 0）
- [x] 产出中文 Markdown 报告：`specs/memory-extra-container/report.md`。
