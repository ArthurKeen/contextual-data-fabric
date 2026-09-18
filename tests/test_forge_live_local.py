"""Forge live mode against the LOCAL compose stacks (Postgres/Ontop, ArangoDB,
ClickHouse) — the same engines CI's ``live-local`` job stands up.

Gated on ``CDF_FORGE_LIVE=1`` so the offline suite never needs Docker. Proves,
for one locally deployable shape: deploy → the estate's own introspection →
CSI/R2RML export → manifest that loads like ``from_env`` → **zero conceptual
drift** between the fixture CSI and what RSA/r2g/ASA saw in the databases.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CDF_FORGE_LIVE") != "1",
    reason="set CDF_FORGE_LIVE=1 with the compose stacks up (make up)",
)
r2g_forge = pytest.importorskip("r2g.forge")

from cdf.eval.forge.dataset import synthesize  # noqa: E402
from cdf.eval.forge.live import LiveTargets, run_live_shape  # noqa: E402
from cdf.eval.forge.sampler import FAMILIES, sample_shape  # noqa: E402

LOCAL = {"postgres", "clickhouse", "arango"}


def _local_shapes():
    """Every family once, first seed whose dialects are all local."""
    out = []
    for family in FAMILIES:
        for seed in range(1, 300):
            shape = sample_shape(seed, family)
            if {s.dialect for s in shape.systems} <= LOCAL:
                out.append(shape)
                break
    return out


@pytest.mark.parametrize("shape", _local_shapes(), ids=lambda s: s.name)
def test_shape_onboards_through_the_estate_with_no_drift(shape, tmp_path: Path) -> None:
    ds = synthesize(shape, rows_per_entity=6)
    report = run_live_shape(shape, ds, LiveTargets.from_env(), tmp_path, rows_per_entity=6)
    assert report.status == "onboarded", report.message
    assert {s.name for s in report.systems} == {s.name for s in shape.systems}
    for system in report.systems:
        assert system.rows_loaded > 0
        assert system.drift == {}, f"{system.name} ({system.kind}) drift: {system.drift}"
        owner = shape.system(system.name)
        expected_declared = [
            r["type"]
            for r in shape.relationships()
            if shape.owner[r["fromEntity"]] == owner.name
            and shape.owner[r["toEntity"]] != owner.name
        ]
        assert system.declared_references == expected_declared
    manifest = json.loads(Path(report.manifest).read_text(encoding="utf-8"))
    assert {s["sourceId"] for s in manifest["sources"]} == {s.source_id for s in shape.systems}
