"""Idempotently build the "Devin Observability" database, datasets, charts and
dashboard in the local Superset demo via the REST API."""

from __future__ import annotations

import json
import os
import sys
import uuid

import requests

BASE = os.environ.get("SUPERSET_URL", "http://localhost:8088")
USER = os.environ.get("SUPERSET_USER", "admin")
PASSWORD = os.environ.get("SUPERSET_PASSWORD", "admin")
DB_NAME = "Devin Observability"
DB_URI = os.environ.get(
    "DEVIN_OBS_SUPERSET_URI", "postgresql://superset:superset@db:5432/devin_obs"
)
DASH_TITLE = "Devin Autonomous Delivery — Observability"
DASH_SLUG = "devin-observability"

s = requests.Session()
tok = s.post(
    f"{BASE}/api/v1/security/login",
    json={"username": USER, "password": PASSWORD, "provider": "db", "refresh": True},
).json()["access_token"]
s.headers["Authorization"] = f"Bearer {tok}"
csrf = s.get(f"{BASE}/api/v1/security/csrf_token/").json()["result"]
s.headers["X-CSRFToken"] = csrf
s.headers["Referer"] = BASE


def find(endpoint: str, col: str, val: str) -> dict | None:
    q = json.dumps({"filters": [{"col": col, "opr": "eq", "value": val}]})
    r = s.get(f"{BASE}/api/v1/{endpoint}/", params={"q": q}).json()
    return r["result"][0] if r.get("result") else None


def upsert(endpoint: str, col: str, val: str, payload: dict) -> int:
    ex = find(endpoint, col, val)
    if ex:
        r = s.put(f"{BASE}/api/v1/{endpoint}/{ex['id']}", json=payload)
        r.raise_for_status()
        return ex["id"]
    r = s.post(f"{BASE}/api/v1/{endpoint}/", json=payload)
    if not r.ok:
        print(endpoint, r.text, file=sys.stderr)
        r.raise_for_status()
    return r.json()["id"]


# --- database -----------------------------------------------------------------
db_id = upsert(
    "database",
    "database_name",
    DB_NAME,
    {
        "database_name": DB_NAME,
        "sqlalchemy_uri": DB_URI,
        "expose_in_sqllab": True,
        "allow_dml": False,
        "extra": json.dumps({"schemas_allowed_for_file_upload": []}),
    },
)
print("database", db_id)

# --- datasets (virtual, so panels stay readable) --------------------------------
DATASETS = {
    "obs_pr_board": """
SELECT number, title, url, branch, state, checks, remediation, approved,
       changes_requested, review_threads, unresolved_threads,
       array_to_string(failed_checks, ', ') AS failed_checks,
       created_at, updated_at, merged_at, last_commit_at, failed_at,
       last_devin_comment_at, last_human_review_at,
       CASE WHEN number IN (8,9,10,11,12) THEN 'Product story (PR1-5)'
            WHEN number IN (5,6) THEN 'Security scan fixes'
            WHEN number IN (3,4,13,14,15) THEN 'Automation & observability'
            ELSE 'Other' END AS workstream,
       CASE WHEN merged_at IS NOT NULL
            THEN EXTRACT(EPOCH FROM (merged_at - created_at))/3600.0
       END AS hours_to_merge
FROM devin_obs.pull_requests
WHERE author LIKE 'devin%%'
""",
    "obs_findings": """
SELECT f.key, f.kind, f.pr_number, f.pr_url, f.branch, f.detail, f.since,
       f.first_seen_at, f.last_seen_at, f.resolved_at, f.dispatched,
       CASE WHEN f.resolved_at IS NULL THEN 'open' ELSE 'resolved' END AS status,
       d.session_id, d.session_url, d.created_at AS dispatched_at
FROM devin_obs.findings f
LEFT JOIN devin_obs.dispatches d ON d.key = f.key
""",
    "obs_check_runs": """
SELECT c.check_run_id, c.pr_number, c.name, c.status, c.conclusion,
       c.started_at, c.completed_at, c.url,
       EXTRACT(EPOCH FROM (c.completed_at - c.started_at))/60.0 AS minutes,
       p.state AS pr_state
FROM devin_obs.check_runs c
JOIN devin_obs.pull_requests p ON p.number = c.pr_number
""",
    "obs_snapshots": """
SELECT snapshot_id, collected_at, repo, source, devin_api, pulls, open_pulls,
       failing_ci, sessions, acus_total, findings
FROM devin_obs.snapshots
""",
    "obs_pr_history": """
SELECT number, collected_at, state, checks, approved, unresolved_threads, remediation
FROM devin_obs.pull_request_history
""",
}
ds_ids: dict[str, int] = {}
for name, sql in DATASETS.items():
    ex = find("dataset", "table_name", name)
    payload = {
        "database": db_id,
        "schema": "devin_obs",
        "table_name": name,
        "sql": sql.strip(),
    }
    if ex:
        s.put(
            f"{BASE}/api/v1/dataset/{ex['id']}", json={"sql": sql.strip()}
        ).raise_for_status()
        ds_ids[name] = ex["id"]
    else:
        r = s.post(f"{BASE}/api/v1/dataset/", json=payload)
        if not r.ok:
            print(name, r.text, file=sys.stderr)
            r.raise_for_status()
        ds_ids[name] = r.json()["id"]
    s.put(f"{BASE}/api/v1/dataset/{ds_ids[name]}/refresh")
print("datasets", ds_ids)


# --- charts -------------------------------------------------------------------
def metric(col: str, agg: str, label: str) -> dict:
    return {
        "expressionType": "SIMPLE",
        "column": {"column_name": col},
        "aggregate": agg,
        "label": label,
        "optionName": f"metric_{uuid.uuid4().hex[:8]}",
    }


def sql_metric(expr: str, label: str) -> dict:
    return {
        "expressionType": "SQL",
        "sqlExpression": expr,
        "label": label,
        "optionName": f"metric_{uuid.uuid4().hex[:8]}",
    }


def chart(name: str, viz: str, ds: str, params: dict, desc: str = "") -> int:
    params = {"datasource": f"{ds_ids[ds]}__table", "viz_type": viz, **params}
    payload = {
        "slice_name": name,
        "viz_type": viz,
        "datasource_id": ds_ids[ds],
        "datasource_type": "table",
        "params": json.dumps(params),
        "description": desc,
    }
    return upsert("chart", "slice_name", name, payload)


COUNT = sql_metric("COUNT(*)", "count")
charts: dict[str, int] = {}

charts["kpi_open"] = chart(
    "Open Devin PRs",
    "big_number_total",
    "obs_pr_board",
    {
        "metric": COUNT,
        "adhoc_filters": [
            {
                "clause": "WHERE",
                "expressionType": "SQL",
                "sqlExpression": "state = 'open'",
            }
        ],
        "subheader": "awaiting CI, review or merge",
    },
)
charts["kpi_merged"] = chart(
    "Merged Devin PRs",
    "big_number_total",
    "obs_pr_board",
    {
        "metric": COUNT,
        "adhoc_filters": [
            {
                "clause": "WHERE",
                "expressionType": "SQL",
                "sqlExpression": "state = 'merged'",
            }
        ],
        "subheader": "human-approved, CI green",
    },
)
charts["kpi_failing"] = chart(
    "Open PRs with failing CI",
    "big_number_total",
    "obs_pr_board",
    {
        "metric": COUNT,
        "adhoc_filters": [
            {
                "clause": "WHERE",
                "expressionType": "SQL",
                "sqlExpression": "state = 'open' AND checks = 'failure'",
            }
        ],
        "subheader": "red = needs a Devin fix session",
    },
)
charts["kpi_gaps"] = chart(
    "Unattended gaps (findings)",
    "big_number_total",
    "obs_findings",
    {
        "metric": COUNT,
        "adhoc_filters": [
            {
                "clause": "WHERE",
                "expressionType": "SQL",
                "sqlExpression": "status = 'open'",
            }
        ],
        "subheader": "CI/review signals no one acted on",
    },
)
HOURS_PER_PR = 2
charts["kpi_ets"] = chart(
    "Engineering time saved (h)",
    "big_number_total",
    "obs_pr_board",
    {
        "metric": sql_metric(f"COUNT(*) * {HOURS_PER_PR}", "hours saved"),
        "adhoc_filters": [
            {
                "clause": "WHERE",
                "expressionType": "SQL",
                "sqlExpression": "state = 'merged'",
            }
        ],
        "y_axis_format": ",d",
        "subheader": f"{HOURS_PER_PR} h per merged PR/task",
    },
    f"Engineering time saved: {HOURS_PER_PR} hours per merged Devin PR.",
)
charts["board"] = chart(
    "PR status board",
    "table",
    "obs_pr_board",
    {
        "query_mode": "raw",
        "all_columns": [
            "number",
            "workstream",
            "title",
            "state",
            "checks",
            "remediation",
            "unresolved_threads",
            "failed_checks",
            "updated_at",
        ],
        "order_by_cols": ['["number", false]'],
        "row_limit": 100,
        "table_timestamp_format": "%Y-%m-%d %H:%M",
        "show_cell_bars": False,
        "conditional_formatting": [
            {
                "column": "checks",
                "operator": "=",
                "targetValue": "failure",
                "colorScheme": "#EFA1AA",
            },
            {
                "column": "checks",
                "operator": "=",
                "targetValue": "success",
                "colorScheme": "#ACE1C4",
            },
        ],
    },
    "One row per Devin PR: what state it is in and what the next remediation step is.",
)
charts["remediation"] = chart(
    "PRs by remediation state",
    "pie",
    "obs_pr_board",
    {
        "groupby": ["remediation"],
        "metric": COUNT,
        "donut": True,
        "show_labels": True,
        "label_type": "key_value",
        "color_scheme": "supersetColors",
    },
)
charts["workstream"] = chart(
    "PRs by workstream and state",
    "echarts_timeseries_bar",
    "obs_pr_board",
    {
        "x_axis": "workstream",
        "groupby": ["state"],
        "metrics": [COUNT],
        "stack": "Stack",
        "x_axis_sort_asc": True,
        "show_legend": True,
        "y_axis_title": "PRs",
        "adhoc_filters": [],
        "orientation": "vertical",
        "rich_tooltip": True,
    },
)
charts["ci_pass"] = chart(
    "CI check conclusions per PR",
    "echarts_timeseries_bar",
    "obs_check_runs",
    {
        "x_axis": "pr_number",
        "groupby": ["conclusion"],
        "metrics": [COUNT],
        "stack": "Stack",
        "show_legend": True,
        "y_axis_title": "check runs",
        "x_axis_sort_asc": True,
        "rich_tooltip": True,
        "adhoc_filters": [
            {
                "clause": "WHERE",
                "expressionType": "SQL",
                "sqlExpression": "conclusion IS NOT NULL",
            }
        ],
    },
)
charts["ci_minutes"] = chart(
    "Slowest CI checks (avg minutes)",
    "echarts_timeseries_bar",
    "obs_check_runs",
    {
        "x_axis": "name",
        "metrics": [sql_metric("AVG(minutes)", "avg minutes")],
        "orientation": "horizontal",
        "row_limit": 12,
        "timeseries_limit_metric": None,
        "x_axis_sort": "avg minutes",
        "x_axis_sort_asc": False,
        "y_axis_format": ",.1f",
        "adhoc_filters": [
            {
                "clause": "WHERE",
                "expressionType": "SQL",
                "sqlExpression": "conclusion = 'success' AND minutes IS NOT NULL",
            }
        ],
    },
)
charts["findings"] = chart(
    "Gap findings & dispatches",
    "echarts_timeseries_bar",
    "obs_findings",
    {
        "x_axis": "kind",
        "groupby": ["status"],
        "metrics": [COUNT],
        "stack": "Stack",
        "orientation": "horizontal",
        "show_legend": True,
        "show_value": True,
        "y_axis_title": "findings",
        "x_axis_sort_asc": True,
        "rich_tooltip": True,
        "color_scheme": "supersetColors",
    },
    "Signals the event-driven automations missed; dispatched=true means a Devin "
    "session was started idempotently.",
)
charts["snapshots"] = chart(
    "Collector snapshots (health)",
    "table",
    "obs_snapshots",
    {
        "query_mode": "raw",
        "all_columns": [
            "collected_at",
            "source",
            "devin_api",
            "pulls",
            "open_pulls",
            "failing_ci",
            "sessions",
            "findings",
        ],
        "row_limit": 50,
        "order_by_cols": ['["collected_at", false]'],
        "table_timestamp_format": "%Y-%m-%d %H:%M",
    },
)
charts["throughput"] = chart(
    "PRs opened vs merged per day",
    "echarts_timeseries_bar",
    "obs_pr_board",
    {
        "x_axis": "created_at",
        "time_grain_sqla": "P1D",
        "groupby": ["state"],
        "metrics": [COUNT],
        "stack": "Stack",
        "show_legend": True,
        "y_axis_title": "PRs",
        "rich_tooltip": True,
    },
)
print("charts", charts)


# --- dashboard ----------------------------------------------------------------
def cid(k: str) -> str:
    return f"CHART-{k}"


def chart_node(k: str, width: int, height: int, parent: str) -> dict:
    return {
        "type": "CHART",
        "id": cid(k),
        "children": [],
        "parents": ["ROOT_ID", "GRID_ID", parent],
        "meta": {
            "chartId": charts[k],
            "width": width,
            "height": height,
            "uuid": str(uuid.uuid4()),
            "sliceName": None,
        },
    }


def row(rid: str, keys: list[tuple[str, int, int]]) -> dict:
    return {
        rid: {
            "type": "ROW",
            "id": rid,
            "children": [cid(k) for k, _, _ in keys],
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        },
        **{cid(k): chart_node(k, w, h, rid) for k, w, h in keys},
    }


def header(hid: str, text: str) -> dict:
    return {
        hid: {
            "type": "HEADER",
            "id": hid,
            "children": [],
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {
                "text": text,
                "headerSize": "MEDIUM_HEADER",
                "background": "BACKGROUND_TRANSPARENT",
            },
        }
    }


rows: list[tuple[str, dict]] = [
    ("HEADER-1", header("HEADER-1", "Is it working? — status at a glance")),
    (
        "ROW-1",
        row(
            "ROW-1",
            [
                ("kpi_open", 2, 40),
                ("kpi_merged", 2, 40),
                ("kpi_failing", 3, 40),
                ("kpi_gaps", 3, 40),
                ("kpi_ets", 2, 40),
            ],
        ),
    ),
    (
        "HEADER-2",
        header(
            "HEADER-2",
            "Delivery — the 5-PR product story, security fixes and automation PRs",
        ),
    ),
    ("ROW-2", row("ROW-2", [("board", 8, 75), ("remediation", 4, 75)])),
    ("ROW-3", row("ROW-3", [("workstream", 6, 50), ("throughput", 6, 50)])),
    (
        "HEADER-3",
        header(
            "HEADER-3",
            "Signals — CI health and unattended gaps the periodic review closes",
        ),
    ),
    ("ROW-4", row("ROW-4", [("ci_pass", 6, 50), ("ci_minutes", 6, 50)])),
    ("ROW-5", row("ROW-5", [("findings", 7, 45), ("snapshots", 5, 45)])),
]
position = {
    "DASHBOARD_VERSION_KEY": "v2",
    "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
    "GRID_ID": {
        "type": "GRID",
        "id": "GRID_ID",
        "children": [r for r, _ in rows],
        "parents": ["ROOT_ID"],
    },
    "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": DASH_TITLE}},
}
for _, nodes in rows:
    position.update(nodes)

dash_payload = {
    "dashboard_title": DASH_TITLE,
    "slug": DASH_SLUG,
    "published": True,
    "position_json": json.dumps(position),
    "json_metadata": json.dumps(
        {
            "color_scheme": "supersetColors",
            "refresh_frequency": 0,
            "expanded_slices": {},
            "label_colors": {
                "open": "#1FA8C9",
                "merged": "#5AC189",
                "closed": "#A0A0A0",
                "success": "#5AC189",
                "failure": "#E04355",
                "pending": "#FCC700",
                "resolved": "#5AC189",
                "open": "#E04355",
            },
        }
    ),
}
dash_id = upsert("dashboard", "slug", DASH_SLUG, dash_payload)
# attach charts to the dashboard
for sid in charts.values():
    s.put(f"{BASE}/api/v1/chart/{sid}", json={"dashboards": [dash_id]})
print("dashboard", dash_id, f"{BASE}/superset/dashboard/{DASH_SLUG}/")
