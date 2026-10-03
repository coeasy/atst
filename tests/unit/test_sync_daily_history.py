# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``scripts/sync_daily_history.py`` 里那几条"踩过才知道"的逻辑的判据。

脚本在 ``scripts/`` 下、不是 ``atst`` 包的一部分，所以这里按文件路径加载，
不去伪造一个包结构。要钉死的是**纯逻辑**——尤其是"指数位从代码段推出来"这条：
``0x052D`` 的 index 位弄反，主站回的是 `5616-57-83` 这种荒唐日期，
而它在本地看完全像一次网络抖动，极难定位。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "sync_daily_history.py"


#: 模块级导入会 add sys.path 并 import atst，重复加载会换来一堆告警，缓存住。
@pytest.fixture(scope="module")
def sync() -> ModuleType:
    spec = importlib.util.spec_from_file_location("atst_sync_daily_history", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # 必须先注册进 sys.modules：脚本里的 Target/State/Outcome 都是 @dataclass，
    # 而 dataclasses 靠 cls.__module__ 反查模块命名空间（本不存在的名字要解析成
    # 类型），没注册就会 AttributeError: 'NoneType' has no attribute '__dict__'。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _row(
    day: str, *, high: float = 10.0, low: float = 9.0, close: float = 9.5, open_: float = 9.0
) -> dict[str, object]:
    return {
        "date": day,
        "datetime": f"{day} 15:00",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100,
    }


# ---------------------------------------------------------------------------
# 指数位自动判定
# ---------------------------------------------------------------------------


def test_index_bit_is_derived_from_code_prefix(sync: ModuleType) -> None:
    """sh000* / sz399* 走指数位，其余走个股位；B 股（sh900*/sz200*）是个股。"""
    assert sync.Target.parse("sh000001").index is True
    assert sync.Target.parse("sz399006").index is True
    assert sync.Target.parse("sh000300").index is True
    assert sync.Target.parse("sh600519").index is False
    assert sync.Target.parse("sz900001").index is False


def test_symbol_prefix_is_tolerated_and_normalized(sync: ModuleType) -> None:
    """三种写法（带/不带市场前缀、大小写）必须归一到同一个代码。"""
    assert (
        sync.Target.parse("sh600519")
        == sync.Target.parse("SH600519")
        == sync.Target.parse("600519")
    )
    assert sync.Target.parse("600519").symbol == "sh600519"
    # 6 开头补 sh，其余补 sz
    assert sync.Target.parse("000001").symbol == "sz000001"
    assert sync.Target.parse("300750").symbol == "sz300750"


def test_index_flag_overrides_auto_detection(sync: ModuleType) -> None:
    """--index / --no-index 是整体覆盖，不是"默认再判一次"."""
    assert sync.Target.parse("sh600519", force_index=True).index is True
    assert sync.Target.parse("sh000001", force_index=False).index is False


# ---------------------------------------------------------------------------
# 类别：从代码段推出来，再决定目录与 index 位
# ---------------------------------------------------------------------------


def test_class_covers_every_asset_class_not_just_a_shares(sync: ModuleType) -> None:
    """六类标的都要识别得到：股票/指数/ETF/LOF/可转债/B 股。

    "全量"不等于"5000 只 A 股"——同一个代码段（sh51x）拿去拉 ETF 和拿去拉
    股票是两回事，类别判错的后果是落盘目录错位、index 位反了主站回荒唐日期。
    """
    assert sync.classify("sh600519") == "stock"
    assert sync.classify("sz300750") == "stock"
    assert sync.classify("sh000001") == "index"
    assert sync.classify("sz399006") == "index"
    assert sync.classify("sh510300") == "etf"
    assert sync.classify("sz159915") == "etf"
    assert sync.classify("sh501018") == "lof"
    assert sync.classify("sz160216") == "lof"
    assert sync.classify("sh113050") == "bond"
    assert sync.classify("sz127045") == "bond"
    # B 股看着像"指数"（sh900 开头是 9xx）也像股票，实际按个股拉
    assert sync.classify("sh900932") == "bshare"
    assert sync.classify("sz200011") == "bshare"


def test_index_bit_follows_the_class_not_the_market_prefix(sync: ModuleType) -> None:
    """只有 index 类走指数位；B 股 / ETF / 转债全部走个股位。"""
    for symbol in ("sh900932", "sh510300", "sz127045", "sh600519"):
        assert sync.Target.parse(symbol).index is False, symbol
    assert sync.Target.parse("sh000001").index is True
    assert sync.Target.parse("sz399001").index is True


def test_class_dir_is_one_directory_per_class(sync: ModuleType) -> None:
    """每类一个目录，落盘才不会把股票和 ETF 混成 5000 个同名文件。"""
    assert sync.class_dir("stock") == "stock"
    assert sync.class_dir("index") == "index"
    assert sync.class_dir("etf") == "etf"
    assert sync.class_dir("bond") == "bond"
    kinds = {kind for kind, _ in sync.CLASS_SEGMENTS}
    assert kinds == {"stock", "index", "etf", "lof", "bond", "bshare"}
    # 目录名必须 = 类别名，否则盘面看目录猜不出是哪一类
    assert all(sync.class_dir(kind) == kind for kind in kinds)


def test_every_scanned_segment_is_classifiable(sync: ModuleType) -> None:
    """段表的每一段都得能判回自己的类别——否则扫出来的代码会落错目录。"""
    for kind, segments in sync.CLASS_SEGMENTS:
        for segment in segments:
            assert sync.classify(f"{segment}123") == kind, f"{segment} 落到了别处"


def test_classify_delegates_to_the_shared_class_table(sync: ModuleType) -> None:
    """脚本的类别判定必须与 ``atst.universe`` 同源，不能是第二份实现。

    脚本原先自己遍历 ``CLASS_SEGMENTS``，而那张表按 ``_SCAN_ORDER`` 建、
    **里面没有 bse**，于是 ``bj920000`` 一路落到 ``return "stock"``——北交所
    就这样被写进 ``day/stock/``。同一个事实两处实现，迟早有一处是错的；现在
    脚本只留一个转发口，这条判据盯的就是"它还是不是同一个答案"。
    """
    from atst.universe import classify as shared
    from atst.universe import classify_with_index as shared_with_index

    probes = (
        "sh600519",
        "sz302132",
        "sh000001",
        "sz399006",
        "sh510300",
        "sh501018",
        "sh113050",
        "sh900932",
        "sz200011",
        "bj920000",
        "bj430047",  # 段表里没有的 bj 段：错的那一份会把它判成 stock
    )
    for symbol in probes:
        assert sync.classify(symbol) == shared(symbol), symbol
        assert sync.classify_with_index(symbol) == shared_with_index(symbol), symbol
    assert sync.classify("bj920000") == "bse"
    assert sync.class_dir("bse") == "bse"


# ---------------------------------------------------------------------------
# --scan：探测计划与两道判据
# ---------------------------------------------------------------------------


def test_scan_plan_sends_index_segments_with_the_index_bit(sync: ModuleType) -> None:
    """指数段必须带 index=True 去探。

    拿个股位探指数，主站回的是 `5616-57-83` 这种非空垃圾数据——"非空即存在"
    的判据会把它当真指数漏掉，或者反过来认进一堆假代码。
    """
    plan = sync.iter_scan_plan([("index", ("sh000",)), ("stock", ("sh600",))])
    assert ("sh000123", "index", True) in plan
    assert ("sh600123", "stock", False) in plan
    assert plan[0][0] == "sh000000"
    assert len(plan) == 2000


def test_scan_plan_matches_iter_scan_codes(sync: ModuleType) -> None:
    """探测计划不能漏掉段尾：段内 000-999 一个都不能少。"""
    plan = {code: index for code, kind, index in sync.iter_scan_plan([("etf", ("sh510",))])}
    assert len(plan) == 1000
    assert plan["sh510000"] is False
    assert plan["sh510999"] is False


def test_scan_exists_only_when_home_page_is_non_empty_and_dated(
    sync: ModuleType, monkeypatch
) -> None:
    """存在性判据是两道：非空首页 + 日期键是真日历日期。

    第二道不是装饰：`sh999999` 主站回 `0080-26-13`、另一支回 `8414-91-57`，
    非空且长得像日期，只判"非空"就会被当成真代码写进代码表。
    """

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def bars(self, code, period, count, index):
            self.calls.append(code)
            table = {
                "sh600519": [{"date": "2026-09-30"}],
                "sh999999": [{"date": "0080-26-13"}],  # 非空，但日期是假的
                "sh600000": [],  # 空首页：主站不认
            }
            return table.get(code, [])

    client = FakeClient()
    plan = sync.iter_scan_plan([("stock", ("sh600",))])
    plan.append(("sh999999", "stock", False))
    plan.append(("sh600000", "stock", False))
    found = sync.scan_universe(client, plan, workers=1, timeout=5)
    assert "sh600519" in found
    # 垃圾日期、空首页都不该进代码表
    assert "sh999999" not in found
    assert "sh600000" not in found
    assert found == {"sh600519": "stock"}


def test_scan_skips_known_symbols_and_retries_transport_errors(
    sync: ModuleType, monkeypatch
) -> None:
    """重跑 --scan 不该把已入库的代码重探一遍（段级断点），但真探的要重试。"""
    probed: list[str] = []

    class FlakyClient:
        def bars(self, code, period, count, index):
            probed.append(code)
            if code == "sh600123":
                raise RuntimeError("connection reset")
            return [{"date": "2026-09-30"}]

    plan = sync.iter_scan_plan([("stock", ("sh600",))])
    found = sync.scan_universe(FlakyClient(), plan, workers=1, timeout=5, known={"sh600000"})
    # 撞上传输异常的那支被重试到 attempts 次，而不是一次失败就判"不存在"
    assert probed.count("sh600123") == 2, "传输异常该重试而不是当成'不存在'"
    assert "sh600123" not in found, "重试仍失败才该判为不存在"
    # 已入库的那一支不该再被探（这就是重跑 --scan 秒回的原因）
    assert "sh600000" not in probed
    assert "sh600519" in found


# ---------------------------------------------------------------------------
# universe.csv：两列（代码, 类别）也要吃得下纯一列
# ---------------------------------------------------------------------------


def test_symbols_from_csv_reads_both_one_and_two_columns(sync: ModuleType, tmp_path: Path) -> None:
    """手搓的一列表和 --scan 产出的两列表都得能当 universe 用。"""
    one = tmp_path / "one.csv"
    one.write_text("symbol\n600519\n000001\n", encoding="utf-8")
    assert [t.symbol for t in sync.symbols_from_csv(one)] == ["sh600519", "sz000001"]

    two = tmp_path / "two.csv"
    two.write_text("sh510300,etf\nsh600519,stock\n", encoding="utf-8")
    parsed = {t.symbol: t.kind for t in sync.symbols_from_csv(two)}
    assert parsed == {"sh510300": "etf", "sh600519": "stock"}


# ---------------------------------------------------------------------------
# 续拉：断点不依赖 state.json
# ---------------------------------------------------------------------------


def test_gap_sleep_is_scaled_down_for_market_wide_runs(sync: ModuleType) -> None:
    """全市场 5200 只 × gap 0.2s 是 17 分钟纯在 sleep——限速得按规模折算。

    小清单照旧慢慢来；规模上去了折算下去，但压到 0.01s 下限（再小就不是
    "礼貌间隔"而是空转了）。想更礼貌就显式给 --gap-sleep，这个折算只是别让
    默认值把全市场同步变成 sitecopy。
    """
    assert sync._effective_gap(0.2, 50) == 0.2, "小清单不该被折算"
    assert sync._effective_gap(0.2, 300) == pytest.approx(0.2 * 200 / 300, rel=1e-9)
    assert sync._effective_gap(0.2, 5200) < 0.05
    assert sync._effective_gap(0.2, 100000) == 0.01, "折算有下限"


def test_day_file_count_skips_the_flat_layer(sync: ModuleType, tmp_path: Path) -> None:
    """统计只数类别子目录：迁移前的扁平文件是孤儿，不能重复计数。"""
    day = tmp_path / "day"
    (day / "stock").mkdir(parents=True)
    (day / "etf").mkdir()
    (day / "stock" / "sh600519.parquet").write_bytes(b"x")
    (day / "etf" / "sh510300.parquet").write_bytes(b"x")
    (day / "sh600036.parquet").write_bytes(b"x")  # 迁移前的扁平孤儿
    assert sync._count_day_files(day) == 2


def test_flat_legacy_file_is_retired_after_the_nested_write(
    sync: ModuleType, monkeypatch, tmp_path: Path
) -> None:
    """读旧扁平文件 → 写类别目录 = 一次迁移：写成功后扁平那份要收走。

    ``_path_of`` nested 优先，扁平文件在迁移成功后永远不会再被读——留着只是
    幽灵副本（占磁盘、迷惑翻目录的人）。它的数据已经合并进 nested 那份。
    """
    out = tmp_path / "day"
    flat = out / "sh600519.parquet"
    sync.write([_row("2026-09-29")], str(flat), fmt="parquet")

    class FakeClient:
        def bars(self, symbol: str, period: str = "day", count: int = 0, index: bool = False):
            return [_row("2026-09-30")]

    monkeypatch.setattr(sync, "TdxClient", lambda **_: FakeClient())
    monkeypatch.setattr(sync.time, "sleep", lambda *_: None)
    syncer = sync.Syncer(out, tmp_path / "state.json", gap_sleep=0, jitter=0)

    outcome = syncer.sync_one(sync.Target.parse("sh600519"))

    assert outcome.status == "OK"
    nested = out / "stock" / "sh600519.parquet"
    assert nested.exists()
    assert not flat.exists(), "扁平幽灵副本该收走"
    # 合并证据：扁平里的 09-29 + 新拉的 09-30 都在 nested 那份里
    assert sync._count_rows(nested) == 2
    assert sync._last_date_on_disk(nested) == "2026-09-30"


def test_replace_retries_when_windows_holds_the_target(
    sync: ModuleType, monkeypatch, tmp_path
) -> None:
    """覆盖同名文件撞 `PermissionError (WinError 5)` 要重试，不是直接抛出去。

    全市场重跑时实测最后一只就吃了这个（上一轮的产物正被系统锁着）。
    单只落盘失败必须降级成 SKIP 让整轮继续，所以重试在这里是降级路径的一部分。
    """
    target = tmp_path / "sh600519.parquet"
    target.write_text("old", encoding="utf-8")
    calls = {"n": 0, "always_fail": False}

    def flaky_replace(src: Path, dst: Path) -> None:
        calls["n"] += 1
        if calls["always_fail"] or calls["n"] < 3:
            raise PermissionError(5, "拒绝访问")
        dst.write_text("new", encoding="utf-8")

    monkeypatch.setattr(sync.os, "replace", flaky_replace)
    sync._replace_with_retry(tmp_path / "sh600519.parquet.tmp", target)
    assert calls["n"] == 3, "该重试而不是抛出去"
    assert target.read_text(encoding="utf-8") == "new"

    # 重试耗尽：该抛，交给 sync_one 转成 SKIP
    calls["always_fail"] = True
    with pytest.raises(OSError):
        sync._replace_with_retry(tmp_path / "sh600519.parquet.tmp", target)


def test_resume_count_falls_back_to_the_date_on_disk(sync: ModuleType, tmp_path: Path) -> None:
    """state 丢了也不退化成"最近 lookback 根从头再拉"——从磁盘那支尾部读回来。"""
    path = tmp_path / "sh600519.parquet"
    sync.write(
        [_row("2026-09-29"), _row("2026-09-30")],
        str(path),
        fmt="parquet",
    )

    assert sync._last_date_on_disk(path) == "2026-09-30"
    assert sync._resume_bar_count(False, 320, "2026-09-30") == 320 + 30
    # 没有磁盘证据时退回铺底窗口
    assert sync._resume_bar_count(False, 320, "") == 320
    # --full 无视一切证据直接拉满窗口
    assert sync._resume_bar_count(True, 320, "2026-09-30") == sync.MAX_WINDOW


def test_resume_count_only_trusts_the_disk(sync: ModuleType) -> None:
    """磁盘是取数决策的唯一事实源，``state.last_date`` 不参与。

    文件被清成 0 行（体检里的"空文件"）而 state 还记着旧日期时，若 state 兜底
    就只会拉 ``lookback + RESUME_OVERLAP`` 的小窗口，历史窗口静默缩水。写盘
    失败时断点不更新，state 只会比磁盘旧——砍掉它不丢任何真证据。
    """
    state = sync.State()
    state.last_date["sh600519"] = "2026-09-30"

    # 空文件（磁盘无证据）：必须退回铺底窗口，state 里那个日期不许兜底
    assert sync._resume_bar_count(False, 320, "") == 320
    # state 记得再多，磁盘有日期才走续拉窗口
    assert sync._resume_bar_count(False, 320, "2026-09-30") == 320 + 30


def test_last_date_on_disk_tolerates_missing_and_broken_files(
    sync: ModuleType, tmp_path: Path
) -> None:
    """坏文件不该让整轮同步崩掉，按"没存过"处理。"""
    assert sync._last_date_on_disk(tmp_path / "nope.parquet") == ""
    broken = tmp_path / "broken.parquet"
    broken.write_text("not a parquet", encoding="utf-8")
    assert sync._last_date_on_disk(broken) == ""


# ---------------------------------------------------------------------------
# 合并与自检
# ---------------------------------------------------------------------------


def test_rows_without_a_date_key_are_dropped(sync: ModuleType) -> None:
    """坏代码会回 `8414-91-57` 这种日期；没有日期键的行进不了时间序列。"""
    merged = sync._dedup_merge([_row("2026-01-05"), {"date": "8414-91-57"}, _row("2026-01-06")])
    assert [row["date"] for row in merged] == ["2026-01-06", "2026-01-05"] or len(merged) == 2
    assert all(row["date"] == "2026-01-05" or row["date"] == "2026-01-06" for row in merged)
    assert len(merged) == 2


def test_validate_rejects_unsorted_series_and_bad_ohlc(sync: ModuleType) -> None:
    """自检要在合并排序之后才做：原始批次倒序不该被当成"日期非升序"。"""
    assert sync._validate(sync._dedup_merge([_row("2026-01-06"), _row("2026-01-05")])) == []

    broken = sync._dedup_merge([_row("2026-01-05", high=9.0, low=10.0)])
    assert any("high<low" in problem for problem in sync._validate(broken))

    missing = sync._dedup_merge([{"date": "2026-01-05"}])
    assert any("缺 OHLC" in problem for problem in sync._validate(missing))


def test_scan_enumerates_every_code_in_a_segment(sync: ModuleType) -> None:
    """--scan 的探测器必须覆盖段内 000-999 全部候选。"""
    codes = list(sync.iter_scan_codes(["sh600"]))
    assert len(codes) == 1000
    assert codes[:3] == ["sh600000", "sh600001", "sh600002"]
    assert codes[-1] == "sh600999"


# ---------------------------------------------------------------------------
# 开场白：规模、预算、以及"你其实只跑了一点点"的提醒
# ---------------------------------------------------------------------------


def test_bar_budget_counts_requests_by_page_not_by_bar(sync: ModuleType) -> None:
    """成本项是请求次数：每只全历史要 8000/800 = 10 页，不是 8000 次请求。"""
    bars_full, requests_full = sync._bar_budget(28, full=True, lookback=320)
    assert (bars_full, requests_full) == (28 * sync.MAX_WINDOW, 28 * 10)

    bars_inc, requests_inc = sync._bar_budget(8385, full=False, lookback=320)
    assert bars_inc == 8385 * sync.BASELINE_WINDOW
    assert requests_inc == 8385  # 单页就够，一次请求一支

    #: 全市场铺底 → 全市场 --full，请求数涨一个数量级（8385 → 83850），
    #: 这个落差得让用户在开跑前就看到，而不是跑完才发现。
    _, wide_full = sync._bar_budget(8385, full=True, lookback=320)
    assert wide_full == 8385 * 10
    assert wide_full > requests_inc * 9


def test_plan_lines_warns_when_the_builtin_sample_universe_is_used(sync: ModuleType) -> None:
    """回落到内置样例宇宙必须点名「--full 不管拉多少只」，并给下一步命令。"""
    targets, source = sync._all_targets(), f"内置默认宇宙（{len(sync._all_targets())} 只）"
    lines = sync._plan_lines(
        targets, source, Path("data/kline/day"), full=True, lookback=320, universe_size=28
    )
    text = "\n".join(lines)
    assert "内置样例宇宙" in text
    assert "--full`` 只管每只拉多深，不管拉多少只" in text
    # 下一步命令要带上 root（原样拼回去，别让用户手打），且用正斜杠：命令行里
    # 反斜杠是转义符。只盯提示段——首行那个 root= 是按本机写法原样回显 Path 的。
    hint = text.split("注意：")[1]
    assert "--root data/kline/day --scan" in hint
    assert hint.rstrip().endswith("--root data/kline/day")
    # 两步命令都得拼上同一个 root：让用户直接整段复制，别自己手打。
    assert hint.count("--root data/kline/day") == 2
    assert "\\" not in hint
    # 预算行要落在开跑前，不是事后才补。
    assert any("预算：" in line for line in lines[:2])


def test_plan_lines_does_not_overclaim_when_limit_trimmed(sync: ModuleType) -> None:
    """``--limit 3`` 之后开场白不能跟着说"内置样例宇宙（3 只）"。"""
    targets, source = sync._all_targets(), "内置默认宇宙（28 只）"
    lines = sync._plan_lines(
        targets[:3], source, Path("data"), full=False, lookback=320, universe_size=28
    )
    assert "内置样例宇宙（28 只）" in "\n".join(lines)
    assert "内置样例宇宙（3 只）" not in "\n".join(lines)


def test_plan_lines_keeps_quiet_for_real_universes(sync: ModuleType) -> None:
    """真宇宙（清单 / 代码表）不该被塞一句"你跑了样例"的噪音。"""
    for source in ("清单 data/universe.csv（8385 只）", "代码表 data/universe.csv（8385 只）"):
        lines = sync._plan_lines(
            sync._all_targets(), source, Path("data"), full=False, lookback=320, universe_size=8385
        )
        assert not any("内置样例宇宙" in line for line in lines)


def test_plan_lines_flags_market_wide_full_runs(sync: ModuleType) -> None:
    """全市场 + --full 是把代价摊开讲的场合，28 只样例不该触发。"""
    targets = sync._all_targets()
    quiet = sync._plan_lines(
        targets, "内置默认宇宙（28 只）", Path("data"), full=True, lookback=320, universe_size=28
    )
    assert not any("提醒：" in line for line in quiet)

    #: 护栏看的是**宇宙规模**，所以这里得真的喂进一个全市场规模的列表——
    #: 光把 universe_size 写成 8385 而 targets 仍只有 28 只，护栏数的是 len(targets)。
    wide_targets = targets * (sync.FULL_WIDE_TARGETS // len(targets) + 1)
    wide = sync._plan_lines(
        wide_targets,
        "清单 data/u.csv（8385 只）",
        Path("data"),
        full=True,
        lookback=320,
        universe_size=len(wide_targets),
    )
    assert len(wide_targets) > sync.FULL_WIDE_TARGETS
    assert any(line.startswith("提醒：") for line in wide)
    assert any("次主站请求" in line for line in wide)


# ---------------------------------------------------------------------------
# 默认宇宙
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 落盘的收尾动作
# ---------------------------------------------------------------------------


def test_atomic_write_clears_the_stale_csv_shadow(sync: ModuleType, tmp_path: Path) -> None:
    """parquet 落盘后顺手清掉同名 csv 影子。

    这条曾经是**静默死代码**：清理挂在 ``_replace_with_retry`` 那个"循环正常结束"
    的分支下，而那条路径要么 ``return`` 要么 ``raise``，永远走不到。所以搬进
    ``_atomic_write_parquet`` 的 ``finally``——成败都清。
    """
    target = tmp_path / "sh600519.parquet"
    shadow = tmp_path / "sh600519.csv"
    shadow.write_text("老 csv", encoding="utf-8")
    sync._atomic_write_parquet([_row("2026-09-30")], target)
    assert target.exists()
    assert not shadow.exists(), "csv 影子还挂在盘上"


def test_run_tally_carries_the_latest_trading_day(sync: ModuleType, monkeypatch, tmp_path) -> None:
    """汇总里带"全市场最新交易日"——盘后一眼看出数据停在哪天。

    取的是**结果里的最大日期**，不碰 ``datetime.now()``：那个一沾就是时区坑
    （本地 UTC+8 过、CI UTC 偏 8 小时的老教训），而"数据有多新"本该只由行情自己回答。
    """
    days = {"sh600519": "2026-09-29", "sz399001": "2026-09-30", "sh600036": "2026-09-28"}

    def fake_sync_one(self, target: sync.Target) -> sync.Outcome:
        return sync.Outcome(target.symbol, "OK", rows=10, day=days[target.symbol])

    monkeypatch.setattr(sync.Syncer, "sync_one", fake_sync_one)
    monkeypatch.setattr(sync.time, "sleep", lambda *_: None)

    out = tmp_path / "day"
    monkeypatch.setattr(sync, "TdxClient", lambda **_: None)
    syncer = sync.Syncer(out, tmp_path / "state.json", dry_run=True, gap_sleep=0, jitter=0)
    tally = syncer.run([sync.Target.parse(s) for s in days], workers=2)

    assert tally["OK"] == 3
    assert tally["latest_day"] == "2026-09-30"


def test_run_retries_a_transient_skip_within_the_same_run(
    sync: ModuleType, monkeypatch, tmp_path
) -> None:
    """首轮 SKIP（主站单节点瞬时抖动）当轮二次重投成功 → 最终 OK、不残留 SKIP。

    不必等下一轮 cron 才把瞬时失败捞回来；REJECT/EMPTY/协议不支持的不重投。
    """
    calls = {"n": 0}

    def flaky_sync_one(self: sync.Syncer, target: sync.Target) -> sync.Outcome:
        calls["n"] += 1
        if calls["n"] == 1:
            return sync.Outcome(target.symbol, "SKIP", detail="TdxError: 瞬时抖动")
        return sync.Outcome(target.symbol, "OK", rows=5, day="2026-10-01")

    monkeypatch.setattr(sync.Syncer, "sync_one", flaky_sync_one)
    monkeypatch.setattr(sync.time, "sleep", lambda *_: None)
    out = tmp_path / "day"
    monkeypatch.setattr(sync, "TdxClient", lambda **_: None)
    syncer = sync.Syncer(out, tmp_path / "state.json", gap_sleep=0, jitter=0)
    tally = syncer.run([sync.Target.parse("sh600519")], workers=1)

    assert tally["OK"] == 1, "二次重投把瞬时 SKIP 变成了 OK"
    assert tally.get("SKIP", 0) == 0, "不该残留 SKIP 计数"
    assert calls["n"] == 2, "确实发生了一次重投"


def test_run_does_not_retry_an_unsupported_symbol(sync: ModuleType, monkeypatch, tmp_path) -> None:
    """北交所 bj* 即便首轮 SKIP 也不当轮重投——协议层必败，重投纯浪费请求。"""
    calls = {"n": 0}

    def skip_sync_one(self: sync.Syncer, target: sync.Target) -> sync.Outcome:
        calls["n"] += 1
        return sync.Outcome(target.symbol, "SKIP", detail="TdxError: E3040")

    monkeypatch.setattr(sync.Syncer, "sync_one", skip_sync_one)
    monkeypatch.setattr(sync.time, "sleep", lambda *_: None)
    out = tmp_path / "day"
    monkeypatch.setattr(sync, "TdxClient", lambda **_: None)
    syncer = sync.Syncer(out, tmp_path / "state.json", gap_sleep=0, jitter=0)
    tally = syncer.run([sync.Target.parse("bj920000")], workers=1)

    assert tally["SKIP"] == 1
    assert calls["n"] == 1, "bj* 不该被二次重投"
    assert "bj920000" not in syncer.state.failed, "协议不支持的不写进 state.failed"


def test_default_universe_is_real_and_mixture_of_stocks_and_indices(sync: ModuleType) -> None:
    """内置宇宙是"零参数可跑"的最后一道兜底，别让它掺进主站认不出来的代码。"""
    targets = [sync.Target.parse(symbol) for symbol, _ in sync.DEFAULT_UNIVERSE]
    assert targets, "内置宇宙不能是空的"
    assert any(target.index for target in targets), "没有指数的话默认宇宙覆盖不到指数位"
    assert any(not target.index for target in targets)
    for target in targets:
        assert target.symbol.startswith(("sh", "sz")), target.symbol
        assert target.index == target.symbol.startswith(sync.INDEX_PREFIXES)


# ---------------------------------------------------------------------------
# --fetch-list：代码表从哪来
# ---------------------------------------------------------------------------


def test_write_universe_csv_roundtrips_through_atst_universe(
    sync: ModuleType, tmp_path: Path
) -> None:
    """脚本写的代码表必须能被 atst.universe 直接读回（同一张表，两个入口）。"""
    from atst.universe import from_table

    root = tmp_path / "root"
    root.mkdir()
    rows = [("sh600519", "stock", "贵州茅台"), ("bj920000", "bse", "XD安徽凤")]
    sync.write_universe_csv(rows, root / "universe.csv")

    assert [item.code for item in from_table(root, "stock")] == ["sh600519"]
    assert [item.name for item in from_table(root, "bse")] == ["XD安徽凤"]


def test_fetch_list_picks_up_bse_even_though_the_scan_order_table_does_not_have_it(
    sync: ModuleType, tmp_path: Path, capsys
) -> None:
    """**回归**：``--fetch-list`` 曾漏掉北交所。

    代码取自脚本的扫描顺序表（``_SCAN_ORDER`` 里没有 bse——客户端 E3040 探不出来
    ），而 ``run_fetch_list`` 当时拿这张表当类别候选面，``bse`` 因此永远进不了
    ``kinds``，新浪 ``hs_a`` 里的 ``bj*`` 也就没人去收。类别候选面改成只有
    atst 的类别表，北交所才回到清单里。
    """
    from atst.universe import ASSET_CLASSES, from_table

    assert "bse" not in {name for name, _ in sync.CLASS_SEGMENTS}, "扫描顺序表变了吗？"
    assert "bse" in {item.name for item in ASSET_CLASSES}, "atst 类别表不该丢 bse"

    root = tmp_path / "root"
    root.mkdir()
    # 预置一份带 bj 行的代码表（等价于"新浪/缓存已经给过 bj*"），走 cache 源离线复现。
    sync.write_universe_csv(
        [("sh600519", "stock", "贵州茅台"), ("bj920000", "bse", "XD安徽凤")],
        root / "universe.csv",
    )

    args = SimpleNamespace(
        fetch_class="all", fetch_source="table", fetch_dry_run=False, workers=2, timeout=5.0
    )
    assert sync.run_fetch_list(args, root) == 0

    written = from_table(root, "bse")
    assert [item.code for item in written] == ["bj920000"], "北交所丢了——就是当年那个 bug"
    assert "bse" in capsys.readouterr().out


def test_fetch_list_reports_a_missing_explicit_source_instead_of_aborting(
    sync: ModuleType, tmp_path: Path, capsys
) -> None:
    """显式 ``--source table`` 要 ETF：这一源给不出来就该说清楚，且不许废掉整张表。"""
    from atst.universe import from_table

    root = tmp_path / "root"
    root.mkdir()
    sync.write_universe_csv([("sh600519", "stock", "贵州茅台")], root / "universe.csv")

    args = SimpleNamespace(
        fetch_class="all", fetch_source="table", fetch_dry_run=False, workers=2, timeout=5.0
    )
    assert sync.run_fetch_list(args, root) == 0

    out = capsys.readouterr().out
    assert "没有 etf 的标的" in out, out
    assert [item.code for item in from_table(root, "stock")] == ["sh600519"], "前几类也该落盘"


# ---------------------------------------------------------------------------
# --doctor：不联网的体检（同步链路的验收读数）
# ---------------------------------------------------------------------------


def _doctor_args(sync: ModuleType, **overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {"index": None, "state": None, "json": False}
    base.update(overrides)
    return SimpleNamespace(**base)


def _write_day(sync: ModuleType, day_dir: Path, kind: str, symbol: str, days: list[str]) -> None:
    """按脚本自己的落盘口径写一支日线（走真 sink，别手搓 parquet）。"""
    path = day_dir / kind / f"{symbol}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "date": day,
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000,
            "amount": 10500.0,
        }
        for day in days
    ]
    sync._atomic_write_parquet(rows, path)


def test_doctor_is_quiet_when_the_table_and_the_disk_agree(
    sync: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """清单里的每一只都落了盘 → 退出码 0，且不报缺口。"""
    root = tmp_path / "data"
    (root / "day").mkdir(parents=True)
    (root / "universe.csv").write_text(
        "代码,类别\nsh600519,stock\nbj920000,bse\n", encoding="utf-8"
    )
    _write_day(sync, root / "day", "stock", "sh600519", ["2026-09-30", "2026-10-01"])
    _write_day(sync, root / "day", "bse", "bj920000", ["2026-09-30", "2026-10-01"])

    assert sync.run_doctor(_doctor_args(sync), root) == 0
    out = capsys.readouterr().out
    assert "落盘：2/2" in out
    assert "最新交易日：2026-10-01" in out
    assert "缺口：0" in out


def test_doctor_reports_a_gap_that_never_landed(
    sync: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """清单里有、day/ 里没有 → 缺口 + 退出码 2 + 给出下一步命令。"""
    root = tmp_path / "data"
    (root / "day").mkdir(parents=True)
    (root / "universe.csv").write_text(
        "代码,类别\nsh600519,stock\nsz300750,stock\n", encoding="utf-8"
    )
    _write_day(sync, root / "day", "stock", "sh600519", ["2026-10-01"])

    assert sync.run_doctor(_doctor_args(sync), root) == 2
    out = capsys.readouterr().out
    assert "缺口：1" in out
    assert "sz300750" in out
    assert "--root" in out, "有缺口就得给出下一步，而不是只报一个数"


def test_doctor_flags_an_empty_file(
    sync: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """文件在、0 行 —— 最像成功的那种失败，必须单独一格。"""
    root = tmp_path / "data"
    (root / "day").mkdir(parents=True)
    (root / "universe.csv").write_text("代码,类别\nsh600519,stock\n", encoding="utf-8")
    _write_day(sync, root / "day", "stock", "sh600519", [])

    assert sync.run_doctor(_doctor_args(sync), root) == 2
    assert "空文件：1" in capsys.readouterr().out


def test_doctor_separates_stale_from_a_gap(
    sync: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """落伍是"要知道"，不是"要修"：它不该把退出码抬成 2。"""
    root = tmp_path / "data"
    (root / "day").mkdir(parents=True)
    (root / "universe.csv").write_text(
        "代码,类别\nsh600519,stock\nsz300750,stock\n", encoding="utf-8"
    )
    _write_day(sync, root / "day", "stock", "sh600519", ["2026-10-01"])
    _write_day(sync, root / "day", "stock", "sz300750", ["2026-09-01"])

    assert sync.run_doctor(_doctor_args(sync), root) == 0
    out = capsys.readouterr().out
    assert "落伍：1" in out
    assert "sz300750" in out


def test_doctor_treats_unsupported_classes_as_non_gap(
    sync: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """北交所（协议层 E3040 取不到）在 universe.csv 里、磁盘上没有 → 不算缺口、退出码 0。

    ``--fetch-list`` 会往 universe.csv 塞 348 只 bj，默认同步又静默剔除它们，
    若 doctor 把它们当缺口，每次体检都会假报几百只缺口。它们应单列「协议不支持」。
    """
    root = tmp_path / "data"
    (root / "day").mkdir(parents=True)
    (root / "universe.csv").write_text(
        "代码,类别\nsh600519,stock\nbj920000,bse\n", encoding="utf-8"
    )
    _write_day(sync, root / "day", "stock", "sh600519", ["2026-10-01"])
    # bj920000 不在磁盘上——这正是默认同步后的真实状态。

    assert sync.run_doctor(_doctor_args(sync), root) == 0
    out = capsys.readouterr().out
    assert "缺口：0" in out, "北交所缺文件不能是缺口"
    assert "协议不支持：1" in out
    assert "bj920000" in out


def test_doctor_state_drift_is_visible_in_the_report(
    sync: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """断点漂移要出现在报告里（且不抬退出码——磁盘才是断点第一事实源）。"""
    root = tmp_path / "data"
    (root / "day").mkdir(parents=True)
    (root / "universe.csv").write_text("代码,类别\nsh600519,stock\n", encoding="utf-8")
    _write_day(sync, root / "day", "stock", "sh600519", ["2026-10-01"])
    sync.State(last_date={"sh600519": "2026-09-01"}).dump(root / "state.json")

    assert sync.run_doctor(_doctor_args(sync), root) == 0
    assert "断点与磁盘不符：1" in capsys.readouterr().out


def test_doctor_json_is_machine_readable(
    sync: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--json`` 给的是同一份读数，键集合稳定。"""
    import json as _json

    root = tmp_path / "data"
    (root / "day").mkdir(parents=True)
    (root / "universe.csv").write_text("代码,类别\nsh600519,stock\n", encoding="utf-8")
    _write_day(sync, root / "day", "stock", "sh600519", ["2026-10-01"])

    assert sync.run_doctor(_doctor_args(sync, json=True), root) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert {
        "root",
        "universe",
        "expected",
        "on_disk",
        "by_kind",
        "latest_day",
        "missing",
        "empty",
        "stale",
        "state_drift",
    } <= set(payload)
    assert payload["on_disk"] == 1


def test_doctor_falls_back_to_the_builtin_universe(
    sync: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """没有代码表时看内置宇宙，并在报告里说明——别让人以为"体检了全市场"。"""
    root = tmp_path / "data"
    (root / "day").mkdir(parents=True)
    assert sync.run_doctor(_doctor_args(sync), root) == 2
    out = capsys.readouterr().out
    assert "内置默认宇宙" in out
    assert "缺口：" in out


def test_scan_and_fetch_list_write_the_same_table_shape(sync: ModuleType, tmp_path: Path) -> None:
    """``--scan`` 与 ``--fetch-list`` 落的代码表必须同形状（同写手 / 同表头 / 三列）。

    两个生产者各写各的格式，下游就迟早长出"先看是两列还是三列"的分支——那正是
    "同一件事在几处各说一遍"的开端。
    """
    scanned = sync._dump_universe(tmp_path, {"sh600519": "stock", "bj920000": "bse"})
    text = scanned.read_text(encoding="utf-8")
    assert text.splitlines()[0] == sync.UNIVERSE_HEADER
    assert text.splitlines()[1:] == ["bj920000,bse,", "sh600519,stock,"]

    fetched = tmp_path / "other.csv"
    sync.write_universe_csv([("bj920000", "bse", ""), ("sh600519", "stock", "")], fetched)
    assert fetched.read_text(encoding="utf-8") == text

    #: 两种形状都要能被同一个读手吃回来（含表头行）。
    assert [t.symbol for t in sync.symbols_from_csv(scanned)] == ["bj920000", "sh600519"]


def test_doctor_says_so_when_the_table_is_empty(
    sync: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """表在、却一只都读不出来 = 这次体检什么都没检，必须报 2 而不是给全 0 报告。"""
    root = tmp_path / "data"
    root.mkdir()
    (root / "universe.csv").write_text("代码,类别,名称\n", encoding="utf-8")
    assert sync.run_doctor(_doctor_args(sync), root) == 2
    assert "一只标的都没读出来" in capsys.readouterr().out
