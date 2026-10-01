"""Akses BigQuery dengan pengaman biaya.

Runner adalah satu-satunya pintu ke BigQuery. Setiap query diberi maximum_bytes_billed, sehingga query yang
melebihi batas ditolak BigQuery sebelum ditagih. FixtureRunner memutar ulang hasil tersimpan untuk test dan demo offline.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("bqgov")


class Runner(Protocol):
    def query(self, name: str, sql: str, params: dict | None = None) -> list[dict]: ...
    def dry_run_bytes(self, sql: str, params: dict | None = None) -> int: ...
    def table_meta(self, table_ref: str) -> dict: ...
    def sample_rows(self, table_ref: str, columns: list[str], n: int = 5) -> list[dict]: ...
    def dataset_tables(self, dataset: str) -> list[dict]: ...


def _plain(v: Any) -> Any:
    """Row values -> JSON-friendly Python values."""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


class BigQueryRunner:
    def __init__(self, project: str, location: str, max_bytes_billed: int):
        from google.cloud import bigquery          # impor di sini agar test tidak butuh library GCP
        self.bq = bigquery
        self.client = bigquery.Client(project=project, location=location)
        self.max_bytes = int(max_bytes_billed)
        self.bytes_billed = 0                       # total byte yang ditagih selama satu eksekusi toolkit

    def _cfg(self, params: dict | None, dry: bool = False):
        qp = []
        for k, v in (params or {}).items():
            if isinstance(v, (list, tuple)):
                qp.append(self.bq.ArrayQueryParameter(k, "STRING", list(v))); continue
            t = "INT64" if isinstance(v, int) else "FLOAT64" if isinstance(v, float) else "STRING"
            qp.append(self.bq.ScalarQueryParameter(k, t, v))
        return self.bq.QueryJobConfig(query_parameters=qp, maximum_bytes_billed=self.max_bytes, dry_run=dry,
                                      use_query_cache=not dry, labels={"tool": "bqgov"})

    def query(self, name: str, sql: str, params: dict | None = None) -> list[dict]:
        job = self.client.query(sql, job_config=self._cfg(params))
        rows = [{k: _plain(v) for k, v in dict(r).items()} for r in job.result()]
        self.bytes_billed += job.total_bytes_billed or 0
        log.info("%s: %d rows, %.1f MB billed", name, len(rows), (job.total_bytes_billed or 0) / 1e6)
        return rows

    def dry_run_bytes(self, sql: str, params: dict | None = None) -> int:
        return int(self.client.query(sql, job_config=self._cfg(params, dry=True)).total_bytes_processed or 0)

    def table_meta(self, table_ref: str) -> dict:
        """Metadata gratis lewat API (tanpa query): jumlah baris, ukuran, waktu ubah, partisi, cluster, skema."""
        t = self.client.get_table(table_ref)
        return {"num_rows": t.num_rows, "num_bytes": t.num_bytes, "modified": _plain(t.modified), "created": _plain(t.created),
                "description": t.description or "", "partitioning": (t.time_partitioning.field or "_PARTITIONTIME") if t.time_partitioning
                else (t.range_partitioning.field if t.range_partitioning else None),
                "clustering": list(t.clustering_fields or []), "expires": _plain(t.expires),
                "schema": [{"name": f.name, "type": f.field_type, "description": f.description or ""} for f in t.schema]}


    def sample_rows(self, table_ref: str, columns: list[str], n: int = 5) -> list[dict]:
        """Contoh baris lewat tabledata.list: gratis, tidak menjalankan query (SELECT ... LIMIT tetap menagih scan penuh)."""
        t = self.client.get_table(table_ref)
        fields = [f for f in t.schema if f.name in set(columns)] or None
        return [{k: _plain(v) for k, v in dict(r).items()} for r in self.client.list_rows(t, selected_fields=fields, max_results=n)]


    def dataset_tables(self, dataset: str) -> list[dict]:
        """Ukuran & jumlah baris per tabel lewat Tables API (gratis). Dipakai bila TABLE_STORAGE tidak boleh dibaca."""
        out = []
        for item in self.client.list_tables(f"{self.client.project}.{dataset}"):
            if item.table_type not in ("TABLE", "MATERIALIZED_VIEW"):
                continue
            t = self.client.get_table(item.reference)
            out.append({"dataset": dataset, "table": t.table_id, "total_rows": t.num_rows or 0, "total_logical_bytes": t.num_bytes or 0,
                        "active_logical_bytes": t.num_bytes or 0, "long_term_logical_bytes": 0, "total_physical_bytes": None,
                        "storage_last_modified_time": _plain(t.modified)})
        return out


class FixtureRunner:
    """Memutar ulang hasil dari tests/fixtures/<name>.json; untuk test dan demo tanpa akun GCP."""

    def __init__(self, folder: str | Path, forbidden: set[str] | None = None):
        self.folder = Path(folder)
        self.bytes_billed = 0
        self.calls: list[str] = []
        self.forbidden = forbidden or set()

    def query(self, name: str, sql: str, params: dict | None = None) -> list[dict]:
        self.calls.append(name)
        if name in self.forbidden:
            raise PermissionError(f"403 Access Denied (simulated) for {name}")
        if name in getattr(self, "disabled", set()):
            raise RuntimeError(f"400 INFORMATION_SCHEMA.TABLE_STORAGE hasn't been enabled (simulated) for {name}")
        f = self.folder / f"{name}.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []

    def dry_run_bytes(self, sql: str, params: dict | None = None) -> int:
        return 1024

    def sample_rows(self, table_ref: str, columns: list[str], n: int = 5) -> list[dict]:
        f = self.folder / "samples.json"
        rows = json.loads(f.read_text(encoding="utf-8")).get(table_ref.split(".", 1)[-1], []) if f.exists() else []
        return [{k: v for k, v in r.items() if k in columns} for r in rows][:n]

    def dataset_tables(self, dataset: str) -> list[dict]:
        f = self.folder / f"api_tables__{dataset}.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []

    def table_meta(self, table_ref: str) -> dict:
        meta = json.loads((self.folder / "table_meta.json").read_text(encoding="utf-8"))
        return meta[table_ref.split(".", 1)[-1] if table_ref.count(".") == 2 else table_ref]
