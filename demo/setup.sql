-- Dataset demo kecil (< 100 MB) dari bigquery-public-data.thelook_ecommerce, lokasi US.
-- Aman untuk BigQuery sandbox: hanya DDL (CREATE ... AS SELECT), tanpa DML. Jalankan sekali, tabel sandbox kedaluwarsa setelah 60 hari.
-- Kolom PII (nama, email, alamat) sengaja TIDAK disalin.

CREATE SCHEMA IF NOT EXISTS gov_raw OPTIONS (location = 'US', description = 'Raw copies of thelook_ecommerce tables');
CREATE SCHEMA IF NOT EXISTS gov_staging OPTIONS (location = 'US');
CREATE SCHEMA IF NOT EXISTS gov_mart OPTIONS (location = 'US', description = 'Reporting marts');

-- raw (tanpa partisi, sengaja: menjadi bahan rekomendasi partitioning)
CREATE OR REPLACE TABLE gov_raw.orders OPTIONS (description = 'One row per order') AS
SELECT order_id, user_id, status, created_at, shipped_at, delivered_at, returned_at, num_of_item
FROM `bigquery-public-data.thelook_ecommerce.orders`;

CREATE OR REPLACE TABLE gov_raw.order_items AS
SELECT id, order_id, user_id, product_id, status, created_at, shipped_at, delivered_at, returned_at, sale_price
FROM `bigquery-public-data.thelook_ecommerce.order_items`;

CREATE OR REPLACE TABLE gov_raw.products AS
SELECT id, cost, category, name, brand, retail_price, department, distribution_center_id
FROM `bigquery-public-data.thelook_ecommerce.products`;

CREATE OR REPLACE TABLE gov_raw.users OPTIONS (description = 'Customers without direct identifiers') AS
SELECT id, age, gender, state, city, country, traffic_source, created_at
FROM `bigquery-public-data.thelook_ecommerce.users`;

-- tabel lama yang tidak dibaca siapa pun: bahan rekomendasi "unused"
CREATE OR REPLACE TABLE gov_raw.legacy_product_snapshot AS
SELECT * FROM gov_raw.products;

-- staging
CREATE OR REPLACE TABLE gov_staging.stg_order_items AS
SELECT oi.id AS order_item_id, oi.order_id, oi.user_id, DATE(oi.created_at) AS order_date, oi.status,
       p.category, p.department, p.brand, oi.sale_price, p.cost
FROM gov_raw.order_items oi JOIN gov_raw.products p ON p.id = oi.product_id
WHERE oi.status NOT IN ('Cancelled');

-- marts
CREATE OR REPLACE TABLE gov_mart.daily_sales OPTIONS (description = 'Daily revenue and margin by product category') AS
SELECT order_date, category, COUNT(DISTINCT order_id) AS orders, COUNT(*) AS items,
       ROUND(SUM(sale_price), 2) AS revenue, ROUND(SUM(sale_price - cost), 2) AS margin
FROM gov_staging.stg_order_items GROUP BY order_date, category;

CREATE OR REPLACE TABLE gov_mart.customer_ltv AS
SELECT s.user_id, u.country, u.traffic_source, MIN(s.order_date) AS first_order, COUNT(DISTINCT s.order_id) AS orders,
       ROUND(SUM(s.sale_price), 2) AS revenue
FROM gov_staging.stg_order_items s JOIN gov_raw.users u ON u.id = s.user_id
GROUP BY s.user_id, u.country, u.traffic_source;

CREATE OR REPLACE VIEW gov_mart.v_top_categories AS
SELECT category, SUM(revenue) AS revenue FROM gov_mart.daily_sales
WHERE order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY) GROUP BY category;
