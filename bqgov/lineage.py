"""Lineage tabel dari riwayat job (sumber -> tujuan) ditambah definisi view.

Aturan:
- Hanya job yang menulis ke tabel bernama (CTAS, INSERT, MERGE, CREATE VIEW tidak lewat job, lihat VIEWS).
- Tabel anonim/temporer (dataset diawali '_' atau tabel 'anon...') dibuang: itu hasil query interaktif, bukan aset.
- Satu pasangan sumber -> tujuan dihitung sekali per hari terakhir terlihat; jumlah job dan biayanya diakumulasi.
"""
from __future__ import annotations

import re

WRITE_TYPES = {"CREATE_TABLE_AS_SELECT", "INSERT", "MERGE", "UPDATE", "DELETE", "QUERY"}
_REF = re.compile(r"`?([a-zA-Z0-9_\-]+)\.([a-zA-Z0-9_]+)\.([a-zA-Z0-9_$]+)`?")


def fq(t: dict | None) -> str | None:
    if not t or not t.get("table_id"):
        return None
    if t.get("dataset_id", "").startswith("_") or t["table_id"].startswith("anon"):
        return None
    return f"{t['project_id']}.{t['dataset_id']}.{t['table_id']}"


def from_jobs(jobs: list[dict], price_per_tib: float) -> list[dict]:
    edges: dict[tuple[str, str], dict] = {}
    for j in jobs:
        if j.get("error_reason") or j.get("statement_type") not in WRITE_TYPES:
            continue
        dst = fq(j.get("destination_table"))
        if not dst:
            continue
        for r in j.get("referenced_tables") or []:
            src = fq(r)
            if not src or src == dst:
                continue
            e = edges.setdefault((src, dst), {"source": src, "target": dst, "via": "job", "jobs": 0, "cost_usd": 0.0, "last_seen": ""})
            e["jobs"] += 1
            e["cost_usd"] += j.get("bytes_billed", 0) / 2 ** 40 * price_per_tib
            e["last_seen"] = max(e["last_seen"], j.get("creation_time") or "")
    return list(edges.values())


def from_views(views: list[dict], project: str) -> list[dict]:
    out = []
    for v in views:
        dst = f"{project}.{v['dataset']}.{v['table']}"
        for p, d, t in set(_REF.findall(v.get("view_definition") or "")):
            src = f"{p}.{d}.{t}"
            if src != dst:
                out.append({"source": src, "target": dst, "via": "view", "jobs": 0, "cost_usd": 0.0, "last_seen": ""})
    return out


def build(snapshot: dict) -> dict:
    price = snapshot["meta"]["prices"]["on_demand_per_tib"]
    edges = from_jobs(snapshot["jobs"], price) + from_views(snapshot["views"], snapshot["meta"]["project"])
    nodes = sorted({e["source"] for e in edges} | {e["target"] for e in edges})
    return {"nodes": nodes, "edges": edges}


def downstream(edges: list[dict], table: str) -> list[str]:
    """Semua tabel yang (langsung atau tidak) bergantung pada `table`: dampak bila tabel ini berubah."""
    nxt: dict[str, set] = {}
    for e in edges:
        nxt.setdefault(e["source"], set()).add(e["target"])
    seen, stack = set(), [table]
    while stack:
        for t in nxt.get(stack.pop(), ()):
            if t not in seen:
                seen.add(t); stack.append(t)
    return sorted(seen)
