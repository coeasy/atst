from __future__ import annotations

import json
from pathlib import Path

from atst.protocol.commands import Family
from atst.transport.hosts import RankingStore


def _row(host: str, *, port: int = 7709) -> dict[str, object]:
    return {
        "host": host,
        "port": port,
        "family": Family.STANDARD,
        "rtt_ms": 1.0,
    }


def test_ranking_load_rejects_valid_payload_copied_under_another_key(tmp_path: Path) -> None:
    path = tmp_path / "ranking.json"
    path.write_text(
        json.dumps(
            {
                "version": RankingStore.VERSION,
                "entries": {"9.9.9.9:7709": _row("1.2.3.4")},
            }
        ),
        encoding="utf-8",
    )

    assert RankingStore(path).load() == {}


def test_ranking_load_rejects_duplicate_embedded_endpoint_identity(tmp_path: Path) -> None:
    path = tmp_path / "ranking.json"
    path.write_text(
        json.dumps(
            {
                "version": RankingStore.VERSION,
                "entries": {
                    "1.2.3.4:7709": _row("1.2.3.4"),
                    "alias:7709": _row("1.2.3.4"),
                },
            }
        ),
        encoding="utf-8",
    )

    assert RankingStore(path).load() == {}


def test_ranking_load_rejects_boolean_version_alias(tmp_path: Path) -> None:
    path = tmp_path / "ranking.json"
    path.write_text(
        json.dumps(
            {
                "version": True,
                "entries": {"1.2.3.4:7709": _row("1.2.3.4")},
            }
        ),
        encoding="utf-8",
    )

    assert RankingStore(path).load() == {}


def test_ranking_load_accepts_matching_key_and_payload_identity(tmp_path: Path) -> None:
    path = tmp_path / "ranking.json"
    path.write_text(
        json.dumps(
            {
                "version": RankingStore.VERSION,
                "entries": {"1.2.3.4:7709": _row("1.2.3.4")},
            }
        ),
        encoding="utf-8",
    )

    loaded = RankingStore(path).load()

    assert list(loaded) == ["1.2.3.4:7709"]
    assert loaded["1.2.3.4:7709"].host == "1.2.3.4"
