-- Seed data for the M5 relational leg demo (matches ./input/mapping.ttl and
-- tests/test_ontop_live.py). `account_id` is the cross-source business key the
-- federated query joins on (the same value appears on the Arango tickets).
CREATE TABLE accounts (
    id         serial PRIMARY KEY,
    account_id text NOT NULL,
    name       text NOT NULL,
    arr        integer
);

INSERT INTO accounts (account_id, name, arr) VALUES
    ('ACME',   'Acme',   50000),
    ('GLOBEX', 'Globex', 12000);
