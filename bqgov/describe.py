"""Saran deskripsi tabel & kolom dengan LLM, ditinjau manusia sebelum diterapkan.

Alur: `bqgov describe` menulis saran ke descriptions.yaml (tidak mengubah apa pun di BigQuery).
Setelah ditinjau/diedit, `bqgov describe --apply descriptions.yaml` menulis deskripsi lewat API.
Model hanya melihat nama tabel, nama & tipe kolom, dan maksimal 5 baris contoh nilai yang di-masking; tidak ada data penuh.
Kolom yang sudah punya deskripsi tidak ditimpa.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

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
        from google.genai import types
        no_afc = types.GenerateContentConfig(automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        # Client harus tetap direferensikan selama request: google-genai 2.x menutup koneksi di __del__, sehingga
        # genai.Client().models.generate_content(...) gagal dengan "client has been closed" (ditemukan pada eksekusi nyata).
        with genai.Client() as client:
            return client.models.generate_content(model=cfg["model"], contents=prompt, config=no_afc).text
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


_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")
SAMPLEABLE = {"BASE TABLE"}      # tabledata.list menolak VIEW/MATERIALIZED VIEW; contoh dari view = query berbayar


def check_config(dcfg: dict) -> None:
    """Gagal cepat dengan pesan jelas sebelum memanggil API (nama model salah ketik ditemukan pada eksekusi nyata)."""
    if dcfg.get("provider") not in ("gemini", "ollama"):
        raise ValueError("describe.provider must be gemini or ollama")
    if not _MODEL.fullmatch(str(dcfg.get("model") or "")):
        raise ValueError(f"describe.model {dcfg.get('model')!r} is not a valid model name (e.g. gemini-2.5-flash, without 'models/')")


_RETRY_DELAY = re.compile(r"retry(?:Delay| in)['\"]?[:\s]+['\"]?(\d+(?:\.\d+)?)s", re.I)


def is_rate_limited(exc: Exception) -> bool:
    return getattr(exc, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(exc) or "429" in str(exc).split(".")[0]


def retry_after(exc: Exception, default: float = 30.0) -> float:
    """Waktu tunggu yang disarankan server (retryDelay), dibatasi 5-120 detik."""
    m = _RETRY_DELAY.search(str(exc))
    return min(120.0, max(5.0, float(m.group(1)) if m else default))


def is_transient(exc: Exception) -> bool:
    """500/503: server sibuk sementara ("high demand", ditemukan pada eksekusi nyata)."""
    return getattr(exc, "code", None) in (500, 503) or any(k in str(exc) for k in ("503 UNAVAILABLE", "500 INTERNAL"))


def call_with_retry(dcfg: dict, prompt: str, call, sleep=time.sleep, attempts: int = 3) -> str:
    """429 = kuota per menit habis: tunggu sesuai saran server. 500/503 = server sibuk: tunggu 10 s, lalu 20 s.
    Keduanya ditemukan pada eksekusi nyata dengan kuota gratis. Error lain langsung diteruskan."""
    for i in range(attempts):
        try:
            return call(dcfg, prompt)
        except Exception as exc:  # noqa: BLE001
            if i == attempts - 1 or not (is_rate_limited(exc) or is_transient(exc)):
                raise
            sleep(retry_after(exc) if is_rate_limited(exc) else 10.0 * 2 ** i)
    raise RuntimeError("unreachable")


def load_existing(path: str | Path) -> dict:
    p = Path(path)
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


def suggest(cfg, runner, snapshot: dict, call=_call, existing: dict | None = None, sleep=time.sleep) -> tuple[dict, dict]:
    """Saran per tabel. Kegagalan satu tabel dicatat di `errors` dan tidak menghentikan tabel lain.

    `existing` (isi descriptions.yaml sebelumnya) dipertahankan apa adanya, termasuk hasil review; hanya tabel yang belum
    punya saran yang dikirim ke model. Request diberi jeda `min_interval_seconds` agar tidak melewati kuota per menit."""
    existing = dict(existing or {})
    gap, last = float(cfg.describe.get("min_interval_seconds", 6)), None
    by_table: dict[str, list] = {}
    for c in snapshot["columns"]:
        by_table.setdefault(f"{cfg.project}.{c['dataset']}.{c['table']}", []).append(c)
    tdesc = {f"{cfg.project}.{t['dataset']}.{t['table']}": t["description"] for t in snapshot["tables"]}
    kind = {f"{cfg.project}.{t['dataset']}.{t['table']}": t["type"] for t in snapshot["tables"]}
    out, errors = dict(existing), {}
    for ref, cols in by_table.items():
        missing = [c for c in cols if not c["description"]]
        if (not missing and tdesc.get(ref)) or ref in existing:
            continue
        if last is not None and gap > 0:
            sleep(max(0.0, gap - (time.monotonic() - last)))
        last = time.monotonic()
        try:
            sample = runner.sample_rows(ref, [c["column"] for c in (missing or cols)][:40], 5) if kind.get(ref, "BASE TABLE") in SAMPLEABLE else []
            prompt = PROMPT.format(language=cfg.describe.get("language", "en"), table=f"{ref} ({kind.get(ref, 'BASE TABLE').lower()})",
                                   columns=", ".join(f"{c['column']}: {c['data_type']}" for c in missing),
                                   samples=json.dumps([{k: mask(v) for k, v in r.items()} for r in sample])[:2000] if sample else "none (view)")
            d = parse(call_with_retry(cfg.describe, prompt, call, sleep=sleep))
        except Exception as exc:  # noqa: BLE001 - satu tabel gagal tidak boleh membuang hasil tabel lain
            errors[ref] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
            continue
        out[ref] = {"table": "" if tdesc.get(ref) else d["table"],
                    "columns": {c["column"]: d["columns"].get(c["column"], "") for c in missing}, "reviewed": False}
    return out, errors


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
