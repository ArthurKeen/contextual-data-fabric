-- Demo analytics table for the M5 ClickHouse leg. account_id is the cross-source
-- business key (matches the corpus accounts in the Postgres/Arango legs), so a
-- federated query can join usage metrics here to account context elsewhere.
CREATE DATABASE IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.usage_metrics (
    id               UInt64,
    account_id       String,
    edition          String,
    query_volume_m   Float64,
    graphrag_enabled UInt8
) ENGINE = MergeTree ORDER BY id;

INSERT INTO analytics.usage_metrics VALUES
    (1, '001Qwvb5LAnzy3yVgi', 'Enterprise', 12.5, 1),
    (2, '001bbkuFW1b7KegAZT', 'Community',    3.0, 0),
    (3, '001LxbLlyzNOfmaOHp', 'Enterprise',   8.2, 1);

-- High-volume per-account query telemetry — the analytics workload ClickHouse is
-- built for, and the demo's dedicated ClickHouse federation leg (QueryEvent).
-- account_id matches the corpus accounts in the Postgres/Arango legs.
CREATE TABLE IF NOT EXISTS analytics.query_events (
    event_id      UInt64,
    account_id    String,
    event_date    Date,
    feature       String,
    query_count   UInt64,
    avg_latency_ms Float64
) ENGINE = MergeTree ORDER BY event_id;

INSERT INTO analytics.query_events VALUES
    (1, '001Qwvb5LAnzy3yVgi', '2026-07-01', 'graph_traversal', 48210, 12.4),
    (2, '001Qwvb5LAnzy3yVgi', '2026-07-01', 'vector_search',   19044,  8.1),
    (3, '001bbkuFW1b7KegAZT', '2026-07-01', 'graph_traversal',  3102, 22.7),
    (4, '001LxbLlyzNOfmaOHp', '2026-07-01', 'graph_traversal', 27650, 10.9),
    (5, '001LxbLlyzNOfmaOHp', '2026-07-01', 'graphrag',          812, 41.3);
