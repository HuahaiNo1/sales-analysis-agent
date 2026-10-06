CREATE TABLE IF NOT EXISTS dim_product (
 product_key integer PRIMARY KEY, product_name text NOT NULL, category_key integer NOT NULL,
 category_name text NOT NULL, subcategory_key integer, subcategory_name text, brand text
);
CREATE TABLE IF NOT EXISTS dim_store (
 store_key integer PRIMARY KEY, store_name text NOT NULL, store_country text NOT NULL,
 store_country_code text, state text
);
CREATE TABLE IF NOT EXISTS dim_customer_geo (
 customer_key integer PRIMARY KEY, customer_country text NOT NULL,
 customer_country_code text, customer_state text
);
CREATE TABLE IF NOT EXISTS dim_date (
 date date PRIMARY KEY, year integer, quarter integer, month integer, year_month text, week_start date
);
CREATE TABLE IF NOT EXISTS fact_sales (
 order_key bigint NOT NULL, line_number integer NOT NULL,
 order_date date NOT NULL REFERENCES dim_date(date), delivery_date date,
 customer_key integer NOT NULL REFERENCES dim_customer_geo(customer_key),
 store_key integer NOT NULL REFERENCES dim_store(store_key),
 product_key integer NOT NULL REFERENCES dim_product(product_key),
 quantity integer NOT NULL CHECK (quantity > 0),
 unit_price numeric(18,6) NOT NULL CHECK (unit_price > 0),
 net_price numeric(18,6) NOT NULL CHECK (net_price > 0),
 unit_cost numeric(18,6) NOT NULL CHECK (unit_cost >= 0),
 currency_code text NOT NULL CHECK (currency_code = 'USD'),
 exchange_rate numeric(18,6) NOT NULL CHECK (exchange_rate = 1),
 dataset_version text NOT NULL DEFAULT 'contoso-v2-2023-2025-692e0347d360',
 PRIMARY KEY (order_key,line_number)
);
ALTER TABLE fact_sales ALTER COLUMN dataset_version SET DEFAULT 'contoso-v2-2023-2025-692e0347d360';
CREATE INDEX IF NOT EXISTS fact_sales_date ON fact_sales(order_date);
CREATE INDEX IF NOT EXISTS fact_sales_store_date ON fact_sales(store_key, order_date);
CREATE TABLE IF NOT EXISTS dataset_versions (
 id text PRIMARY KEY, metadata jsonb NOT NULL, published_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash text PRIMARY KEY, subject text NOT NULL, expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
 id uuid PRIMARY KEY, subject text NOT NULL, state_version integer NOT NULL DEFAULT 0,
 current_query jsonb, latest_run_id uuid, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS runs (
 id uuid PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES conversations(id), subject text NOT NULL,
 client_request_id text NOT NULL, status text NOT NULL, progress_stage text NOT NULL,
 message text NOT NULL, answer text, error_code text, result_id uuid, agent_runtime jsonb,
 cancel_requested boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(conversation_id,client_request_id)
);
CREATE TABLE IF NOT EXISTS results (
 id uuid PRIMARY KEY, run_id uuid, subject text NOT NULL, scope_hash text NOT NULL,
 payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL
);
ALTER TABLE fact_sales ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scoped_sales ON fact_sales;
CREATE POLICY scoped_sales ON fact_sales TO sales_reader USING (
 store_key = ANY(string_to_array(current_setting('app.allowed_stores', true), ',')::integer[])
);
GRANT SELECT ON fact_sales,dim_product,dim_store,dim_customer_geo,dim_date,dataset_versions TO sales_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM sales_app;
GRANT SELECT ON fact_sales,dim_product,dim_store,dim_customer_geo,dim_date,dataset_versions TO sales_app;
GRANT SELECT,INSERT,UPDATE,DELETE ON sessions,conversations,runs,results TO sales_app;
ALTER ROLE sales_reader SET default_transaction_read_only=on;
ALTER ROLE sales_reader SET statement_timeout='10s';
