"""Deterministic statistics-driven physical planning (P2.2 WP-9/WP-10)."""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

from .catalog import ClassStatistics, PropertyStatistics, SourceCatalog, SourceStatistics
from .types import PartitionPlan, SubQuery

DEFAULT_ROWS = 1_000_000
DEFAULT_BYTES_PER_ROW = 1024
DEFAULT_FILTER_SELECTIVITY = 0.25
DP_JOIN_LIMIT = 8
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


@dataclass(frozen=True)
class LegEstimate:
    """Inspectable estimate for one source leg."""

    source_id: str
    estimated_rows: int
    estimated_bytes: int
    estimated_cost_usd: float | None
    snapshot_id: str | None = None
    as_of: str | None = None
    used_statistics: bool = False
    resolution_enabled: bool = False
    estimated_resolution_calls: int | None = None


@dataclass(frozen=True)
class SeedDirection:
    """A bind-join edge selected by the physical planner."""

    from_source_ids: tuple[str, ...]
    to_source_id: str
    variables: tuple[str, ...]


@dataclass(frozen=True)
class PlanEstimate:
    """Deterministic, serializable physical strategy and its estimate."""

    strategy: str
    legs: tuple[LegEstimate, ...]
    stages: tuple[tuple[str, ...], ...]
    execution_order: tuple[str, ...]
    seed_directions: tuple[SeedDirection, ...]
    estimated_rows: int
    estimated_bytes: int
    estimated_cost_usd: float | None
    statistics_version: str | None
    estimated_resolution_calls: int | None = None


def _bare(value: str) -> str:
    return value[1:] if value.startswith("?") else value


def _local_iri(term: str, catalog: SourceCatalog) -> str | None:
    if not term.startswith("<") or not term.endswith(">"):
        return None
    iri = term[1:-1]
    return catalog._local(iri)  # planner and catalog share the configured namespace


def _property_stats(
    source_stats: SourceStatistics,
    class_stats: ClassStatistics | None,
    property_name: str,
) -> PropertyStatistics | None:
    if class_stats is not None and property_name in class_stats.properties:
        return class_stats.properties[property_name]
    return source_stats.properties.get(property_name)


def _leg_variables_ndv(
    subquery: SubQuery,
    catalog: SourceCatalog,
    stats: SourceStatistics | None,
    class_stats: ClassStatistics | None,
) -> dict[str, int]:
    if stats is None:
        return {}
    out: dict[str, int] = {}
    for triple in subquery.triples:
        prop = _local_iri(triple.predicate, catalog)
        if prop is None or not triple.object.startswith("?"):
            continue
        prop_stats = _property_stats(stats, class_stats, prop)
        if prop_stats is not None and prop_stats.ndv is not None:
            out[_bare(triple.object)] = prop_stats.ndv
    return out


def _estimate_leg(
    subquery: SubQuery,
    catalog: SourceCatalog,
) -> tuple[LegEstimate, dict[str, int]]:
    stats = catalog.statistics_for(subquery.source)
    resolution = catalog.resolution_for(subquery.source)
    resolution_enabled = resolution is not None and resolution.mode == "canonical_hub"
    if stats is None:
        return (
            LegEstimate(
                source_id=subquery.source.source_id,
                estimated_rows=DEFAULT_ROWS,
                estimated_bytes=DEFAULT_ROWS * DEFAULT_BYTES_PER_ROW,
                estimated_cost_usd=None,
                resolution_enabled=resolution_enabled,
            ),
            {},
        )

    class_names = [
        _local_iri(t.object, catalog)
        for t in subquery.triples
        if t.predicate == f"<{RDF_TYPE}>"
    ]
    class_candidates = [stats.classes[name] for name in class_names if name in stats.classes]
    class_stats = class_candidates[0] if class_candidates else None
    rows = (
        class_stats.row_count
        if class_stats is not None and class_stats.row_count is not None
        else stats.row_count
    )
    rows = DEFAULT_ROWS if rows is None else rows

    variable_properties: dict[str, PropertyStatistics] = {}
    selectivity = 1.0
    for triple in subquery.triples:
        prop_name = _local_iri(triple.predicate, catalog)
        if prop_name is None:
            continue
        prop_stats = _property_stats(stats, class_stats, prop_name)
        if prop_stats is None:
            continue
        if triple.object.startswith("?"):
            variable_properties[_bare(triple.object)] = prop_stats
        else:
            if prop_stats.selectivity is not None:
                selectivity *= prop_stats.selectivity
            elif prop_stats.ndv:
                selectivity *= 1.0 / prop_stats.ndv

    for expression in subquery.filters:
        matched = False
        for variable in re.findall(r"\?([A-Za-z_][A-Za-z0-9_]*)", expression):
            prop_stats = variable_properties.get(variable)
            if prop_stats is None:
                continue
            matched = True
            if prop_stats.selectivity is not None:
                selectivity *= prop_stats.selectivity
            elif prop_stats.ndv:
                selectivity *= min(1.0, 1.0 / prop_stats.ndv)
        if not matched:
            selectivity *= DEFAULT_FILTER_SELECTIVITY

    estimated_rows = max(0, int(round(rows * min(1.0, selectivity))))
    source_bytes = (
        class_stats.estimated_bytes
        if class_stats is not None and class_stats.estimated_bytes is not None
        else stats.estimated_bytes
    )
    source_rows = (
        class_stats.row_count
        if class_stats is not None and class_stats.row_count is not None
        else stats.row_count
    )
    bytes_per_row = (
        source_bytes / source_rows
        if source_bytes is not None and source_rows not in (None, 0)
        else DEFAULT_BYTES_PER_ROW
    )
    estimated_bytes = max(0, int(round(estimated_rows * bytes_per_row)))
    cost = (
        estimated_bytes / 1_000_000_000 * stats.cost_per_gb_usd
        if stats.cost_per_gb_usd is not None
        else None
    )
    return (
        LegEstimate(
            source_id=subquery.source.source_id,
            estimated_rows=estimated_rows,
            estimated_bytes=estimated_bytes,
            estimated_cost_usd=cost,
            snapshot_id=stats.snapshot_id,
            as_of=stats.as_of,
            used_statistics=True,
            resolution_enabled=resolution_enabled,
            estimated_resolution_calls=estimated_rows if resolution_enabled else 0,
        ),
        _leg_variables_ndv(subquery, catalog, stats, class_stats),
    )


def _shared(left: SubQuery, right: SubQuery) -> tuple[str, ...]:
    return tuple(
        sorted({_bare(v) for v in left.variables} & {_bare(v) for v in right.variables})
    )


def _components(subqueries: tuple[SubQuery, ...]) -> list[list[int]]:
    unseen = set(range(len(subqueries)))
    components: list[list[int]] = []
    while unseen:
        root = min(unseen, key=lambda i: subqueries[i].source.source_id)
        unseen.remove(root)
        component = [root]
        frontier = [root]
        while frontier:
            current = frontier.pop()
            neighbors = sorted(
                [i for i in unseen if _shared(subqueries[current], subqueries[i])],
                key=lambda i: subqueries[i].source.source_id,
            )
            for neighbor in neighbors:
                unseen.remove(neighbor)
                component.append(neighbor)
                frontier.append(neighbor)
        components.append(component)
    return components


def _joined_rows(
    left_rows: int,
    right_rows: int,
    shared: tuple[str, ...],
    left_ndv: dict[str, int],
    right_ndv: dict[str, int],
) -> int:
    if left_rows == 0 or right_rows == 0:
        return 0
    if not shared:
        return left_rows * right_rows
    denominators = [
        max(left_ndv.get(var, 1), right_ndv.get(var, 1)) for var in shared
    ]
    return max(1, int(round(left_rows * right_rows / max(denominators))))


def _best_component_order(
    component: list[int],
    subqueries: tuple[SubQuery, ...],
    estimates: list[LegEstimate],
    ndvs: list[dict[str, int]],
) -> tuple[list[int], str]:
    if len(component) <= 1:
        return component, "single-source"
    if len(component) > DP_JOIN_LIMIT:
        remaining = set(component)
        first = min(
            remaining,
            key=lambda i: (estimates[i].estimated_rows, subqueries[i].source.source_id),
        )
        order = [first]
        remaining.remove(first)
        while remaining:
            connected = [
                i
                for i in remaining
                if any(_shared(subqueries[i], subqueries[j]) for j in order)
            ]
            candidates = connected or list(remaining)
            chosen = min(
                candidates,
                key=lambda i: (
                    estimates[i].estimated_rows,
                    subqueries[i].source.source_id,
                ),
            )
            order.append(chosen)
            remaining.remove(chosen)
        return order, "greedy"

    # Exact left-deep dynamic programming. State stores cumulative intermediate
    # rows, current rows, order, and known join-variable NDVs.
    states: dict[frozenset[int], tuple[int, int, tuple[int, ...], dict[str, int]]] = {}
    for i in component:
        states[frozenset({i})] = (
            estimates[i].estimated_rows,
            estimates[i].estimated_rows,
            (i,),
            dict(ndvs[i]),
        )
    for size in range(2, len(component) + 1):
        for subset_tuple in itertools.combinations(component, size):
            subset = frozenset(subset_tuple)
            best: tuple[int, int, tuple[int, ...], dict[str, int]] | None = None
            for right in subset:
                previous = states[subset - {right}]
                previous_indices = previous[2]
                shared_variables: set[str] = set()
                for left in previous_indices:
                    shared_variables.update(_shared(subqueries[left], subqueries[right]))
                shared = tuple(sorted(shared_variables))
                if not shared:
                    continue
                rows = _joined_rows(
                    previous[1],
                    estimates[right].estimated_rows,
                    shared,
                    previous[3],
                    ndvs[right],
                )
                merged_ndv = dict(previous[3])
                for variable, ndv in ndvs[right].items():
                    merged_ndv[variable] = min(merged_ndv.get(variable, ndv), ndv)
                candidate = (
                    previous[0] + rows,
                    rows,
                    previous_indices + (right,),
                    merged_ndv,
                )
                candidate_key = (
                    candidate[0],
                    tuple(subqueries[i].source.source_id for i in candidate[2]),
                )
                best_key = (
                    best[0],
                    tuple(subqueries[i].source.source_id for i in best[2]),
                ) if best is not None else None
                if best_key is None or candidate_key < best_key:
                    best = candidate
            if best is not None:
                states[subset] = best
    return list(states[frozenset(component)][2]), "dynamic-programming"


def _legacy_stages(plan: PartitionPlan) -> tuple[tuple[str, ...], ...]:
    ordered = sorted(plan.sub_queries, key=lambda sq: sq.source.kind == "arango")
    stages = (
        tuple(sq.source.source_id for sq in ordered if sq.source.kind != "arango"),
        tuple(sq.source.source_id for sq in ordered if sq.source.kind == "arango"),
    )
    return tuple(stage for stage in stages if stage)


def estimate_plan(plan: PartitionPlan, catalog: SourceCatalog) -> PlanEstimate:
    """Choose a stable strategy using validated CSI statistics only."""
    estimated = [_estimate_leg(subquery, catalog) for subquery in plan.sub_queries]
    estimates = [item[0] for item in estimated]
    ndvs = [item[1] for item in estimated]
    has_statistics = any(item.used_statistics for item in estimates)

    if not has_statistics:
        stages = _legacy_stages(plan)
        order_ids = tuple(source_id for stage in stages for source_id in stage)
        strategy = "legacy-no-statistics"
        ordered_indices = [
            next(i for i, sq in enumerate(plan.sub_queries) if sq.source.source_id == source_id)
            for source_id in order_ids
        ]
    else:
        component_orders: list[list[int]] = []
        methods: set[str] = set()
        for component in _components(plan.sub_queries):
            order, method = _best_component_order(
                component, plan.sub_queries, estimates, ndvs
            )
            component_orders.append(order)
            methods.add(method)
        stages = tuple(
            tuple(
                plan.sub_queries[order[depth]].source.source_id
                for order in component_orders
                if depth < len(order)
            )
            for depth in range(max((len(order) for order in component_orders), default=0))
        )
        ordered_indices = [
            next(i for i, sq in enumerate(plan.sub_queries) if sq.source.source_id == source_id)
            for stage in stages
            for source_id in stage
        ]
        order_ids = tuple(
            plan.sub_queries[index].source.source_id for index in ordered_indices
        )
        strategy = (
            "dynamic-programming"
            if "dynamic-programming" in methods
            else "greedy"
            if "greedy" in methods
            else "single-source"
        )

    resolution_ids = {
        subquery.source.source_id
        for subquery in plan.sub_queries
        if (
            (binding := catalog.resolution_for(subquery.source)) is not None
            and binding.mode == "canonical_hub"
        )
    }
    if resolution_ids and stages:
        first = tuple(
            source_id
            for source_id in order_ids
            if source_id in set(stages[0]) or source_id in resolution_ids
        )
        stages = (
            first,
            *tuple(
                tuple(
                    source_id
                    for source_id in stage
                    if source_id not in resolution_ids
                )
                for stage in stages[1:]
            ),
        )
        stages = tuple(stage for stage in stages if stage)
        order_ids = tuple(source_id for stage in stages for source_id in stage)
        ordered_indices = [
            next(
                index
                for index, subquery in enumerate(plan.sub_queries)
                if subquery.source.source_id == source_id
            )
            for source_id in order_ids
        ]

    seed_directions: list[SeedDirection] = []
    prior: list[int] = []
    for stage in stages:
        stage_indices = [
            next(
                index
                for index, subquery in enumerate(plan.sub_queries)
                if subquery.source.source_id == source_id
            )
            for source_id in stage
        ]
        for index in stage_indices:
            variables = tuple(
                sorted(
                    {
                        variable
                        for previous in prior
                        for variable in _shared(
                            plan.sub_queries[previous],
                            plan.sub_queries[index],
                        )
                    }
                )
            )
            if variables:
                from_ids = tuple(
                    plan.sub_queries[previous].source.source_id
                    for previous in prior
                    if _shared(plan.sub_queries[previous], plan.sub_queries[index])
                )
                seed_directions.append(
                    SeedDirection(
                        from_ids,
                        plan.sub_queries[index].source.source_id,
                        variables,
                    )
                )
        prior.extend(stage_indices)

    running_rows = 0
    running_ndv: dict[str, int] = {}
    prior_indices: list[int] = []
    for index in ordered_indices:
        if not prior_indices:
            running_rows = estimates[index].estimated_rows
        else:
            shared = tuple(
                sorted(
                    {
                        variable
                        for previous in prior_indices
                        for variable in _shared(
                            plan.sub_queries[previous], plan.sub_queries[index]
                        )
                    }
                )
            )
            running_rows = _joined_rows(
                running_rows,
                estimates[index].estimated_rows,
                shared,
                running_ndv,
                ndvs[index],
            )
        for variable, ndv in ndvs[index].items():
            running_ndv[variable] = min(running_ndv.get(variable, ndv), ndv)
        prior_indices.append(index)

    costs = [item.estimated_cost_usd for item in estimates]
    resolution_estimates = [
        item.estimated_resolution_calls for item in estimates if item.resolution_enabled
    ]
    return PlanEstimate(
        strategy=strategy,
        legs=tuple(estimates[index] for index in ordered_indices),
        stages=stages,
        execution_order=order_ids,
        seed_directions=tuple(seed_directions),
        estimated_rows=running_rows,
        estimated_bytes=sum(item.estimated_bytes for item in estimates),
        estimated_cost_usd=(
            sum(cost for cost in costs if cost is not None)
            if costs and all(cost is not None for cost in costs)
            else None
        ),
        estimated_resolution_calls=(
            sum(value for value in resolution_estimates if value is not None)
            if all(value is not None for value in resolution_estimates)
            else None
        ),
        statistics_version="1" if has_statistics else None,
    )
