-- One-time Snowflake setup for the CDF telemetry leg (Sprint 2 / PRD §7.7).
-- Run once in a Snowsight worksheet as ACCOUNTADMIN. Creates the warehouse, the
-- database/schema for the telemetry corpus, a cost guardrail (CC-11), and a
-- read-only role for the query path (CC-7). Names match the .env defaults.
--
-- Replace <YOUR_USER> with your trial login before running the last GRANT.

USE ROLE ACCOUNTADMIN;

-- XS warehouse, fast auto-suspend — the demo workload (46 rows) costs ~nothing.
CREATE WAREHOUSE IF NOT EXISTS CDF_WH
  WITH WAREHOUSE_SIZE = 'XSMALL'
       AUTO_SUSPEND = 60
       AUTO_RESUME = TRUE
       INITIALLY_SUSPENDED = TRUE;
-- Per-leg statement cap (CC-11): a runaway query can't hold the warehouse.
ALTER WAREHOUSE CDF_WH SET STATEMENT_TIMEOUT_IN_SECONDS = 60;

-- Database + schema for the usage-telemetry corpus.
CREATE DATABASE IF NOT EXISTS TELEMETRY;
CREATE SCHEMA IF NOT EXISTS TELEMETRY.PUBLIC;

-- Resource monitor: a hard credit ceiling so the trial cannot overspend (CC-11).
CREATE RESOURCE MONITOR IF NOT EXISTS CDF_MON
  WITH CREDIT_QUOTA = 20
       FREQUENCY = MONTHLY
       START_TIMESTAMP = IMMEDIATELY
       TRIGGERS ON 90 PERCENT DO NOTIFY
                ON 100 PERCENT DO SUSPEND;
ALTER WAREHOUSE CDF_WH SET RESOURCE_MONITOR = CDF_MON;

-- Read-only role for the QUERY path (least privilege, CC-7). The loader (S2)
-- runs once as your admin user; the engine only ever uses CDF_RO.
CREATE ROLE IF NOT EXISTS CDF_RO;
GRANT USAGE ON WAREHOUSE CDF_WH TO ROLE CDF_RO;
GRANT USAGE ON DATABASE TELEMETRY TO ROLE CDF_RO;
GRANT USAGE ON SCHEMA TELEMETRY.PUBLIC TO ROLE CDF_RO;
GRANT SELECT ON ALL TABLES IN SCHEMA TELEMETRY.PUBLIC TO ROLE CDF_RO;
GRANT SELECT ON FUTURE TABLES IN SCHEMA TELEMETRY.PUBLIC TO ROLE CDF_RO;

-- Let your login assume the read-only role (so the .env SNOWFLAKE_ROLE works).
GRANT ROLE CDF_RO TO USER <YOUR_USER>;

-- Sanity: the query-path role can reach the warehouse + schema.
-- USE ROLE CDF_RO; USE WAREHOUSE CDF_WH; SELECT 1;
