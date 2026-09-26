"""传输池参数可达性账（第 27 轮 A1，G40「声明⇄行动」家族的新格）。

`docs/ARCHITECTURE.md` 把"配置面即执行面契约"写成核心不变量，而本轮现读发现它只覆盖
`ConnectionPool.__init__` 的 **6/17** 个参数：其余 11 个在 `tstdx/` 生产树里没有任何构造点，
`docs/configuration.md` 的键账也不含它们。这类缺口的病不是"参数没用"，而是**读者无从知道
自己够不够得到**——旧口径下它既不进配置面、也不进任何表，改了没人红。

本判据把这件事变成一张必须闭合的账：

* 参数集 = 配置可达集 ∪ 文档点名集，两边都不许有富余（差集为空）；
* 配置可达集与文档点名集**不得相交**（同一个参数不许既说"配得到"又说"配不到"）；
* 文档点名集不得与 schema 字段名相交（否则"不经配置面"那句话本身就是假告示）；
* `family` 那一格声称"公开 API 按次给"，就真的从 `TdxClient.__init__` 现读它。

口径沿用 F-50/F-52 那条纪律：**只量这一个类**，不做全仓自动化读者推断（第 28 步已证明那种
扫描会在两处独立假阴上失明）。三条"零命中 ⇒ 判据自身失效"的自校写在下面的断言里。
"""

from __future__ import annotations

import dataclasses
import inspect
import re
from pathlib import Path
from typing import Any

from tstdx.client.sync import TdxClient
from tstdx.config.schema import Config
from tstdx.transport.pool import ConnectionPool, pool_settings_from_config

ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/configuration.md"
DOC = ROOT / DOC_REL
#: 表所在小节的锚。文档改这个标题会红——那是有意的：这张表有人对账，不是抄完就飘的散文。
HEADING = "### 传输池参数：哪些**不**经配置面"
_ROW_NAME = re.compile(r"^\|\s*`([A-Za-z_][A-Za-z0-9_]*)`\s*\|")


def _pool_params() -> list[str]:
    """`ConnectionPool.__init__` 的形参（去掉 ``self``）。"""
    params = list(inspect.signature(ConnectionPool.__init__).parameters)
    assert params and params[0] == "self", f"签名形状与判据假设不符：{params[:2]}"
    names = params[1:]
    assert names, "一个参数都没读到，说明本判据自身失效"
    return names


def _config_reachable() -> set[str]:
    """配置面真正翻译出去的键——用默认 `Config` 现读，不抄文档里的名单。"""
    keys = set(pool_settings_from_config(Config()))
    assert keys, "配置可达集为空，说明本判据自身失效"
    return keys


def _schema_field_names() -> set[str]:
    """五个配置段的全部字段名（用来证明"不经配置面"那句没有反例）。"""
    cfg = Config()
    names: set[str] = set()
    for section in Config._SUBCONFIGS:
        sub: Any = getattr(cfg, section)
        if dataclasses.is_dataclass(sub):
            names |= {f.name for f in dataclasses.fields(sub)}
    assert names, "schema 一个字段都没读到，说明本判据自身失效"
    return names


def _doc_block() -> str:
    text = DOC.read_text(encoding="utf-8")
    if HEADING not in text:
        raise AssertionError(f"{DOC_REL} 里找不到小节锚 {HEADING!r}，判据对文档改名是瞎的")
    rest = text.split(HEADING, 1)[1]
    return re.split(r"^## ", rest, maxsplit=1, flags=re.M)[0]


def _doc_named() -> set[str]:
    names = {m.group(1) for line in _doc_block().splitlines() if (m := _ROW_NAME.match(line))}
    assert names, f"{DOC_REL} 那张表一行都没数到，说明本判据自身失效"
    return names


def _diff(params: list[str], config_keys: set[str], doc_names: set[str]) -> dict[str, set[str]]:
    """四方对账的纯函数：正控用它造红，不靠改文档。"""
    universe = set(params)
    return {
        "参数既不在配置面也不在文档": universe - config_keys - doc_names,
        "文档点名的参数已不在池签名里": doc_names - universe,
        "配置可达的参数被文档误列为不可达": config_keys & doc_names,
        "参数数与两张集合之和不符（重复归类）": (
            set()
            if len(universe) == len(config_keys | doc_names) and config_keys <= universe
            else {"规模不符"}
        ),
    }


def test_pool_params_are_all_either_config_reachable_or_doc_named() -> None:
    offenders = {
        k: sorted(v)
        for k, v in _diff(_pool_params(), _config_reachable(), _doc_named()).items()
        if v
    }
    assert offenders == {}, f"传输池参数可达性账不平：{offenders}"


def test_doc_named_params_are_not_secretly_schema_keys() -> None:
    """「没有配置键」这句话的反证：若某个点名参数其实是 schema 字段，文档就是假告示。"""
    collide = sorted(_doc_named() & _schema_field_names())
    assert collide == [], f"文档声称不经配置面、但配置 schema 里确有这些键：{collide}"


def test_family_row_is_the_public_api_it_claims() -> None:
    """表里 `family` 那格写"公开 API 按次给"——那就从 `TdxClient` 现读它。"""
    assert "family" in inspect.signature(TdxClient.__init__).parameters, (
        "文档把 family 归给公开 API，但 TdxClient.__init__ 已不接收它"
    )


def test_the_account_catches_every_planted_divergence() -> None:
    """三型违约各造一次正控：新增参数漏登记 / 文档抄错名字 / 一格双重归类。"""
    params, config_keys, doc_names = _pool_params(), _config_reachable(), _doc_named()

    # ① 池多一个没人登记的参数 ⇒ 必须进"未覆盖"
    planted_param = "planted_knob"
    assert _diff([*params, planted_param], config_keys, doc_names)[
        "参数既不在配置面也不在文档"
    ] == {planted_param}

    # ② 文档表里写一个池根本没有的名字 ⇒ 必须进"已不在签名里"
    assert _diff(params, config_keys, doc_names | {"tstdx_bogus_knob"})[
        "文档点名的参数已不在池签名里"
    ] == {"tstdx_bogus_knob"}

    # ③ 同一个参数既说配得到又说配不到 ⇒ 必须进"被文档误列"
    stolen = sorted(config_keys)[0]
    assert _diff(params, config_keys, doc_names | {stolen})["配置可达的参数被文档误列为不可达"] == {
        stolen
    }
