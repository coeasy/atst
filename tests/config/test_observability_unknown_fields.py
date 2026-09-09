from __future__ import annotations

import pytest

from tstdx.config.schema import config_from_dict
from tstdx.errors import ValidationError


@pytest.mark.parametrize(
    ("section", "payload", "unknown"),
    [
        ("metrics", {"enabledd": True}, "enabledd"),
        ("logging", {"leve": "DEBUG"}, "leve"),
        ("tracing", {"exportorr": "otel"}, "exportorr"),
    ],
)
def test_observability_nested_typos_fail_closed(
    section: str,
    payload: dict[str, object],
    unknown: str,
) -> None:
    with pytest.raises(ValidationError, match=unknown):
        config_from_dict({"observability": {section: payload}})


def test_observability_partial_known_overrides_still_deep_merge_defaults() -> None:
    cfg = config_from_dict(
        {
            "observability": {
                "metrics": {"enabled": True},
                "logging": {"level": "DEBUG"},
                "tracing": {"enabled": True},
            }
        }
    )

    assert cfg.observability.metrics == {"enabled": True, "exporter": "prom"}
    assert cfg.observability.logging == {"level": "DEBUG", "json": False}
    assert cfg.observability.tracing == {"enabled": True, "exporter": "otel"}
