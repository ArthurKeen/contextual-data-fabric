"""M11 authoritative catalog manifest and conditional RSA adapter tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from cdf.catalog import (
    build_manifest,
    canonical_content_hash,
    export_catalog,
    load_manifest,
    parse_manifest,
)
from cdf.catalog.adapters.rsa import rsa_bundle_to_csi
from cdf.catalog.cli import main
from cdf.query import SourceCatalog
from cdf.service import FederationService

_R2RML = """\
@prefix rr: <http://www.w3.org/ns/r2rml#> .
<#Customer> a rr:TriplesMap ;
  rr:logicalTable [ rr:tableName "customers" ] ;
  rr:subjectMap [ rr:template "urn:customer/{id}" ;
                  rr:class <urn:arango-sparql:concept#Customer> ] .
"""


def _csi(ref: str = "customers", concept: str = "Customer") -> dict:
    return {
        "csiVersion": "1",
        "conceptualModel": {
            "entities": [
                {
                    "name": concept,
                    "properties": [{"name": "customerId"}, {"name": "accountId"}],
                }
            ],
            "relationships": [],
        },
        "provenance": {
            "producer": "r2g",
            "direction": "forward",
            "source": {"kind": "postgresql", "ref": ref},
        },
        "statistics": {
            "version": "1",
            "snapshotId": f"{ref}-1",
            "source": {"rowCount": 10},
        },
    }


def _built(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='1'\n")
    csi_dir = root / "csi"
    mapping_dir = root / "r2rml"
    csi_dir.mkdir()
    mapping_dir.mkdir()
    (csi_dir / "postgresql-customers.json").write_text(json.dumps(_csi()))
    (mapping_dir / "postgresql_customers.ttl").write_text(_R2RML)
    manifest = root / "manifest.json"
    build_manifest(
        csi_dirs=[csi_dir],
        r2rml_dirs=[mapping_dir],
        output=manifest,
        root=root,
    )
    return root, manifest, csi_dir / "postgresql-customers.json"


def test_build_is_idempotent_and_manifest_loads(tmp_path: Path) -> None:
    root, manifest, _ = _built(tmp_path)
    first = manifest.read_bytes()
    build_manifest(
        csi_dirs=[root / "csi"],
        r2rml_dirs=[root / "r2rml"],
        output=manifest,
        root=root,
    )
    assert manifest.read_bytes() == first
    loaded = load_manifest(manifest, root=root)
    assert loaded.manifest.generation.startswith("sha256:")
    assert loaded.source_catalog().source_of_class(
        "urn:arango-sparql:concept#Customer"
    ).source_id == "postgresql:customers"


def test_hash_drift_is_rejected(tmp_path: Path) -> None:
    root, manifest, csi_path = _built(tmp_path)
    csi_path.write_text(csi_path.read_text() + "\n")
    with pytest.raises(ValueError, match="hash drift"):
        load_manifest(manifest, root=root)


def test_path_traversal_and_secret_fields_are_rejected(tmp_path: Path) -> None:
    _, manifest, _ = _built(tmp_path)
    raw = json.loads(manifest.read_text())
    traversal = deepcopy(raw)
    traversal["sources"][0]["csi"]["path"] = "../outside.json"
    with pytest.raises(ValueError, match="repository-relative"):
        parse_manifest(traversal)

    secret = deepcopy(raw)
    secret["sources"][0]["auth"]["password"] = "forbidden"
    with pytest.raises(ValueError, match="secret-like"):
        parse_manifest(secret)


def test_source_id_overlap_and_invalid_policy_fields_are_rejected(tmp_path: Path) -> None:
    _, manifest, _ = _built(tmp_path)
    raw = json.loads(manifest.read_text())
    overlap = deepcopy(raw)
    second = deepcopy(overlap["sources"][0])
    second.update(sourceId="postgresql:other", ref="other")
    overlap["sources"].append(second)
    with pytest.raises(ValueError, match="overlaps owners"):
        parse_manifest(overlap)

    for field, value, message in [
        ("entitlements", {**raw["sources"][0]["entitlements"], "mask": "encrypt"}, "mask"),
        (
            "auth",
            {"mode": "service", "delegation": "user"},
            "delegation",
        ),
        (
            "runtimeResolution",
            {"mode": "canonical", "entityKey": None, "resolver": "aer"},
            "entityKey",
        ),
    ]:
        invalid = deepcopy(raw)
        invalid["sources"][0][field] = value
        with pytest.raises(ValueError, match=message):
            parse_manifest(invalid)


def test_manifest_content_hash_detects_manifest_drift(tmp_path: Path) -> None:
    root, manifest, _ = _built(tmp_path)
    raw = json.loads(manifest.read_text())
    raw["generation"] = "other"
    manifest.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="content hash drift"):
        load_manifest(manifest, root=root)
    raw["contentHash"] = canonical_content_hash(raw)
    manifest.write_text(json.dumps(raw))
    assert load_manifest(manifest, root=root).manifest.generation == "other"


def test_legacy_catalog_and_manifest_catalog_have_routing_parity(tmp_path: Path) -> None:
    root, manifest, csi_path = _built(tmp_path)
    document = json.loads(csi_path.read_text())
    legacy = SourceCatalog.from_csi_documents([document])
    authoritative = load_manifest(manifest, root=root).source_catalog()
    iri = legacy.iri("Customer")
    assert authoritative.source_of_class(iri) == legacy.source_of_class(iri)
    assert authoritative.statistics_for("postgresql:customers") == legacy.statistics_for(
        "postgresql:customers"
    )
    assert authoritative.generation_for("postgresql:customers") is not None
    assert authoritative.entitlements_for("postgresql:customers").classification == "internal"
    assert authoritative.join_keys_for("postgresql:customers") == ()
    assert authoritative.resolution_for("postgresql:customers").mode == "none"
    assert authoritative.auth_for("postgresql:customers").mode == "service"


def test_from_env_manifest_is_authoritative_over_legacy_directories(tmp_path: Path) -> None:
    root, manifest, _ = _built(tmp_path)
    empty = root / "empty"
    empty.mkdir()
    service = FederationService.from_env(
        {
            "CDF_CATALOG_MANIFEST": str(manifest),
            "CDF_CSI_DIR": str(empty),
            "CDF_R2RML_DIR": str(empty),
            "ONTOP_SPARQL_ENDPOINT": "http://ontop.invalid/sparql",
            "CDF_NL_DISABLED": "true",
        }
    )
    assert {source.source_id for source in service.catalog.sources} == {
        "postgresql:customers"
    }
    assert "postgresql:customers" in service.executors


def test_cli_validate_load_and_export_preserve_artifact_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, manifest, csi_path = _built(tmp_path)
    assert main(["validate", str(manifest), "--root", str(root)]) == 0
    assert main(["load", str(manifest), "--root", str(root)]) == 0
    target = root / "export"
    assert main(["export", str(manifest), str(target), "--root", str(root)]) == 0
    assert (target / "csi" / csi_path.name).read_bytes() == csi_path.read_bytes()
    exported = export_catalog(manifest, root / "export-2", root=root)
    assert len(exported) == 2
    assert "valid:" in capsys.readouterr().out


def test_rsa_bundle_matches_golden_and_does_not_fabricate_r2rml() -> None:
    root = Path(__file__).parent
    bundle = json.loads((root / "fixtures" / "rsa-bundle-v1.json").read_text())
    expected = json.loads((root / "goldens" / "rsa-csi-v1.json").read_text())
    actual = rsa_bundle_to_csi(bundle)
    assert actual == expected
    assert "r2rml" not in actual
    assert actual["cdfRelationalPhysicalMapping"]["extensionVersion"] == "1"


def test_rsa_requires_source_provenance() -> None:
    bundle = {
        "conceptualSchema": {
            "entities": [{"name": "Customer", "properties": []}],
            "relationships": [],
        },
        "physicalMapping": {},
        "metadata": {"producer": "relational-schema-analyzer"},
    }
    with pytest.raises(ValueError, match="source.kind"):
        rsa_bundle_to_csi(bundle)
