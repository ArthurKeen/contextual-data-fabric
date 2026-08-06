"""Exhaustive oracle benchmark for the statistics-driven physical planner.

The production optimizer uses dynamic programming for connected plans of up to
eight source legs. This evaluator independently enumerates every valid
left-deep order under the documented cardinality model, then checks the selected
order, seed directions, result cardinality, remote bytes, and source cost.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cdf.query import SourceCatalog, estimate_plan, partition_query

SCHEMA_VERSION = 1
DEFAULT_CORPUS = Path(__file__).with_name("corpora") / "optimizer-oracle-v1.json"


@dataclass(frozen=True)
class OracleSource:
    source_id: str
    kind: str
    ref: str
    entity: str
    row_count: int
    bytes_per_row: int
    cost_per_gb_usd: float
    variables: dict[str, int]


@dataclass(frozen=True)
class OracleCase:
    id: str
    sources: tuple[OracleSource, ...]
    expected_oracle_order: tuple[str, ...]


@dataclass(frozen=True)
class OrderEvaluation:
    order: tuple[str, ...]
    cumulative_rows: int
    final_rows: int


@dataclass(frozen=True)
class OptimizerOracleCaseResult:
    id: str
    source_count: int
    feasible_plan_count: int
    selected_order: tuple[str, ...]
    oracle_order: tuple[str, ...]
    selected_cumulative_rows: int
    oracle_cumulative_rows: int
    objective_ratio: float
    order_optimal: bool
    golden_order_match: bool
    seed_directions_match: bool
    final_rows_match: bool
    estimated_bytes_match: bool
    estimated_cost_match: bool
    passed: bool
    errors: tuple[str, ...]


@dataclass(frozen=True)
class OptimizerOracleReport:
    schema_version: int
    corpus_version: str
    objective: str
    total: int
    passed: int
    all_passed: bool
    total_feasible_plans: int
    max_objective_ratio: float
    cases: tuple[OptimizerOracleCaseResult, ...]


def _positive_integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{path} must be a positive integer")
    return value


def _positive_number(value: Any, path: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise ValueError(f"{path} must be a positive finite number")
    return float(value)


def _non_empty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value


def load_optimizer_corpus(
    path: str | Path = DEFAULT_CORPUS,
) -> tuple[str, tuple[OracleCase, ...]]:
    """Load and validate the disclosed optimizer oracle corpus."""

    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    corpus_version = _non_empty_string(document.get("corpus_version"), "corpus_version")
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty array")
    cases: list[OracleCase] = []
    seen_ids: set[str] = set()
    for case_index, raw_case in enumerate(raw_cases):
        path_prefix = f"cases[{case_index}]"
        if not isinstance(raw_case, dict):
            raise ValueError(f"{path_prefix} must be an object")
        case_id = _non_empty_string(raw_case.get("id"), f"{path_prefix}.id")
        if case_id in seen_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        raw_sources = raw_case.get("sources")
        if not isinstance(raw_sources, list) or not 2 <= len(raw_sources) <= 8:
            raise ValueError(f"{path_prefix}.sources must contain 2 through 8 sources")
        sources: list[OracleSource] = []
        source_ids: set[str] = set()
        entities: set[str] = set()
        for source_index, raw_source in enumerate(raw_sources):
            source_path = f"{path_prefix}.sources[{source_index}]"
            if not isinstance(raw_source, dict):
                raise ValueError(f"{source_path} must be an object")
            kind = _non_empty_string(raw_source.get("kind"), f"{source_path}.kind")
            ref = _non_empty_string(raw_source.get("ref"), f"{source_path}.ref")
            source_id = f"{kind}:{ref}"
            entity = _non_empty_string(raw_source.get("entity"), f"{source_path}.entity")
            if source_id in source_ids:
                raise ValueError(f"{path_prefix} has duplicate source {source_id}")
            if entity in entities:
                raise ValueError(f"{path_prefix} has duplicate entity {entity}")
            source_ids.add(source_id)
            entities.add(entity)
            raw_variables = raw_source.get("variables")
            if not isinstance(raw_variables, dict) or not raw_variables:
                raise ValueError(f"{source_path}.variables must be a non-empty object")
            variables = {
                _non_empty_string(name, f"{source_path}.variables key"): _positive_integer(
                    ndv, f"{source_path}.variables.{name}"
                )
                for name, ndv in raw_variables.items()
            }
            sources.append(
                OracleSource(
                    source_id=source_id,
                    kind=kind,
                    ref=ref,
                    entity=entity,
                    row_count=_positive_integer(
                        raw_source.get("row_count"), f"{source_path}.row_count"
                    ),
                    bytes_per_row=_positive_integer(
                        raw_source.get("bytes_per_row"), f"{source_path}.bytes_per_row"
                    ),
                    cost_per_gb_usd=_positive_number(
                        raw_source.get("cost_per_gb_usd"),
                        f"{source_path}.cost_per_gb_usd",
                    ),
                    variables=variables,
                )
            )
        expected = raw_case.get("expected_oracle_order")
        if (
            not isinstance(expected, list)
            or len(expected) != len(sources)
            or set(expected) != source_ids
        ):
            raise ValueError(
                f"{path_prefix}.expected_oracle_order must contain every source exactly once"
            )
        cases.append(
            OracleCase(
                id=case_id,
                sources=tuple(sources),
                expected_oracle_order=tuple(expected),
            )
        )
    return corpus_version, tuple(cases)


def _shared_variables(left: OracleSource, right: OracleSource) -> tuple[str, ...]:
    return tuple(sorted(set(left.variables) & set(right.variables)))


def _evaluate_order(order: tuple[OracleSource, ...]) -> OrderEvaluation | None:
    running_rows = order[0].row_count
    cumulative_rows = running_rows
    prior = [order[0]]
    running_ndv = dict(order[0].variables)
    for source in order[1:]:
        shared = tuple(
            sorted(
                {
                    variable
                    for previous in prior
                    for variable in _shared_variables(previous, source)
                }
            )
        )
        if not shared:
            return None
        denominators = [
            max(running_ndv.get(variable, 1), source.variables.get(variable, 1))
            for variable in shared
        ]
        running_rows = max(
            1,
            int(round(running_rows * source.row_count / max(denominators))),
        )
        cumulative_rows += running_rows
        for variable, ndv in source.variables.items():
            running_ndv[variable] = min(running_ndv.get(variable, ndv), ndv)
        prior.append(source)
    return OrderEvaluation(
        order=tuple(source.source_id for source in order),
        cumulative_rows=cumulative_rows,
        final_rows=running_rows,
    )


def enumerate_oracle(case: OracleCase) -> tuple[OrderEvaluation, int]:
    """Return the optimal connected left-deep order and feasible-plan count."""

    candidates = [
        result
        for permutation in itertools.permutations(case.sources)
        if (result := _evaluate_order(permutation)) is not None
    ]
    if not candidates:
        raise ValueError(f"{case.id} has no connected left-deep plan")
    return min(candidates, key=lambda item: (item.cumulative_rows, item.order)), len(candidates)


def _csi(source: OracleSource) -> dict[str, Any]:
    properties = {
        variable: {"ndv": ndv} for variable, ndv in source.variables.items()
    }
    return {
        "csiVersion": "1",
        "conceptualModel": {
            "entities": [
                {
                    "name": source.entity,
                    "properties": [
                        *({"name": variable} for variable in source.variables),
                        {"name": "benchmarkValue"},
                    ],
                }
            ]
        },
        "provenance": {
            "producer": "optimizer-oracle",
            "direction": "forward",
            "source": {"kind": source.kind, "ref": source.ref},
        },
        "statistics": {
            "version": "1",
            "snapshotId": f"{source.ref}-oracle-v1",
            "asOf": "2026-08-05T00:00:00Z",
            "source": {
                "rowCount": source.row_count,
                "estimatedBytes": source.row_count * source.bytes_per_row,
                "costPerGbUsd": source.cost_per_gb_usd,
            },
            "classes": {
                source.entity: {
                    "rowCount": source.row_count,
                    "estimatedBytes": source.row_count * source.bytes_per_row,
                    "properties": properties,
                }
            },
        },
    }


def _query(case: OracleCase) -> str:
    projection = " ".join(f"?value_{index}" for index in range(len(case.sources)))
    lines = []
    for index, source in enumerate(case.sources):
        predicates = [
            f"c:{variable} ?{variable}" for variable in source.variables
        ]
        predicates.append(f"c:benchmarkValue ?value_{index}")
        lines.append(
            f"  ?entity_{index} a c:{source.entity} ; "
            + " ; ".join(predicates)
            + " ."
        )
    return (
        "PREFIX c: <urn:arango-sparql:concept#>\n"
        f"SELECT {projection} WHERE {{\n"
        + "\n".join(lines)
        + "\n}"
    )


def _expected_seed_directions(
    order: tuple[str, ...],
    sources: dict[str, OracleSource],
) -> tuple[tuple[tuple[str, ...], str, tuple[str, ...]], ...]:
    directions = []
    prior: list[str] = []
    for source_id in order:
        source = sources[source_id]
        variables = tuple(
            sorted(
                {
                    variable
                    for previous_id in prior
                    for variable in _shared_variables(sources[previous_id], source)
                }
            )
        )
        if variables:
            from_ids = tuple(
                previous_id
                for previous_id in prior
                if _shared_variables(sources[previous_id], source)
            )
            directions.append((from_ids, source_id, variables))
        prior.append(source_id)
    return tuple(directions)


def evaluate_optimizer_case(case: OracleCase) -> OptimizerOracleCaseResult:
    """Compare one production plan with the exhaustive oracle."""

    oracle, feasible_plan_count = enumerate_oracle(case)
    catalog = SourceCatalog.from_csi_documents([_csi(source) for source in case.sources])
    estimate = estimate_plan(partition_query(_query(case), catalog), catalog)
    sources = {source.source_id: source for source in case.sources}
    selected_sources = tuple(sources[source_id] for source_id in estimate.execution_order)
    selected = _evaluate_order(selected_sources)
    if selected is None:
        raise AssertionError("production optimizer selected a disconnected order")
    expected_seed_directions = _expected_seed_directions(estimate.execution_order, sources)
    actual_seed_directions = tuple(
        (direction.from_source_ids, direction.to_source_id, direction.variables)
        for direction in estimate.seed_directions
    )
    expected_bytes = sum(
        source.row_count * source.bytes_per_row for source in case.sources
    )
    expected_cost = sum(
        source.row_count
        * source.bytes_per_row
        / 1_000_000_000
        * source.cost_per_gb_usd
        for source in case.sources
    )
    checks = {
        "order_optimal": estimate.execution_order == oracle.order,
        "golden_order_match": oracle.order == case.expected_oracle_order,
        "seed_directions_match": actual_seed_directions == expected_seed_directions,
        "final_rows_match": estimate.estimated_rows == oracle.final_rows,
        "estimated_bytes_match": estimate.estimated_bytes == expected_bytes,
        "estimated_cost_match": estimate.estimated_cost_usd is not None
        and math.isclose(estimate.estimated_cost_usd, expected_cost, rel_tol=1e-12),
    }
    errors = tuple(name for name, passed in checks.items() if not passed)
    ratio = selected.cumulative_rows / oracle.cumulative_rows
    return OptimizerOracleCaseResult(
        id=case.id,
        source_count=len(case.sources),
        feasible_plan_count=feasible_plan_count,
        selected_order=estimate.execution_order,
        oracle_order=oracle.order,
        selected_cumulative_rows=selected.cumulative_rows,
        oracle_cumulative_rows=oracle.cumulative_rows,
        objective_ratio=ratio,
        **checks,
        passed=not errors,
        errors=errors,
    )


def evaluate_optimizer_corpus(
    path: str | Path = DEFAULT_CORPUS,
) -> OptimizerOracleReport:
    """Evaluate every fixed case against the exhaustive oracle."""

    corpus_version, cases = load_optimizer_corpus(path)
    results = tuple(evaluate_optimizer_case(case) for case in cases)
    passed = sum(case.passed for case in results)
    return OptimizerOracleReport(
        schema_version=SCHEMA_VERSION,
        corpus_version=corpus_version,
        objective="minimize cumulative estimated intermediate rows; tie-break by source id",
        total=len(results),
        passed=passed,
        all_passed=passed == len(results),
        total_feasible_plans=sum(case.feasible_plan_count for case in results),
        max_objective_ratio=max(case.objective_ratio for case in results),
        cases=results,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare the CDF physical planner with an exhaustive oracle",
    )
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = evaluate_optimizer_corpus(args.corpus)
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        for case in report.cases:
            mark = "PASS" if case.passed else "FAIL"
            print(
                f"{mark}  {case.id}  plans={case.feasible_plan_count} "
                f"ratio={case.objective_ratio:.3f}"
            )
            for error in case.errors:
                print(f"      - {error}")
        print(
            f"\noptimizer-oracle: {report.passed}/{report.total} passed; "
            f"{report.total_feasible_plans} plans enumerated"
        )
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
