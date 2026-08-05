"""Conditional relational-schema-analyzer (RSA) bundle → CSI v1 adapter.

r2g remains the default producer because it emits CSI and executable R2RML as a
coherent pair.  This adapter only normalizes an RSA bundle into CSI; it never
claims to produce, infer, or fabricate R2RML.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from cdf.query.catalog import parse_csi_statistics

from ..model import validate_finite_statistics

RSA_RELATIONAL_EXTENSION_VERSION = "1"


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _nonempty(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _conceptual_model(value: Any) -> dict[str, Any]:
    raw = deepcopy(_object(value, "conceptualSchema"))
    entities = raw.get("entities", raw.get("classes"))
    if not isinstance(entities, list) or not entities:
        raise ValueError("conceptualSchema.entities must be a non-empty array")
    normalized_entities: list[dict[str, Any]] = []
    for index, entity_value in enumerate(entities):
        entity = _object(entity_value, f"conceptualSchema.entities[{index}]")
        name = entity.get("name", entity.get("className"))
        normalized = dict(entity)
        normalized.pop("className", None)
        normalized["name"] = _nonempty(name, f"conceptualSchema.entities[{index}].name")
        properties = normalized.get("properties", normalized.get("attributes", []))
        if not isinstance(properties, list):
            raise ValueError(
                f"conceptualSchema.entities[{index}].properties must be an array"
            )
        normalized_properties: list[dict[str, Any]] = []
        for prop_index, prop_value in enumerate(properties):
            prop = _object(
                prop_value,
                f"conceptualSchema.entities[{index}].properties[{prop_index}]",
            )
            prop_name = prop.get("name", prop.get("attributeName"))
            normalized_prop = dict(prop)
            normalized_prop.pop("attributeName", None)
            normalized_prop["name"] = _nonempty(
                prop_name,
                f"conceptualSchema.entities[{index}].properties[{prop_index}].name",
            )
            normalized_properties.append(normalized_prop)
        normalized.pop("attributes", None)
        normalized["properties"] = normalized_properties
        normalized_entities.append(normalized)
    relationships = raw.get("relationships", [])
    if not isinstance(relationships, list):
        raise ValueError("conceptualSchema.relationships must be an array")
    return {
        "entities": normalized_entities,
        "relationships": deepcopy(relationships),
        **({"properties": deepcopy(raw["properties"])} if "properties" in raw else {}),
    }


def _number_fields(raw: dict[str, Any]) -> dict[str, Any]:
    aliases = {
        "rowCount": ("rowCount", "row_count", "cardinality"),
        "estimatedBytes": ("estimatedBytes", "estimated_bytes", "sizeBytes"),
        "costPerGbUsd": ("costPerGbUsd", "cost_per_gb_usd"),
    }
    result: dict[str, Any] = {}
    for output, inputs in aliases.items():
        for name in inputs:
            if name in raw:
                result[output] = raw[name]
                break
    return result


def _property_stats(value: Any, path: str) -> dict[str, Any]:
    raw = _object(value, path)
    result: dict[str, Any] = {}
    if "ndv" in raw:
        result["ndv"] = raw["ndv"]
    elif "distinctCount" in raw:
        result["ndv"] = raw["distinctCount"]
    if "selectivity" in raw:
        result["selectivity"] = raw["selectivity"]
    return result


def _statistics(metadata: dict[str, Any]) -> dict[str, Any] | None:
    value = metadata.get("statistics", metadata.get("stats"))
    if value is None:
        return None
    raw = _object(value, "metadata.statistics")
    if raw.get("version") == "1" and isinstance(raw.get("source"), dict):
        normalized = deepcopy(raw)
        validate_finite_statistics(normalized)
        return normalized
    source_raw = raw.get("source", raw)
    source = _number_fields(_object(source_raw, "metadata.statistics.source"))
    classes_raw = raw.get("classes", raw.get("tables", {}))
    classes_object = _object(classes_raw, "metadata.statistics.classes")
    classes: dict[str, Any] = {}
    for name, class_value in classes_object.items():
        class_raw = _object(class_value, f"metadata.statistics.classes.{name}")
        class_stats = _number_fields(class_raw)
        props_raw = class_raw.get("properties", class_raw.get("columns", {}))
        props_object = _object(props_raw, f"metadata.statistics.classes.{name}.properties")
        if props_object:
            class_stats["properties"] = {
                prop_name: _property_stats(
                    prop_value,
                    f"metadata.statistics.classes.{name}.properties.{prop_name}",
                )
                for prop_name, prop_value in props_object.items()
            }
        classes[str(name)] = class_stats
    result: dict[str, Any] = {
        "version": "1",
        "source": source,
        "classes": classes,
    }
    snapshot_id = raw.get("snapshotId", raw.get("snapshot_id"))
    as_of = raw.get("asOf", raw.get("as_of"))
    if snapshot_id is not None:
        result["snapshotId"] = snapshot_id
    if as_of is not None:
        result["asOf"] = as_of
    validate_finite_statistics(result)
    return result


def rsa_bundle_to_csi(
    bundle: dict[str, Any],
    *,
    source_kind: str | None = None,
    source_ref: str | None = None,
) -> dict[str, Any]:
    """Normalize an RSA bundle to CSI v1 without producing an R2RML mapping."""
    conceptual = _conceptual_model(bundle.get("conceptualSchema"))
    physical = deepcopy(_object(bundle.get("physicalMapping"), "physicalMapping"))
    metadata = _object(bundle.get("metadata"), "metadata")
    input_provenance = bundle.get("provenance", metadata.get("provenance", {}))
    provenance_raw = _object(input_provenance, "provenance")
    source_raw = provenance_raw.get("source", metadata.get("source", {}))
    source = _object(source_raw, "provenance.source")
    kind = source_kind or source.get("kind")
    ref = source_ref or source.get("ref")
    kind = _nonempty(kind, "provenance.source.kind")
    ref = _nonempty(ref, "provenance.source.ref")
    producer = provenance_raw.get("producer", metadata.get("producer"))
    producer = _nonempty(producer, "provenance.producer")
    generated_at = provenance_raw.get("generatedAt", metadata.get("generatedAt"))

    output: dict[str, Any] = {
        "csiVersion": "1",
        "conceptualModel": conceptual,
        "cdfRelationalPhysicalMapping": {
            "extensionVersion": RSA_RELATIONAL_EXTENSION_VERSION,
            "physicalMapping": physical,
        },
        "provenance": {
            "producer": producer,
            "producerVersion": str(
                provenance_raw.get("producerVersion", metadata.get("producerVersion", "unknown"))
            ),
            "direction": "forward",
            "source": {"kind": kind, "ref": ref},
        },
    }
    if generated_at is not None:
        output["provenance"]["generatedAt"] = generated_at
    statistics = _statistics(metadata)
    if statistics is not None:
        output["statistics"] = statistics
        parse_csi_statistics(output)
    return output
