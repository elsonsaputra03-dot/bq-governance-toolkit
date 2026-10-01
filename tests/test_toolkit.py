"""Test end-to-end dengan FixtureRunner: tanpa jaringan, tanpa akun GCP."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from bqgov import cli, collect, config, describe, dq, finops, lineage, report
from bqgov.bq import FixtureRunner

HERE = Path(__file__).parent
NOW = datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc)
P = "demo-project"


@pytest.fixture(scope="module")
def cfg():
    return config.load(HERE / "config.test.yaml")


@pytest.fixture(scope="module")
def runner():
    return FixtureRunner(HERE / "fixtures")


@pytest.fixture(scope="module")
def snap(cfg, runner):
    return collect.collect(cfg, runner)


def test_config_defaults_and_region(cfg):
    assert cfg.region == "region-us"
    assert cfg.prices["on_demand_per_tib"] == 6.25            # bawaan dipakai bila tidak diisi
    assert cfg.guards["max_bytes_billed"] == 1 << 30


def test_toolkit_own_jobs_excluded(snap):
    assert all({"key": "tool", "value": "bqgov"} not in j["labels"] for j in snap["jobs"])
    assert snap["meta"]["toolkit_jobs_in_window"] == 1


def test_table_description_literal_is_unquoted(snap):
    d = {t["table"]: t["description"] for t in snap["tables"]}
    assert d["daily_sales"] == "Daily revenue and margin by product category" and d["order_items"] == ""


def test_lineage_jobs_and_views_without_anonymous_tables(snap):
    lin = lineage.build(snap)
    pairs = {(e["source"].split(".", 1)[1], e["target"].split(".", 1)[1], e["via"]) for e in lin["edges"]}
    assert ("gov_raw.order_items", "gov_staging.stg_order_items", "job") in pairs
    assert ("gov_staging.stg_order_items", "gov_mart.customer_ltv", "job") in pairs
    assert ("gov_mart.daily_sales", "gov_mart.v_top_categories", "view") in pairs
    assert not any("anon" in n or "._" in n for n in lin["nodes"])
    e = next(e for e in lin["edges"] if e["target"].endswith("stg_order_items") and e["source"].endswith("order_items"))
    assert e["jobs"] == 7                                         # satu per hari selama 7 hari


def test_downstream_impact(snap):
    edges = lineage.build(snap)["edges"]
    down = lineage.downstream(edges, f"{P}.gov_raw.order_items")
    assert set(down) == {f"{P}.gov_staging.stg_order_items", f"{P}.gov_mart.daily_sales", f"{P}.gov_mart.customer_ltv", f"{P}.gov_mart.v_top_categories"}


def test_fingerprint_groups_literals():
    a = finops.fingerprint("SELECT * FROM t WHERE d = '2024-01-02' AND n > 5")
    b = finops.fingerprint("select *  from t where d = '2025-12-31' and n > 99")
    assert a == b


def test_costs_exclude_failed_and_cache_hits(snap, cfg):
    c = finops.costs(snap, cfg.finops)
    billed = sum(j["bytes_billed"] for j in snap["jobs"] if not j["error_reason"])
    assert c["total_usd"] == pytest.approx(billed / 2 ** 40 * 6.25)
    assert c["cache_hit_jobs"] == 3
    assert {p["pipeline"] for p in c["by_pipeline"]} >= {"adhoc", "stg_order_items", "mart_daily_sales", "mart_customer_ltv"}
    assert c["top_patterns"][0]["runs"] >= 7                       # pola berulang dengan literal berbeda digabung


def test_usage_full_scans_filters_and_star(snap, cfg):
    u = finops.table_usage(snap, cfg.finops)[f"{P}.gov_raw.order_items"]
    assert u["full_scans"] == 42 and u["select_star"] == 21
    assert u["filters"]["created_at"] == 42 and u["filters"]["status"] == 21


def test_recommendations(snap, cfg):
    recs = {(r["type"], r["table"].split(".", 1)[1]): r for r in finops.recommendations(snap, cfg.finops)}
    p = recs[("partition", "gov_raw.order_items")]
    assert p["column"] == "created_at" and "DATE(created_at)" in p["ddl"] and p["addressable_usd"] > 0
    assert recs[("cluster", "gov_raw.order_items")]["columns"] == ["status"]
    assert ("select_star", "gov_raw.order_items") in recs
    assert ("unused", "gov_raw.legacy_product_snapshot") in recs
    assert ("unused", "gov_mart.daily_sales") not in recs           # dibaca lewat view -> bukan kandidat
    assert not any(t == "partition" and k.startswith("gov_mart") for t, k in recs)


def test_dq_checks(cfg, runner):
    res = dq.run(cfg, runner, previous={"dq": [{"table": f"{P}.gov_mart.daily_sales", "check": "row_count", "value": 10000}]}, now=NOW)
    st = {(r["table"].split(".", 1)[1], r["check"]): r for r in res}
    assert st[("gov_mart.daily_sales", "freshness_hours")]["status"] == "pass"
    assert st[("gov_mart.customer_ltv", "freshness_hours")]["status"] == "fail"       # 60 jam > 50
    assert st[("gov_mart.daily_sales", "null_pct:revenue")]["status"] == "warn"       # 0,2% di antara 0,1 dan 1
    assert st[("gov_mart.customer_ltv", "duplicate_keys")]["status"] == "fail"
    assert "row_change_pct" not in {c for _, c in st}                                  # tidak dikonfigurasi -> tidak dicek


def test_dq_guard_skips_large_scans(cfg, runner):
    big = type("Big", (FixtureRunner,), {"dry_run_bytes": lambda self, sql, params=None: 10 ** 13})(HERE / "fixtures")
    res = dq.run(cfg, big, now=NOW)
    assert {r["check"] for r in res if r["status"] == "warn"} == {"profile_skipped"}
    assert not any(c.startswith("dq__") for c in big.calls)                           # query mahal tidak pernah dijalankan


def test_describe_suggest_mask_and_review_gate(cfg, runner, snap, tmp_path):
    seen = {}

    def fake_llm(_cfg, prompt):
        seen["prompt"] = prompt
        cols = [c.split(":")[0].strip() for c in prompt.split("Columns (name: type): ")[1].split("\n")[0].split(",")]
        return "Sure! " + json.dumps({"table": "Customer attributes", "columns": {c: f"Meaning of {c}" for c in cols}})

    sug = describe.suggest(cfg, runner, snap, call=fake_llm)
    users = sug[f"{P}.gov_raw.users"]
    assert users["table"] == "" and users["reviewed"] is False                     # deskripsi tabel yang ada tidak ditimpa
    assert set(users["columns"]) == {"id", "age", "gender", "state", "city", "country", "traffic_source", "created_at"}
    assert f"{P}.gov_mart.daily_sales" not in sug                                   # sudah lengkap -> dilewati
    assert describe.mask("call +62 812-3456-7890 or a@b.co") == "call *** or ***"
    f = tmp_path / "d.yaml"; describe.write(sug, f)
    assert all(v["reviewed"] is False for v in yaml.safe_load(f.read_text()).values())


def test_report_and_cli_end_to_end(tmp_path):
    rc = cli.main(["-c", str(HERE / "config.test.yaml"), "--fixtures", str(HERE / "fixtures"), "-o", str(tmp_path), "run"])
    assert rc == 1                                                                   # ada DQ fail -> exit code 1 untuk CI
    rep = json.loads((tmp_path / "latest.json").read_text())
    assert rep["summary"]["tables"] == 9 and rep["summary"]["dq"]["fail"] == 2
    assert 0 < rep["summary"]["column_doc_pct"] < 100
    md = (tmp_path / "REPORT.md").read_text()
    assert "## Recommendations" in md and "partition" in md and "legacy_product_snapshot" in md


def test_published_report_has_no_emails(tmp_path):
    cli.main(["-c", str(HERE / "config.test.yaml"), "--fixtures", str(HERE / "fixtures"), "-o", str(tmp_path), "run"])
    text = (tmp_path / "latest.json").read_text() + (tmp_path / "REPORT.md").read_text()
    assert "@" not in text.replace("@days", "") and "user-" in text


def test_storage_falls_back_to_tables_api_when_forbidden(cfg):
    snap = collect.collect(cfg, FixtureRunner(HERE / "fixtures", forbidden={"storage"}))
    assert snap["meta"]["storage_source"] == "tables_api" and "metadataViewer" in snap["meta"]["warnings"][0]
    assert len(snap["storage"]) == 8 and all(r["long_term_logical_bytes"] == 0 for r in snap["storage"])
    rep = report.build(snap, [], cfg.finops)
    assert "Warning" in report.markdown(rep) and rep["recommendations"]         # analisis tetap jalan


def test_jobs_forbidden_gives_short_error(tmp_path, capsys, monkeypatch):
    real = FixtureRunner.__init__
    monkeypatch.setattr(FixtureRunner, "__init__", lambda self, folder, forbidden=None: real(self, folder, {"jobs"}))
    rc = cli.main(["-c", str(HERE / "config.test.yaml"), "--fixtures", str(HERE / "fixtures"), "-o", str(tmp_path), "run"])
    err = capsys.readouterr().err
    assert rc == 3 and "resourceViewer" in err and "Traceback" not in err


def test_storage_falls_back_when_not_enabled(cfg):
    r = FixtureRunner(HERE / "fixtures"); r.disabled = {"storage"}
    snap = collect.collect(cfg, r)
    w = snap["meta"]["warnings"][0]
    assert snap["meta"]["storage_source"] == "tables_api" and "enable_info_schema_storage" in w and "ALTER PROJECT `demo-project`" in w
    assert len(snap["storage"]) == 8


def test_metadata_is_one_query_per_kind_not_per_dataset(cfg):
    r = FixtureRunner(HERE / "fixtures"); collect.collect(cfg, r)
    assert r.calls == ["storage", "jobs", "tables", "columns", "views"]           # 5 query, berapa pun jumlah datasetnya


def test_storage_gaps_filled_from_tables_api(cfg):
    class Partial(FixtureRunner):                                                  # TABLE_STORAGE baru terisi sebagian
        def query(self, name, sql, params=None):
            rows = super().query(name, sql, params)
            return [x for x in rows if x["table"] not in ("order_items", "users")] if name == "storage" else rows
    snap = collect.collect(cfg, Partial(HERE / "fixtures"))
    assert snap["meta"]["storage_source"] == "information_schema+tables_api"
    assert "gov_raw.order_items" in snap["meta"]["warnings"][0] and len(snap["storage"]) == 8
    recs = {(x["type"], x["table"].split(".", 1)[1]) for x in finops.recommendations(snap, cfg.finops)}
    assert ("partition", "gov_raw.order_items") in recs                           # tidak hilang walau storage belum lengkap


def test_filter_inside_function_detected():
    assert finops._FILTER.findall("SELECT 1 FROM t WHERE DATE(created_at) = DATE '2024-01-01' AND status = 'x'") == ["created_at", "status"]
    assert finops._FILTER.findall("SELECT * FROM a JOIN b ON a.id = b.id WHERE b.x > 1") == ["x"]
