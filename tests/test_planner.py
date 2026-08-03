"""Tests for the query-graph partition planner (cdf.query.planner)."""

from __future__ import annotations

import pytest

from cdf.query import SourceCatalog, UnsupportedQueryError, partition_query

PREFIX = "PREFIX c: <urn:arango-sparql:concept#>\n"


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
        "provenance": {"producer": "test", "direction": "forward",
                       "source": {"kind": kind, "ref": ref}},
    }


def _two_source_catalog() -> SourceCatalog:
    # Relational source owns Order (+ its FK relationship placed_by);
    # graph source owns User.
    return SourceCatalog.from_csi_documents(
        [
            _csi("postgresql", "shop", [("Order", ["id", "total"])],
                 [("placed_by", "Order", "User")]),
            _csi("arango", "docs", [("User", ["name"])]),
        ]
    )


def _sub_by_kind(plan):
    return {sq.source.kind: sq for sq in plan.sub_queries}


def test_single_source_no_join_keys():
    cat = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "shop", [("Order", ["id", "total"])])]
    )
    plan = partition_query(
        PREFIX + "SELECT ?o ?t WHERE { ?o a c:Order ; c:total ?t }", cat
    )
    assert plan.is_single_source
    assert plan.join_keys == ()
    assert plan.sub_queries[0].source.kind == "postgresql"
    assert not plan.unresolved


def test_two_source_partition_and_join_key():
    plan = partition_query(
        PREFIX
        + """SELECT ?u ?name WHERE {
             ?o a c:Order ; c:placed_by ?u ; c:total ?t .
             ?u a c:User ; c:name ?name .
           }""",
        _two_source_catalog(),
    )
    subs = _sub_by_kind(plan)
    assert set(subs) == {"postgresql", "arango"}
    # ?u bridges the two sources -> it is the join key.
    assert plan.join_keys == ("?u",)
    # The relationship triple stays with its subject's (relational) source.
    pg_preds = {t.predicate for t in subs["postgresql"].triples}
    assert "<urn:arango-sparql:concept#placed_by>" in pg_preds
    assert "<urn:arango-sparql:concept#total>" in pg_preds
    # The User property is answered by the graph source.
    graph_preds = {t.predicate for t in subs["arango"].triples}
    assert "<urn:arango-sparql:concept#name>" in graph_preds
    assert not plan.unresolved


def test_join_key_selected_in_both_sub_queries():
    plan = partition_query(
        PREFIX
        + """SELECT ?u ?name WHERE {
             ?o a c:Order ; c:placed_by ?u .
             ?u a c:User ; c:name ?name .
           }""",
        _two_source_catalog(),
    )
    subs = _sub_by_kind(plan)
    assert "?u" in subs["postgresql"].sparql
    assert "?u" in subs["arango"].sparql
    # Each sub-query is a self-contained SELECT.
    assert subs["postgresql"].sparql.startswith("SELECT")
    assert subs["arango"].sparql.startswith("SELECT")


def test_shared_property_name_resolved_by_class_binding():
    # Both sources expose a "name" property; the subject's class must decide.
    cat = SourceCatalog.from_csi_documents(
        [
            _csi("postgresql", "shop", [("Order", ["name"])]),
            _csi("arango", "docs", [("User", ["name"])]),
        ]
    )
    plan = partition_query(
        PREFIX + "SELECT ?n WHERE { ?u a c:User ; c:name ?n }", cat
    )
    subs = _sub_by_kind(plan)
    # Routed to the graph source (User's source), NOT left unresolved/ambiguous.
    assert set(subs) == {"arango"}
    assert not plan.unresolved
    assert any(t.predicate.endswith("name>") for t in subs["arango"].triples)


def test_unknown_class_is_unresolved_not_dropped():
    cat = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "shop", [("Order", ["id"])])]
    )
    plan = partition_query(
        PREFIX + "SELECT ?x WHERE { ?x a c:Ghost ; c:ghostprop ?i }", cat
    )
    # Unknown class (?x can't bind) + unknown property -> both unresolved.
    assert len(plan.unresolved) == 2
    assert plan.sub_queries == ()


def test_unknown_property_on_known_class_routes_to_class_source():
    # A property absent from the catalog still routes to the subject's class
    # source (class binding wins) rather than being dropped.
    cat = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "shop", [("Order", ["id"])])]
    )
    plan = partition_query(
        PREFIX + "SELECT ?o WHERE { ?o a c:Order ; c:mystery ?m }", cat
    )
    assert not plan.unresolved
    assert plan.sub_queries[0].source.kind == "postgresql"
    assert len(plan.sub_queries[0].triples) == 2


def test_projection_is_preserved():
    plan = partition_query(
        PREFIX + "SELECT ?u ?name WHERE { ?u a c:User ; c:name ?name }",
        SourceCatalog.from_csi_documents([_csi("arango", "docs", [("User", ["name"])])]),
    )
    assert plan.projection == ("?u", "?name")


@pytest.mark.parametrize(
    "pattern",
    [
        "{ ?u a c:User } UNION { ?u a c:Order }",
        "?u a c:User BIND('x' AS ?n)",
    ],
)
def test_unsupported_constructs_refuse_not_silently_drop(pattern):
    cat = SourceCatalog.from_csi_documents(
        [_csi("arango", "docs", [("User", ["name"])], ())]
    )
    with pytest.raises(UnsupportedQueryError):
        partition_query(PREFIX + f"SELECT ?u WHERE {{ {pattern} }}", cat)


def test_order_by_and_limit_are_allowed():
    # Result modifiers don't affect partitioning; they must NOT be refused.
    cat = SourceCatalog.from_csi_documents([_csi("arango", "docs", [("User", ["name"])])])
    plan = partition_query(
        PREFIX + "SELECT ?u ?n WHERE { ?u a c:User ; c:name ?n } ORDER BY ?n LIMIT 10",
        cat,
    )
    assert plan.sub_queries[0].source.kind == "arango"


def test_deterministic_sub_query_order():
    cat = _two_source_catalog()
    q = (
        PREFIX
        + "SELECT ?u WHERE { ?o a c:Order ; c:placed_by ?u . ?u a c:User ; c:name ?n }"
    )
    ids1 = [sq.source.source_id for sq in partition_query(q, cat).sub_queries]
    ids2 = [sq.source.source_id for sq in partition_query(q, cat).sub_queries]
    assert ids1 == ids2 == sorted(ids1)


# -- E1 single-leg FILTER / OPTIONAL pushdown --------------------------------


def _account_document_catalog() -> SourceCatalog:
    return SourceCatalog.from_csi_documents(
        [
            _csi("postgresql", "crm", [("Account", ["accountId", "accountName", "region"])]),
            _csi("arango", "docs", [("Document", ["accountId", "role", "filename", "tag"])]),
        ]
    )


def test_filter_pushed_to_single_source_leg():
    cat = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "shop", [("Order", ["total"])])]
    )
    plan = partition_query(
        PREFIX + "SELECT ?o ?t WHERE { ?o a c:Order ; c:total ?t . FILTER(?t <= 1000) }",
        cat,
    )
    sq = plan.sub_queries[0]
    assert sq.filters == ("?t <= 1000",)  # numeric literal rendered bare
    assert "FILTER(?t <= 1000)" in sq.sparql


def test_filter_on_join_key_replicated_to_both_legs():
    cat = SourceCatalog.from_csi_documents(
        [
            _csi("postgresql", "a", [("Left", ["k", "lname"])]),
            _csi("arango", "b", [("Right", ["k", "rname"])]),
        ]
    )
    plan = partition_query(
        PREFIX
        + """SELECT ?lname ?rname WHERE {
             ?l a c:Left ; c:k ?k ; c:lname ?lname .
             ?r a c:Right ; c:k ?k ; c:rname ?rname .
             FILTER(?k >= 100)
           }""",
        cat,
    )
    assert plan.join_keys == ("?k",)
    subs = _sub_by_kind(plan)
    # The filter on the join key is replicated into *every* leg that binds it.
    assert subs["postgresql"].filters == ("?k >= 100",)
    assert subs["arango"].filters == ("?k >= 100",)
    assert "FILTER(?k >= 100)" in subs["postgresql"].sparql
    assert "FILTER(?k >= 100)" in subs["arango"].sparql


def test_filter_on_unbound_variable_refuses():
    cat = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "shop", [("Order", ["total"])])]
    )
    with pytest.raises(UnsupportedQueryError, match="unbound"):
        partition_query(
            PREFIX + "SELECT ?o WHERE { ?o a c:Order ; c:total ?t . FILTER(?z > 5) }",
            cat,
        )


def test_single_source_optional_attached_and_projected():
    plan = partition_query(
        PREFIX
        + """SELECT ?accountName ?filename WHERE {
             ?a a c:Account ; c:accountName ?accountName ; c:accountId ?k .
             ?d a c:Document ; c:accountId ?k ; c:role ?r .
             OPTIONAL { ?d c:filename ?filename }
           }""",
        _account_document_catalog(),
    )
    subs = _sub_by_kind(plan)
    arango = subs["arango"]
    assert len(arango.optional_groups) == 1
    assert "OPTIONAL {" in arango.sparql
    # The optional projection var rides the envelope: in variables AND the SELECT.
    assert "?filename" in arango.variables
    assert "?filename" in arango.sparql.split("WHERE")[0]
    # The other leg carries no optional.
    assert subs["postgresql"].optional_groups == ()


def test_cross_source_optional_refuses():
    with pytest.raises(UnsupportedQueryError, match="cross-source OPTIONAL"):
        partition_query(
            PREFIX
            + """SELECT ?accountName ?role WHERE {
                 ?a a c:Account ; c:accountId ?k .
                 ?d a c:Document ; c:accountId ?k .
                 OPTIONAL { ?a c:accountName ?accountName . ?d c:role ?role }
               }""",
            _account_document_catalog(),
        )


def test_optional_variable_bound_in_another_leg_refuses():
    # ?region is required on the Postgres leg but re-bound inside an OPTIONAL on
    # the Arango leg — the single-leg pushdown would make a value another leg
    # needs optional, so it's refused (not well-designed).
    with pytest.raises(UnsupportedQueryError, match="well-designed"):
        partition_query(
            PREFIX
            + """SELECT ?region WHERE {
                 ?a a c:Account ; c:accountId ?k ; c:region ?region .
                 ?d a c:Document ; c:accountId ?k .
                 OPTIONAL { ?d c:tag ?region }
               }""",
            _account_document_catalog(),
        )


def test_plain_bgp_serialization_is_unchanged():
    # The pushdown is strictly additive: a BGP-only query still serializes with
    # no FILTER/OPTIONAL and empty pushdown fields (the regression contract).
    plan = partition_query(
        PREFIX
        + "SELECT ?u ?name WHERE { ?o a c:Order ; c:placed_by ?u . ?u a c:User ; c:name ?name }",
        _two_source_catalog(),
    )
    for sq in plan.sub_queries:
        assert sq.filters == ()
        assert sq.optional_groups == ()
        assert "FILTER" not in sq.sparql
        assert "OPTIONAL" not in sq.sparql
