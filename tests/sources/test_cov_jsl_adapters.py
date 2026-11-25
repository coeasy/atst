# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""集思录可转债适配器离线单测（覆盖率补强）。

全部离线：直接喂 JSON 文本给 ``parse()``。覆盖 ``rows`` / ``data`` 两种顶层
形状、``cell`` 包裹与裸行、非映射行跳过、字段回退，以及非 JSON 响应翻译成
``SourceDeprecated``（E7030，HTTP 410 语义）。
"""

from __future__ import annotations

import json

import pytest

from atst.errors import SourceDeprecated
from atst.sources.jsl.adapters import JslSource

pytestmark = pytest.mark.unit


def _src() -> JslSource:
    # 集思录声明 needs_cookie，构造时必须给一个（不发请求）。
    return JslSource(cookie="canned-cookie", max_retries=0)


def test_build_url_default_and_override() -> None:
    src = _src()
    assert src.build_url(["113001"]) == JslSource.BASE
    assert src.build_url(["113001"], url="https://example.test/cb") == "https://example.test/cb"


def test_parse_rows_with_cell_and_bare_rows() -> None:
    """cell 包裹与裸行都接受；非映射行跳过；价格字段有回退。"""
    payload = {
        "rows": [
            {
                "cell": {
                    "bond_id": "113001",
                    "price": "110.50",
                    "last_price": "109.80",
                    "volume": "1200",
                    "amount": "132600",
                    "bond_nm": "转债A",
                }
            },
            {"cell": {"id": "113002", "curr_iss_amt": "100.20", "pre_price": "99.90"}},
            {"bond_id": "113003"},
            {"cell": "not-a-mapping"},
            "raw-row",
        ]
    }
    quotes = _src().parse(json.dumps(payload), ["113001"])
    assert [q.code for q in quotes] == ["113001", "113002", "113003"]

    first = quotes[0]
    assert first.price == pytest.approx(110.50)
    assert first.last_close == pytest.approx(109.80)
    assert first.volume == 1200
    assert first.amount == pytest.approx(132600.0)
    assert first.extra["name"] == "转债A"
    assert first.extra["raw"]["bond_id"] == "113001"

    # price 缺失时回退 curr_iss_amt；last_close 回退 pre_price
    second = quotes[1]
    assert second.price == pytest.approx(100.20)
    assert second.last_close == pytest.approx(99.90)
    assert second.volume == 0
    assert second.extra["name"] == ""


def test_parse_accepts_data_key() -> None:
    """没有 rows 时读 data（兼容旧版响应形状）。"""
    payload = {"data": [{"cell": {"bond_id": "123001", "price": "101"}}]}
    quotes = _src().parse(json.dumps(payload), [])
    assert [q.code for q in quotes] == ["123001"]


def test_parse_empty_payload_shapes() -> None:
    """缺 rows/data 或空 rows 都返回空列表，不抛错。"""
    assert _src().parse("{}", []) == []
    assert _src().parse(json.dumps({"rows": []}), []) == []
    assert _src().parse(json.dumps({"rows": None}), []) == []


def test_parse_non_json_is_source_deprecated() -> None:
    """HTML/登录页 → SourceDeprecated（E7030），context 带 200 字样本。"""
    text = "<html><body>请先登录</body></html>"
    with pytest.raises(SourceDeprecated) as caught:
        _src().parse(text, [])
    assert caught.value.code == "E7030"
    assert caught.value.context["source"] == "jsl"
    assert caught.value.context["sample"] == text[:200]
    assert isinstance(caught.value.cause, json.JSONDecodeError)


@pytest.mark.parametrize("payload", ["[]", "42", '"x"', "null", '[{"bond_id": "1"}]'])
def test_parse_json_but_not_object_is_source_deprecated(payload: str) -> None:
    """合法 JSON 但顶层不是对象：同样是改版/需 cookie 信号，必须落 E7030。

    回归：此前 ``payload.get(...)`` 会抛 ``AttributeError``，被基座改写成通用
    解析失败（WebSourceError），丢掉「源已改版」这一可操作信号。
    """
    with pytest.raises(SourceDeprecated) as caught:
        _src().parse(payload, [])
    assert caught.value.code == "E7030"
    assert caught.value.context["source"] == "jsl"
    assert caught.value.context["sample"] == payload[:200]
