"""Seed deterministic graph fixtures for live tests and CI goldens.

Creates database ``cmf`` with the small ``tickets`` and ``documents`` fixtures
needed by the checked-in live tests. The full demo corpus loader replaces the
documents collection during ``make seed``.

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
    for name in ("tickets", "documents"):
        if not db.has_collection(name):
            db.create_collection(name)
    # `account_id` is the cross-source business key: it matches an accounts row
    # in the Ontop/Postgres leg so the federated query can join on it.
    db.collection("tickets").insert(
        {
            "_key": "1",
            "_uri": "tickets/1",
            "subject": "Escalation: ops burden and competitor evaluation",
            "severity": "high",
            "account_id": "001bbkuFW1b7KegAZT",  # Meridian Logistics (corpus)
        },
        overwrite=True,
    )
    db.collection("tickets").insert(
        {
            "_key": "2",
            "_uri": "tickets/2",
            "subject": "Question: GraphRAG on ArangoGraph",
            "severity": "low",
            "account_id": "001LxbLlyzNOfmaOHp",  # Northwind Analytics (corpus)
        },
        overwrite=True,
    )
    documents = (
        ("acme-ci", "001Qwvb5LAnzy3yVgi", "slack"),
        ("meridian-ci", "001bbkuFW1b7KegAZT", "email"),
        ("northwind-ci", "001LxbLlyzNOfmaOHp", "docs"),
    )
    for key, account_id, source in documents:
        db.collection("documents").insert(
            {
                "_key": key,
                "_uri": f"documents/{key}",
                "account_id": account_id,
                "source": source,
                "filename": f"{key}.txt",
                "citable_url": f"https://example.invalid/{key}",
                "role": "ci-fixture",
                "questions_served": [],
                "event_date": "2026-07-01",
            },
            overwrite=True,
        )
    print(f"seeded {DB}.tickets (2) and {DB}.documents (3) at {URL}")


if __name__ == "__main__":
    main()
