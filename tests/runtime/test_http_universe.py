# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""HTTP 面上的「按类别取标的清单」：与库层/CLI 面同口径。

这张面存在的理由不是"多一个接口"，而是**同一个业务事实不许在几处各说一遍**：
类别表出自 :data:`atst.universe.ASSET_CLASSES`，清单本身就出自
:data:`atst.universe.from_table` 读的那份代码表，这条路由只做协议翻译
（查询串 → 读表 → JSON）。所以这里的判据全部对着**库层的真身**比，不抄期望值。

第二件要钉死的是这张面**永不外呼**：新浪那一级是秒级外呼、tdx 那一级是分钟级作业，
把它们塞进请求路径就等于把后台批任务搬进网关（占住连接、超时语义全乱）。所以这里
用"把 ``from_sina`` / ``from_tdx_scan`` 换成炸弹"来量——真要外呼，测试当场炸。

入参错归类是第三件：类别名写错必须落在 E1010（客户端写错），不许变成
"未捕获异常 → internal error"——本库在四面一致地把入参错误报成 E9000 这件事上
吃过一次亏（V18-C1）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from atst.client.api import Client  # noqa: E402
from atst.integration.runtime_http import create_runtime_app  # noqa: E402
from atst.universe import ASSET_CLASSES  # noqa: E402
from atst.universe._classes import NODE_TO_CLASSES, SINA_NODES  # noqa: E402


def _table_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    (root / "universe.csv").write_text(
        "代码,类别,名称\n"
        "sh600519,stock,贵州茅台\n"
        "sz300750,stock,宁德时代\n"
        "bj920000,bse,XD安徽凤\n"
        "sh510300,etf,沪深300ETF\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture()
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """把两个联网源换成炸弹：这张面**任何**外呼都是缺陷。"""

    def _boom(*_: object, **__: object) -> object:
        raise AssertionError("HTTP 清单面不该外呼（新浪/tdx 都不行）")

    monkeypatch.setattr("atst.universe._sources.from_sina", _boom)
    monkeypatch.setattr("atst.universe._sources.from_tdx_scan", _boom)


@pytest.fixture()
def http() -> object:
    #: ``raise_server_exceptions=False``：fastapi 的 ``@app.exception_handler(Exception)``
    #: 收在 Starlette 的 ``ServerErrorMiddleware`` 里，它**发完信封还会把异常重新抛出**。
    #: 想让"入参错误 → E1010 信封"这件事在测试里可观测，就必须关掉这个重抛
    #: （与 ``tests/architecture/test_face_exposure_projection.py`` 同一口径）。
    with TestClient(create_runtime_app(Client()), raise_server_exceptions=False) as client:
        yield client


def test_universe_classes_route_is_the_same_table_as_the_package(
    http: object, no_network: None
) -> None:
    """``GET /v13/universe`` 与库层的类别表逐格相等（含节点反向索引）。"""
    payload = http.get("/v13/universe").json()  # type: ignore[attr-defined]
    assert [row["kind"] for row in payload["classes"]] == [item.name for item in ASSET_CLASSES]
    assert {row["node"] for row in payload["sina_nodes"]} == {node for node, _ in SINA_NODES}
    assert payload["node_to_classes"]["hs_a"] == ["stock", "bse"]
    assert set(payload["node_to_classes"]) == set(NODE_TO_CLASSES)


def test_universe_route_reads_the_code_table(
    http: object, tmp_path: Path, no_network: None
) -> None:
    """读 ``<root>/universe.csv``，按类别切，且把名称带出来。"""
    root = _table_root(tmp_path)
    payload = http.get(  # type: ignore[attr-defined]
        "/v13/universe/stock", params={"root": str(root)}
    ).json()
    assert payload["kind"] == "stock"
    assert payload["source"] == "table"
    assert payload["count"] == 2
    assert payload["items"] == [
        {"code": "sh600519", "name": "贵州茅台", "kind": "stock"},
        {"code": "sz300750", "name": "宁德时代", "kind": "stock"},
    ]


def test_universe_route_carries_the_northbound_class(
    http: object, tmp_path: Path, no_network: None
) -> None:
    """北交所在代码表里是独立类别，不该被并进 stock。"""
    root = _table_root(tmp_path)
    payload = http.get(  # type: ignore[attr-defined]
        "/v13/universe/bse", params={"root": str(root)}
    ).json()
    assert [item["code"] for item in payload["items"]] == ["bj920000"]


def test_universe_route_serves_all_classes_in_the_table_order(
    http: object, tmp_path: Path, no_network: None
) -> None:
    """``kind=all`` = 类别表顺序拼接，不是文件里的原始行序。"""
    root = _table_root(tmp_path)
    payload = http.get(  # type: ignore[attr-defined]
        "/v13/universe/all", params={"root": str(root)}
    ).json()
    assert payload["count"] == 4
    kinds = [item["kind"] for item in payload["items"]]
    order = [item.name for item in ASSET_CLASSES]
    assert kinds == sorted(kinds, key=order.index), f"类别顺序不是类别表顺序：{kinds}"


def test_universe_route_honours_the_limit(http: object, tmp_path: Path, no_network: None) -> None:
    """``limit`` 对清单生效（试水用），报的 count 是截断之后的数。"""
    root = _table_root(tmp_path)
    payload = http.get(  # type: ignore[attr-defined]
        "/v13/universe/stock", params={"root": str(root), "limit": 1}
    ).json()
    assert payload["count"] == 1
    assert [item["code"] for item in payload["items"]] == ["sh600519"]


def test_a_missing_code_table_is_a_state_not_an_error(
    http: object, tmp_path: Path, no_network: None
) -> None:
    """表还没生成是部署常态：200 + count 0 + 一句怎么办，不是 4xx。"""
    payload = http.get(  # type: ignore[attr-defined]
        "/v13/universe/stock", params={"root": str(tmp_path / "nope")}
    ).json()
    assert payload["count"] == 0
    assert payload["items"] == []
    assert "--fetch-list" in payload["hint"]


def test_the_route_never_reaches_out_to_the_network(
    http: object, tmp_path: Path, no_network: None
) -> None:
    """``no_network`` 装的是炸弹：这条请求能 200，就证明它没走新浪/tdx。"""
    root = _table_root(tmp_path)
    assert http.get("/v13/universe/etf", params={"root": str(root)}).status_code == 200  # type: ignore[attr-defined]


def test_an_unknown_class_is_a_validation_error(
    http: object, tmp_path: Path, no_network: None
) -> None:
    """类别名写错 → E1010 + 422，不许变成 E9000 的 internal error。"""
    response = http.get(  # type: ignore[attr-defined]
        "/v13/universe/nope", params={"root": str(_table_root(tmp_path))}
    )
    assert response.status_code == 422, response.text
    body = response.json()["error"]
    assert body["code"] == "E1010"
    assert "未知标的类别" in body["message"]
    assert "stock" in body["message"], "报错要顺手把人该用的名字念出来"


def test_undeclared_query_knobs_are_still_rejected(
    http: object, tmp_path: Path, no_network: None
) -> None:
    """查询串白名单 = 路由签名：``?source=sina`` 这种"想让它外呼"的旋钮当场 422。"""
    response = http.get(  # type: ignore[attr-defined]
        "/v13/universe/stock", params={"root": str(_table_root(tmp_path)), "source": "sina"}
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "E1010"
    assert "source" in body["message"]
