"""Bangkitkan fixture INFORMATION_SCHEMA sintetis yang meniru demo workload (dipakai test & `bqgov --fixtures`)."""
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

P, MB = "demo-project", 2 ** 20
NOW = datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc)
F = Path(__file__).parent / "fixtures"


def main():
    F.mkdir(exist_ok=True)
    w = lambda name, obj: (F / f"{name}.json").write_text(json.dumps(obj, indent=1), encoding="utf-8")
    storage = [("gov_raw", "orders", 125000, 14), ("gov_raw", "order_items", 181000, 24), ("gov_raw", "products", 29000, 3),
               ("gov_raw", "users", 100000, 8), ("gov_raw", "legacy_product_snapshot", 29000, 3),
               ("gov_staging", "stg_order_items", 172000, 18), ("gov_mart", "daily_sales", 19000, 1), ("gov_mart", "customer_ltv", 80000, 4)]
    w("storage", [{"dataset": d, "table": t, "total_rows": r, "total_logical_bytes": m * MB,
                   "active_logical_bytes": 0 if t.startswith("legacy") else m * MB, "long_term_logical_bytes": m * MB if t.startswith("legacy") else 0,
                   "total_physical_bytes": m * MB // 3, "storage_last_modified_time": NOW.isoformat()} for d, t, r, m in storage])
    T = lambda d, t: {"project_id": P, "dataset_id": d, "table_id": t}
    jobs, rnd = [], random.Random(7)

    def job(q, refs, dst=None, st="SELECT", mb=10, user="analyst@example.com", label="adhoc", cache=False, err=None, days=1, tool=False):
        n = len(jobs) + 1
        jobs.append({"job_id": f"job_{n}", "creation_time": (NOW - timedelta(days=days, minutes=n)).isoformat(), "user_email": user,
                     "job_type": "QUERY", "statement_type": st, "state": "DONE", "cache_hit": cache, "bytes_billed": 0 if cache else mb * MB,
                     "bytes_processed": mb * MB, "slot_ms": 1000, "query": q, "referenced_tables": refs, "destination_table": dst,
                     "labels": [{"key": "pipeline", "value": label}] + ([{"key": "tool", "value": "bqgov"}] if tool else []), "error_reason": err})

    for d in range(1, 8):
        job("CREATE OR REPLACE TABLE gov_staging.stg_order_items AS SELECT oi.id, p.category FROM gov_raw.order_items oi JOIN gov_raw.products p ON p.id = oi.product_id",
            [T("gov_raw", "order_items"), T("gov_raw", "products")], T("gov_staging", "stg_order_items"), "CREATE_TABLE_AS_SELECT", 27, "etl-sa@demo.iam", "stg_order_items", days=d)
        job("CREATE OR REPLACE TABLE gov_mart.daily_sales AS SELECT order_date, category FROM gov_staging.stg_order_items GROUP BY order_date, category",
            [T("gov_staging", "stg_order_items")], T("gov_mart", "daily_sales"), "CREATE_TABLE_AS_SELECT", 18, "etl-sa@demo.iam", "mart_daily_sales", days=d)
        job("CREATE OR REPLACE TABLE gov_mart.customer_ltv AS SELECT s.user_id FROM gov_staging.stg_order_items s JOIN gov_raw.users u ON u.id = s.user_id",
            [T("gov_staging", "stg_order_items"), T("gov_raw", "users")], T("gov_mart", "customer_ltv"), "CREATE_TABLE_AS_SELECT", 26, "etl-sa@demo.iam", "mart_customer_ltv", days=d)
        for _ in range(3):
            dd = f"2024-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
            job(f"SELECT status, COUNT(*) n FROM gov_raw.order_items WHERE created_at >= TIMESTAMP '{dd}' GROUP BY status", [T("gov_raw", "order_items")], mb=24, days=d)
            job(f"SELECT * FROM gov_raw.order_items WHERE created_at >= TIMESTAMP '{dd}' AND status = 'Complete' LIMIT 100",
                [T("gov_raw", "order_items")], mb=24, user="bi@example.com", days=d)
        job("SELECT * FROM gov_mart.v_top_categories", [T("gov_mart", "daily_sales")], mb=1, user="bi@example.com", days=d)
        job("SELECT country, AVG(revenue) FROM gov_mart.customer_ltv GROUP BY country", [T("gov_mart", "customer_ltv")], mb=4, cache=d % 2 == 0, days=d)
    job("SELECT 1 FROM gov_raw.orders", [T("gov_raw", "orders")], {"project_id": P, "dataset_id": "_abc123", "table_id": "anon_xyz"}, mb=14, days=2)
    job("SELECT broken FROM gov_raw.orders", [T("gov_raw", "orders")], err="invalidQuery", mb=0, days=2)
    job("SELECT * FROM `region-us`.INFORMATION_SCHEMA.TABLE_STORAGE", [], mb=10, user="me@example.com", label="x", tool=True, days=0)
    w("jobs", jobs)

    cols = {("gov_raw", "orders"): "order_id:INT64:One order, user_id:INT64, status:STRING, created_at:TIMESTAMP, shipped_at:TIMESTAMP, delivered_at:TIMESTAMP, returned_at:TIMESTAMP, num_of_item:INT64",
            ("gov_raw", "order_items"): "id:INT64, order_id:INT64, user_id:INT64, product_id:INT64, status:STRING, created_at:TIMESTAMP, shipped_at:TIMESTAMP, delivered_at:TIMESTAMP, returned_at:TIMESTAMP, sale_price:FLOAT64",
            ("gov_raw", "products"): "id:INT64:Product id, cost:FLOAT64:Unit cost, category:STRING:Category, name:STRING:Name, brand:STRING:Brand, retail_price:FLOAT64:List price, department:STRING:Department, distribution_center_id:INT64:Warehouse",
            ("gov_raw", "users"): "id:INT64, age:INT64, gender:STRING, state:STRING, city:STRING, country:STRING, traffic_source:STRING, created_at:TIMESTAMP",
            ("gov_raw", "legacy_product_snapshot"): "id:INT64, cost:FLOAT64, category:STRING",
            ("gov_staging", "stg_order_items"): "order_item_id:INT64, order_id:INT64, user_id:INT64, order_date:DATE:Order date, status:STRING, category:STRING, department:STRING, brand:STRING, sale_price:FLOAT64, cost:FLOAT64",
            ("gov_mart", "daily_sales"): "order_date:DATE:Calendar date of the order, category:STRING:Product category, orders:INT64:Distinct orders, items:INT64:Order items, revenue:FLOAT64:Revenue in USD, margin:FLOAT64:Revenue minus cost in USD",
            ("gov_mart", "customer_ltv"): "user_id:INT64, country:STRING, traffic_source:STRING, first_order:DATE, orders:INT64, revenue:FLOAT64"}
    tdesc = {"orders": '"One row per order"', "users": '"Customers without direct identifiers"', "daily_sales": '"Daily revenue and margin by product category"'}
    for ds in ("gov_raw", "gov_staging", "gov_mart"):
        rows = []
        for (d, t), spec in cols.items():
            if d != ds: continue
            for c in spec.split(", "):
                name, typ, *desc = c.split(":")
                rows.append({"table": t, "column": name, "data_type": typ, "is_nullable": "YES", "is_partitioning_column": "NO",
                             "clustering_ordinal_position": None, "description": desc[0] if desc else None})
        w(f"columns__{ds}", rows)
        w(f"tables__{ds}", [{"table": t, "table_type": "BASE TABLE", "creation_time": (NOW - timedelta(days=40)).isoformat(), "description_raw": tdesc.get(t)}
                            for (d, t) in cols if d == ds] + ([{"table": "v_top_categories", "table_type": "VIEW", "creation_time": NOW.isoformat(), "description_raw": None}] if ds == "gov_mart" else []))
        w(f"views__{ds}", [{"table": "v_top_categories", "view_definition": f"SELECT category, SUM(revenue) AS revenue FROM `{P}.gov_mart.daily_sales` GROUP BY category"}] if ds == "gov_mart" else [])
    w("table_meta", {"gov_mart.daily_sales": {"num_rows": 19000, "num_bytes": MB, "modified": (NOW - timedelta(hours=5)).isoformat(), "created": NOW.isoformat(),
                                              "description": "x", "partitioning": None, "clustering": [], "expires": None, "schema": []},
                     "gov_mart.customer_ltv": {"num_rows": 80000, "num_bytes": 4 * MB, "modified": (NOW - timedelta(hours=60)).isoformat(), "created": NOW.isoformat(),
                                               "description": "", "partitioning": None, "clustering": [], "expires": None, "schema": []}})
    w("dq__gov_mart__daily_sales", [{"row_count": 19000, "null__order_date": 0, "null__revenue": 38, "duplicate_keys": 0}])
    w("dq__gov_mart__customer_ltv", [{"row_count": 80000, "null__user_id": 0, "duplicate_keys": 12}])
    w("samples", {"gov_raw.users": [{"id": 1, "age": 34, "gender": "F", "state": "Texas", "city": "Austin", "country": "United States",
                                     "traffic_source": "Search", "created_at": "2023-01-02T00:00:00"}]})
    print(f"fixtures: {len(jobs)} jobs -> {F}")


if __name__ == "__main__":
    main()
