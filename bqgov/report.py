"""Gabungkan snapshot mentah menjadi laporan: JSON untuk dashboard dan Markdown untuk dibaca manusia."""
from __future__ import annotations

import hashlib
import json
import re

from . import finops, lineage

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def redact(rep: dict) -> dict:
    """Ganti setiap email dengan pengenal stabil (user-xxxxxx) sebelum laporan dipublikasikan."""
    sub = lambda m: "user-" + hashlib.sha256(m.group(0).lower().encode()).hexdigest()[:6]
    return json.loads(_EMAIL.sub(sub, json.dumps(rep, default=str)))


def build(snapshot: dict, dq_results: list[dict], cfg_finops: dict) -> dict:
    proj = snapshot["meta"]["project"]
    lin = lineage.build(snapshot)
    cols = snapshot["columns"]
    documented = sum(1 for c in cols if c["description"])
    tables_doc = sum(1 for t in snapshot["tables"] if t["description"])
    impact = sorted(({"table": n, "downstream": len(lineage.downstream(lin["edges"], n))} for n in lin["nodes"]), key=lambda x: -x["downstream"])[:15]
    store = finops.storage_costs(snapshot)
    return {
        "meta": snapshot["meta"],
        "summary": {"tables": len(snapshot["tables"]), "columns": len(cols),
                    "column_doc_pct": round(documented / len(cols) * 100, 1) if cols else 0.0,
                    "table_doc_pct": round(tables_doc / len(snapshot["tables"]) * 100, 1) if snapshot["tables"] else 0.0,
                    "storage_gib": round(sum(s["logical_bytes"] for s in store) / 2 ** 30, 3),
                    "storage_usd_month": round(sum(s["cost_usd_month"] for s in store), 4),
                    "dq": {k: sum(1 for r in dq_results if r["status"] == k) for k in ("pass", "warn", "fail")}},
        "costs": finops.costs(snapshot, cfg_finops),
        "storage": store,
        "usage": finops.table_usage(snapshot, cfg_finops),
        "recommendations": finops.recommendations(snapshot, cfg_finops),
        "lineage": {**lin, "impact": impact},
        "dictionary": [{"table": f"{proj}.{c['dataset']}.{c['table']}", "column": c["column"], "type": c["data_type"], "description": c["description"],
                        "partitioning": (c.get("is_partitioning_column") or "NO") == "YES", "clustering": c.get("clustering_ordinal_position")} for c in cols],
        "dq": dq_results,
    }


def _usd(x: float) -> str:
    if x == 0:
        return "$0"
    return "< $0.0001" if x < 0.0001 else f"${x:,.4f}" if x < 1 else f"${x:,.2f}"


def markdown(r: dict) -> str:
    m, s, c = r["meta"], r["summary"], r["costs"]
    L = [f"# BigQuery governance report: `{m['project']}`", "",
         f"Collected {m['collected_at']} · job window {m['lookback_days']} days · location {m['location']} · "
         f"prices: ${m['prices']['on_demand_per_tib']}/TiB on-demand (estimates from list price, not a billing export)", "",
         *([f"> **Warning:** {w}" for w in m.get("warnings", [])] + ([""] if m.get("warnings") else [])),
         "## Summary", "", "| Metric | Value |", "|---|---|",
         f"| Tables / columns | {s['tables']} / {s['columns']} |", f"| Column descriptions | {s['column_doc_pct']}% |",
         f"| Table descriptions | {s['table_doc_pct']}% |", f"| Storage | {s['storage_gib']} GiB, {_usd(s['storage_usd_month'])}/month |",
         f"| Query cost in window | {_usd(c['total_usd'])} over {c['jobs']} jobs ({c['cache_hit_jobs']} cache hits) |",
         f"| Data quality | {s['dq']['pass']} pass · {s['dq']['warn']} warn · {s['dq']['fail']} fail |", "",
         "## Query cost by user", "", "| User | Cost |", "|---|---|"]
    L += [f"| {u['user']} | {_usd(u['cost_usd'])} |" for u in c["by_user"][:15]]
    L += ["", "## Query cost by pipeline label", "", "| Pipeline | Cost |", "|---|---|"]
    L += [f"| {p['pipeline']} | {_usd(p['cost_usd'])} |" for p in c["by_pipeline"][:15]]
    L += ["", "## Most expensive query patterns", "", "| Runs | Cost | Users | Query (literals normalized) |", "|---|---|---|---|"]
    L += [f"| {p['runs']} | {_usd(p['cost_usd'])} | {len(p['users'])} | `{p['fingerprint'][:120].replace('|', '/')}` |" for p in c["top_patterns"][:10]]
    L += ["", "## Recommendations", "", "Addressable cost = query cost in the window that the change could reduce, or monthly storage for unused tables; "
          "actual savings depend on query filters.", "", "| Type | Table | Evidence | Addressable |", "|---|---|---|---|"]
    L += [f"| {x['type']} | `{x['table']}` | {x['detail']} | {_usd(x['addressable_usd'])} |" for x in r["recommendations"]] or ["| – | – | none | – |"]
    L += ["", "## Data quality", "", "| Table | Check | Status | Value | Threshold |", "|---|---|---|---|---|"]
    L += [f"| `{d['table']}` | {d['check']} | {d['status']} | {d['value']} | {d['threshold']} |" for d in r["dq"]] or ["| – | – | – | – | – |"]
    L += ["", "## Lineage: widest downstream impact", "", "| Table | Downstream tables |", "|---|---|"]
    L += [f"| `{i['table']}` | {i['downstream']} |" for i in r["lineage"]["impact"][:10]]
    return "\n".join(L) + "\n"
