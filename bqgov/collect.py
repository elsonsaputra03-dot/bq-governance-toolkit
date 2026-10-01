"""Kumpulkan metadata mentah dari INFORMATION_SCHEMA menjadi satu snapshot (dict yang bisa ditulis ke JSON)."""
from __future__ import annotations

import ast
from datetime import datetime, timezone

from . import sql
from .bq import Runner
from .config import Config


def _desc(raw: str | None) -> str:
    """TABLE_OPTIONS menyimpan deskripsi sebagai literal string ber-kutip ("..."); ubah jadi teks biasa."""
    if not raw:
        return ""
    try:
        v = ast.literal_eval(raw)
        return v if isinstance(v, str) else str(v)
    except (ValueError, SyntaxError):
        return raw.strip('"')


ROLE_HINT = {
    "storage": "roles/bigquery.metadataViewer (Owner alone is not enough for INFORMATION_SCHEMA.TABLE_STORAGE)",
    "jobs": "roles/bigquery.resourceViewer (permission bigquery.jobs.listAll for JOBS_BY_PROJECT)",
}


def is_permission_error(exc: Exception) -> bool:
    return isinstance(exc, PermissionError) or getattr(exc, "code", None) == 403 or "Access Denied" in str(exc)


def is_storage_disabled(exc: Exception) -> bool:
    """TABLE_STORAGE harus diaktifkan per project/region (ALTER PROJECT ... enable_info_schema_storage)."""
    return "hasn't been enabled" in str(exc) or "enable_info_schema_storage" in str(exc)


def collect(cfg: Config, runner: Runner) -> dict:
    fmt = {"project": cfg.project, "region": cfg.region}
    warnings, storage_source = [], "information_schema"
    try:
        storage = runner.query("storage", sql.STORAGE.format(**fmt))
    except Exception as exc:  # noqa: BLE001
        disabled = is_storage_disabled(exc)
        if not (disabled or is_permission_error(exc)):
            raise
        # cadangan gratis: Tables API, tanpa pemisahan aktif/long-term (semua dianggap aktif)
        storage = [row for ds in cfg.datasets for row in runner.dataset_tables(ds)]
        storage_source = "tables_api"
        fix = (f"Enable it once: ALTER PROJECT `{cfg.project}` SET OPTIONS (`{cfg.region}.enable_info_schema_storage` = TRUE); "
               f"data appears within about a day." if disabled else f"Grant {ROLE_HINT['storage']}.")
        warnings.append(f"TABLE_STORAGE not available; storage taken from the Tables API for configured datasets only, "
                        f"without long-term split. {fix}")
    all_jobs = runner.query("jobs", sql.JOBS.format(**fmt), {"days": int(cfg.lookback_days)})
    own = [j for j in all_jobs if {"key": "tool", "value": "bqgov"} in (j.get("labels") or [])]   # query toolkit sendiri
    jobs = [j for j in all_jobs if j not in own]
    tables, columns, views = [], [], []
    for ds in cfg.datasets:
        f = {**fmt, "dataset": ds}
        for t in runner.query(f"tables__{ds}", sql.TABLES.format(**f)):
            tables.append({"dataset": ds, "table": t["table"], "type": t["table_type"], "created": t.get("creation_time"),
                           "description": _desc(t.get("description_raw"))})
        for c in runner.query(f"columns__{ds}", sql.COLUMNS.format(**f)):
            columns.append({"dataset": ds, **c, "description": c.get("description") or ""})
        for v in runner.query(f"views__{ds}", sql.VIEWS.format(**f)):
            views.append({"dataset": ds, **v})
    return {"meta": {"warnings": warnings, "storage_source": storage_source, "project": cfg.project, "location": cfg.location, "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "lookback_days": cfg.lookback_days, "datasets": cfg.datasets, "prices": cfg.prices,
                     "toolkit_jobs_in_window": len(own), "toolkit_bytes_in_window": sum(j.get("bytes_billed", 0) for j in own)},
            "storage": storage, "jobs": jobs, "tables": tables, "columns": columns, "views": views}
