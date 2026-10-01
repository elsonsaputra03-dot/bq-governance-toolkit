"""Biaya dan rekomendasi dari riwayat job + storage.

Biaya query = byte ditagih / 2^40 x harga on-demand per TiB (estimasi on-demand; proyek dengan kapasitas/edisi dibayar per slot).
Biaya storage = GiB aktif x harga aktif + GiB long-term x harga long-term (logical billing).
Semua angka adalah ESTIMASI dari list price; tidak menggantikan laporan billing.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .lineage import fq

GIB, TIB = 2 ** 30, 2 ** 40
DATE_TYPES = {"DATE", "DATETIME", "TIMESTAMP"}
CLUSTER_TYPES = {"STRING", "INT64", "BOOL", "DATE", "NUMERIC", "BIGNUMERIC", "TIMESTAMP", "DATETIME"}
_LIT = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|\b\d+(?:\.\d+)?\b")
_WS = re.compile(r"\s+")
# hanya predikat WHERE/AND/OR; kondisi JOIN ... ON adalah kunci join (sering milik tabel lain), bukan filter
# kolom boleh dibungkus satu fungsi, mis. WHERE DATE(created_at) = ... (ditemukan pada eksekusi nyata pertama)
_FILTER = re.compile(r"(?:where|and|or)\s+(?:[a-z_]\w*\s*\(\s*)?(?:[a-z_][\w]*\.)?`?([a-z_][\w]*)`?\s*\)?\s*"
                     r"(?:=|!=|<>|>=|<=|>|<|\bbetween\b|\bin\b|\blike\b|\bis\b)", re.I)
_STAR = re.compile(r"select\s+(?:distinct\s+)?\*", re.I)


def fingerprint(q: str | None) -> str:
    """Normalisasi literal & spasi supaya query yang sama dengan nilai berbeda dihitung sebagai satu pola."""
    return _WS.sub(" ", _LIT.sub("?", q or "")).strip().lower()[:500]


def job_cost(j: dict, price: float) -> float:
    return j.get("bytes_billed", 0) / TIB * price


def _labels(j: dict) -> dict:
    return {l["key"]: l["value"] for l in j.get("labels") or []}


def costs(snapshot: dict, cfg_finops: dict) -> dict:
    price = snapshot["meta"]["prices"]["on_demand_per_tib"]
    jobs = [j for j in snapshot["jobs"] if not j.get("error_reason")]
    by_user, by_day, by_type, by_pipe = Counter(), Counter(), Counter(), Counter()
    patterns: dict[str, dict] = {}
    for j in jobs:
        c = job_cost(j, price)
        by_user[j.get("user_email") or "unknown"] += c
        by_day[(j.get("creation_time") or "")[:10]] += c
        by_type[j.get("statement_type") or "UNKNOWN"] += c
        by_pipe[_labels(j).get(cfg_finops["pipeline_label"], "(no label)")] += c
        fp = fingerprint(j.get("query"))
        p = patterns.setdefault(fp, {"fingerprint": fp, "example": (j.get("query") or "")[:400], "runs": 0, "cost_usd": 0.0, "bytes_billed": 0,
                                     "users": set(), "cache_hits": 0})
        p["runs"] += 1; p["cost_usd"] += c; p["bytes_billed"] += j.get("bytes_billed", 0); p["users"].add(j.get("user_email") or "unknown")
        p["cache_hits"] += 1 if j.get("cache_hit") else 0
    top_patterns = sorted(patterns.values(), key=lambda p: -p["cost_usd"])[:25]
    for p in top_patterns:
        p["users"] = sorted(p["users"])
    expensive = sorted(({"job_id": j["job_id"], "user": j.get("user_email"), "time": j.get("creation_time"), "cost_usd": job_cost(j, price),
                         "bytes_billed": j.get("bytes_billed", 0), "query": (j.get("query") or "")[:400]}
                        for j in jobs if job_cost(j, price) >= cfg_finops["expensive_query_usd"]), key=lambda x: -x["cost_usd"])[:50]
    total = sum(by_user.values())
    return {"total_usd": total, "jobs": len(jobs), "cache_hit_jobs": sum(1 for j in jobs if j.get("cache_hit")),
            "by_user": [{"user": k, "cost_usd": v} for k, v in by_user.most_common()],
            "by_day": [{"date": k, "cost_usd": v} for k, v in sorted(by_day.items())],
            "by_statement_type": [{"type": k, "cost_usd": v} for k, v in by_type.most_common()],
            "by_pipeline": [{"pipeline": k, "cost_usd": v} for k, v in by_pipe.most_common()],
            "top_patterns": top_patterns, "expensive_jobs": expensive}


def storage_costs(snapshot: dict) -> list[dict]:
    pr = snapshot["meta"]["prices"]
    out = []
    for s in snapshot["storage"]:
        act, lt = (s.get("active_logical_bytes") or 0) / GIB, (s.get("long_term_logical_bytes") or 0) / GIB
        out.append({"table": f"{snapshot['meta']['project']}.{s['dataset']}.{s['table']}", "rows": s.get("total_rows") or 0,
                    "logical_bytes": s.get("total_logical_bytes") or 0, "active_gib": act, "long_term_gib": lt,
                    "cost_usd_month": act * pr["active_storage_per_gib_month"] + lt * pr["long_term_storage_per_gib_month"]})
    return sorted(out, key=lambda x: -x["cost_usd_month"])


def table_usage(snapshot: dict, cfg_finops: dict) -> dict[str, dict]:
    """Per tabel: jumlah baca, byte, full scan (job satu-tabel yang membaca >= rasio ukuran tabel), kolom yang difilter."""
    price = snapshot["meta"]["prices"]["on_demand_per_tib"]
    size = {f"{snapshot['meta']['project']}.{s['dataset']}.{s['table']}": s.get("total_logical_bytes") or 0 for s in snapshot["storage"]}
    cols = defaultdict(dict)
    for c in snapshot["columns"]:
        cols[f"{snapshot['meta']['project']}.{c['dataset']}.{c['table']}"][c["column"].lower()] = c
    use: dict[str, dict] = {}
    for j in snapshot["jobs"]:
        if j.get("error_reason"):
            continue
        refs = [t for t in (fq(r) for r in j.get("referenced_tables") or []) if t]
        filt = {m.lower() for m in _FILTER.findall(j.get("query") or "")}
        star = bool(_STAR.search(j.get("query") or ""))
        for t in refs:
            u = use.setdefault(t, {"reads": 0, "bytes_billed": 0, "cost_usd": 0.0, "full_scans": 0, "full_scan_cost_usd": 0.0,
                                   "filters": Counter(), "select_star": 0, "select_star_cost_usd": 0.0, "users": set(), "last_read": ""})
            u["reads"] += 1; u["users"].add(j.get("user_email") or "unknown"); u["last_read"] = max(u["last_read"], j.get("creation_time") or "")
            share = job_cost(j, price) / len(refs)
            u["bytes_billed"] += j.get("bytes_billed", 0) // len(refs); u["cost_usd"] += share
            for f in filt & set(cols.get(t, {})):
                u["filters"][f] += 1
            if star:
                u["select_star"] += 1; u["select_star_cost_usd"] += share
            if len(refs) == 1 and size.get(t) and j.get("bytes_processed", 0) >= cfg_finops["full_scan_ratio"] * size[t]:
                u["full_scans"] += 1; u["full_scan_cost_usd"] += share
    for u in use.values():
        u["users"] = sorted(u["users"]); u["filters"] = dict(u["filters"].most_common())
    return use


def recommendations(snapshot: dict, cfg_finops: dict) -> list[dict]:
    meta, proj = snapshot["meta"], snapshot["meta"]["project"]
    use = table_usage(snapshot, cfg_finops)
    store = {s["table"]: s for s in storage_costs(snapshot)}
    cols = defaultdict(dict)
    for c in snapshot["columns"]:
        cols[f"{proj}.{c['dataset']}.{c['table']}"][c["column"].lower()] = c
    kinds = {f"{proj}.{t['dataset']}.{t['table']}": t["type"] for t in snapshot["tables"]}
    recs = []
    for t, st in store.items():
        u, tc, gib = use.get(t), cols.get(t, {}), st["logical_bytes"] / GIB
        partitioned = any((c.get("is_partitioning_column") or "NO") == "YES" for c in tc.values())
        clustered = any(c.get("clustering_ordinal_position") for c in tc.values())
        if u and not partitioned and gib >= cfg_finops["partition_min_gib"] and u["full_scans"] >= 3:
            dates = [f for f in u["filters"] if (tc.get(f, {}).get("data_type") or "") in DATE_TYPES]
            if dates:
                col, typ = dates[0], tc[dates[0]]["data_type"]
                expr = col if typ == "DATE" else f"DATE({col})"
                recs.append({"type": "partition", "table": t, "column": col,
                             "detail": f"{u['full_scans']} full scans in {meta['lookback_days']} days, and `{col}` is filtered in {u['filters'][col]} queries.",
                             "addressable_usd": round(u["full_scan_cost_usd"], 6),
                             "ddl": f"CREATE TABLE `{t}_part` PARTITION BY {expr} AS SELECT * FROM `{t}`;  -- lalu ganti nama setelah validasi"})
        if u and not clustered and gib >= cfg_finops["cluster_min_gib"]:
            cand = [f for f, n in u["filters"].items() if n >= 3 and (tc.get(f, {}).get("data_type") or "").split("<")[0] in CLUSTER_TYPES
                    and (tc.get(f, {}).get("data_type") or "") not in DATE_TYPES][:4]
            if cand:
                recs.append({"type": "cluster", "table": t, "columns": cand,
                             "detail": "Frequently filtered: " + ", ".join(f"`{c}` ({u['filters'][c]}x)" for c in cand) + ".",
                             "addressable_usd": round(u["cost_usd"], 6),
                             "ddl": f"CREATE TABLE `{t}_clustered` CLUSTER BY {', '.join(cand)} AS SELECT * FROM `{t}`;  -- lalu ganti nama setelah validasi"})
        if u and u["select_star"] >= 3 and len(tc) >= 10:
            recs.append({"type": "select_star", "table": t, "detail": f"{u['select_star']} queries use SELECT * on a {len(tc)}-column table.",
                         "addressable_usd": round(u["select_star_cost_usd"], 6), "ddl": ""})
        if kinds.get(t, "BASE TABLE") == "BASE TABLE" and not u and t.split(".")[1] in meta["datasets"]:
            recs.append({"type": "unused", "table": t, "detail": f"No reads in the last {meta['lookback_days']} days.",
                         "addressable_usd": round(st["cost_usd_month"], 6), "ddl": f"-- review owner, then: DROP TABLE `{t}`;"})
    order = {"partition": 0, "cluster": 1, "select_star": 2, "unused": 3}
    return sorted(recs, key=lambda r: (order[r["type"]], -r["addressable_usd"]))
