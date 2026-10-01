"""Konfigurasi dari YAML dengan nilai bawaan yang aman."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULTS = {
    "location": "US",
    "datasets": [],
    "lookback_days": 30,
    "guards": {"max_bytes_billed": 1 << 30, "dry_run_first": True},
    "prices": {"on_demand_per_tib": 6.25, "active_storage_per_gib_month": 0.02, "long_term_storage_per_gib_month": 0.01},
    "finops": {"expensive_query_usd": 1.0, "full_scan_ratio": 0.9, "partition_min_gib": 10.0, "cluster_min_gib": 1.0,
               "pipeline_label": "pipeline", "unused_days": 30},
    "dq": [],
    "describe": {"provider": "none", "model": "", "ollama_url": "http://localhost:11434", "language": "en"},
    "publish": {"redact_users": True},
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@dataclass
class Config:
    project: str
    location: str
    datasets: list[str]
    lookback_days: int
    guards: dict
    prices: dict
    finops: dict
    dq: list[dict]
    describe: dict
    publish: dict
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def region(self) -> str:
        """`region-us` style qualifier for regional INFORMATION_SCHEMA views."""
        return f"region-{self.location.lower()}"


def load(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not data.get("project"):
        raise ValueError(f"{path}: 'project' wajib diisi")
    m = _merge(DEFAULTS, data)
    return Config(project=m["project"], location=m["location"], datasets=list(m["datasets"]), lookback_days=int(m["lookback_days"]),
                  guards=m["guards"], prices=m["prices"], finops=m["finops"], dq=list(m["dq"]), describe=m["describe"], publish=m["publish"], raw=m)
