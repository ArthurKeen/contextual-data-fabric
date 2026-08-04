"""Native Snowflake :class:`SourceExecutor` (M5 — Option B, ADR-0002).

**Snowflake takes SQL directly** (ADR-0002): plain SQL over the Python connector,
deterministic and token-free, exactly like the Postgres leg — no query prompt, no
Cortex. So rather than stand up a second Ontop container + JDBC driver (the written
Sprint-2 Option A), the Snowflake relational leg is compiled **natively** here, the
same shape as :class:`~cdf.adapters.clickhouse.ClickHouseExecutor`: it consumes the
**same r2g-emitted R2RML** and compiles the E1 single-source BGP straight to
Snowflake SQL. Choosing this over Ontop also sidesteps the JDBC/Arrow/Java-17
result-format quirk (PRD §7.7) — the Python connector has no such wrinkle.

The compile/parse/VALUES machinery is dialect-neutral and shared with the ClickHouse
executor (imported below); only three things differ for Snowflake, and they are the
whole reason this is a separate module:

- **Identifiers are double-quoted** (``"USAGE_METRICS"``), not backtick-quoted.
  We load unquoted so Snowflake folds physical names to UPPERCASE (PRD §7.7); the
  R2RML therefore carries uppercase names and double-quoting matches them exactly.
- **SELECT aliases are also double-quoted** so Snowflake preserves the bare SPARQL
  variable's case (``… AS "qv"``). Unquoted, Snowflake upper-folds the alias to
  ``QV`` and the reassembly join — which is keyed on the exact variable name —
  silently misses. This is the Snowflake analogue of CC-12's naming discipline.
- **String literals double the single quote** (``'O''Brien'``, standard SQL), and
  booleans render as ``TRUE``/``FALSE`` (a real Snowflake type), where ClickHouse
  used backslash-escaping and ``1``/``0``.

Only a live Snowflake validates the emitted dialect — see ``deploy/snowflake`` and
the opt-in ``tests/test_snowflake_live.py`` (a fake transport accepts SQL a real
warehouse may reject; the arango-sparql-py cross-validation lesson).

TODO(simplify): once both native SQL legs are demo-proven, lift the shared compiler
into a dialect-parameterised module so ``compile_sql`` isn't reimplemented per leg.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from collections.abc import Mapping as _Mapping
from datetime import datetime, timezone
from typing import Any

from rdflib import RDF, Literal, URIRef
from rdflib.plugins.sparql import prepareQuery
from rdflib.term import Variable

# Dialect-neutral machinery is shared with the ClickHouse leg (R2RML parsing and
# BGP/VALUES extraction carry no dialect); only emission differs below.
from cdf.adapters.clickhouse import (
    Mapping,
    Transport,
    _collect_bgp,
    _collect_filters,
    _literal_value,
    _parse_values,
    parse_r2rml,
)
from cdf.query.executor import Binding, SourceResult
from cdf.query.types import SubQuery

__all__ = ["SnowflakeExecutor", "SnowflakeError", "compile_sql", "parse_r2rml"]


class SnowflakeError(ValueError):
    """Raised when an R2RML mapping or a sub-query cannot be compiled."""


# ---------------------------------------------------------------------------
# Snowflake SQL emission (the dialect-specific part)
# ---------------------------------------------------------------------------


def _ident(name: str) -> str:
    """Double-quote a Snowflake identifier (preserves case; matches uppercase
    physical names loaded unquoted, and preserves alias case for reassembly)."""
    return '"' + name.replace('"', '""') + '"'


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):  # before int — bool is an int subclass
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    # Standard SQL: single quote doubled. Backslash is also doubled because
    # Snowflake interprets backslash escape sequences in string literals.
    escaped = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{escaped}'"


def compile_sql(sparql: str, mapping: Mapping) -> str:
    """Compile a single-source BGP sub-query to Snowflake SQL.

    Mirrors the ClickHouse compiler exactly except for the Snowflake dialect
    helpers (``_ident``/``_sql_literal``) — see the module docstring.
    """
    algebra = prepareQuery(sparql).algebra
    projection = [str(v) for v in (algebra.get("PV") or [])]
    triples: list[tuple[Any, Any, Any]] = []
    _collect_bgp(algebra, triples)

    subject_class: dict[Variable, str] = {}
    for subject, predicate, obj in triples:
        if predicate == RDF.type and isinstance(obj, URIRef) and isinstance(subject, Variable):
            subject_class[subject] = str(obj)
    if not subject_class:
        raise SnowflakeError("sub-query has no typed subject to map to a table")

    aliases: dict[Variable, str] = {}
    from_parts: list[str] = []
    for i, (svar, cls) in enumerate(subject_class.items()):
        if cls not in mapping:
            raise SnowflakeError(f"class {cls!r} is not in the R2RML mapping")
        alias = f"t{i}"
        aliases[svar] = alias
        from_parts.append(f"{_ident(mapping[cls]['table'])} AS {alias}")

    def column_expr(svar: Variable, predicate: URIRef) -> str:
        cols = mapping[subject_class[svar]]["columns"]
        col = cols.get(str(predicate))
        if col is None:
            raise SnowflakeError(
                f"property {predicate!r} not mapped for class {subject_class[svar]!r}"
            )
        return f"{aliases[svar]}.{_ident(col)}"

    var_exprs: dict[Variable, list[str]] = {}
    where: list[str] = []
    for subject, predicate, obj in triples:
        if predicate == RDF.type:
            continue
        if subject not in aliases:
            raise SnowflakeError("a property is used on an untyped subject")
        expr = column_expr(subject, predicate)
        if isinstance(obj, Variable):
            var_exprs.setdefault(obj, []).append(expr)
        elif isinstance(obj, Literal):
            where.append(f"{expr} = {_sql_literal(_literal_value(obj))}")
        elif isinstance(obj, URIRef):
            where.append(f"{expr} = {_sql_literal(str(obj))}")

    # A variable shared by more than one triple is an equi-join.
    for exprs in var_exprs.values():
        for other in exprs[1:]:
            where.append(f"{exprs[0]} = {other}")

    # Bind-join: VALUES(?k){…} -> col IN (…) (single var) or tuple IN (multi-var).
    val_vars, val_rows = _parse_values(sparql)
    if val_vars and val_rows:
        maybe_cols = [var_exprs.get(Variable(v), [None])[0] for v in val_vars]
        cols = [c for c in maybe_cols if c is not None]
        if len(cols) == len(val_vars):  # every VALUES var is bound to a column
            if len(cols) == 1:
                inlist = ", ".join(_sql_literal(r[0]) for r in val_rows)
                where.append(f"{cols[0]} IN ({inlist})")
            else:
                tuples = ", ".join(
                    "(" + ", ".join(_sql_literal(v) for v in r) + ")" for r in val_rows
                )
                where.append(f"({', '.join(cols)}) IN ({tuples})")

    # Pushed-down E1 FILTER conjuncts: ?var op literal -> col op literal.
    filters: list[tuple[Variable, str, Literal]] = []
    _collect_filters(algebra, filters)
    for fvar, fop, flit in filters:
        bound = var_exprs.get(fvar)
        if not bound:
            raise SnowflakeError(f"FILTER references variable {fvar!r} not bound in this leg")
        where.append(f"{bound[0]} {fop} {_sql_literal(_literal_value(flit))}")

    select: list[str] = []
    for pv in projection:
        var = Variable(pv)
        if var in var_exprs:
            select.append(f"{var_exprs[var][0]} AS {_ident(pv)}")
    sql = f"SELECT {', '.join(select) or '*'} FROM {', '.join(from_parts)}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql


# ---------------------------------------------------------------------------
# Transport + executor
# ---------------------------------------------------------------------------


#: Upper bound on idle pooled sessions per executor. Concurrency above the cap
#: still works (extra connections are opened, used, then closed on return);
#: the cap only bounds what idles between queries.
_POOL_MAX = 4

#: Snowflake error numbers that mean the *session/auth* is dead — not the SQL.
#: 390114 ("Authentication token has expired. The user must authenticate again.")
#: is the one pooling exposes: a session idling in the pool past the master-token
#: TTL (~4h default) can no longer renew, so its next use fails. A freshly
#: authenticated connection (the default transport reconnects with the stored
#: password) recovers cleanly, so these are retriable, not query faults.
_SESSION_AUTH_ERRNOS = frozenset({390114})


def _is_session_auth_error(exc: Any) -> bool:
    """True if a Snowflake ``ProgrammingError`` reports a dead session (expired or
    invalid auth token) rather than a bad query. Snowflake tags these with a
    connection-class SQLSTATE (``08xxx``, e.g. ``08001``), distinct from query
    faults, which carry a ``42xxx`` (syntax/access) SQLSTATE."""
    sqlstate = getattr(exc, "sqlstate", None) or ""
    return sqlstate.startswith("08") or getattr(exc, "errno", None) in _SESSION_AUTH_ERRNOS


def _snowflake_transport(connect_args: _Mapping[str, Any]) -> Transport:
    """Default transport: run SQL over ``snowflake-connector-python``.

    Connections are **pooled** (bounded LIFO). The ~1–3s TLS+auth session setup
    was ~95% of the demo's federated-query latency when paid per query; now a
    connection is borrowed per call, reused across calls, and returned. A
    borrowed session that went stale (idle timeout, network drop) is discarded
    and the query retried **once** on a fresh session.

    An expired/invalid auth token surfaces as a ``ProgrammingError`` (SQLSTATE
    ``08xxx``, e.g. 390114) but is a dead *session*, not a bad query — pooling is
    exactly what exposes it, a session idling past the master-token TTL. Such a
    connection is discarded, the whole idle pool drained (sibling sessions share
    the expiry), and the query retried once on a freshly authenticated session.
    Genuine SQL errors (``42xxx`` — syntax/access) still propagate immediately:
    the session is healthy, the query is at fault, so a bad query is never
    re-executed.
    """
    args = {k: v for k, v in connect_args.items() if v}
    pool: list[Any] = []
    lock = threading.Lock()

    def _borrow() -> Any:
        with lock:
            while pool:
                conn = pool.pop()
                if not conn.is_closed():
                    return conn
        import snowflake.connector  # lazy: engine driver, not a core dep

        return snowflake.connector.connect(**args)

    def _give_back(conn: Any) -> None:
        with lock:
            if len(pool) < _POOL_MAX and not conn.is_closed():
                pool.append(conn)
                return
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — best-effort close of a surplus session
            pass

    def _discard(conn: Any) -> None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — best-effort close of a broken session
            pass

    def _drain() -> None:
        """Close every idle pooled session. An auth-token expiry hits all sessions
        opened in the same window, so one expiry poisons the whole idle pool — a
        single re-auth would otherwise just borrow the next dead session."""
        with lock:
            idle = pool[:]
            pool.clear()
        for conn in idle:
            _discard(conn)

    def transport(sql: str) -> Any:
        import snowflake.connector  # lazy: engine driver, not a core dep

        last: Exception | None = None
        for _attempt in range(2):
            conn = _borrow()
            try:
                cur = conn.cursor(snowflake.connector.DictCursor)
                try:
                    cur.execute(sql)
                    rows = cur.fetchall()  # DictCursor: rows keyed by the quoted alias
                finally:
                    cur.close()
            except snowflake.connector.errors.ProgrammingError as exc:
                if _is_session_auth_error(exc):
                    # Dead session (e.g. expired token) — not the SQL. Drop it,
                    # flush the idle pool, and retry once on a fresh auth session.
                    _discard(conn)
                    _drain()
                    last = exc
                    continue
                _give_back(conn)  # session healthy — the SQL is at fault
                raise
            except snowflake.connector.errors.Error as exc:
                _discard(conn)  # stale/broken session — retry once on a fresh one
                last = exc
                continue
            _give_back(conn)
            return rows
        assert last is not None  # both attempts hit session-level errors
        raise last

    return transport


class SnowflakeExecutor:
    """Executes a sub-query against Snowflake by compiling R2RML+BGP → SQL."""

    def __init__(
        self,
        *,
        r2rml: str | None = None,
        mapping: Mapping | None = None,
        transport: Transport | None = None,
        connect_args: _Mapping[str, Any] | None = None,
        source_objects: tuple[str, ...] = (),
        clock: Callable[[], str] | None = None,
    ) -> None:
        """
        Args:
            r2rml: R2RML Turtle (``r2g export-r2rml``) parsed to concept→
                table/column. Provide this or ``mapping``.
            mapping: a pre-parsed mapping (dependency injection for tests).
            transport: ``(sql) -> rows`` seam; default runs the Python connector.
            connect_args: kwargs for ``snowflake.connector.connect`` (``account``,
                ``user``, ``password``, ``warehouse``, ``database``, ``schema``,
                ``role``) for the default transport. Credentials stay in the
                engine env (CC-7).
            source_objects: physical tables served, cited per result (FR-2).
            clock: as-of stamp provider (FR-12); defaults to UTC now.
        """
        if mapping is None:
            if r2rml is None:
                raise ValueError("SnowflakeExecutor requires r2rml or mapping")
            mapping = parse_r2rml(r2rml)
        if not mapping:
            raise ValueError("R2RML mapping is empty (no TriplesMaps parsed)")
        if transport is None:
            if not connect_args or not connect_args.get("account"):
                raise ValueError(
                    "SnowflakeExecutor requires a transport or connect_args with an account"
                )
            transport = _snowflake_transport(connect_args)
        self._mapping = mapping
        self._transport = transport
        self._source_objects = tuple(source_objects)
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    def execute(self, subquery: SubQuery) -> SourceResult:
        sql = compile_sql(subquery.sparql, self._mapping)
        rows: tuple[Binding, ...] = tuple(dict(row) for row in self._transport(sql))
        return SourceResult(
            rows=rows,
            native_query=sql,
            as_of=self._clock(),
            source_objects=self._source_objects,
        )
