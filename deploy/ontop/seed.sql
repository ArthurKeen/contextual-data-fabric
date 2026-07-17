--
-- PostgreSQL database dump
--

\restrict uhaypabZmtrAh3OLyCeb6Dk9AyazDQn0e4m8P84X5BXctTZhEzDSA1dSLEmNcgU

-- Dumped from database version 16.14 (Debian 16.14-1.pgdg13+1)
-- Dumped by pg_dump version 16.14 (Debian 16.14-1.pgdg13+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounts (
    id bigint NOT NULL,
    account_id text,
    account_name text,
    account_trajectory text,
    csm_owner text,
    current_acv_usd bigint,
    current_product_tier text,
    deployment_date text,
    health_band text,
    health_score double precision,
    industry text,
    last_activity_date text,
    primary_champion text,
    products_contracted jsonb,
    region text,
    seats_sold bigint,
    segment text
);


--
-- Name: accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.accounts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: accounts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.accounts_id_seq OWNED BY public.accounts.id;


--
-- Name: contacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contacts (
    id bigint NOT NULL,
    account_id text,
    active_from text,
    active_to text,
    contact_id text,
    email text,
    engagement_status text,
    full_name text,
    influence text,
    is_primary boolean,
    role text,
    title text
);


--
-- Name: contacts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contacts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: contacts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contacts_id_seq OWNED BY public.contacts.id;


--
-- Name: contracts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contracts (
    id bigint NOT NULL,
    account_id text,
    auto_renew boolean,
    billing_frequency text,
    contract_id text,
    days_to_renewal bigint,
    end_date text,
    is_downgrade boolean,
    payment_terms text,
    product_scope text,
    renewal_date text,
    seat_count bigint,
    signed_date text,
    status text,
    term_months bigint,
    value_usd bigint
);


--
-- Name: contracts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contracts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: contracts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contracts_id_seq OWNED BY public.contracts.id;


--
-- Name: nps_surveys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.nps_surveys (
    id bigint NOT NULL,
    account_id text,
    nps_score bigint,
    score bigint,
    score_band text,
    sentiment_aligned boolean,
    survey_date text,
    survey_period text,
    survey_year bigint,
    verbatim_sentiment text
);


--
-- Name: nps_surveys_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.nps_surveys_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: nps_surveys_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.nps_surveys_id_seq OWNED BY public.nps_surveys.id;


--
-- Name: opportunities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.opportunities (
    id bigint NOT NULL,
    account_id text,
    amount_usd bigint,
    close_date text,
    contract_id text,
    forecast_category text,
    is_open boolean,
    is_won boolean,
    opportunity_type text,
    product_scope text,
    renewal_date text,
    stage text,
    stage_detail text
);


--
-- Name: opportunities_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.opportunities_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: opportunities_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.opportunities_id_seq OWNED BY public.opportunities.id;


--
-- Name: usage_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.usage_metrics (
    id bigint NOT NULL,
    account_id text,
    cluster_nodes bigint,
    contracted_seats_mirror bigint,
    edition text,
    graphrag_enabled boolean,
    is_peak_period boolean,
    period text,
    queries_per_node_m double precision,
    query_volume_growth_pct double precision,
    query_volume_m double precision,
    seats_active bigint,
    smartgraphs_enabled boolean,
    volume_trend text
);


--
-- Name: usage_metrics_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.usage_metrics_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: usage_metrics_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.usage_metrics_id_seq OWNED BY public.usage_metrics.id;


--
-- Name: accounts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts ALTER COLUMN id SET DEFAULT nextval('public.accounts_id_seq'::regclass);


--
-- Name: contacts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contacts ALTER COLUMN id SET DEFAULT nextval('public.contacts_id_seq'::regclass);


--
-- Name: contracts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts ALTER COLUMN id SET DEFAULT nextval('public.contracts_id_seq'::regclass);


--
-- Name: nps_surveys id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nps_surveys ALTER COLUMN id SET DEFAULT nextval('public.nps_surveys_id_seq'::regclass);


--
-- Name: opportunities id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.opportunities ALTER COLUMN id SET DEFAULT nextval('public.opportunities_id_seq'::regclass);


--
-- Name: usage_metrics id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_metrics ALTER COLUMN id SET DEFAULT nextval('public.usage_metrics_id_seq'::regclass);


--
-- Data for Name: accounts; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.accounts (id, account_id, account_name, account_trajectory, csm_owner, current_acv_usd, current_product_tier, deployment_date, health_band, health_score, industry, last_activity_date, primary_champion, products_contracted, region, seats_sold, segment) FROM stdin;
1	001Qwvb5LAnzy3yVgi	Helio Retail Corp.	contracting	Alex Rivera	110000	Enterprise	2022-01-15	watch	6.14	E-commerce & Retail Personalization	2024-05-15	\N	["Enterprise", "ArangoGraph"]	North America	\N	Enterprise
2	001bbkuFW1b7KegAZT	Meridian Logistics, LLC	expanding	Alex Rivera	190000	Enterprise	2021-01-15	watch	7.57	Logistics & Supply Chain	2025-01-25	\N	["Enterprise"]	EMEA	500	Enterprise
3	001LxbLlyzNOfmaOHp	Northwind Analytics, Inc.	expanding	Alex Rivera	220000	ArangoGraph	2020-03-01	healthy	8.64	Data Analytics & Business Intelligence	2025-06-01	Sarah Chen	["Community", "Enterprise", "ArangoGraph"]	North America	\N	Enterprise
\.


--
-- Data for Name: contacts; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.contacts (id, account_id, active_from, active_to, contact_id, email, engagement_status, full_name, influence, is_primary, role, title) FROM stdin;
1	001Qwvb5LAnzy3yVgi	2022-01-15	2023-11-30	he_contact_priya_nair	p.nair@helioretail.com	disengaged	Priya Nair	decision-maker	t	champion	Head of Personalization
2	001Qwvb5LAnzy3yVgi	2023-12-01	\N	he_contact_marcus_webb	m.webb@helioretail.com	active	Marcus Webb	user	f	user	VP Engineering
3	001Qwvb5LAnzy3yVgi	2022-01-15	\N	he_contact_diane_choi	d.choi@helioretail.com	active	Diane Choi	decision-maker	f	economic_buyer	CFO
4	001bbkuFW1b7KegAZT	2021-01-15	2024-09-01	me_contact_james_okafor	j.okafor@meridianlogistics.com	disengaged	James Okafor	decision-maker	t	champion	Director of Engineering
5	001bbkuFW1b7KegAZT	2024-07-01	\N	me_contact_taylor_brooks	t.brooks@meridianlogistics.com	active	Taylor Brooks	user	f	user	Engineering Manager
6	001bbkuFW1b7KegAZT	2021-01-15	\N	me_contact_patricia_vance	p.vance@meridianlogistics.com	active	Patricia Vance	decision-maker	f	economic_buyer	CFO
7	001LxbLlyzNOfmaOHp	2020-03-01	\N	nw_contact_sarah_chen	sarah.chen@northwindanalytics.com	active	Sarah Chen	decision-maker	t	champion	VP of Data Platform
8	001LxbLlyzNOfmaOHp	2020-03-01	\N	nw_contact_michael_torres	m.torres@northwindanalytics.com	active	Michael Torres	decision-maker	f	economic_buyer	CTO
\.


--
-- Data for Name: contracts; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.contracts (id, account_id, auto_renew, billing_frequency, contract_id, days_to_renewal, end_date, is_downgrade, payment_terms, product_scope, renewal_date, seat_count, signed_date, status, term_months, value_usd) FROM stdin;
1	001Qwvb5LAnzy3yVgi	t	annual	he_contract_enterprise_2022	-1270	2023-01-14	f	Net 60	Enterprise	2023-01-15	75	2022-01-15	expired	12	150000
2	001Qwvb5LAnzy3yVgi	t	annual	he_contract_arangograph_2023	-905	2024-01-14	f	Net 60	ArangoGraph	2024-01-15	96	2023-01-15	expired	12	240000
3	001Qwvb5LAnzy3yVgi	f	annual	he_contract_downgrade_2024	-494	2025-02-28	t	Net 30	Enterprise	2025-03-01	55	2024-03-01	expired	12	110000
4	001bbkuFW1b7KegAZT	t	annual	me_contract_enterprise_2021	-1635	2022-01-14	f	Net 60	Enterprise	2022-01-15	90	2021-01-15	expired	12	180000
5	001bbkuFW1b7KegAZT	t	annual	me_contract_enterprise_2022	-1270	2023-01-14	f	Net 60	Enterprise	2023-01-15	90	2022-01-15	expired	12	180000
6	001bbkuFW1b7KegAZT	t	annual	me_contract_enterprise_2023	-905	2024-01-14	f	Net 60	Enterprise	2024-01-15	92	2023-01-15	expired	12	185000
7	001bbkuFW1b7KegAZT	t	annual	me_contract_enterprise_2024	-539	2025-01-14	f	Net 60	Enterprise	2025-01-15	94	2024-01-15	expired	12	188000
8	001bbkuFW1b7KegAZT	t	annual	me_contract_enterprise_2025	-174	2026-01-14	f	Net 60	Enterprise	2026-01-15	95	2025-01-15	expired	12	190000
9	001LxbLlyzNOfmaOHp	f	none	nw_contract_community_2020	-1955	2021-02-28	f	N/A (free tier)	Community	2021-03-01	0	2020-03-01	expired	12	0
10	001LxbLlyzNOfmaOHp	t	annual	nw_contract_enterprise_2021	-1590	2022-02-28	f	Net 30	Enterprise	2022-03-01	60	2021-03-01	expired	12	120000
11	001LxbLlyzNOfmaOHp	t	annual	nw_contract_enterprise_2022	-1225	2023-02-28	f	Net 30	Enterprise	2023-03-01	72	2022-03-01	expired	12	145000
12	001LxbLlyzNOfmaOHp	f	annual	nw_contract_enterprise_2023	-1133	2023-05-31	f	Net 60	Enterprise	2023-06-01	80	2023-03-01	expired	3	160000
13	001LxbLlyzNOfmaOHp	t	annual	nw_contract_arangograph_2023	-767	2024-05-31	f	Net 60	ArangoGraph	2024-06-01	80	2023-06-01	expired	12	200000
14	001LxbLlyzNOfmaOHp	t	annual	nw_contract_arangograph_2024	-402	2025-05-31	f	Net 60	ArangoGraph	2025-06-01	88	2024-06-01	expired	12	220000
15	001LxbLlyzNOfmaOHp	t	annual	nw_contract_arangograph_2025	-37	2026-05-31	f	Net 60	ArangoGraph	2026-06-01	88	2025-06-01	expired	12	220000
\.


--
-- Data for Name: nps_surveys; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.nps_surveys (id, account_id, nps_score, score, score_band, sentiment_aligned, survey_date, survey_period, survey_year, verbatim_sentiment) FROM stdin;
1	001Qwvb5LAnzy3yVgi	8	8	passive	t	2022-03-31	2022-Q1	2022	positive
2	001Qwvb5LAnzy3yVgi	8	8	passive	t	2022-06-30	2022-Q2	2022	positive
3	001Qwvb5LAnzy3yVgi	7	7	passive	t	2022-12-31	2022-Q4	2022	positive
4	001Qwvb5LAnzy3yVgi	7	7	passive	t	2023-03-31	2023-Q1	2023	positive
5	001Qwvb5LAnzy3yVgi	6	6	detractor	t	2023-09-30	2023-Q3	2023	neutral
6	001Qwvb5LAnzy3yVgi	4	4	detractor	t	2024-03-31	2024-Q1	2024	negative
7	001Qwvb5LAnzy3yVgi	3	3	detractor	t	2024-06-30	2024-Q2	2024	negative
8	001bbkuFW1b7KegAZT	8	8	passive	t	2022-03-31	2022-Q1	2022	positive
9	001bbkuFW1b7KegAZT	7	7	passive	t	2022-06-30	2022-Q2	2022	positive
10	001bbkuFW1b7KegAZT	8	8	passive	t	2022-09-30	2022-Q3	2022	positive
11	001bbkuFW1b7KegAZT	8	8	passive	t	2022-12-31	2022-Q4	2022	positive
12	001bbkuFW1b7KegAZT	8	8	passive	t	2023-03-31	2023-Q1	2023	positive
13	001bbkuFW1b7KegAZT	7	7	passive	t	2023-06-30	2023-Q2	2023	positive
14	001bbkuFW1b7KegAZT	8	8	passive	t	2023-09-30	2023-Q3	2023	positive
15	001bbkuFW1b7KegAZT	7	7	passive	t	2023-12-31	2023-Q4	2023	positive
16	001bbkuFW1b7KegAZT	7	7	passive	t	2024-03-31	2024-Q1	2024	neutral
17	001bbkuFW1b7KegAZT	8	8	passive	t	2024-06-30	2024-Q2	2024	neutral
18	001bbkuFW1b7KegAZT	7	7	passive	f	2024-09-30	2024-Q3	2024	negative
19	001bbkuFW1b7KegAZT	8	8	passive	f	2024-12-31	2024-Q4	2024	negative
20	001bbkuFW1b7KegAZT	7	7	passive	f	2025-03-31	2025-Q1	2025	negative
21	001bbkuFW1b7KegAZT	8	8	passive	t	2025-06-30	2025-Q2	2025	neutral
22	001LxbLlyzNOfmaOHp	8	8	passive	t	2022-03-31	2022-Q1	2022	positive
23	001LxbLlyzNOfmaOHp	9	9	promoter	t	2022-06-30	2022-Q2	2022	positive
24	001LxbLlyzNOfmaOHp	8	8	passive	t	2022-09-30	2022-Q3	2022	positive
25	001LxbLlyzNOfmaOHp	9	9	promoter	t	2022-12-31	2022-Q4	2022	positive
26	001LxbLlyzNOfmaOHp	9	9	promoter	t	2023-03-31	2023-Q1	2023	positive
27	001LxbLlyzNOfmaOHp	8	8	passive	t	2023-06-30	2023-Q2	2023	positive
28	001LxbLlyzNOfmaOHp	9	9	promoter	t	2023-09-30	2023-Q3	2023	positive
29	001LxbLlyzNOfmaOHp	9	9	promoter	t	2023-12-31	2023-Q4	2023	positive
30	001LxbLlyzNOfmaOHp	9	9	promoter	t	2024-03-31	2024-Q1	2024	positive
31	001LxbLlyzNOfmaOHp	8	8	passive	t	2024-06-30	2024-Q2	2024	positive
32	001LxbLlyzNOfmaOHp	9	9	promoter	t	2024-09-30	2024-Q3	2024	positive
33	001LxbLlyzNOfmaOHp	9	9	promoter	t	2024-12-31	2024-Q4	2024	positive
34	001LxbLlyzNOfmaOHp	9	9	promoter	t	2025-03-31	2025-Q1	2025	positive
35	001LxbLlyzNOfmaOHp	8	8	passive	t	2025-06-30	2025-Q2	2025	positive
\.


--
-- Data for Name: opportunities; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.opportunities (id, account_id, amount_usd, close_date, contract_id, forecast_category, is_open, is_won, opportunity_type, product_scope, renewal_date, stage, stage_detail) FROM stdin;
1	001Qwvb5LAnzy3yVgi	150000	2022-01-15	he_contract_enterprise_2022	Closed	f	t	new	Enterprise	2023-01-14	closed-won	New closed-won for Enterprise
2	001Qwvb5LAnzy3yVgi	240000	2023-01-15	he_contract_arangograph_2023	Closed	f	t	expansion	ArangoGraph	2024-01-14	closed-won	Expansion closed-won for ArangoGraph
3	001Qwvb5LAnzy3yVgi	90000	2023-10-15	\N	Omitted	f	f	expansion	GenAI	\N	closed-lost	Expansion did not close — GenAI scope was declined
4	001Qwvb5LAnzy3yVgi	110000	2024-03-01	he_contract_downgrade_2024	Closed	f	t	renewal	Enterprise	2025-02-28	closed-won	Renewal closed-won for Enterprise
5	001Qwvb5LAnzy3yVgi	80000	2025-01-15	\N	Commit	t	f	renewal	Enterprise	\N	negotiation	Renewal for Enterprise in active negotiation; outcome pending
6	001bbkuFW1b7KegAZT	180000	2021-01-15	me_contract_enterprise_2021	Closed	f	t	new	Enterprise	2022-01-14	closed-won	New closed-won for Enterprise
7	001bbkuFW1b7KegAZT	180000	2022-01-15	me_contract_enterprise_2022	Closed	f	t	renewal	Enterprise	2023-01-14	closed-won	Renewal closed-won for Enterprise
8	001bbkuFW1b7KegAZT	185000	2023-01-15	me_contract_enterprise_2023	Closed	f	t	renewal	Enterprise	2024-01-14	closed-won	Renewal closed-won for Enterprise
9	001bbkuFW1b7KegAZT	188000	2024-01-15	me_contract_enterprise_2024	Closed	f	t	renewal	Enterprise	2025-01-14	closed-won	Renewal closed-won for Enterprise
10	001bbkuFW1b7KegAZT	60000	2024-06-30	\N	Omitted	f	f	expansion	ArangoGraph	\N	closed-lost	Expansion did not close — ArangoGraph scope was declined
11	001bbkuFW1b7KegAZT	190000	2025-01-15	me_contract_enterprise_2025	Commit	t	f	renewal	Enterprise	2026-01-14	negotiation	Renewal for Enterprise in active negotiation; outcome pending
12	001LxbLlyzNOfmaOHp	0	2020-03-01	nw_contract_community_2020	Closed	f	t	new	Community	2021-02-28	closed-won	New closed-won for Community
13	001LxbLlyzNOfmaOHp	120000	2021-03-01	nw_contract_enterprise_2021	Closed	f	t	expansion	Enterprise	2022-02-28	closed-won	Expansion closed-won for Enterprise
14	001LxbLlyzNOfmaOHp	145000	2022-03-01	nw_contract_enterprise_2022	Closed	f	t	expansion	Enterprise	2023-02-28	closed-won	Expansion closed-won for Enterprise
15	001LxbLlyzNOfmaOHp	200000	2023-06-01	nw_contract_arangograph_2023	Closed	f	t	expansion	ArangoGraph	2024-05-31	closed-won	Expansion closed-won for ArangoGraph
16	001LxbLlyzNOfmaOHp	240000	2025-06-01	\N	Best Case	t	f	renewal	ArangoGraph	\N	proposal	Renewal for ArangoGraph in active proposal; outcome pending
\.


--
-- Data for Name: usage_metrics; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.usage_metrics (id, account_id, cluster_nodes, contracted_seats_mirror, edition, graphrag_enabled, is_peak_period, period, queries_per_node_m, query_volume_growth_pct, query_volume_m, seats_active, smartgraphs_enabled, volume_trend) FROM stdin;
1	001Qwvb5LAnzy3yVgi	6	\N	Enterprise	f	f	2022-Q1	1	\N	6	\N	t	baseline
2	001Qwvb5LAnzy3yVgi	6	\N	Enterprise	f	f	2022-Q2	1.2	20	7.2	\N	t	rising
3	001Qwvb5LAnzy3yVgi	8	\N	Enterprise	f	f	2022-Q3	1.12	25	9	\N	t	rising
4	001Qwvb5LAnzy3yVgi	8	\N	Enterprise	f	f	2022-Q4	1.38	22.2	11	\N	t	rising
5	001Qwvb5LAnzy3yVgi	10	\N	ArangoGraph	t	t	2023-Q1	1.2	9.1	12	\N	t	rising
6	001Qwvb5LAnzy3yVgi	10	\N	ArangoGraph	t	f	2023-Q2	1.15	-4.2	11.5	\N	t	falling
7	001Qwvb5LAnzy3yVgi	8	\N	ArangoGraph	t	f	2023-Q3	1.19	-17.4	9.5	\N	t	falling
8	001Qwvb5LAnzy3yVgi	8	\N	ArangoGraph	f	f	2023-Q4	1	-15.8	8	\N	t	falling
9	001Qwvb5LAnzy3yVgi	6	\N	Enterprise	f	f	2024-Q1	1	-25	6	\N	t	falling
10	001Qwvb5LAnzy3yVgi	4	\N	Enterprise	f	f	2024-Q2	1.12	-25	4.5	\N	t	falling
11	001bbkuFW1b7KegAZT	4	\N	Enterprise	f	f	2021-Q1	1.25	\N	5	\N	t	baseline
12	001bbkuFW1b7KegAZT	4	\N	Enterprise	f	f	2021-Q2	1.39	11.2	5.56	\N	t	rising
13	001bbkuFW1b7KegAZT	4	\N	Enterprise	f	f	2021-Q3	1.54	11	6.17	\N	t	rising
14	001bbkuFW1b7KegAZT	6	\N	Enterprise	f	f	2021-Q4	1.14	11	6.85	\N	t	rising
15	001bbkuFW1b7KegAZT	6	\N	Enterprise	f	f	2022-Q1	1.27	11.1	7.61	\N	t	rising
16	001bbkuFW1b7KegAZT	6	\N	Enterprise	f	f	2022-Q2	1.41	11	8.45	\N	t	rising
17	001bbkuFW1b7KegAZT	8	\N	Enterprise	f	f	2022-Q3	1.17	11	9.38	\N	t	rising
18	001bbkuFW1b7KegAZT	8	\N	Enterprise	f	f	2022-Q4	1.3	11	10.41	\N	t	rising
19	001bbkuFW1b7KegAZT	8	\N	Enterprise	f	f	2023-Q1	1.38	6.2	11.06	\N	t	rising
20	001bbkuFW1b7KegAZT	8	\N	Enterprise	f	f	2023-Q2	1.47	6.1	11.74	\N	t	rising
21	001bbkuFW1b7KegAZT	10	\N	Enterprise	f	f	2023-Q3	1.24	6	12.45	\N	t	rising
22	001bbkuFW1b7KegAZT	10	\N	Enterprise	f	f	2023-Q4	1.28	2.6	12.77	\N	t	rising
23	001bbkuFW1b7KegAZT	10	\N	Enterprise	f	f	2024-Q1	1.31	2.7	13.12	\N	t	rising
24	001bbkuFW1b7KegAZT	10	\N	Enterprise	f	f	2024-Q2	1.35	2.7	13.47	\N	t	rising
25	001bbkuFW1b7KegAZT	12	\N	Enterprise	f	f	2024-Q3	1.15	2.7	13.83	\N	t	rising
26	001bbkuFW1b7KegAZT	12	\N	Enterprise	f	f	2024-Q4	1.18	2.7	14.2	\N	t	rising
27	001bbkuFW1b7KegAZT	12	\N	Enterprise	f	f	2025-Q1	1.22	2.7	14.59	\N	t	rising
28	001bbkuFW1b7KegAZT	12	500	Enterprise	f	t	2025-Q2	1.25	2.8	15	430	t	rising
29	001LxbLlyzNOfmaOHp	3	\N	Enterprise	f	f	2021-Q1	0.83	\N	2.5	\N	t	baseline
30	001LxbLlyzNOfmaOHp	3	\N	Enterprise	f	f	2021-Q2	0.96	15.2	2.88	\N	t	rising
31	001LxbLlyzNOfmaOHp	3	\N	Enterprise	f	f	2021-Q3	1.1	14.9	3.31	\N	t	rising
32	001LxbLlyzNOfmaOHp	4	\N	Enterprise	f	f	2021-Q4	0.95	15.1	3.81	\N	t	rising
33	001LxbLlyzNOfmaOHp	4	\N	Enterprise	f	f	2022-Q1	1.09	15	4.38	\N	t	rising
34	001LxbLlyzNOfmaOHp	4	\N	Enterprise	f	f	2022-Q2	1.26	15.1	5.04	\N	t	rising
35	001LxbLlyzNOfmaOHp	6	\N	Enterprise	f	f	2022-Q3	0.96	14.9	5.79	\N	t	rising
36	001LxbLlyzNOfmaOHp	6	\N	Enterprise	f	f	2022-Q4	1.11	15	6.66	\N	t	rising
37	001LxbLlyzNOfmaOHp	6	\N	Enterprise	f	f	2023-Q1	1.28	15	7.66	\N	t	rising
38	001LxbLlyzNOfmaOHp	8	\N	ArangoGraph	f	f	2023-Q2	1.1	15	8.81	\N	t	rising
39	001LxbLlyzNOfmaOHp	8	\N	ArangoGraph	f	f	2023-Q3	1.14	3.6	9.13	\N	t	rising
40	001LxbLlyzNOfmaOHp	8	\N	ArangoGraph	f	f	2023-Q4	1.19	3.8	9.48	\N	t	rising
41	001LxbLlyzNOfmaOHp	8	\N	ArangoGraph	f	f	2024-Q1	1.23	3.8	9.84	\N	t	rising
42	001LxbLlyzNOfmaOHp	10	\N	ArangoGraph	f	f	2024-Q2	1.02	3.9	10.22	\N	t	rising
43	001LxbLlyzNOfmaOHp	10	\N	ArangoGraph	f	f	2024-Q3	1.06	3.8	10.61	\N	t	rising
44	001LxbLlyzNOfmaOHp	10	\N	ArangoGraph	f	f	2024-Q4	1.1	3.8	11.01	\N	t	rising
45	001LxbLlyzNOfmaOHp	10	\N	ArangoGraph	f	f	2025-Q1	1.14	3.8	11.43	\N	t	rising
46	001LxbLlyzNOfmaOHp	10	\N	ArangoGraph	f	t	2025-Q2	1.19	3.8	11.87	\N	t	rising
\.


--
-- Name: accounts_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.accounts_id_seq', 3, true);


--
-- Name: contacts_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.contacts_id_seq', 8, true);


--
-- Name: contracts_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.contracts_id_seq', 15, true);


--
-- Name: nps_surveys_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.nps_surveys_id_seq', 35, true);


--
-- Name: opportunities_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.opportunities_id_seq', 16, true);


--
-- Name: usage_metrics_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.usage_metrics_id_seq', 46, true);


--
-- Name: accounts accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_pkey PRIMARY KEY (id);


--
-- Name: contacts contacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contacts
    ADD CONSTRAINT contacts_pkey PRIMARY KEY (id);


--
-- Name: contracts contracts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_pkey PRIMARY KEY (id);


--
-- Name: nps_surveys nps_surveys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nps_surveys
    ADD CONSTRAINT nps_surveys_pkey PRIMARY KEY (id);


--
-- Name: opportunities opportunities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.opportunities
    ADD CONSTRAINT opportunities_pkey PRIMARY KEY (id);


--
-- Name: usage_metrics usage_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_metrics
    ADD CONSTRAINT usage_metrics_pkey PRIMARY KEY (id);


--
-- PostgreSQL database dump complete
--

\unrestrict uhaypabZmtrAh3OLyCeb6Dk9AyazDQn0e4m8P84X5BXctTZhEzDSA1dSLEmNcgU


-- CC-7 / CC-11 floor (mirrors load_corpus.py): read-only demo role with a
-- statement timeout for the query path.
DO $$ BEGIN CREATE ROLE cdf_demo LOGIN PASSWORD 'cdf_demo'; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
GRANT CONNECT ON DATABASE crm TO cdf_demo;
GRANT USAGE ON SCHEMA public TO cdf_demo;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO cdf_demo;
ALTER ROLE cdf_demo SET statement_timeout = '15000ms';
