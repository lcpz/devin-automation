"""Live demo of the governed-evidence feature (lcpz/superset PRs #8-#12).

Runs inside the superset_app container (needs the MCP server on :5008 and the
web app on :8088). Prints a transcript and writes every raw response to
/tmp/governed_evidence_demo/*.json so the outputs can be inspected afterwards.

    docker cp governed_evidence_demo.py superset_app:/tmp/ && \
    docker exec superset_app /app/.venv/bin/python /tmp/governed_evidence_demo.py
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import UTC, datetime

import requests
from fastmcp import Client

logging.disable(logging.CRITICAL)

BASE = os.environ.get("SUPERSET_URL", "http://127.0.0.1:8088")
MCP = os.environ.get("MCP_URL", "http://127.0.0.1:5008/mcp")
USER, PASSWORD = "admin", "admin"
OUT = "/tmp/governed_evidence_demo"
os.makedirs(OUT, exist_ok=True)

DATASET_NAME = os.environ.get("DEMO_DATASET", "birth_names")


def step(title: str) -> None:
    print(f"\n=== {title} ===")


def save(name: str, obj: object) -> None:
    with open(f"{OUT}/{name}.json", "w") as fh:
        json.dump(obj, fh, indent=2, default=str)


def show(obj: object, keys: list[str] | None = None, limit: int = 1200) -> None:
    if keys and isinstance(obj, dict):
        obj = {k: obj[k] for k in keys if k in obj}
    text = json.dumps(obj, indent=2, default=str)
    print(text if len(text) <= limit else text[:limit] + "\n  ...")


def rest_session() -> requests.Session:
    s = requests.Session()
    tok = s.post(
        f"{BASE}/api/v1/security/login",
        json={"username": USER, "password": PASSWORD, "provider": "db"},
    ).json()["access_token"]
    s.headers["Authorization"] = f"Bearer {tok}"
    csrf = s.get(f"{BASE}/api/v1/security/csrf_token/").json()["result"]
    s.headers["X-CSRFToken"] = csrf
    s.headers["Referer"] = BASE
    return s


def canonical_sha256(evidence: dict) -> str:
    return hashlib.sha256(
        json.dumps(evidence, sort_keys=True, allow_nan=True).encode()
    ).hexdigest()


async def main() -> None:  # noqa: C901
    s = rest_session()
    async with Client(MCP, log_handler=lambda m: None) as mcp:

        async def tool(name: str, request: dict) -> dict:
            res = await mcp.call_tool(name, {"request": request})
            data = json.loads(res.content[0].text)
            save(f"mcp_{name}", data)
            return data

        # -- pick a dataset -------------------------------------------------
        step("0. Pick the dataset under governance")
        q = {"filters": [{"col": "table_name", "opr": "eq", "value": DATASET_NAME}]}
        ds = s.get(f"{BASE}/api/v1/dataset/", params={"q": json.dumps(q)}).json()
        dataset = ds["result"][0]
        ds_id, ds_uuid = dataset["id"], dataset["uuid"]
        print(f"dataset {DATASET_NAME}: id={ds_id} uuid={ds_uuid}")

        # -- PR3 (#10): reverse lineage -------------------------------------
        step("PR3 #10 - get_dataset_usage: who depends on this dataset?")
        usage = await tool(
            "get_dataset_usage", {"dataset_uuid": ds_uuid, "page_size": 5}
        )
        charts = usage.get("charts", {})
        show({k: charts.get(k) for k in ("count", "truncated", "page", "page_size")})
        print(
            "dashboards:",
            {k: usage.get("dashboards", {}).get(k) for k in ("count", "truncated")},
        )
        for c in charts.get("result", []):
            print(f"  chart {c['id']:>4}  {c['slice_name']!r:35} {c['viz_type']}")
        chart = charts["result"][0] if charts.get("result") else None

        # -- PR1 (#8): dataset identity on ChartInfo -----------------------
        step("PR1 #8 - get_chart_info carries dataset identity (dataset_uuid)")
        if chart:
            info = await tool("get_chart_info", {"identifier": chart["id"]})
            show(
                info,
                [
                    "id",
                    "uuid",
                    "slice_name",
                    "datasource_id",
                    "dataset_uuid",
                    "datasource_name",
                ],
            )
        else:
            print("no dependent chart found")

        # -- make a governed change so there is fresh history --------------
        step("Change: update the dataset description (creates a version + activity)")
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        r = s.put(
            f"{BASE}/api/v1/dataset/{ds_id}",
            json={"description": f"governed-evidence demo {stamp}"},
        )
        print("PUT /api/v1/dataset ->", r.status_code)

        # -- PR2 (#9): bounded versions/activity + disclosure via REST -----
        step("PR2 #9 - REST GET /dataset/<uuid>/versions/ (bounded, disclosed)")
        versions = s.get(
            f"{BASE}/api/v1/dataset/{ds_uuid}/versions/",
            params={"page": 0, "page_size": 3},
        ).json()
        save("rest_versions", versions)
        show(versions, ["count", "truncated", "page", "page_size", "retention"])
        if versions.get("result"):
            print("newest version:")
            show(versions["result"][0], limit=600)

        step("PR2 #9 - REST GET /dataset/<uuid>/activity/")
        activity = s.get(
            f"{BASE}/api/v1/dataset/{ds_uuid}/activity/",
            params={"page": 0, "page_size": 3, "include": "all"},
        ).json()
        save("rest_activity", activity)
        show(
            activity, ["count", "truncated", "page", "page_size", "retention", "window"]
        )
        if activity.get("result"):
            print("newest activity row:")
            show(activity["result"][0], limit=600)

        # -- PR4 (#11): per-asset version/activity MCP tools ----------------
        step("PR4 #11 - MCP get_dataset_versions / get_dataset_activity")
        mv = await tool("get_dataset_versions", {"uuid": ds_uuid, "page_size": 2})
        show(mv, ["asset", "count", "truncated", "page", "page_size", "retention"])
        ma = await tool(
            "get_dataset_activity", {"uuid": ds_uuid, "page_size": 2, "include": "all"}
        )
        show(
            ma,
            ["asset", "count", "truncated", "page", "page_size", "retention", "window"],
        )
        if chart:
            cv = await tool(
                "get_chart_versions", {"uuid": chart["uuid"], "page_size": 1}
            )
            print("dependent chart's own history (get_chart_versions):")
            show(cv, ["asset", "count", "truncated", "retention"])

        # -- PR5 (#12): migration evidence export with SHA-256 -------------
        step("PR5 #12 - REST GET /dataset/<uuid>/migration_evidence/")
        ev = s.get(
            f"{BASE}/api/v1/dataset/{ds_uuid}/migration_evidence/",
            params={"page": 0, "page_size": 5, "record_limit": 20},
        ).json()
        save("rest_migration_evidence", ev)
        evidence, digest = ev["evidence"], ev["digest"]
        print("top-level evidence keys:", sorted(evidence.keys()))
        show({"digest": digest})
        inv = evidence["inventory"]
        show(
            {
                "schema_version": evidence["schema_version"],
                "window": evidence["window"],
                "page": evidence["page"],
                "inventory": {
                    "charts": {
                        k: inv["charts"][k] for k in ("count", "page", "page_size")
                    },
                    "dashboards": {
                        k: inv["dashboards"][k] for k in ("count", "page", "page_size")
                    },
                },
                "assets_with_before/after+activity": [
                    {
                        "kind": a["kind"],
                        "name": a["name"],
                        "before": (a["before"] or {}).get("issued_at"),
                        "after": (a["after"] or {}).get("issued_at"),
                        "versions_in_window": a["versions_in_window"]["count"],
                        "activity_count": a["activity"]["count"],
                    }
                    for a in evidence["assets"]
                ],
                "report_executions": {
                    k: evidence["report_executions"][k] for k in ("count", "truncated")
                },
                "query_executions": {
                    k: evidence["query_executions"][k]
                    for k in ("authorized", "count", "truncated")
                },
                "coverage.complete": evidence["coverage"]["complete"],
                "coverage.retention": evidence["coverage"].get("retention"),
            },
            limit=3000,
        )
        print("coverage.notes:")
        for n in evidence["coverage"].get("notes", []):
            print("  -", n[:220] + ("..." if len(n) > 220 else ""))

        step("PR5 #12 - MCP export_dataset_migration_evidence")
        mev = await tool(
            "export_dataset_migration_evidence",
            {"dataset_uuid": ds_uuid, "page_size": 5},
        )
        show(mev, ["digest"])
        print("MCP evidence keys:", sorted(mev.get("evidence", {}).keys()))

        # -- verify the digest independently, then tamper ------------------
        step("Verify: recompute SHA-256 over the canonical evidence object")
        recomputed = canonical_sha256(evidence)
        print("server digest :", digest["value"])
        print("recomputed    :", recomputed)
        print("MATCH" if recomputed == digest["value"] else "MISMATCH")
        tampered = json.loads(json.dumps(evidence))
        tampered["inventory"]["charts"]["count"] -= 1
        print(
            "after tampering inventory.charts.count -> digest",
            canonical_sha256(tampered)[:16],
            "... (differs)",
        )
        print(
            "\nNote: the digest proves this evidence object was not altered after "
            "export; it does not attest that the live database is truthful."
        )
        print(f"\nraw outputs saved under {OUT}/")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print("DEMO FAILED:", repr(exc))
        sys.exit(1)
