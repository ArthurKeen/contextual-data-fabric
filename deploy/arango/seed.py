"""Seed a demo ArangoDB for the M5 graph leg (matches tests/test_arango_live.py).

Creates database ``cmf`` and a ``tickets`` collection with one document. The
document carries ``_uri`` (the transpiler's subject projection reads
``doc._uri``) and ``subject`` (the mapped conceptual property).

    .venv/bin/python deploy/arango/seed.py
"""

from __future__ import annotations

import os

from arango import ArangoClient

URL = os.getenv("ARANGO_URL", "http://localhost:8529")
DB = os.getenv("ARANGO_DB", "cmf")
USER = os.getenv("ARANGO_USER", "root")
PASSWORD = os.getenv("ARANGO_PASSWORD", "cdf")


def main() -> None:
    client = ArangoClient(hosts=URL)
    system = client.db("_system", username=USER, password=PASSWORD)
    if not system.has_database(DB):
        system.create_database(DB)
    db = client.db(DB, username=USER, password=PASSWORD)
    if not db.has_collection("tickets"):
        db.create_collection("tickets")
    db.collection("tickets").insert(
        {
            "_key": "1",
            "_uri": "tickets/1",
            "subject": "login broken",
            "severity": "high",
        },
        overwrite=True,
    )
    print(f"seeded {DB}.tickets with 1 document at {URL}")


if __name__ == "__main__":
    main()
