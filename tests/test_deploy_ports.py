"""Port agreement across every entry point that brings the stacks up.

Three separate incidents have now presented identically — the stacks come up
healthy, `make gate` reports a wall of "refused, zero citations", and nothing
in the output points at the cause. Each time the root cause was two entry
points disagreeing about a host port:

  * compose defaulted to the vanilla ports while the Makefile exported the
    deliberately-shifted ones, so `docker compose up` (rather than `make up`)
    made every leg unreachable;
  * CI hardcoded Ontop on 8090 after the Makefile moved it to 18090, so the
    live-local golden gate talked to a port nothing was listening on.

A port mismatch is invisible at every layer that could report it: the
container is healthy, the HTTP call gets a connection refused (or worse, a
404 from an unrelated server), and the executor degrades to zero citations.
These tests make the disagreement fail loudly and offline instead.

Parsed with regex rather than PyYAML on purpose: PyYAML lives in the
`benchmark` extra and is not installed in CI's `check` job, and these
assertions must run there.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "Makefile"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Every compose file that publishes a host port, and the Makefile variable
# whose default it must match.
COMPOSE_PORT_BINDINGS = (
    ("deploy/arango/docker-compose.yml", "CDF_ARANGO_PORT"),
    ("deploy/ontop/docker-compose.yml", "CDF_POSTGRES_PORT"),
    ("deploy/ontop/docker-compose.yml", "CDF_ONTOP_PORT"),
    ("deploy/clickhouse/docker-compose.yml", "CDF_CLICKHOUSE_HTTP_PORT"),
)

# CI job -> the endpoint env vars it sets, and the port pin each must use.
# Keyed by the URL env var because that is the half a human edits by hand.
CI_ENDPOINT_PORT_VARS = {
    "ARANGO_URL": "CDF_ARANGO_PORT",
    "ONTOP_SPARQL_ENDPOINT": "CDF_ONTOP_PORT",
    "ONTOP_REFORMULATE_ENDPOINT": "CDF_ONTOP_PORT",
    "CLICKHOUSE_DSN": "CDF_CLICKHOUSE_HTTP_PORT",
}


def _makefile_port_defaults() -> dict[str, str]:
    """The `export CDF_*_PORT ?= N` defaults the Makefile hands to compose."""
    text = MAKEFILE.read_text(encoding="utf-8")
    return dict(
        re.findall(r"^export\s+(CDF_\w*PORT)\s*\?=\s*(\d+)\s*$", text, re.MULTILINE)
    )


def _compose_port_default(compose_path: Path, var: str) -> str | None:
    """The `${VAR:-N}` fallback compose uses when the variable is unset."""
    match = re.search(
        r"\$\{" + re.escape(var) + r":-(\d+)\}", compose_path.read_text(encoding="utf-8")
    )
    return match.group(1) if match else None


def _ci_jobs() -> dict[str, str]:
    """Split ci.yml into `job name -> job body` at two-space indented keys."""
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    starts = [
        (m.group(1), m.start()) for m in re.finditer(r"^  (\w[\w-]*):$", text, re.MULTILINE)
    ]
    bounds = [s for _, s in starts] + [len(text)]
    return {name: text[start : bounds[i + 1]] for i, (name, start) in enumerate(starts)}


def test_makefile_declares_a_default_for_every_port_it_uses() -> None:
    """A `$(CDF_*_PORT)` reference with no `export ... ?=` silently expands empty."""
    text = MAKEFILE.read_text(encoding="utf-8")
    declared = set(_makefile_port_defaults())
    referenced = set(re.findall(r"\$\((CDF_\w*PORT)\)", text))

    assert referenced, "expected the Makefile to reference at least one CDF_*_PORT"
    assert referenced <= declared, (
        f"Makefile references undeclared port variables {sorted(referenced - declared)}; "
        "they expand to the empty string and produce URLs like http://127.0.0.1:/sparql"
    )


@pytest.mark.parametrize(("compose_rel_path", "var"), COMPOSE_PORT_BINDINGS)
def test_compose_default_matches_makefile_default(compose_rel_path: str, var: str) -> None:
    """`docker compose up` and `make up` must land on the same host port."""
    makefile_defaults = _makefile_port_defaults()
    assert var in makefile_defaults, f"Makefile declares no default for {var}"

    compose_path = REPO_ROOT / compose_rel_path
    compose_default = _compose_port_default(compose_path, var)
    assert compose_default is not None, (
        f"{compose_rel_path} does not publish a host port via ${{{var}:-...}}; "
        "a literal port here cannot be overridden and will drift from the Makefile"
    )
    assert compose_default == makefile_defaults[var], (
        f"{compose_rel_path} defaults {var} to {compose_default} but the Makefile "
        f"exports {makefile_defaults[var]} — bring the stacks up the other way and "
        "every leg on this port becomes unreachable, with the gate reporting only "
        "'refused, zero citations'"
    )


@pytest.mark.parametrize("job", ["live-local", "live-full"])
def test_ci_pins_every_port_the_makefile_declares(job: str) -> None:
    """CI must pin the ports rather than inherit the Makefile's `?=` defaults."""
    body = _ci_jobs()[job]
    pinned = dict(re.findall(r"^\s+(CDF_\w*PORT):\s*(\d+)\s*$", body, re.MULTILINE))

    for var in {v for _, v in COMPOSE_PORT_BINDINGS}:
        assert var in pinned, (
            f"CI job {job!r} does not pin {var}; it would inherit the Makefile "
            "default, so changing that default silently repoints the stacks "
            "without repointing the endpoint URLs below it"
        )


@pytest.mark.parametrize("job", ["live-local", "live-full"])
def test_ci_endpoint_urls_use_the_pinned_ports(job: str) -> None:
    """The port in each CI endpoint URL must equal that job's pin for it.

    This is the assertion that would have caught the live-local failure: the
    URLs said 8090 while `make up` published 18090.
    """
    body = _ci_jobs()[job]
    pinned = dict(re.findall(r"^\s+(CDF_\w*PORT):\s*(\d+)\s*$", body, re.MULTILINE))

    checked = 0
    for env_var, port_var in CI_ENDPOINT_PORT_VARS.items():
        for value in re.findall(rf"^\s+{re.escape(env_var)}:\s*(\S.*)$", body, re.MULTILINE):
            # Interpolated forms (`${{ env.CDF_ONTOP_PORT }}`) are correct by
            # construction; only hand-written literals can drift.
            if "${{" in value:
                assert port_var in value, (
                    f"{job}: {env_var} interpolates a port variable, but not {port_var}"
                )
                checked += 1
                continue
            literal = re.search(r"127\.0\.0\.1:(\d+)", value)
            assert literal is not None, f"{job}: cannot find a port in {env_var}={value!r}"
            assert literal.group(1) == pinned[port_var], (
                f"{job}: {env_var} points at port {literal.group(1)} but {port_var} "
                f"is pinned to {pinned[port_var]} — `make up` publishes the pinned "
                "port, so this endpoint would be unreachable and its leg would "
                "contribute zero citations"
            )
            checked += 1

    assert checked, f"CI job {job!r} sets none of {sorted(CI_ENDPOINT_PORT_VARS)}"


def test_demo_server_fallback_endpoints_match_makefile_default() -> None:
    """A bare `python deploy/demo/server.py` (no DEMO_ENV) falls back to the
    module's `setdefault` endpoints — the fourth copy of the constant. A stale
    port there renders a healthy-looking UI over a silently dead Postgres leg,
    the same disguise as the other three incidents."""
    ontop = _makefile_port_defaults()["CDF_ONTOP_PORT"]
    server = (REPO_ROOT / "deploy" / "demo" / "server.py").read_text(encoding="utf-8")
    ports = re.findall(r"http://localhost:(\d+)/(?:sparql|ontop/reformulate)", server)
    assert ports, "no Ontop fallback endpoints found in deploy/demo/server.py"
    assert set(ports) == {ontop}, (
        f"deploy/demo/server.py falls back to Ontop port(s) {sorted(set(ports))}; "
        f"the Makefile's CDF_ONTOP_PORT default is {ontop}"
    )
