# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""标量请求字段的四面一致口径：布尔/浮点不得按 Python 真值规则静默读错。

已修缺陷（2026-11-22）：
* MCP ``strict`` 用 ``bool("false")`` → ``True``，与 HTTP(FastAPI) / WS 相反；
* WS ``subscribe.interval`` 用整数口读，小数 ``1.5`` 被 E1010 拒绝，而 CLI/Python 接受；
* WS ``subscribe.diff_only`` 用裸 ``bool()``，``"false"`` → ``True``。

这里把三条口统一后的行为钉住：缺席用声明缺省、合法原样、坏值当场 ValidationError。
"""

from __future__ import annotations

import pytest

from atst.errors import ValidationError
from atst.server.wire_fields import as_request_bool, as_request_float

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("false", False),
        ("0", False),
        ("no", False),
        ("true", True),
        ("1", True),
        (True, True),
        (False, False),
        (None, False),
    ],
)
def test_as_request_bool(value: object, expected: bool) -> None:
    assert as_request_bool(face="t", where="t", name="strict", value=value) is expected


@pytest.mark.parametrize("value", ["tdx", 1, [], "yesplease"])
def test_as_request_bool_rejects_ambiguous(value: object) -> None:
    with pytest.raises(ValidationError, match="布尔"):
        as_request_bool(face="t", where="t", name="strict", value=value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1.5, 1.5), ("2.5", 2.5), (1, 1.0), (None, 1.0)],
)
def test_as_request_float(value: object, expected: float) -> None:
    got = as_request_float(face="t", where="t", name="interval", value=value, default=1.0, lo=0.0)
    assert got == expected


@pytest.mark.parametrize("value", [True, "abc", float("nan"), float("inf"), -1, []])
def test_as_request_float_rejects_bad(value: object) -> None:
    with pytest.raises(ValidationError, match="有限实数"):
        as_request_float(face="t", where="t", name="interval", value=value, default=1.0, lo=0.0)
