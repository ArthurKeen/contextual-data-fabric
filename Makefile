# Contextual Data Fabric — one-command demo + gates (WP-P1.7 / CC-8).
#
#   make install  create .venv and install the engine + live-leg libraries
#   make up       bring up both stacks (ArangoDB; Postgres + Ontop)
#   make seed     load the corpus (structured -> Postgres, unstructured ->
#                 ArangoDB) + emit the mappings
#   make gate     the pre-demo golden gate — run before ANY demo (PJ's rule)
#   make demo     up + seed + gate, then serve the browser demo at :8099
#   make test     lint + types + unit suite (what CI runs)
#   make down     stop the stacks (volumes preserved)
#
# From a fresh clone (Docker running):  make install && make demo

# Host ports — overridable; defaults match this machine's running stacks.
export CDF_ARANGO_PORT   ?= 8530
export CDF_POSTGRES_PORT ?= 5433
export CDF_ONTOP_PORT    ?= 8090
export CDF_UI_PORT       ?= 8099
export CDF_CLICKHOUSE_HTTP_PORT ?= 8123

# The two owned sibling libraries (SPARQL->AQL transpiler, ArangoDB analyzer)
# are installed from local checkouts by default — override if they live
# elsewhere. arango-sparql-py is also public on GitHub (see `make install`).
CDF_SIBLINGS ?= $(HOME)/code

# Engine environment (CC-7: credentials stay here, in the engine's env).
DEMO_ENV = ARANGO_URL=http://127.0.0.1:$(CDF_ARANGO_PORT) ARANGO_DB=cmf \
           ARANGO_USER=root ARANGO_PASSWORD=cdf \
           ONTOP_SPARQL_ENDPOINT=http://127.0.0.1:$(CDF_ONTOP_PORT)/sparql \
           CLICKHOUSE_DSN=clickhouse://cdf:cdf@127.0.0.1:$(CDF_CLICKHOUSE_HTTP_PORT)/analytics \
           CDF_CSI_DIR=deploy/csi CDF_R2RML_DIR=deploy/r2rml \
           CDF_PREPARED_QUESTIONS=deploy/questions.json

# Snowflake creds live in the gitignored .env (CC-7). Recipes that touch the
# Snowflake leg source it so SNOWFLAKE_* reach gate.py / load_corpus.py, which
# read them from the environment (server.py loads .env itself).
LOAD_ENV = set -a; . ./.env 2>/dev/null || true; set +a;

PY = .venv/bin/python

.PHONY: install up seed gate demo test down jdbc free-ui

install:
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -e ".[test,service,dev]" "psycopg[binary]" python-arango snowflake-connector-python
	# Owned sibling libraries: local checkout if present, else public GitHub.
	# The [nl] extra pulls the NL engine (arango-query-core + openai/anthropic) so
	# the free-form NL front-end is actually wired — without it default_client()
	# degrades to prepared-questions-only.
	$(PY) -m pip install -e "$(CDF_SIBLINGS)/arango-sparql-py[nl]" 2>/dev/null \
	  || $(PY) -m pip install "arango-sparql-py[nl] @ git+https://github.com/ArthurKeen/arango-sparql-py"
	# query-core pin is checkout-dependent, so key the override off the local
	# sibling's presence (do NOT force a version on the git-main fallback):
	#  - Local siblings present (dev): the local arango-sparql-py checkout is the
	#    grounding-seam branch (fix/unroutable-subject-error) whose nl2sparql
	#    adapter imports LabelIndex/grounding_index and REQUIRES query-core
	#    ccfe56c, so install the local arango-query-core AFTER [nl] to override
	#    the acb60ae the extra pulled.
	#  - Fresh clone (arango-sparql-py from git main above): main correctly pins
	#    acb60ae — query-core main (ccfe56c) adds a *required* grounding_index
	#    seam main doesn't implement, so overriding would BREAK [nl]. Skip it.
	# Retire this line once the grounding branch merges to main (it carries the
	# acb60ae -> ccfe56c bump and the seam impl together).
	$(PY) -m pip install -e "$(CDF_SIBLINGS)/arango-query-core" 2>/dev/null || true
	$(PY) -m pip install -e "$(CDF_SIBLINGS)/arango-schema-analyzer"
	@echo "OK — now: make demo   (Docker must be running)"

jdbc: deploy/ontop/jdbc/postgresql.jar
deploy/ontop/jdbc/postgresql.jar:
	curl -sL -o $@ https://jdbc.postgresql.org/download/postgresql-42.7.4.jar

up: jdbc
	docker compose -p cdf-arango -f deploy/arango/docker-compose.yml up -d --wait
	docker compose -p cdf-ontop  -f deploy/ontop/docker-compose.yml  up -d --wait
	# ClickHouse self-seeds via docker-entrypoint-initdb.d/seed.sql on first
	# create (fresh container each up-after-down), so no `seed` step is needed.
	docker compose -p cdf-clickhouse -f deploy/clickhouse/docker-compose.yml up -d --wait

seed:
	PG_DSN=postgresql://cdf:cdf@127.0.0.1:$(CDF_POSTGRES_PORT)/crm $(PY) deploy/ontop/load_corpus.py
	$(LOAD_ENV) $(PY) deploy/snowflake/load_corpus.py   # telemetry -> Snowflake USAGE_METRICS (46 rows)
	docker compose -p cdf-ontop -f deploy/ontop/docker-compose.yml restart ontop
	$(DEMO_ENV) $(PY) deploy/arango/seed.py           # tickets (kept as a small typed collection)
	$(DEMO_ENV) $(PY) deploy/arango/load_corpus.py    # documents + chunks (account_id stamp)
	$(DEMO_ENV) $(PY) deploy/arango/export_csi.py     # reverse CSI over the live graph
	@sleep 12  # Ontop reloads the R2RML mapping on start

gate:
	$(LOAD_ENV) $(DEMO_ENV) $(PY) deploy/demo/gate.py

free-ui:
	@pids=$$(lsof -ti tcp:$(CDF_UI_PORT) 2>/dev/null); \
	if [ -n "$$pids" ]; then echo "freeing port $(CDF_UI_PORT) (was: $$pids)"; kill $$pids 2>/dev/null; sleep 1; fi

demo: up seed gate free-ui
	$(DEMO_ENV) $(PY) deploy/demo/server.py

test:
	.venv/bin/ruff check src tests deploy
	.venv/bin/mypy src
	$(PY) -m pytest tests -q

down:
	docker compose -p cdf-arango -f deploy/arango/docker-compose.yml down
	docker compose -p cdf-ontop  -f deploy/ontop/docker-compose.yml  down
	docker compose -p cdf-clickhouse -f deploy/clickhouse/docker-compose.yml down
