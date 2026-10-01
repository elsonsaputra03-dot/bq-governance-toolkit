"""Saran deskripsi tabel & kolom dengan LLM, ditinjau manusia sebelum diterapkan.

Alur: `bqgov describe` menulis saran ke descriptions.yaml (tidak mengubah apa pun di BigQuery).
Setelah ditinjau/diedit, `bqgov describe --apply descriptions.yaml` menulis deskripsi lewat API.
Model hanya melihat nama tabel, nama & tipe kolom, dan maksimal 5 baris contoh nilai yang di-masking; tidak ada data penuh.
Kolom yang sudah punya deskripsi tidak ditimpa.
"""
from __future__ import annotations

import json
import re

import yaml

PROMPT = """You document a data warehouse. Write a concise {language} description for the table and for each column listed.
Rules: one sentence each, at most 20 words, describe meaning not type, no marketing words, say "unclear" if you cannot tell.
Return JSON only: {{"table": "...", "columns": {{"<column>": "..."}}}}

Table: {table}
Columns (name: type): {columns}
Sample values (masked): {samples}"""

_MASK = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+|\+?\d[\d\s-]{7,}\d")


def mask(v) -> str:
    return _MASK.sub("***", str(v))[:40]


def _call(cfg: dict, prompt: str) -> str:
    if cfg["provider"] == "gemini":
        from google import genai           # pip install .[ai]; kunci di GEMINI_API_KEY
        return genai.Client().models.generate_content(model=cfg["model"], contents=prompt).text
    if cfg["provider"] == "ollama":
        import requests
        r = requests.post(f"{cfg['ollama_url']}/api/generate", json={"model": cfg["model"], "prompt": prompt, "stream": False, "format": "json"}, timeout=120)
        r.raise_for_status(); return r.json()["response"]
    raise ValueError("describe.provider = none; set gemini or ollama")


def parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("model did not return JSON")
    d = json.loads(m.group(0))
    return {"table": str(d.get("table", "")).strip(), "columns": {k: str(v).strip() for k, v in (d.get("columns") or {}).items()}}


def suggest(cfg, runner, snapshot: dict, call=_call) -> dict:
    by_table: dict[str, list] = {}
    for c in snapshot["columns"]:
        by_table.setdefault(f"{cfg.project}.{c['dataset']}.{c['table']}", []).append(c)
    tdesc = {f"{cfg.project}.{t['dataset']}.{t['table']}": t["description"] for t in snapshot["tables"]}
    out = {}
    for ref, cols in by_table.items():
        missing = [c for c in cols if not c["description"]]
        if not missing and tdesc.get(ref):
            continue
        sample = runner.sample_rows(ref, [c["column"] for c in (missing or cols)][:40], 5)   # gratis: tabledata.list, bukan query
        prompt = PROMPT.format(language=cfg.describe.get("language", "en"), table=ref,
                               columns=", ".join(f"{c['column']}: {c['data_type']}" for c in missing),
                               samples=json.dumps([{k: mask(v) for k, v in r.items()} for r in sample])[:2000])
        d = parse(call(cfg.describe, prompt))
        out[ref] = {"table": "" if tdesc.get(ref) else d["table"],
                    "columns": {c["column"]: d["columns"].get(c["column"], "") for c in missing}, "reviewed": False}
    return out


def write(suggestions: dict, path: str) -> None:
    head = "# Saran deskripsi dari LLM. Tinjau & edit, ubah reviewed: true, lalu: bqgov describe --apply <file>\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(head); yaml.safe_dump(suggestions, f, allow_unicode=True, sort_keys=True, width=120)


def apply(path: str, client) -> list[str]:
    """Terapkan hanya entri reviewed: true; deskripsi yang sudah ada di BigQuery tidak ditimpa."""
    done = []
    for ref, d in (yaml.safe_load(open(path, encoding="utf-8")) or {}).items():
        if not d.get("reviewed"):
            continue
        t = client.get_table(ref)
        fields = []
        for f in t.schema:
            new = d.get("columns", {}).get(f.name)
            fields.append(f.__class__.from_api_repr({**f.to_api_repr(), "description": new}) if new and not f.description else f)
        t.schema = fields
        props = ["schema"]
        if d.get("table") and not t.description:
            t.description = d["table"]; props.append("description")
        client.update_table(t, props); done.append(ref)
    return done
