# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``atst.universe`` 的判据（**离线**：新浪 / tdx 一律 monkeypatch 顶掉）。

要钉的不只是"classify 把 sh600519 判成 A 股"这种显然的事，而是三条踩过才知道的：

1. **跨源交叉比对挖出来的段**——``sz302`` 只有新浪有、旧段表漏它，漏了这只标的
   就永远扫不出来；北交所现在只剩 ``bj920`` 段，把 ``bj430/830/870`` 留在段表里
   只会让每一轮 ``--scan`` 白探几千个注定空的候选。段表变了必须在这红。
2. **``hs_a`` 是一个包供两个类别**——里面混着 ``bj*``，整包算 stock 就会把北交所
   塞进 ``day/stock/``。所以拉回来要按 classify 重新拆。
3. **``--dry-run`` 必须真的不探**——tdx 段探测一次十几分钟，dry-run 去跑它就
   失去意义了；``probe=False`` 只能报估算候选量。
"""

from __future__ import annotations

import pytest

from atst.universe import _sources, _walk_sources, row_needed_real_probe, universe_report
from atst.universe._classes import ASSET_CLASSES, class_of, classify, classify_with_index
from atst.universe._scan import iter_candidates, segment_candidates
from atst.universe._sources import _is_real_date_key, from_sina, from_sina_multi, from_table

# ---------------------------------------------------------------------------
# 类别判定
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        ("sh600519", "stock"),  # 沪主板
        ("sh688981", "stock"),  # 科创板
        ("sz300750", "stock"),  # 创业板
        ("sz002594", "stock"),  # 中小板已并入深主板
        ("sz302132", "stock"),  # 深市新段：跨源比对发现旧段表漏了它
        ("bj920000", "bse"),  # 北交所（2026 只剩 920 段）
        ("bj430047", "bse"),  # 段表里没有的 bj 段也必须是 bse：判据是前缀，不是段表
        ("sh510300", "etf"),
        ("sz159919", "etf"),
        ("sh501018", "lof"),
        ("sz160216", "lof"),
        ("sh113050", "bond"),  # 可转债
        ("sz121003", "bond"),
        ("sh000001", "index"),
        ("sz399006", "index"),  # 指数优先于"000 开头算 A 股"
        ("sh900901", "bshare"),  # 沪 B
        ("sz200011", "bshare"),  # 深 B
    ],
)
def test_classify_maps_code_prefix_to_asset_class(code: str, kind: str) -> None:
    """每个前缀段都必须落到唯一类别；index 不许被 stock 抢走。"""
    assert classify(code) == kind


@pytest.mark.parametrize(
    ("code", "kind", "index_bit"),
    [("sh000001", "index", True), ("sh600519", "stock", False), ("sz399006", "index", True)],
)
def test_classify_with_index_carries_the_0x052D_flag(code: str, kind: str, index_bit: bool) -> None:
    """指数位与类别必须同源，别再出现"类别判 index、指数位却按个股位"的分家。"""
    assert classify_with_index(code) == (kind, index_bit)


def test_every_asset_class_has_a_terminating_entry_point() -> None:
    """每个类别要么有新浪节点（秒级），要么有段表（可探测）——不能两头落空。

    落空的类别在 ``auto`` 下会永远走到 ``source="none"``，静默返回空清单。
    """
    for item in ASSET_CLASSES:
        assert item.sina_node or item.tdx_segments, f"{item.name} 既无新浪节点也无段表"


def test_bse_segments_are_pinned_to_the_real_2026_prefix() -> None:
    """北交所只剩 bj920：430/830/870 段实测为空，留着只是让 --scan 白探。"""
    assert class_of("bse").tdx_segments == ("bj920",)


def test_unknown_asset_class_raises_instead_of_defaulting_to_stock() -> None:
    """未知类别抛 KeyError——默认成 stock 只会把脏数据写进 day/stock/。"""
    with pytest.raises(KeyError, match="未知标的类别"):
        class_of("nope")


def test_bj_classification_is_by_prefix_not_by_the_probe_segments() -> None:
    """``bj`` 走前缀判据，不靠段表——"探哪些段"与"这代码算哪类"是两件事。

    早先写成 ``token[:3] == "bj"``，对真实代码恒为假（``"bj920000"[:3]`` 是
    ``"bj9"``），于是这条保护只剩 ``bj`` 两个字面量能触发，bse 实际全靠段表里
    的 ``bj920`` 兜着。北交所真开新段（430/830）那天，那些代码会被静默判成
    ``stock`` 写进 ``day/stock/``。B 股则相反：段（``sh900`` / ``sz200``）稳定，
    不该再抄一份前缀分支（早先那句 ``token[2:4] in ("00", "01")`` 是死分支）。
    """
    assert classify("bj920000") == "bse"
    assert classify("bj430047") == "bse"
    assert classify("bj830799") == "bse"
    assert "bj430" not in class_of("bse").tdx_segments


def test_every_declared_segment_classifies_back_to_its_own_class() -> None:
    """类别表里的每个段都必须判回它自己。

    段表同时是"探什么"和"算哪类"的事实源；这条一红就说明"探得到、却归错类"
    （``--scan`` 探出来的 ETF 会被写进 ``day/stock/``）。逐段取第一个流水号来判，
    判据从 :data:`ASSET_CLASSES` 推导，不从测试里再抄一份段表。
    """
    for item in ASSET_CLASSES:
        for segment in item.tdx_segments:
            assert classify(f"{segment}001") == item.name, f"{segment} 没判回 {item.name}"


# ---------------------------------------------------------------------------
# limit 语义：单类 / 全类别必须一致
# ---------------------------------------------------------------------------


def test_limit_zero_returns_nothing_and_touches_no_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``limit=0`` 就是"一条都不要"，且不必为此开网。

    早先的 ``rows[:limit] if limit else rows`` 把 ``0`` 当成"不限"，于是
    ``--limit 0`` 会先把全市场拉一遍再原样返回全部——参数写了不生效，还白付了
    tdx 那十几分钟。
    """
    from atst.universe import list_all_universe, list_universe

    def _boom(*_a: object, **_k: object) -> list[object]:
        raise AssertionError("limit=0 不该去碰任何取数源")

    monkeypatch.setattr(_sources, "from_table", _boom)
    monkeypatch.setattr(_sources, "from_sina", _boom)
    monkeypatch.setattr(_sources, "from_tdx_scan", _boom)
    assert list_universe("stock", limit=0) == []
    assert list_all_universe(limit=0) == []


def test_limit_is_per_class_when_asking_for_all(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``all`` + ``limit=N`` 是"每类 N 条"，不是"总量 N 条"。

    代码表 / 新浪那两级原先根本不接 ``limit``（只有 tdx 探测接），``all`` 那支
    因此对它们完全不截——同一个参数在两条路径上含义不同。
    """
    from atst.universe import list_all_universe

    root = tmp_path / "root"
    root.mkdir()
    (root / "universe.csv").write_text(
        "代码,类别,名称\n"
        "sh600519,stock,贵州茅台\nsh600000,stock,浦发银行\n"
        "sh510300,etf,沪深300ETF\nsh510500,etf,中证500ETF\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_sources, "from_sina", lambda *a, **k: [])
    monkeypatch.setattr(_sources, "from_tdx_scan", lambda *a, **k: [])
    rows = list_all_universe(root=root, limit=1)
    assert [row.code for row in rows] == ["sh600519", "sh510300"]


# ---------------------------------------------------------------------------
# 段表 → 候选代码
# ---------------------------------------------------------------------------


def test_segment_candidates_expands_to_three_digits() -> None:
    """一个段 = 1000 个候选（``sh600`` → sh600000…sh600999）。"""
    codes = list(segment_candidates("sh600"))
    assert len(codes) == 1000
    assert codes[0] == "sh600000"
    assert codes[-1] == "sh600999"


@pytest.mark.parametrize("segment", ["", "ab", "sh6", "sh60a", "600600", "sh60"])
def test_malformed_segments_yield_nothing(segment: str) -> None:
    """段必须形如 ``字母 + 3 位数字``；混进裸 6 位数字会枚举出一大片错码。"""
    assert list(segment_candidates(segment)) == []


def test_iter_candidates_dedups_across_segments() -> None:
    """多段合并要按段顺序且去重（``iter_candidates`` 也吃单个字符串）。"""
    merged = list(iter_candidates(("sz127", "sz127", "sz131")))
    assert merged[:2] == ["sz127000", "sz127001"]
    assert merged[-1] == "sz131999"
    assert len(merged) == 1000 + 1000
    assert list(iter_candidates("sh510"))[:1] == ["sh510000"]


# ---------------------------------------------------------------------------
# 源 1：磁盘代码表
# ---------------------------------------------------------------------------


def test_from_table_reads_three_column_table_with_names(tmp_path: pytest.TempPathFactory) -> None:
    """三列表（代码,类别,名称）连名称一起带出来。"""
    root = tmp_path / "root"
    root.mkdir()
    (root / "universe.csv").write_text(
        "代码,类别,名称\n"
        "sh600519,stock,贵州茅台\n"
        "sz300750,stock,宁德时代\n"
        "\n"
        "# 注释行不该被当成标的\n",
        encoding="utf-8",
    )
    rows = from_table(root, "stock")
    assert [item.code for item in rows] == ["sh600519", "sz300750"]
    assert [item.name for item in rows] == ["贵州茅台", "宁德时代"]


def test_from_table_falls_back_to_prefix_for_single_column(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """老格式只有一列代码时，按前缀现判类别，别把整张表当成一个类别。"""
    root = tmp_path / "root"
    root.mkdir()
    (root / "universe.csv").write_text("sh600519\nsz159919\n", encoding="utf-8")
    assert [item.code for item in from_table(root, "stock")] == ["sh600519"]
    # 同一张表换个类别取，就该只拿到 ETF
    assert [item.code for item in from_table(root, "etf")] == ["sz159919"]


def test_from_table_returns_empty_when_file_or_root_is_missing(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """没表 = 这一源没拿下（继续降级），不是错误。"""
    assert from_table(tmp_path / "empty", "stock") == []


# ---------------------------------------------------------------------------
# 源 2：新浪节点
# ---------------------------------------------------------------------------


class _Quote:
    """新浪行情对象的替身（只看得到 .code / .extra["name"]）。"""

    def __init__(self, code: str, name: str) -> None:
        self.code = code
        self.extra = {"name": name}


class _FakeSinaSource:
    def __init__(self, payload: list[_Quote]) -> None:
        self._payload = payload
        self.nodes: list[str] = []

    def fetch_all(self, node: str, page_size: int = 100, workers: int = 3) -> list[_Quote]:
        self.nodes.append(node)
        return list(self._payload)


@pytest.fixture()
def fake_sina(monkeypatch: pytest.MonkeyPatch) -> _FakeSinaSource:
    """把 atst.web.create_source 换成假源——``hs_a`` 包里混着 bj*。"""
    payload = [
        _Quote("sh600000", "浦发银行"),
        _Quote("bj920000", "XD安徽凤"),
        _Quote("sz300750", "宁德时代"),
    ]
    source = _FakeSinaSource(payload)
    monkeypatch.setattr("atst.web.create_source", lambda *_: source, raising=True)
    return source


def test_from_sina_multi_splits_the_shared_node_into_two_classes(
    fake_sina: _FakeSinaSource,
) -> None:
    """``hs_a`` 一个节点供 stock + bse 两个类别，只拉一次且按 classify 拆开。"""
    rows = from_sina_multi(("stock", "bse"))
    assert fake_sina.nodes == ["hs_a"]  # 共享节点只打一次
    assert sorted(item.code for item in rows) == ["bj920000", "sh600000", "sz300750"]
    assert {item.code: item.kind for item in rows} == {
        "sh600000": "stock",
        "sz300750": "stock",
        # 关键：bj* 必须归 bse，不能整包算作 A 股塞进 day/stock/
        "bj920000": "bse",
    }
    assert rows[0].name == "浦发银行"  # 名称要跟着走


def test_from_sina_multi_dedups(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一个代码被两个类别同时命中时只留一条。"""
    monkeypatch.setattr(
        "atst.web.create_source", lambda *_: _FakeSinaSource([_Quote("sh600000", "x")])
    )
    assert [item.code for item in from_sina_multi(("stock", "bse"))] == ["sh600000"]


def test_from_sina_of_a_class_without_a_node_is_empty() -> None:
    """新浪没有 ETF/LOF/可转债/指数节点（实测返回空），拿空则该走 tdx。"""
    assert from_sina("etf") == []
    assert from_sina("bond") == []


# ---------------------------------------------------------------------------
# 源 3：tdx 段探测的日期判据
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("2026-10-03 15:00", True),
        ("2026-10-03", True),
        ("0080-26-13 15:00", False),  # 月 26 日不存在
        ("8414-91-57", False),  # 主站垃圾日期
        ("5616-57-83", False),  # 指数位弄反时主站的回包
        ("", False),
        ("2026-1-3", False),
    ],
)
def test_real_date_key_rejects_nonsense(value: str, ok: bool) -> None:
    """日期键能构造出来才算数——只判"非空"会把垃圾码收进代码表。"""
    assert _is_real_date_key(value) is ok


# ---------------------------------------------------------------------------
# 降级编排
# ---------------------------------------------------------------------------


def test_walk_sources_stops_before_tdx_on_dry_run(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``skip_tdx`` 必须**不真探**——只回 "tdx" 这个结论 + 空结果。"""
    monkeypatch.setattr(_sources, "from_sina", lambda *a, **k: [])
    monkeypatch.setattr(
        _sources, "from_tdx_scan", lambda *a, **k: pytest.fail("dry-run 不该探 tdx")
    )
    rows, used = _walk_sources(
        class_of("etf"),
        root=tmp_path,  # 空根：代码表也没货
        limit=None,
        workers=1,
        timeout=1.0,
        skip_tdx=True,
    )
    assert rows == [] and used == "tdx"


def test_walk_sources_prefers_table_and_only_then_sina(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """auto 的顺序是代码表 → 新浪 → tdx，命中即止。"""
    root = tmp_path / "root"
    root.mkdir()
    (root / "universe.csv").write_text(
        "代码,类别,名称\nsh600519,stock,贵州茅台\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        _sources, "from_sina", lambda *a, **k: pytest.fail("代码表命中就不该走新浪")
    )
    rows, used = _walk_sources(class_of("stock"), root=root, limit=None, workers=1, timeout=1.0)
    assert used == "table"
    assert [item.code for item in rows] == ["sh600519"]


def test_report_estimates_instead_of_probing_on_dry_run(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没代码表 + 没新浪节点的类别，dry-run 只报候选量（段数 × 1000）。"""
    monkeypatch.setattr(_sources, "from_sina", lambda *a, **k: [])
    monkeypatch.setattr(
        _sources, "from_tdx_scan", lambda *a, **k: pytest.fail("dry-run 不该探 tdx")
    )
    rows = universe_report(
        ("etf", "bond"),
        source="auto",
        root=tmp_path,
        limit=None,
        workers=1,
        timeout=1.0,
        probe=False,
    )
    for row in rows:
        assert row["source"] == "tdx"
        assert row["count"] == 0
        assert row["estimate"] == row["segments"] * 1000


def test_report_survives_an_explicit_source_that_cannot_serve_one_class(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--dry-run --source sina`` 撞上"ETF 没有新浪节点"是**正常**，不是故障。

    :func:`~atst.universe._sources.from_sina` 对无节点的类别返回空，而显式源拿空会
    抛 ``LookupError``——报告层必须把它摊成表格里的一格（0 + 原因），不然
    ``--fetch-list --fetch-dry-run`` 会在 ETF 这一行炸掉，前面的报告全没了。
    """
    rows = universe_report(
        ("stock", "etf"), source="sina", root=tmp_path, workers=1, timeout=1.0, probe=False
    )
    assert [row["kind"] for row in rows] == ["stock", "etf"]
    assert rows[0]["error"] is None
    assert rows[1]["error"], "没给原因，用户只会看到「ETF 是空的」却不知道为什么"
    assert rows[1]["count"] == 0


def test_row_needed_real_probe_flags_only_the_missing_tdx_row() -> None:
    """只有"没拿到数 + 源本来是 tdx"这一行才触发估算。"""
    assert row_needed_real_probe({"count": 0, "source": "tdx"}) is True
    assert row_needed_real_probe({"count": 5, "source": "tdx"}) is False
    assert row_needed_real_probe({"count": 0, "source": "table"}) is False


def test_explicit_source_raises_lookerror_when_it_cannot_provide(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式指定源拿不到就抛——悄悄降级成另一份数据会让结果无法解释。"""
    monkeypatch.setattr(_sources, "from_table", lambda *a, **k: [])
    from atst.universe import list_universe

    with pytest.raises(LookupError):
        list_universe("stock", source="table", root=tmp_path, workers=1, timeout=1.0)
    with pytest.raises(LookupError, match="未知标的清单源"):
        list_universe("stock", source="nope", root=None, workers=1, timeout=1.0)


# ---------------------------------------------------------------------------
# 第 1 轮审计：单一事实源 / 无孤儿 / 无多余等待
# ---------------------------------------------------------------------------


def test_node_to_classes_is_derived_from_the_class_table() -> None:
    """节点↔类别 只能有一份事实源：手抄一份就会在换节点后悄悄变旧。"""
    from atst.universe._classes import ASSET_CLASSES, NODE_TO_CLASSES

    derived: dict[str, list[str]] = {}
    for item in ASSET_CLASSES:
        if item.sina_node:
            derived.setdefault(item.sina_node, []).append(item.name)
    assert {node: tuple(names) for node, names in derived.items()} == NODE_TO_CLASSES
    # hs_a 一个节点供两类，这是"整包要按 classify 拆开"的由来
    assert NODE_TO_CLASSES["hs_a"] == ("stock", "bse")
    assert NODE_TO_CLASSES["hs_b"] == ("bshare",)


def test_asset_class_has_no_unused_em_fs_knob() -> None:
    """``em_fs`` 曾是"宣告了却永远为 None"的幻影旋钮；缺口改由 TBD_SOURCES 如实登记。"""
    from atst.universe._classes import TBD_SOURCES, AssetClass

    assert not hasattr(AssetClass, "em_fs")
    assert any("clist" in name for name, _ in TBD_SOURCES), "缺口必须在文档里可见"


def test_from_sina_multi_reuses_one_source_across_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个节点也只用**一个**源实例：fetch_all 自带令牌桶，多实例=多份配额。"""
    created: list[_FakeSinaSource] = []

    def _factory(*_: object) -> _FakeSinaSource:
        src = _FakeSinaSource([])
        created.append(src)
        return src

    monkeypatch.setattr("atst.web.create_source", _factory)
    rows = from_sina_multi(("stock", "bse", "bshare"))
    assert rows == []
    assert len(created) == 1, "按节点重建源会让同一进程里并存多个限流器"


def test_from_table_dedups_a_repeated_code(tmp_path: pytest.TempPathFactory) -> None:
    """手搓的表里同一代码写两遍是常事，别把重复行带进清单。"""
    root = tmp_path / "root"
    root.mkdir()
    (root / "universe.csv").write_text(
        "代码,类别,名称\nsh600519,stock,贵州茅台\nsh600519,stock,贵州茅台\n", encoding="utf-8"
    )
    assert [item.code for item in from_table(root, "stock")] == ["sh600519"]


def test_from_table_stops_at_the_line_cap(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文件被写成别的东西时要有上限——判据挂在循环头，不是挂在末尾。"""
    root = tmp_path / "root"
    root.mkdir()
    (root / "universe.csv").write_text(
        "代码,类别,名称\n" + "".join(f"sh60000{i},stock,x\n" for i in range(9)), encoding="utf-8"
    )
    monkeypatch.setattr(_sources, "_MAX_TABLE_LINES", 3)
    assert len(from_table(root, "stock")) == 2  # 表头占 1 行，只剩 2 行额度


def test_probe_once_does_not_sleep_after_the_last_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """重试退避只在"还有下一次"时发生：最后一次失败后再睡一觉是白等。"""
    slept: list[float] = []
    monkeypatch.setattr(_sources.time, "sleep", lambda seconds: slept.append(seconds))

    class _Boom:
        def bars(self, *_: object, **__: object) -> object:
            raise RuntimeError("transport down")

    with pytest.raises(RuntimeError, match="transport down"):
        _sources._probe_once(_Boom(), "sh600000", False, attempts=3)
    assert len(slept) == 2, f"3 次尝试只该有 2 次退避，实际 {len(slept)}"


def test_tdx_scan_order_follows_candidates_not_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """结果顺序按**候选顺序**出，不许泄漏 ``as_completed`` 的完成序。

    完成顺序随并发负载与网络抖动变；一旦泄漏进结果，"同一份数据两次跑出两份
    顺序"就会让 CLI 输出、快照判据、以及"两条取数链路结果应当一致"的比较变成
    偶发红。这里让第一只候选故意最慢，逼完成序与候选序相反。
    """
    import time as _time

    from atst.universe._sources import from_tdx_scan

    slow = "sh110000"  # bond 段的第一个候选

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None: ...

        def bars(self, code: str, **_kwargs: object) -> list[dict[str, str]]:
            if code == slow:
                _time.sleep(0.4)
            return [{"datetime": "2026-09-30"}]

        def close(self) -> None: ...

    monkeypatch.setattr("atst.client.TdxClient", _FakeClient)
    rows = from_tdx_scan("bond", workers=3, limit=6, attempts=1)
    assert [row.code for row in rows] == [f"sh11000{i}" for i in range(6)]


def test_the_package_has_no_orphan_private_symbols() -> None:
    """``atst/universe`` 里每个私有模块级符号都得真被用上。

    专抓"定义了、导出着、却谁都不引用"这一类：它们不会让任何测试变红，只会
    让下一个人以为"这里有个口子可以走"。判据是**引用数 ≥ 2**（定义 + 至少一次使用）。
    """
    import ast
    import re
    from pathlib import Path

    package = Path(_sources.__file__).resolve().parent
    sources = {path.name: path.read_text(encoding="utf-8") for path in package.glob("*.py")}
    blob = "\n".join(sources.values())

    orphans: list[str] = []
    for name, text in sources.items():
        for node in ast.parse(text).body:
            targets: list[str] = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                targets = [node.name]
            elif isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            for target in targets:
                if not target.startswith("_") or target.startswith("__"):
                    continue
                if len(re.findall(rf"\b{re.escape(target)}\b", blob)) < 2:
                    orphans.append(f"{name}:{target}")
    assert not orphans, f"孤儿私有符号（定义了但没人用）：{orphans}"


# ---------------------------------------------------------------------------
# CLI 面：与 Python 面同口径
# ---------------------------------------------------------------------------


def _cli_args(**overrides: object) -> object:
    from types import SimpleNamespace

    base: dict[str, object] = {
        "kind": "all",
        "source": "auto",
        "root": "data",
        "limit": None,
        "workers": 1,
        "show": 10,
        "list_class": False,
        "dry_run": False,
        "json": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_cli_dry_run_never_probes_tdx(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``atst universe all --dry-run`` 不许真探 tdx：那正是 dry-run 要避开的代价。"""
    from atst.cli.runtime_commands import _cmd_universe

    monkeypatch.setattr(_sources, "from_sina", lambda *a, **k: [])
    monkeypatch.setattr(
        _sources, "from_tdx_scan", lambda *a, **k: pytest.fail("--dry-run 不该探 tdx")
    )
    rc = _cmd_universe(_cli_args(root=str(tmp_path), dry_run=True))
    assert rc == 0


def test_cli_dry_run_reports_the_candidate_estimate(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:  # type: ignore[no-untyped-def]
    """没代码表、没新浪节点的类别要报"约 N 候选待探测"，不能只丢一个 0。"""
    from atst.cli.runtime_commands import _cmd_universe

    monkeypatch.setattr(_sources, "from_sina", lambda *a, **k: [])
    _cmd_universe(_cli_args(kind="etf", root=str(tmp_path), dry_run=True))
    out = capsys.readouterr().out
    assert "候选待探测" in out
    assert str(len(class_of("etf").tdx_segments) * 1000) in out


def test_cli_list_class_json_is_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    """``--list-class --json`` 与人类可读版出自同一份类别表。"""
    import json

    from atst.cli.runtime_commands import _cmd_universe

    assert _cmd_universe(_cli_args(list_class=True, json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [row["kind"] for row in payload] == [item.name for item in ASSET_CLASSES]
    assert {"kind", "label", "sina_node", "segments", "index_bit", "directory"} == set(payload[0])


def test_cli_list_class_prints_the_sina_reference_table(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``SINA_NODES`` 是公开参考数据，得真出现在某个面上，而不是只躺在模块里。"""
    from atst.cli.runtime_commands import _cmd_universe
    from atst.universe._classes import SINA_NODES

    assert _cmd_universe(_cli_args(list_class=True)) == 0
    out = capsys.readouterr().out
    for node, _label in SINA_NODES:
        assert node in out


def test_cli_rejects_an_unknown_kind_with_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    """未知类别要报错退出，别静默当 stock。"""
    from atst.cli.runtime_commands import _cmd_universe

    assert _cmd_universe(_cli_args(kind="nope")) == 2
    assert "未知标的类别" in capsys.readouterr().err


def test_cli_rejects_the_stale_cache_source_name(capsys: pytest.CaptureFixture[str]) -> None:
    """旧文档里的 ``--source cache`` 早改叫 ``table``；报错要说清新名字。"""
    from atst.cli.runtime_commands import _cmd_universe

    assert _cmd_universe(_cli_args(kind="stock", source="cache")) == 2
    assert "table" in capsys.readouterr().err


def test_cli_dry_run_json_is_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:  # type: ignore[no-untyped-def]
    """``--dry-run --json`` 必须真给 JSON——不然用户拿到人读文本却以为拿到了 JSON。"""
    import json

    from atst.cli.runtime_commands import _cmd_universe

    monkeypatch.setattr(_sources, "from_sina", lambda *a, **k: [])
    assert _cmd_universe(_cli_args(kind="etf", root=str(tmp_path), dry_run=True, json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["kind"] == "etf"
    assert payload[0]["estimate"] == len(class_of("etf").tdx_segments) * 1000
