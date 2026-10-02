#!/usr/bin/env python3
# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""CLI 接口有效性探针（P-interface）。

逐条真实调用 ``atst`` 的每一个对外入口（29 个子命令 / 嵌套子动作 / ``query``
通用口），把结果归到六档，输出一张矩阵。判据分两层：

* **解析面**（永远必须绿）：每个子命令、每个嵌套子动作都能``--help`` 过
  argparse，且必填参数确实必填、缺了给非零退出码。这一层不碰网络，
  任何红都是接口本身的缺陷。
* **调用面**（按契约分层）：真实发一次请求，看返回的 JSON 是不是
  ``{"data": ...}`` 信封、数据是不是非空、命中的错误类是不是这一路**允许**
  失败的那个类。TDX 已登记 offline 的命令（``security_list`` /
  ``minute_history`` / ``block_quotes`` 等）报 ``CommandOffline`` 是**预期**，
  不算接口无效；报的是没登记过的类才是。

六档结论：

============================  ===========================================
``OK``                        拿到数据，非空
``EMPTY``                     接口通了但返回空（可能是非交易时段，需复核）
``OFFLINE``                   该能力在两个 Provider 上都不是 alive（账本级失效）
``NETWORK``                   连接/超时/限流——环境问题，不是接口缺陷
``BROKEN``                    接口自爆：argparse 面过、真实调用却抛没登记的异常
``MALFORMED``                 该出 JSON 却不是合法 JSON，或信封缺 ``data``/``capabilities``
============================  ===========================================

退出码：``0`` 全绿；``1`` 存在 ``BROKEN`` 或 ``MALFORMED``（接口真的无效）；
``2`` 网络全断（环境不可用，不是本仓缺陷，等同于"跳过"）。

用法::

    python scripts/verify_interfaces.py                  # 全量（含网络）
    python scripts/verify_interfaces.py --mode argparse  # 只扫解析面，秒级
    python scripts/verify_interfaces.py --mode core     # 只打 tdx 核心链
    python scripts/verify_interfaces.py --out reports/interface_matrix.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT

#: 退出码
EXIT_OK = 0
EXIT_BROKEN = 1
EXIT_NO_NETWORK = 2


@dataclass(frozen=True)
class Probe:
    """一条探针：一次真实调用（或一次 argparse 面检查）。"""

    id: str
    argv: list[str]
    #: ``text`` 出裸文本（``version``），``envelope`` 出 ``{"data": ...}``，
    #: ``capabilities`` 出 ``{"capabilities": [...]}``，``stats`` 出裸统计对象，
    #: ``table`` 出 markdown 表格（这些命令不带 ``--json`` 时的默认面）
    shape: str = "envelope"
    #: 数据必须非空才算 OK（``capabilities`` / ``security-count`` 这类恒非空）
    non_empty: bool = True
    #: 只跑哪一层（``all`` = 默认全量）
    group: str = "all"


@dataclass
class Result:
    """一条探针的观测结果。"""

    probe_id: str
    argv: list[str]
    status: str = "BROKEN"
    exit_code: int | None = None
    elapsed_ms: int = 0
    detail: str = ""
    error_class: str = ""

    @property
    def fatal(self) -> bool:
        """是否构成"接口无效"（必查）。"""
        return self.status in {"BROKEN", "MALFORMED"}


# --------------------------------------------------------------------------
# 探针表：每条给出真实可发的参数。宁可少写一条，也不要留一个"看起来能跑"
# 实际必红的参数组合——那只会把矩阵变成噪音。
# --------------------------------------------------------------------------

TDX_BARS = ["bars", "sh600519", "--provider", "tdx", "--period", "day", "--count", "5"]
PROBES: tuple[Probe, ...] = (
    # ---- 解析面：不需要真实请求，恒绿 ----
    Probe("argparse:version", ["--help"], shape="text", non_empty=False, group="argparse"),
    Probe("argparse:bars", ["bars", "--help"], shape="text", non_empty=False, group="argparse"),
    Probe(
        "argparse:fund nav",
        ["fund", "nav", "--help"],
        shape="text",
        non_empty=False,
        group="argparse",
    ),
    Probe(
        "argparse:hosts list",
        ["hosts", "list", "--help"],
        shape="text",
        non_empty=False,
        group="argparse",
    ),
    Probe(
        "parse-error:missing-required", ["bars"], shape="text", non_empty=False, group="argparse"
    ),
    # ---- 本地：不碰网络 ----
    Probe("version", ["version"], shape="text", non_empty=True),
    Probe("capabilities", ["capabilities"], shape="capabilities"),
    Probe("feedback stats", ["feedback", "stats", "--json"], shape="stats"),
    Probe("hosts list", ["hosts", "list"], shape="table"),
    Probe("server-test", ["server-test"], shape="table", non_empty=False),
    # ---- TDX 核心链：协议主路，必须能给数 ----
    Probe("bars(tdx)", TDX_BARS),
    Probe("quotes(tdx)", ["quotes", "sh600519", "--provider", "tdx"]),
    Probe("snapshot(tdx)", ["snapshot", "sh600519", "--provider", "tdx"], non_empty=False),
    Probe("minute(tdx)", ["minute", "sh600519", "--provider", "tdx"], non_empty=False),
    Probe("security-count(tdx)", ["security-count", "--provider", "tdx"], shape="envelope"),
    Probe(
        "adjusted-bars(tdx)", ["adjusted-bars", "sh600519", "--count", "5", "--json"], shape="table"
    ),
    Probe(
        "minute-klines(tdx)",
        ["minute-klines", "sh600519", "--period", "5min", "--count", "5", "--json"],
        shape="table",
    ),
    Probe("trades(tdx)", ["trades", "sh600519", "--provider", "tdx"], non_empty=False),
    Probe(
        "query:bars",
        [
            "query",
            "bars",
            "--provider",
            "tdx",
            "--kwargs_json",
            '{"symbol": "sh600519", "count": 3}',
        ],
    ),
    # ---- Web Provider 面：东财/新浪/腾讯/百度，允许网络层失败 ----
    Probe("all-market", ["all-market", "--page_size", "5", "--json"], shape="table"),
    Probe("blocks", ["blocks", "1", "--count", "5", "--json"], shape="table"),
    Probe("changes", ["changes", "--size", "5", "--json"], shape="table"),
    Probe("hot", ["hot", "--size", "5", "--json"], shape="table"),
    Probe("margin", ["margin", "sh600519", "--json"], shape="table"),
    Probe("sector-flow", ["sector-flow", "--json"], shape="table"),
    Probe("baidu", ["baidu", "sh600519", "--count", "5", "--json"], shape="table"),
    Probe("fund nav", ["fund", "nav", "--code", "000001", "--json"], shape="table"),
    Probe("fund estimate", ["fund", "estimate", "--code", "000001", "--json"], shape="table"),
    Probe("fund list", ["fund", "list", "--json"], shape="table"),
    Probe(
        "index constituents", ["index", "constituents", "--code", "399006", "--json"], shape="table"
    ),
    Probe("goods", ["goods", "sh600519", "--json"], shape="table"),
    Probe("f10", ["f10", "sh600519", "--json"], shape="table"),
    Probe("list", ["list", "1", "--count", "5", "--json"], shape="table"),
    Probe("quotes-snapshot", ["quotes-snapshot", "sh600519", "--json"], shape="table"),
    Probe(
        "probe:0x0530",
        ["probe", "0x0530", "--market", "1", "--code", "600519", "--json"],
        shape="table",
    ),
    # ---- 已登记 offline 的 TDX 命令：报 CommandOffline 是预期 ----
    # security-list 走表格面，不支持 --json（送 --json 会被 argparse 拒绝）
    Probe("security-list(tdx,offline)", ["security-list", "--provider", "tdx"], shape="table"),
)

#: 分组：argparse 面 / local（不碰网络）/ tdx（协议主路）/ web（网页源）/ offline（账本已登记失效）
TDX_IDS = {
    "bars(tdx)",
    "quotes(tdx)",
    "snapshot(tdx)",
    "minute(tdx)",
    "security-count(tdx)",
    "security-list(tdx,offline)",
    "minute-klines(tdx)",
    "trades(tdx)",
    "query:bars",
}
LOCAL_IDS = {"version", "capabilities", "feedback stats", "hosts list", "server-test"}
OFFLINE_IDS = {
    "security-list(tdx,offline)",
    "blocks",
    "list",
    "f10",
    "goods",
    "changes",
    "hot",
}


def group_for(probe: Probe) -> str:
    """给探针定组：决定它在 ``--mode core`` 这一档跑不跑。"""
    if probe.id.startswith("argparse:") or probe.id.startswith("parse-error:"):
        return "argparse"
    if probe.id in TDX_IDS:
        return "tdx"
    if probe.id in LOCAL_IDS:
        return "local"
    if probe.id in OFFLINE_IDS:
        return "offline"
    return "web"


def _shape_of(payload: Any) -> str:
    """判断返回体属于哪一类信封。"""
    if isinstance(payload, dict):
        if "capabilities" in payload:
            return "capabilities"
        if "data" in payload:
            return "envelope"
        if "error" in payload:
            return "error"
        if "total_commands" in payload:
            return "stats"
    if isinstance(payload, list):
        return "envelope"
    return "unknown"


#: 错误类 → 允许的结论。键是 ``error`` 字段 / ``type`` 字段名，值决定降级到哪档。
TOLERANCE: dict[str, str] = {
    # 账本登记 offline / 布局未锁定：命令**能发**，但结构化入口拦在发包前
    "CommandOffline": "OFFLINE",
    "NotImplementedFeature": "OFFLINE",
    # 传输层与网页源：环境级，不是接口缺陷
    "ConnectionFailed": "NETWORK",
    "AllHostsUnreachable": "NETWORK",
    "ConnectionClosed": "NETWORK",
    "ReadTimeout": "NETWORK",
    "TimeoutError": "NETWORK",
    "RateLimited": "NETWORK",
    "RateLimitedLocal": "NETWORK",
    "WebSourceError": "NETWORK",
    # 内容级：服务器答了但给不出可用内容（如 F10 停发空字节）
    "DataError": "OFFLINE",
    "TruncatedDataError": "NETWORK",
}

#: 错误码里的措辞兜底（东财 push2 常见的 "Server disconnected" 只落在 message 里）
TOLERANCE_HINTS: tuple[tuple[str, str], ...] = (
    ("多主站实测无响应", "OFFLINE"),
    ("实测无响应", "OFFLINE"),
    # E9010：命令账本里该命令的 body 布局还没经过真机 golden 验证，已主动停止发包
    ("尚未经过真机 golden 验证", "OFFLINE"),
    # E2040：主站池整体不可达（本机出口问题，不是接口缺陷）
    ("所有主站均不可达", "NETWORK"),
    ("读取超时", "NETWORK"),
    ("Server disconnected", "NETWORK"),
    ("httpx 请求失败", "NETWORK"),
    ("连接超时", "NETWORK"),
    ("403", "NETWORK"),
    ("429", "NETWORK"),
)


def _error_class_of(text: str) -> str:
    """从输出里抠出错误类名（信封里通常带 ``error`` / ``type`` / ``class``）。"""
    try:
        found = _error_class_of_payload(json.loads(text))
        if found:
            return found
    except (json.JSONDecodeError, TypeError):
        pass
    for key in ('"error"', '"type"', '"class"', "error_class"):
        pos = text.find(key)
        if pos < 0:
            continue
        rest = text[pos + len(key) :].lstrip().lstrip(": ").lstrip()
        for closer in ('"', "'"):
            if rest.startswith(closer):
                end = rest.find(closer, 1)
                if end > 0:
                    return rest[1:end]
        return rest.split(",")[0].split("}")[0].strip()[:48]
    return ""


def _error_class_of_payload(node: Any) -> str:
    """在 JSON 树里找 ``error`` / ``type`` / ``class`` 三个键的任一个值。"""
    queue: list[Any] = [node]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            for key in ("error", "type", "class", "error_class"):
                value = current.get(key)
                if isinstance(value, str) and value:
                    return value
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)  # 宽搜先查当前层，避免钻进 data 里的业务字段
    return ""


def _tolerance_of(text: str) -> str:
    """按错误类或措辞决定这条失败是否"允许"。"""
    cls = _error_class_of(text)
    if cls in TOLERANCE:
        return TOLERANCE[cls]
    for hint, verdict in TOLERANCE_HINTS:
        if hint in text:
            return verdict
    return ""


def _is_argv_error(stderr: str) -> bool:
    """argparse 面失败（非零 + usage 措辞）。"""
    return (
        "unrecognized arguments" in stderr
        or "invalid choice" in stderr
        or "the following" in stderr
    )


def _run(probe: Probe, timeout: int) -> Result:
    """跑一条探针，返回观测结果。"""
    started = time.monotonic()
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    argv = [sys.executable, "-m", "atst", *probe.argv]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(REPO),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return Result(
            probe.id, probe.argv, "BROKEN", None, int((time.monotonic() - started) * 1000), "超时"
        )
    except OSError as exc:  # pragma: no cover - 环境异常
        return Result(
            probe.id,
            probe.argv,
            "BROKEN",
            None,
            int((time.monotonic() - started) * 1000),
            f"{type(exc).__name__}: {exc}",
        )

    ms = int((time.monotonic() - started) * 1000)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    result = Result(probe.id, probe.argv, "BROKEN", proc.returncode, ms, "")

    # argparse 面明确拒绝入参（缺必填 / 错参数名）：这是它该有的行为
    if proc.returncode == 2 and _is_argv_error(err):
        result.status = "OK"
        result.detail = "argparse 正确拒绝非法入参"
        return result
    if not out:
        result.status = "BROKEN"
        # 截断只留给展示用：这里存完整文本，否则 _classify 抠不出错误类名
        result.detail = err or out
        return result
    if "Traceback (most recent call last)" in out or "Traceback (most recent call last)" in err:
        result.status = "BROKEN"
        result.detail = ""  # 交给 _classify 按错误类定档
        result.detail = (err or out).splitlines()[-1][:160]
        return result

    # version / --help 走裸文本：只要非空即通
    if probe.shape == "text":
        result.status = "OK"
        result.detail = out.splitlines()[0][:60] if out.splitlines() else ""
        return result

    # markdown 表格面：非 JSON 是正常的默认输出，只要不是空壳就通
    if probe.shape == "table":
        lines = [line for line in out.splitlines() if line.strip()]
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, list):
            # 探针都带 --json，整张表会压成一行文本，按记录条数判定而非文本行数
            if not payload:
                result.status = "EMPTY"
                result.detail = "表格面返回空集合"
            else:
                result.status = "OK"
                result.detail = f"{len(payload)} 条记录"
            return result
        if len(lines) < 2:
            result.status = "EMPTY"
            result.detail = "表格面只吐出一行"
        else:
            result.status = "OK"
            result.detail = f"{len(lines)} 行表格"
        return result

    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        result.status = "MALFORMED"
        result.detail = f"非法 JSON（{exc.msg}）：{out[:120]}"
        return result

    shape = _shape_of(payload)
    if shape == "error":
        # 接口显式报错：交给 _classify 看它命中的是不是"允许失败"的错误类
        # （必须留完整 JSON——截断会让 json 解析失败，抠不出错误类名）
        result.error_class = _error_class_of(out)
        result.detail = out
        return result
    if shape != probe.shape and probe.shape != "text":
        result.status = "MALFORMED"
        result.detail = f"信封不符：期望 {probe.shape}，实得 {shape}"
        return result

    if probe.shape == "stats":
        result.status = "OK" if payload else "MALFORMED"
        result.detail = "裸统计对象" if payload else "空统计对象"
        return result

    if probe.shape == "capabilities":
        names = payload.get("capabilities") or []
        if not isinstance(names, list) or not names:
            result.status = "MALFORMED"
            result.detail = "capabilities 不是非空列表"
            return result
        result.status = "OK"
        result.detail = f"{len(names)} 个能力名"
        return result

    data = payload.get("data") if isinstance(payload, dict) else payload
    if data is None:
        result.status = "MALFORMED"
        result.detail = "信封缺 data"
        return result
    if probe.non_empty and isinstance(data, (list, dict)) and not data:
        result.status = "EMPTY"
        result.detail = "返回空集合"
        return result

    result.status = "OK"
    if isinstance(data, list):
        result.detail = f"{len(data)} 行"
    elif isinstance(data, dict):
        result.detail = f"{len(data)} 键"
    else:
        result.detail = str(data)[:40]
    return result


def _classify(result: Result) -> Result:
    """把 BROKEN 降级到 OFFLINE / NETWORK：命中的是"允许失败"的错误类就摘干净。"""
    if result.status != "BROKEN":
        return result
    verdict = _tolerance_of(result.detail)
    result.error_class = _error_class_of(result.detail)
    if verdict:
        result.status = verdict
        result.detail = f"{result.error_class or '环境'}：{result.detail[:80]}"
    return result


# --------------------------------------------------------------------------
# 解析面专用：把 argparse 面整棵扫一遍
# --------------------------------------------------------------------------


def scan_argparse_surface(timeout: int = 30) -> list[Result]:
    """每个子命令 / 嵌套子动作用 ``--help`` 过一遍 argparse。"""
    sys.path.insert(0, str(REPO))
    from atst.cli.parser import build_parser  # 局部导入：CLI 只在解析面才需要

    parser = build_parser()
    results: list[Result] = []

    def walk(sub_action: Any, prefix: list[str]) -> None:
        for name, child in sorted(sub_action.choices.items()):
            argv = [*prefix, name, "--help"]
            started = time.monotonic()
            proc = subprocess.run(
                [sys.executable, "-m", "atst", *argv],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(REPO),
            )
            ms = int((time.monotonic() - started) * 1000)
            ok = proc.returncode == 0 and bool((proc.stdout or "").strip())
            results.append(
                Result(
                    " ".join(argv[:-1]),
                    argv,
                    "OK" if ok else "BROKEN",
                    proc.returncode,
                    ms,
                    "" if ok else ((proc.stderr or proc.stdout) or "无输出")[-160:],
                )
            )
            for nested in child._actions:
                if type(nested).__name__ == "_SubParsersAction":
                    walk(nested, [*prefix, name])

    subs = [a for a in parser._actions if type(a).__name__ == "_SubParsersAction"]
    if subs:
        walk(subs[0], [])
    return results


# --------------------------------------------------------------------------


def _render(results: list[Result]) -> str:
    lines = [
        "",
        "接口有效性矩阵",
        "==============",
        "",
        "{:<12} {:<34} {:>6}  说明".format("结论", "探针", "耗时"),
        "-" * 96,
    ]
    for r in sorted(results, key=lambda x: (x.status, x.probe_id)):
        lines.append(f"{r.status:<12} {r.probe_id:<34} {r.elapsed_ms:>5}ms  {r.detail[:44]}")
    total = len(results)
    counter: dict[str, int] = {}
    for r in results:
        counter[r.status] = counter.get(r.status, 0) + 1
    lines.append("-" * 96)
    parts = "  ".join(f"{k}={v}" for k, v in sorted(counter.items()))
    lines.append(f"合计 {total} 条：{parts}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="atst CLI 接口有效性探针")
    ap.add_argument(
        "--mode",
        default="all",
        choices=["all", "argparse", "core"],
        help="argparse=只扫解析面；core=只打 tdx 核心链；all=全量（默认）",
    )
    ap.add_argument("--timeout", type=int, default=60, help="单条探针超时秒数")
    ap.add_argument("--out", default="", help="矩阵结果额外写 JSON 到该路径")
    args = ap.parse_args(argv)

    results: list[Result] = []

    if args.mode in {"all", "argparse"}:
        surface = scan_argparse_surface(min(args.timeout, 30))
        results.extend(surface)
        print(f"[解析面] 扫了 {len(surface)} 个子命令 / 嵌套子动作")

    if args.mode != "argparse":
        for probe in PROBES:
            group = group_for(probe)
            if args.mode == "core" and group not in {"argparse", "local", "tdx"}:
                continue
            if args.mode == "argparse" and group != "argparse":
                continue
            results.append(_classify(_run(probe, args.timeout)))

    print(_render(results))

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "generated_at_ns": time.time_ns(),
                    "mode": args.mode,
                    "results": [
                        {
                            "probe": r.probe_id,
                            "argv": r.argv,
                            "status": r.status,
                            "exit_code": r.exit_code,
                            "elapsed_ms": r.elapsed_ms,
                            "error_class": r.error_class,
                            "detail": r.detail,
                        }
                        for r in results
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
        print(f"\n矩阵已写入 {target}")

    fatal = [r for r in results if r.fatal]
    if fatal:
        print(f"\n{len(fatal)} 条接口无效（BROKEN/MALFORMED），先修这些：")
        for r in fatal:
            print(f"  - {r.probe_id}: {r.detail}")
        return EXIT_BROKEN

    network_only = [r for r in results if r.status == "NETWORK"]
    if network_only and len(network_only) == len(results):
        print("\n网络整体不可用（全部探针都落在 NETWORK），判定为环境问题，非接口缺陷。")
        return EXIT_NO_NETWORK

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
