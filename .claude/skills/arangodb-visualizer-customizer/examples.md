## Ready-to-copy Python installer skeleton (themes + saved queries + canvas actions)

This is a minimal, **idempotent** installer pattern for ArangoDB Graph Visualizer assets.

It supports the common split:
- **Themes** in the **target DB** (`_graphThemeStore`)
- **Saved queries** + **canvas actions** in **`_system`** by default (`_editor_saved_queries`, `_canvasActions`)
- **Viewpoint links** in the **target DB** (`_viewpointActions` edges from `_viewpoints/*` → `_canvasActions/*`)

### Copy/paste skeleton

```python
#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from arango import ArangoClient


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_db(
    *,
    endpoint: str,
    username: str,
    password: str,
    database: str,
):
    client = ArangoClient(hosts=endpoint)
    return client.db(database, username=username, password=password)


def ensure_collection(db, name: str, *, edge: bool = False) -> None:
    if db.has_collection(name):
        return
    db.create_collection(name, edge=edge, system=name.startswith("_"))


def upsert_by_fields(col, match_fields: dict[str, Any], doc: dict[str, Any]) -> str:
    """
    Upsert using a field match (not AQL).
    Returns the document _id.

    Notes:
    - Prefer stable `_key` where possible.
    - When matching on fields, ensure they uniquely identify the asset.
    """
    existing = list(col.find(match_fields))
    if existing:
        key = existing[0]["_key"]
        doc = {**doc, "_key": key, "createdAt": existing[0].get("createdAt", doc.get("createdAt"))}
        col.replace(doc, check_rev=False)
        return f"{col.name}/{key}"
    res = col.insert(doc)
    return res["_id"]


def upsert_by_key(col, key: str, doc: dict[str, Any]) -> str:
    doc = {**doc, "_key": key}
    if col.has(key):
        existing = col.get(key)
        doc["createdAt"] = existing.get("createdAt", doc.get("createdAt"))
        col.replace(doc, check_rev=False)
        return f"{col.name}/{key}"
    res = col.insert(doc)
    return res["_id"]


def ensure_default_viewpoint(db, graph_name: str) -> str:
    """
    Create a default viewpoint programmatically so the graph can be automated
    without requiring the user to open it in the UI first.
    """
    ensure_collection(db, "_viewpoints")
    vp_col = db.collection("_viewpoints")
    existing = list(vp_col.find({"graphId": graph_name, "name": "Default"}))
    if existing:
        return existing[0]["_id"]
    now = now_iso()
    res = vp_col.insert({
        "graphId": graph_name,
        "name": "Default",
        "description": f"Default viewpoint for {graph_name}",
        "createdAt": now,
        "updatedAt": now,
    })
    return res["_id"]


def ensure_action_link(target_db, *, viewpoint_id: str, action_id: str) -> None:
    ensure_collection(target_db, "_viewpointActions", edge=True)
    edge_col = target_db.collection("_viewpointActions")
    existing = list(edge_col.find({"_from": viewpoint_id, "_to": action_id}))
    if existing:
        return
    edge_col.insert({"_from": viewpoint_id, "_to": action_id, "createdAt": now_iso()})


def install_theme(target_db, *, graph_id: str, theme_path: Path) -> str:
    ensure_collection(target_db, "_graphThemeStore")
    col = target_db.collection("_graphThemeStore")

    theme = json.loads(theme_path.read_text(encoding="utf-8"))
    theme["graphId"] = theme.get("graphId") or graph_id
    theme.setdefault("name", "custom-theme")
    theme.setdefault("description", "")
    theme.setdefault("nodeConfigMap", {})
    theme.setdefault("edgeConfigMap", {})
    theme.setdefault("isDefault", True)

    ts = now_iso()
    theme["updatedAt"] = ts

    # Upsert by (graphId, name) — preserves createdAt on update
    return upsert_by_fields(col, {"graphId": theme["graphId"], "name": theme["name"]}, theme)


def install_saved_queries(
    queries_db,
    *,
    queries: list[dict[str, Any]],
    database_name: Optional[str] = None,
) -> int:
    ensure_collection(queries_db, "_editor_saved_queries")
    col = queries_db.collection("_editor_saved_queries")
    ts = now_iso()

    processed = 0
    for q in queries:
        # Normalize display fields
        if "title" not in q and "name" in q:
            q["title"] = q["name"]
        q.setdefault("name", q.get("title") or "Untitled query")
        # CRITICAL: The query editor reads `content` (and `value` for older versions),
        # NOT `queryText`. `queryText` is only for canvas actions.
        aql = q.pop("queryText", None)
        q.setdefault("content", aql or "")
        q.setdefault("value", q["content"])  # cross-version compatibility
        q.setdefault("bindVariables", {})
        q["updatedAt"] = ts
        q.setdefault("createdAt", ts)
        if database_name:
            q.setdefault("databaseName", database_name)

        key = q.get("_key")
        if key:
            upsert_by_key(col, key, q)
        else:
            upsert_by_fields(col, {"title": q["title"]}, q)
        processed += 1
    return processed


def install_canvas_actions(
    actions_db,
    target_db,
    *,
    graph_id: str,
    actions: list[dict[str, Any]],
) -> int:
    ensure_collection(actions_db, "_canvasActions")
    action_col = actions_db.collection("_canvasActions")

    viewpoint_id = ensure_default_viewpoint(target_db, graph_id)

    processed = 0
    for a in actions:
        # Normalize
        a.setdefault("graphId", graph_id)
        if "title" in a and "name" not in a:
            a["name"] = a["title"]
        a.setdefault("title", a.get("name") or "Canvas action")
        a.setdefault("name", a["title"])
        a.setdefault("queryText", "")
        a.setdefault("bindVariables", {"nodes": []})

        key = a.get("_key")
        if not key:
            # Derive a stable key from graph_id + name (snake_case)
            import re
            slug = re.sub(r"[^a-z0-9]+", "_", f"{graph_id}_{a['name']}".lower()).strip("_")
            key = slug

        action_id = upsert_by_key(action_col, key, a)

        # Link action to viewpoint in target DB so it appears in UI
        ensure_action_link(target_db, viewpoint_id=viewpoint_id, action_id=action_id)
        processed += 1
    return processed


def main() -> None:
    # ---- Configure ----
    endpoint = os.environ["ARANGO_ENDPOINT"]  # e.g. https://cluster.arango.ai:8529
    username = os.environ.get("ARANGO_USERNAME", "root")
    password = os.environ.get("ARANGO_PASSWORD", "")
    target_database = os.environ["ARANGO_DATABASE"]

    graph_id = os.environ.get("VIS_GRAPH_ID", "IC_Knowledge_Graph")
    theme_path = Path(os.environ.get("VIS_THEME_JSON", "docs/theme.json"))

    # Convention: store saved queries + actions in _system.
    # If your deployment stores these in the target DB, set VIS_META_DB=target.
    meta_db_mode = (os.environ.get("VIS_META_DB", "system") or "system").lower()

    # Load assets (replace with your repo's JSON layout)
    queries = json.loads(Path("docs/saved_queries.json").read_text(encoding="utf-8"))
    actions = json.loads(Path("docs/canvas_actions.json").read_text(encoding="utf-8"))

    # ---- Connect ----
    target_db = get_db(endpoint=endpoint, username=username, password=password, database=target_database)
    sys_db = get_db(endpoint=endpoint, username=username, password=password, database="_system")
    meta_db = target_db if meta_db_mode == "target" else sys_db

    # ---- Install ----
    theme_id = install_theme(target_db, graph_id=graph_id, theme_path=theme_path)
    q_count = install_saved_queries(meta_db, queries=queries, database_name=target_database)
    a_count = install_canvas_actions(meta_db, target_db, graph_id=graph_id, actions=actions)

    print(f"✓ Theme: {theme_id}")
    print(f"✓ Saved queries: {q_count}")
    print(f"✓ Canvas actions: {a_count}")
    print("Done. Refresh the Visualizer UI (theme in Legend; queries panel; right-click actions).")


if __name__ == "__main__":
    main()
```

### Notes you'll almost certainly need to adjust

- **Asset loading**: replace `docs/saved_queries.json` and `docs/canvas_actions.json` with your repo's actual files (or embed lists inline).
- **Graph ID**: set `VIS_GRAPH_ID` to the actual graph name in ArangoDB (must match what the Visualizer uses).
- **Metadata DB**: if your environment stores `_editor_saved_queries` and `_canvasActions` in the target DB, set `VIS_META_DB=target`.
- **Viewpoint**: created programmatically by `ensure_default_viewpoint()` — no manual UI step required.
