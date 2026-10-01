"""bqgov: collect | report | dq | describe | demo-sql."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import collect as collect_mod, config, describe as describe_mod, dq as dq_mod, report as report_mod
from .bq import BigQueryRunner, FixtureRunner


def _runner(cfg, args):
    return FixtureRunner(args.fixtures) if args.fixtures else BigQueryRunner(cfg.project, cfg.location, cfg.guards["max_bytes_billed"])


def _latest(folder: Path) -> dict | None:
    files = sorted(folder.glob("report-*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bqgov", description="BigQuery governance & FinOps toolkit")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--fixtures", help="putar ulang hasil dari folder fixture (tanpa BigQuery)")
    ap.add_argument("-o", "--out", default="snapshots")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="collect + DQ + report (JSON + Markdown)")
    d = sub.add_parser("describe", help="saran deskripsi LLM ke YAML, atau --apply YAML yang sudah ditinjau")
    d.add_argument("--apply"); d.add_argument("--file", default="descriptions.yaml")
    sub.add_parser("demo-sql", help="cetak SQL untuk membuat dataset demo dari bigquery-public-data")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if a.cmd == "demo-sql":
        print((Path(__file__).parent.parent / "demo" / "setup.sql").read_text(encoding="utf-8")); return 0
    cfg = config.load(a.config)
    runner, out = _runner(cfg, a), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if a.cmd == "run":
        snap = collect_mod.collect(cfg, runner)
        dq = dq_mod.run(cfg, runner, previous=_latest(out))
        rep = report_mod.build(snap, dq, cfg.finops)
        rep["meta"]["toolkit_bytes_billed"] = runner.bytes_billed
        if cfg.publish.get("redact_users", True):
            rep = report_mod.redact(rep)
        stamp = rep["meta"]["collected_at"][:10]
        (out / f"report-{stamp}.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
        (out / "latest.json").write_text(json.dumps(rep, default=str), encoding="utf-8")
        (out / "REPORT.md").write_text(report_mod.markdown(rep), encoding="utf-8")
        s = rep["summary"]
        print(f"ok: {s['tables']} tables, {s['columns']} columns, query cost ${rep['costs']['total_usd']:.4f}, "
              f"{len(rep['recommendations'])} recommendations, DQ {s['dq']}, toolkit billed {runner.bytes_billed / 1e6:.1f} MB -> {out}/")
        return 1 if s["dq"]["fail"] else 0

    if a.cmd == "describe":
        if a.apply:
            from google.cloud import bigquery
            done = describe_mod.apply(a.apply, bigquery.Client(project=cfg.project, location=cfg.location))
            print(f"applied descriptions to {len(done)} tables"); return 0
        sugg = describe_mod.suggest(cfg, runner, collect_mod.collect(cfg, runner))
        describe_mod.write(sugg, a.file)
        print(f"wrote suggestions for {len(sugg)} tables to {a.file}; review, set reviewed: true, then --apply"); return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
