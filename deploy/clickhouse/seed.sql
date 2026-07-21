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
