# BigQuery governance report: `concrete-list-461509-g8`

Collected 2026-10-02T04:29:05+00:00 · job window 30 days · location US · prices: $6.25/TiB on-demand (estimates from list price, not a billing export)

> **Warning:** 1 table(s) missing from TABLE_STORAGE (collection may be recent); filled from the Tables API without long-term split: gov_raw.users.

## Summary

| Metric | Value |
|---|---|
| Tables / columns | 9 / 66 |
| Column descriptions | 0.0% |
| Table descriptions | 33.3% |
| Storage | 0.048 GiB, $0.0010/month |
| Query cost in window | $0.0025 over 45 jobs (3 cache hits) |
| Data quality | 9 pass · 0 warn · 0 fail |

## Query cost by user

| User | Cost |
|---|---|
| user-0cc424 | $0.0025 |

## Query cost by pipeline label

| Pipeline | Cost |
|---|---|
| adhoc | $0.0015 |
| (no label) | $0.0006 |
| stg_order_items | $0.0002 |
| mart_daily_sales | $0.0001 |

## Most expensive query patterns

| Runs | Cost | Users | Query (literals normalized) |
|---|---|---|---|
| 3 | $0.0004 | 1 | `create or replace table gov_staging.stg_order_items as select oi.id as order_item_id, oi.order_id, oi.user_id, date(oi.c` |
| 4 | $0.0003 | 1 | `select * from gov_raw.order_items where created_at >= timestamp ? and status = ? limit ?` |
| 4 | $0.0002 | 1 | `select status, count(*) n, sum(sale_price) rev from gov_raw.order_items where date(created_at) = date ? group by status` |
| 4 | $0.0002 | 1 | `select user_id, sum(sale_price) from gov_raw.order_items where status = ? and created_at between timestamp ? and timesta` |
| 4 | $0.0002 | 1 | `select * from gov_raw.orders where status = ? and created_at >= timestamp ?` |
| 4 | $0.0002 | 1 | `select category, sum(revenue) from gov_mart.daily_sales where order_date >= date ? group by category` |
| 4 | $0.0002 | 1 | `select * from gov_mart.v_top_categories` |
| 3 | $0.0002 | 1 | `create or replace table gov_mart.daily_sales options (description = ?) as select order_date, category, count(distinct or` |
| 1 | $0.0001 | 1 | `create or replace table gov_mart.customer_ltv as select s.user_id, u.country, u.traffic_source, min(s.order_date) as fir` |
| 1 | < $0.0001 | 1 | `create or replace table gov_raw.order_items as select id, order_id, user_id, product_id, status, created_at, shipped_at,` |

## Recommendations

Addressable cost = query cost in the window that the change could reduce, or monthly storage for unused tables; actual savings depend on query filters.

| Type | Table | Evidence | Addressable |
|---|---|---|---|
| partition | `concrete-list-461509-g8.gov_raw.order_items` | `created_at` is filtered in 12 queries in 30 days, but the table is not partitioned, so each of them reads every row. | $0.0008 |
| cluster | `concrete-list-461509-g8.gov_raw.order_items` | Frequently filtered: `status` (8x). | $0.0009 |
| select_star | `concrete-list-461509-g8.gov_raw.order_items` | 4 queries use SELECT * on a 10-column table. | $0.0003 |
| unused | `concrete-list-461509-g8.gov_raw.legacy_product_snapshot` | No reads in the last 30 days. | < $0.0001 |

## Data quality

| Table | Check | Status | Value | Threshold |
|---|---|---|---|---|
| `concrete-list-461509-g8.gov_mart.daily_sales` | row_count | pass | 44312 | >= 100 |
| `concrete-list-461509-g8.gov_mart.daily_sales` | freshness_hours | pass | 0.24 | warn 26 / fail 50 |
| `concrete-list-461509-g8.gov_mart.daily_sales` | null_pct:order_date | pass | 0.0 | warn 0.1 / fail 1.0 |
| `concrete-list-461509-g8.gov_mart.daily_sales` | null_pct:revenue | pass | 0.0 | warn 0.1 / fail 1.0 |
| `concrete-list-461509-g8.gov_mart.daily_sales` | duplicate_keys | pass | 0 | = 0 |
| `concrete-list-461509-g8.gov_mart.customer_ltv` | row_count | pass | 71973 | >= 0 |
| `concrete-list-461509-g8.gov_mart.customer_ltv` | freshness_hours | pass | 13.5 | warn 26 / fail 50 |
| `concrete-list-461509-g8.gov_mart.customer_ltv` | null_pct:user_id | pass | 0.0 | warn 0.0001 / fail 1.0 |
| `concrete-list-461509-g8.gov_mart.customer_ltv` | duplicate_keys | pass | 0 | = 0 |

## Lineage: widest downstream impact

| Table | Downstream tables |
|---|---|
| `bigquery-public-data.thelook_ecommerce.products` | 6 |
| `bigquery-public-data.thelook_ecommerce.order_items` | 5 |
| `concrete-list-461509-g8.gov_raw.products` | 5 |
| `concrete-list-461509-g8.gov_raw.order_items` | 4 |
| `concrete-list-461509-g8.gov_staging.stg_order_items` | 3 |
| `bigquery-public-data.thelook_ecommerce.users` | 2 |
| `bigquery-public-data.thelook_ecommerce.orders` | 1 |
| `concrete-list-461509-g8.gov_mart.daily_sales` | 1 |
| `concrete-list-461509-g8.gov_raw.users` | 1 |
| `concrete-list-461509-g8.gov_mart.customer_ltv` | 0 |
