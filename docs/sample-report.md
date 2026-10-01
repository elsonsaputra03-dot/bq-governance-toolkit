> **Sample output generated from the test fixtures** (`tests/fixtures`, synthetic). It shows the report format; it is not a real project.

# BigQuery governance report: `demo-project`

Collected 2026-10-01T14:06:26+00:00 · job window 30 days · location US · prices: $6.25/TiB on-demand (estimates from list price, not a billing export)

## Summary

| Metric | Value |
|---|---|
| Tables / columns | 9 / 59 |
| Column descriptions | 27.1% |
| Table descriptions | 33.3% |
| Storage | 0.073 GiB, $0.0014/month |
| Query cost in window | $0.0092 over 78 jobs (3 cache hits) |
| Data quality | 5 pass · 2 warn · 2 fail |

## Query cost by user

| User | Cost |
|---|---|
| user-506729 | $0.0032 |
| user-5d36b5 | $0.0030 |
| user-50972b | $0.0030 |

## Query cost by pipeline label

| Pipeline | Cost |
|---|---|
| adhoc | $0.0062 |
| stg_order_items | $0.0011 |
| mart_customer_ltv | $0.0011 |
| mart_daily_sales | $0.0008 |

## Most expensive query patterns

| Runs | Cost | Users | Query (literals normalized) |
|---|---|---|---|
| 21 | $0.0030 | 1 | `select status, count(*) n from gov_raw.order_items where created_at >= timestamp ? group by status` |
| 21 | $0.0030 | 1 | `select * from gov_raw.order_items where created_at >= timestamp ? and status = ? limit ?` |
| 7 | $0.0011 | 1 | `create or replace table gov_staging.stg_order_items as select oi.id, p.category from gov_raw.order_items oi join gov_raw` |
| 7 | $0.0011 | 1 | `create or replace table gov_mart.customer_ltv as select s.user_id from gov_staging.stg_order_items s join gov_raw.users ` |
| 7 | $0.0008 | 1 | `create or replace table gov_mart.daily_sales as select order_date, category from gov_staging.stg_order_items group by or` |
| 7 | < $0.0001 | 1 | `select country, avg(revenue) from gov_mart.customer_ltv group by country` |
| 1 | < $0.0001 | 1 | `select ? from gov_raw.orders` |
| 7 | < $0.0001 | 1 | `select * from gov_mart.v_top_categories` |

## Recommendations

Addressable cost = query cost in the window that the change could reduce, or monthly storage for unused tables; actual savings depend on query filters.

| Type | Table | Evidence | Addressable |
|---|---|---|---|
| partition | `demo-project.gov_raw.order_items` | 42 full scans in 30 days, and `created_at` is filtered in 42 queries. | $0.0060 |
| cluster | `demo-project.gov_raw.order_items` | Frequently filtered: `status` (21x). | $0.0066 |
| select_star | `demo-project.gov_raw.order_items` | 21 queries use SELECT * on a 10-column table. | $0.0030 |
| unused | `demo-project.gov_raw.legacy_product_snapshot` | No reads in the last 30 days. | < $0.0001 |

## Data quality

| Table | Check | Status | Value | Threshold |
|---|---|---|---|---|
| `demo-project.gov_mart.daily_sales` | row_count | pass | 19000 | >= 100 |
| `demo-project.gov_mart.daily_sales` | freshness_hours | warn | 37.11 | warn 26 / fail 50 |
| `demo-project.gov_mart.daily_sales` | null_pct:order_date | pass | 0.0 | warn 0.1 / fail 1.0 |
| `demo-project.gov_mart.daily_sales` | null_pct:revenue | warn | 0.2 | warn 0.1 / fail 1.0 |
| `demo-project.gov_mart.daily_sales` | duplicate_keys | pass | 0 | = 0 |
| `demo-project.gov_mart.customer_ltv` | row_count | pass | 80000 | >= 0 |
| `demo-project.gov_mart.customer_ltv` | freshness_hours | fail | 92.11 | warn 26 / fail 50 |
| `demo-project.gov_mart.customer_ltv` | null_pct:user_id | pass | 0.0 | warn 0.0001 / fail 1.0 |
| `demo-project.gov_mart.customer_ltv` | duplicate_keys | fail | 12 | = 0 |

## Lineage: widest downstream impact

| Table | Downstream tables |
|---|---|
| `demo-project.gov_raw.order_items` | 4 |
| `demo-project.gov_raw.products` | 4 |
| `demo-project.gov_staging.stg_order_items` | 3 |
| `demo-project.gov_mart.daily_sales` | 1 |
| `demo-project.gov_raw.users` | 1 |
| `demo-project.gov_mart.customer_ltv` | 0 |
| `demo-project.gov_mart.v_top_categories` | 0 |
