"""Beban kerja demo: membangun ulang mart (pipeline berlabel) dan query analis, supaya riwayat job punya
biaya per pipeline, pola query berulang, full scan, SELECT *, dan lineage. Jalankan beberapa hari berbeda untuk tren.

Biaya: tiap query membaca puluhan MB (minimum tagihan 10 MB per tabel); satu putaran ~1 GB dari kuota gratis 1 TiB/bulan.
Pemakaian:  python demo/workload.py --project PROJECT_ID [--rounds 2]
"""
import argparse
import random

from google.cloud import bigquery

REBUILD = {   # pipeline -> CTAS (DDL; aman untuk sandbox)
    "stg_order_items": open("demo/setup.sql", encoding="utf-8").read().split("-- staging")[1].split("-- marts")[0].strip(),
    "mart_daily_sales": "CREATE OR REPLACE TABLE gov_mart.daily_sales OPTIONS (description = 'Daily revenue and margin by product category') AS "
                        "SELECT order_date, category, COUNT(DISTINCT order_id) AS orders, COUNT(*) AS items, ROUND(SUM(sale_price), 2) AS revenue, "
                        "ROUND(SUM(sale_price - cost), 2) AS margin FROM gov_staging.stg_order_items GROUP BY order_date, category",
}
ADHOC = [   # analis: filter tanggal & status di tabel raw tanpa partisi -> full scan berulang
    "SELECT status, COUNT(*) n, SUM(sale_price) rev FROM gov_raw.order_items WHERE DATE(created_at) = DATE '{d}' GROUP BY status",
    "SELECT * FROM gov_raw.order_items WHERE created_at >= TIMESTAMP '{d}' AND status = 'Complete' LIMIT 100",
    "SELECT user_id, SUM(sale_price) FROM gov_raw.order_items WHERE status = 'Returned' AND created_at BETWEEN TIMESTAMP '{d}' AND TIMESTAMP_ADD(TIMESTAMP '{d}', INTERVAL 7 DAY) GROUP BY user_id",
    "SELECT * FROM gov_raw.orders WHERE status = 'Shipped' AND created_at >= TIMESTAMP '{d}'",
    "SELECT category, SUM(revenue) FROM gov_mart.daily_sales WHERE order_date >= DATE '{d}' GROUP BY category",
    "SELECT * FROM gov_mart.v_top_categories",
    "SELECT country, AVG(revenue) FROM gov_mart.customer_ltv GROUP BY country",
]

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--project", required=True); ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()
    client = bigquery.Client(project=a.project, location="US")
    run = lambda sql, label: client.query(sql, job_config=bigquery.QueryJobConfig(labels={"pipeline": label}, maximum_bytes_billed=500 * 2**20)).result()
    for name, sql in REBUILD.items():
        run(sql, name); print("rebuilt", name)
    rnd = random.Random()
    for _ in range(a.rounds):
        for q in ADHOC:
            d = f"2024-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
            run(q.format(d=d), "adhoc"); print("ran", q[:60], d)
