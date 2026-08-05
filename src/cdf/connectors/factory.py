"""Build source executors from resolved connector fields at open time."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .secrets import ResolvedConnector


def executor_builder(
    document: dict[str, Any],
    *,
    r2rml_path: Path | None = None,
) -> Callable[[ResolvedConnector], Any]:
    """Capture only mappings; resolve connection fields on each build."""
    r2rml = (
        r2rml_path.read_text(encoding="utf-8")
        if r2rml_path is not None and r2rml_path.is_file()
        else None
    )

    def build(resolved: ResolvedConnector) -> Any:
        fields = resolved.fields
        if resolved.kind == "arango":
            import arango

            from cdf.adapters import ArangoExecutor

            url = _required(fields, "url", resolved)
            client = arango.ArangoClient(hosts=url)
            db = client.db(
                fields.get("database", "cmf"),
                username=fields.get("user", "root"),
                password=fields.get("password", ""),
            )
            return ArangoExecutor(
                csi=document,
                db=db,
                client=client,
                source_objects=(resolved.ref,),
            )
        if resolved.kind == "clickhouse":
            if r2rml is None:
                raise ValueError(f"R2RML mapping missing for {resolved.source_id!r}")
            from cdf.adapters import ClickHouseExecutor

            return ClickHouseExecutor(
                r2rml=r2rml,
                dsn=_required(fields, "dsn", resolved),
                source_objects=(resolved.ref,),
            )
        if resolved.kind == "snowflake":
            if r2rml is None:
                raise ValueError(f"R2RML mapping missing for {resolved.source_id!r}")
            from cdf.adapters import SnowflakeExecutor
            from cdf.adapters.snowflake import validate_snowflake_connect_args

            return SnowflakeExecutor(
                r2rml=r2rml,
                connect_args=validate_snowflake_connect_args(fields.reveal()),
                source_objects=(resolved.ref,),
            )
        from cdf.adapters import OntopExecutor

        return OntopExecutor(
            endpoint=_required(fields, "endpoint", resolved),
            reformulate_endpoint=fields.get("reformulate_endpoint"),
            source_objects=(resolved.ref,),
        )

    return build


def _required(
    fields: Any,
    name: str,
    resolved: ResolvedConnector,
) -> str:
    value = fields.get(name)
    if not value:
        raise ValueError(f"connector {resolved.source_id!r} requires field {name!r}")
    return value

