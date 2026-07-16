-- Seed data for the M5 relational leg demo (matches ./input/mapping.ttl and
-- tests/test_ontop_live.py).
CREATE TABLE accounts (
    id   serial PRIMARY KEY,
    name text NOT NULL,
    arr  integer
);

INSERT INTO accounts (name, arr) VALUES
    ('Acme',   50000),
    ('Globex', 12000);
