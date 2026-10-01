"""Data quality per tabel yang dikonfigurasi.

Cek gratis (metadata API, tanpa query): freshness, jumlah baris minimum, perubahan jumlah baris vs snapshot sebelumnya.
Cek berbayar (satu query per tabel, dibatasi maximum_bytes_billed, didahului dry run): null rate kolom wajib dan duplikat key.
Status: pass / warn / fail, dengan nilai dan ambang yang tercatat supaya bisa diaudit.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import sql
from .bq import Runner


def grade(value: float, warn: float, fail: float) -> str:
    return "fail" if value >= fail else "warn" if value >= warn else "pass"


def _hours_since(iso: str | None, now: datetime) -> float:
    if not iso:
        return float("inf")
    return (now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 3600


def run(cfg, runner: Runner, previous: dict | None = None, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    prev_rows = {r["table"]: r["value"] for r in (previous or {}).get("dq", []) if r["check"] == "row_count"}
    out = []

    def add(table, check, status, value, threshold="", detail=""):
        out.append({"table": table, "check": check, "status": status, "value": value, "threshold": threshold, "detail": detail})

    for spec in cfg.dq:
        ref = f"{cfg.project}.{spec['table']}"
        try:
            meta = runner.table_meta(ref)
        except Exception as exc:  # noqa: BLE001 - tabel hilang/akses ditolak adalah temuan DQ, bukan crash
            add(ref, "exists", "fail", None, detail=str(exc)[:200]); continue
        add(ref, "row_count", "pass" if (meta["num_rows"] or 0) >= spec.get("min_rows", 0) else "fail",
            meta["num_rows"], f">= {spec.get('min_rows', 0)}")
        if "freshness_hours" in spec:
            h = _hours_since(meta["modified"], now); w, f = spec["freshness_hours"]
            add(ref, "freshness_hours", grade(h, w, f), round(h, 2), f"warn {w} / fail {f}")
        if "row_change_pct" in spec and prev_rows.get(ref):
            ch = abs((meta["num_rows"] or 0) - prev_rows[ref]) / prev_rows[ref] * 100; w, f = spec["row_change_pct"]
            add(ref, "row_change_pct", grade(ch, w, f), round(ch, 2), f"warn {w} / fail {f}", f"previous {prev_rows[ref]}")
        nn, uk = spec.get("not_null", []), spec.get("unique_key", [])
        if not (nn or uk):
            continue
        q = sql.dq_profile(ref, nn, uk)
        if cfg.guards.get("dry_run_first", True):
            est = runner.dry_run_bytes(q)
            if est > cfg.guards["max_bytes_billed"]:
                add(ref, "profile_skipped", "warn", est, f"<= {cfg.guards['max_bytes_billed']} bytes", "scan too large for the configured guard")
                continue
        r = runner.query(f"dq__{spec['table'].replace('.', '__')}", q)[0]
        n = r.get("row_count") or 0
        for c in nn:
            pct = (r.get(f"null__{c}", 0) / n * 100) if n else 0.0
            w, f = spec.get("null_pct", [0.0001, 1.0])
            add(ref, f"null_pct:{c}", grade(pct, w, f), round(pct, 4), f"warn {w} / fail {f}")
        if uk:
            d = r.get("duplicate_keys") or 0
            add(ref, "duplicate_keys", "pass" if d == 0 else "fail", d, "= 0", f"key ({', '.join(uk)})")
    return out
