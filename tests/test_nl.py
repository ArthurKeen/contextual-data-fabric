"""Tests for the NL → SPARQL front-end (cdf.query.nl) + the service NL path.

Uses a minimal duck-typed fake LLM client (the module only needs
``client.generate(messages).content``), so nothing here touches a provider,
a network, or the arango-sparql-py engine."""

from __future__ import annotations

from cdf.query import SourceCatalog
from cdf.query.nl import build_system_prompt, extract_sparql, nl_to_sparql

PREFIX = "PREFIX c: <urn:arango-sparql:concept#>\n"

GOOD = PREFIX + """SELECT ?name ?subject WHERE {
  ?a a c:Account ; c:accountId ?aid ; c:name ?name .
  ?t a c:Ticket  ; c:accountId ?aid ; c:subject ?subject .
}"""

# References concepts the catalog does not know -> must be refused/repaired.
BAD = PREFIX + "SELECT ?x WHERE { ?x a c:Nonexistent ; c:ghostprop ?y }"

# Uses FILTER -> E1 refuses unsupported constructs.
FILTERED = PREFIX + "SELECT ?n WHERE { ?a a c:Account ; c:name ?n . FILTER(?n = 'x') }"


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeClient:
    """Returns canned replies; replays the last once exhausted."""

    provider = "fake"
    model = "fake"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    def generate(self, messages: list[dict[str, str]]) -> _Resp:
        self.calls.append(messages)
        reply = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        return _Resp(reply)


def _csi(kind, ref, entities, relationships=()):
    return {
        "csiVersion": "1",
        "conceptualModel": {
            "entities": [
                {"name": n, "properties": [{"name": p} for p in props]} for n, props in entities
            ],
            "relationships": [
                {"type": t, "fromEntity": f, "toEntity": to} for t, f, to in relationships
            ],
        },
        "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
        "provenance": {"producer": "t", "direction": "forward",
                       "source": {"kind": kind, "ref": ref}},
    }


def _catalog() -> SourceCatalog:
    return SourceCatalog.from_csi_documents(
        [
            _csi("postgresql", "crm", [("Account", ["accountId", "name", "arr"])]),
            _csi("arango", "tickets", [("Ticket", ["accountId", "subject"])]),
        ]
    )


# -- vocabulary --------------------------------------------------------------


def test_vocabulary_groups_properties_under_their_class():
    vocab = {v["source_id"]: v for v in _catalog().vocabulary()}
    assert set(vocab) == {"postgresql:crm", "arango:tickets"}
    pg = {c["name"]: c["properties"] for c in vocab["postgresql:crm"]["classes"]}
    assert pg["Account"] == sorted(["accountId", "name", "arr"])
    ar = {c["name"]: c["properties"] for c in vocab["arango:tickets"]["classes"]}
    assert "subject" in ar["Ticket"]


def test_prompt_ties_each_property_to_its_owning_class():
    # Regression: a Chunk's documentId must NOT read as a Document property —
    # a flat property bag let the LLM attach it to Document and get 0 rows.
    cat = SourceCatalog.from_csi_documents(
        [
            _csi("arango", "cmf", [
                ("Document", ["source", "filename", "accountId"]),
                ("Chunk", ["documentId", "text", "accountId"]),
            ]),
        ]
    )
    prompt = build_system_prompt(cat)
    doc_line = next(ln for ln in prompt.splitlines() if "class Document" in ln)
    chunk_line = next(ln for ln in prompt.splitlines() if "class Chunk" in ln)
    assert "documentId" not in doc_line  # Document does NOT own documentId
    assert "documentId" in chunk_line    # Chunk does
    assert "filename" in doc_line


def test_system_prompt_grounds_in_catalog_concepts():
    prompt = build_system_prompt(_catalog())
    assert "urn:arango-sparql:concept#" in prompt
    assert "Account" in prompt and "Ticket" in prompt
    assert "accountId" in prompt


# -- extraction --------------------------------------------------------------


def test_extract_sparql_from_fence():
    assert extract_sparql("here you go:\n```sparql\n" + GOOD + "\n```\ndone").startswith("PREFIX")


def test_extract_sparql_bare():
    assert extract_sparql("SELECT ?x WHERE { ?x a c:Account }").startswith("SELECT")


# -- translation -------------------------------------------------------------


def test_translates_valid_question_in_one_call():
    result = nl_to_sparql("accounts and their tickets", _catalog(), client=_FakeClient([GOOD]))
    assert result.ok
    assert result.sparql and "c:accountId" in result.sparql
    assert result.llm_calls == 1
    assert result.error is None


def test_repair_loop_recovers():
    client = _FakeClient([BAD, GOOD])  # first bad, then good
    result = nl_to_sparql("q", _catalog(), client=client, max_repairs=2)
    assert result.ok
    assert result.llm_calls == 2
    assert any("repair" in w for w in result.warnings)
    # The repair turn fed the validation error back to the model.
    assert len(client.calls[-1]) > 2


def test_unbound_projected_var_triggers_repair():
    # Projects ?ghost, which no triple binds -> must repair, not accept.
    bad_unbound = PREFIX + "SELECT ?name ?ghost WHERE { ?a a c:Account ; c:name ?name }"
    client = _FakeClient([bad_unbound, GOOD])
    result = nl_to_sparql("q", _catalog(), client=client, max_repairs=2)
    assert result.ok
    assert result.llm_calls == 2
    assert any("not bound" in w or "repair" in w for w in result.warnings)


def test_refuses_when_never_grounded():
    result = nl_to_sparql("q", _catalog(), client=_FakeClient([BAD]), max_repairs=2)
    assert not result.ok
    assert result.error is not None
    assert result.llm_calls == 3  # initial + 2 repairs


def test_unsupported_construct_is_rejected():
    result = nl_to_sparql("q", _catalog(), client=_FakeClient([FILTERED]), max_repairs=1)
    assert not result.ok  # FILTER never validates -> refused


# -- service integration -----------------------------------------------------


def test_service_federate_question_uses_nl_when_configured():
    from cdf.query import SourceResult
    from cdf.service.app import FederationService

    class _Exec:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, _sq):
            return SourceResult(rows=self._rows)

    service = FederationService(
        catalog=_catalog(),
        executors={
            "postgresql:crm": _Exec(({"a": "u1", "aid": "ACME", "name": "Acme"},)),
            "arango:tickets": _Exec(({"t": "t1", "aid": "ACME", "subject": "login"},)),
        },
        nl_client=_FakeClient([GOOD]),
    )
    env = service.federate_question("which accounts have tickets?")
    assert env.status == "grounded"
    assert env.bindings == ({"name": "Acme", "subject": "login"},)


def test_service_refuses_question_without_nl_or_prepared():
    from cdf.service.app import FederationService

    service = FederationService(catalog=_catalog(), executors={})  # nl_client=None
    env = service.federate_question("anything")
    assert env.status == "refused"
    assert "no NL front-end" in (env.refusal_reason or "")
