"""Idempotently build the "Governed Evidence" dashboard in the local Superset demo.

It reads Superset's own metadata database (the versioning tables that
lcpz/superset PRs #8-#12 build on: version_transaction, version_changes,
tables_version, slices_version, dashboards_version) so technical users can see
in the UI what the REST/MCP evidence endpoints expose.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import requests

BASE = os.environ.get("SUPERSET_URL", "http://localhost:8088")
USER = os.environ.get("SUPERSET_USER", "admin")
PASSWORD = os.environ.get("SUPERSET_PASSWORD", "admin")
DB_NAME = "Superset Metadata (versioning)"
DB_URI = os.environ.get(
    "SUPERSET_META_URI", "postgresql://superset:superset@db:5432/superset"
)
DASH_TITLE = "Governed Evidence — Versioning, Lineage & Retention"
DASH_SLUG = "governed-evidence"
RETENTION_DAYS = int(os.environ.get("VERSION_HISTORY_DAYS", "30"))
DIGEST = os.environ.get("EVIDENCE_DIGEST", "")

s = requests.Session()
tok = s.post(
    f"{BASE}/api/v1/security/login",
    json={"username": USER, "password": PASSWORD, "provider": "db", "refresh": True},
).json()["access_token"]
s.headers["Authorization"] = f"Bearer {tok}"
s.headers["X-CSRFToken"] = s.get(f"{BASE}/api/v1/security/csrf_token/").json()["result"]
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

ASSET_NAMES = """
  SELECT 'dataset' AS kind, id, uuid::text AS uuid, table_name AS name FROM tables
  UNION ALL SELECT 'chart', id, uuid::text, slice_name FROM slices
  UNION ALL SELECT 'dashboard', id, uuid::text, dashboard_title FROM dashboards
"""

DATASETS = {
    "gov_activity": f"""
SELECT c.id AS change_id, t.issued_at, COALESCE(u.username, '(system)') AS changed_by,
       c.entity_kind, c.entity_id, a.name AS entity_name, a.uuid AS entity_uuid,
       c.kind, c.operation, c.path::text AS path,
       LEFT(c.from_value::text, 120) AS from_value,
       LEFT(c.to_value::text, 120) AS to_value,
       c.transaction_id
FROM version_changes c
JOIN version_transaction t ON t.id = c.transaction_id
LEFT JOIN ab_user u ON u.id = t.user_id
LEFT JOIN ({ASSET_NAMES}) a ON a.kind = c.entity_kind AND a.id = c.entity_id
""",
    "gov_versions": """
WITH v AS (
  SELECT 'dataset' AS kind, id, uuid::text AS uuid, table_name AS name,
         transaction_id, end_transaction_id, operation_type FROM tables_version
  UNION ALL SELECT 'chart', id, uuid::text, slice_name,
         transaction_id, end_transaction_id, operation_type FROM slices_version
  UNION ALL SELECT 'dashboard', id, uuid::text, dashboard_title,
         transaction_id, end_transaction_id, operation_type FROM dashboards_version
)
SELECT v.kind, v.id, v.uuid, v.name, v.transaction_id, t.issued_at,
       COALESCE(u.username, '(system)') AS changed_by,
       CASE v.operation_type WHEN 0 THEN 'insert' WHEN 1 THEN 'update'
            WHEN 2 THEN 'delete' END AS operation,
       (v.end_transaction_id IS NULL) AS is_current
FROM v JOIN version_transaction t ON t.id = v.transaction_id
LEFT JOIN ab_user u ON u.id = t.user_id
""",
    "gov_asset_summary": f"""
WITH v AS (
  SELECT 'dataset' AS kind, id, transaction_id FROM tables_version
  UNION ALL SELECT 'chart', id, transaction_id FROM slices_version
  UNION ALL SELECT 'dashboard', id, transaction_id FROM dashboards_version
)
SELECT a.kind, a.id, a.uuid, a.name,
       COUNT(v.transaction_id) AS versions_retained,
       MIN(t.issued_at) AS first_version_at, MAX(t.issued_at) AS last_version_at,
       COUNT(DISTINCT t.user_id) AS distinct_editors
FROM ({ASSET_NAMES}) a
LEFT JOIN v ON v.kind = a.kind AND v.id = a.id
LEFT JOIN version_transaction t ON t.id = v.transaction_id
GROUP BY a.kind, a.id, a.uuid, a.name
""",
    "gov_lineage": """
SELECT d.id AS dataset_id, d.uuid::text AS dataset_uuid, d.table_name AS dataset,
       s.id AS chart_id, s.uuid::text AS chart_uuid, s.slice_name AS chart, s.viz_type,
       db.id AS dashboard_id, db.dashboard_title AS dashboard
FROM tables d
JOIN slices s ON s.datasource_id = d.id AND s.datasource_type = 'table'
LEFT JOIN dashboard_slices ds ON ds.slice_id = s.id
LEFT JOIN dashboards db ON db.id = ds.dashboard_id
""",
    "gov_retention": f"""
SELECT {RETENTION_DAYS} AS version_history_days,
       NOW() - INTERVAL '{RETENTION_DAYS} days' AS history_begins_at,
       MIN(issued_at) AS oldest_retained_transaction,
       MAX(issued_at) AS newest_transaction,
       COUNT(*) AS transactions_retained,
       (SELECT COUNT(*) FROM version_changes) AS change_rows_retained,
       (MIN(issued_at) > NOW() - INTERVAL '{RETENTION_DAYS} days') AS nothing_pruned_yet
FROM version_transaction
""",
}
ds_ids: dict[str, int] = {}
for name, sql in DATASETS.items():
    ex = find("dataset", "table_name", name)
    if ex:
        s.put(
            f"{BASE}/api/v1/dataset/{ex['id']}", json={"sql": sql.strip()}
        ).raise_for_status()
        ds_ids[name] = ex["id"]
    else:
        r = s.post(
            f"{BASE}/api/v1/dataset/",
            json={
                "database": db_id,
                "schema": "public",
                "table_name": name,
                "sql": sql.strip(),
            },
        )
        if not r.ok:
            print(name, r.text, file=sys.stderr)
            r.raise_for_status()
        ds_ids[name] = r.json()["id"]
    s.put(f"{BASE}/api/v1/dataset/{ds_ids[name]}/refresh")
print("datasets", ds_ids)


def sql_metric(expr: str, label: str) -> dict:
    return {
        "expressionType": "SQL",
        "sqlExpression": expr,
        "label": label,
        "optionName": f"metric_{uuid.uuid4().hex[:8]}",
    }


def chart(name: str, viz: str, ds: str, params: dict, desc: str = "") -> int:
    params = {"datasource": f"{ds_ids[ds]}__table", "viz_type": viz, **params}
    return upsert(
        "chart",
        "slice_name",
        name,
        {
            "slice_name": name,
            "viz_type": viz,
            "datasource_id": ds_ids[ds],
            "datasource_type": "table",
            "params": json.dumps(params),
            "description": desc,
        },
    )


COUNT = sql_metric("COUNT(*)", "count")
charts: dict[str, int] = {}

charts["kpi_assets"] = chart(
    "Assets with version history",
    "big_number_total",
    "gov_asset_summary",
    {
        "metric": COUNT,
        "adhoc_filters": [
            {
                "clause": "WHERE",
                "expressionType": "SQL",
                "sqlExpression": "versions_retained > 0",
            }
        ],
        "subheader": "datasets + charts + dashboards",
    },
)
charts["kpi_versions"] = chart(
    "Versions retained",
    "big_number_total",
    "gov_versions",
    {"metric": COUNT, "subheader": "rows across *_version tables"},
)
charts["kpi_changes"] = chart(
    "Activity rows (field-level changes)",
    "big_number_total",
    "gov_activity",
    {"metric": COUNT, "subheader": "version_changes"},
)
charts["kpi_editors"] = chart(
    "Distinct editors",
    "big_number_total",
    "gov_versions",
    {"metric": sql_metric("COUNT(DISTINCT changed_by)", "editors")},
)
charts["retention"] = chart(
    "Retention disclosure (what /versions/ and /activity/ report)",
    "table",
    "gov_retention",
    {
        "query_mode": "raw",
        "all_columns": [
            "version_history_days",
            "history_begins_at",
            "oldest_retained_transaction",
            "newest_transaction",
            "transactions_retained",
            "change_rows_retained",
            "nothing_pruned_yet",
        ],
        "row_limit": 1,
        "table_timestamp_format": "%Y-%m-%d %H:%M",
    },
    "history_begins_at is the retention cutoff the API discloses: absence of "
    "history before it is not proof of no change.",
)
charts["activity"] = chart(
    "Activity feed — who changed what (newest first)",
    "table",
    "gov_activity",
    {
        "query_mode": "raw",
        "all_columns": [
            "issued_at",
            "changed_by",
            "entity_kind",
            "entity_name",
            "kind",
            "operation",
            "path",
            "from_value",
            "to_value",
            "transaction_id",
        ],
        "order_by_cols": ['["issued_at", false]'],
        "row_limit": 200,
        "table_timestamp_format": "%Y-%m-%d %H:%M:%S",
    },
)
charts["changes_by_kind"] = chart(
    "Changes by asset kind and operation",
    "echarts_timeseries_bar",
    "gov_activity",
    {
        "x_axis": "entity_kind",
        "groupby": ["operation"],
        "metrics": [COUNT],
        "stack": "Stack",
        "show_legend": True,
        "y_axis_title": "change rows",
        "rich_tooltip": True,
    },
)
charts["versions_per_day"] = chart(
    "Versions issued per day, by asset kind",
    "echarts_timeseries_bar",
    "gov_versions",
    {
        "x_axis": "issued_at",
        "time_grain_sqla": "P1D",
        "groupby": ["kind"],
        "metrics": [COUNT],
        "stack": "Stack",
        "show_legend": True,
        "y_axis_title": "versions",
        "rich_tooltip": True,
    },
)
charts["most_changed"] = chart(
    "Most-versioned assets",
    "echarts_timeseries_bar",
    "gov_asset_summary",
    {
        "x_axis": "name",
        "metrics": [sql_metric("SUM(versions_retained)", "versions")],
        "orientation": "horizontal",
        "row_limit": 12,
        "x_axis_sort": "versions",
        "x_axis_sort_asc": False,
        "adhoc_filters": [
            {
                "clause": "WHERE",
                "expressionType": "SQL",
                "sqlExpression": "versions_retained > 1",
            }
        ],
    },
)
charts["lineage_table"] = chart(
    "Reverse lineage — dataset → charts → dashboards (get_dataset_usage)",
    "table",
    "gov_lineage",
    {
        "query_mode": "raw",
        "all_columns": [
            "dataset",
            "dataset_uuid",
            "chart",
            "chart_uuid",
            "viz_type",
            "dashboard",
        ],
        "order_by_cols": ['["dataset", true]'],
        "row_limit": 500,
    },
    "Same relation the MCP tool get_dataset_usage and the migration-evidence "
    "inventory walk (chart.dataset_uuid from PR #8).",
)
charts["dependents"] = chart(
    "Dependent charts per dataset (blast radius)",
    "echarts_timeseries_bar",
    "gov_lineage",
    {
        "x_axis": "dataset",
        "metrics": [sql_metric("COUNT(DISTINCT chart_id)", "charts")],
        "orientation": "horizontal",
        "row_limit": 15,
        "x_axis_sort": "charts",
        "x_axis_sort_asc": False,
    },
)
print("charts", charts)


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


def markdown_row(rid: str, mid: str, code: str, height: int) -> dict:
    return {
        rid: {
            "type": "ROW",
            "id": rid,
            "children": [mid],
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        },
        mid: {
            "type": "MARKDOWN",
            "id": mid,
            "children": [],
            "parents": ["ROOT_ID", "GRID_ID", rid],
            "meta": {"width": 12, "height": height, "code": code},
        },
    }


digest_line = (
    f"Latest export digest (sha256, covers `evidence`): `{DIGEST}`"
    if DIGEST
    else "Run `governed_evidence_demo.py` to produce an export and its digest."
)
EVIDENCE_MD = f"""
#### How this maps to the shipped API (PRs #8–#12)

| Step | Surface | What it proves |
|---|---|---|
| Identity | `get_chart_info(...).dataset_uuid` (#8) | charts point at datasets by UUID, so evidence survives export/import |
| Bounded history | `GET /api/v1/dataset/<uuid>/versions/`, `/activity/` (#9) | `count`, `truncated`, `page`, `retention.history_begins_at` — the *Retention disclosure* table above |
| Reverse lineage | `get_dataset_usage` (#10) | the *Reverse lineage* table below, paginated with `truncated` |
| Agent tools | `get_dataset_versions`, `get_dataset_activity`, `get_chart_versions` … (#11) | same envelope plus an `asset` header |
| Evidence bundle | `GET /api/v1/dataset/<uuid>/migration_evidence/`, MCP `export_dataset_migration_evidence` (#12) | dependents + before/after snapshots + activity + report/SQL Lab executions + coverage notes, SHA-256 digested |

{digest_line}

The digest proves the exported bundle was not altered afterwards; it does **not** attest that this
database is truthful — the bundle says so itself in `coverage.notes`. Everything on this page is read
live from the same tables the endpoints read (`version_transaction`, `version_changes`, `*_version`).
"""  # noqa: E501

rows: list[tuple[str, dict]] = [
    (
        "HEADER-1",
        header("HEADER-1", "What is under version control — and how far back"),
    ),
    (
        "ROW-1",
        row(
            "ROW-1",
            [
                ("kpi_assets", 3, 40),
                ("kpi_versions", 3, 40),
                ("kpi_changes", 3, 40),
                ("kpi_editors", 3, 40),
            ],
        ),
    ),
    ("ROW-2", row("ROW-2", [("retention", 12, 25)])),
    (
        "HEADER-2",
        header("HEADER-2", "Activity — who changed what (what /activity/ returns)"),
    ),
    ("ROW-3", row("ROW-3", [("activity", 12, 70)])),
    (
        "ROW-4",
        row(
            "ROW-4",
            [
                ("changes_by_kind", 4, 50),
                ("versions_per_day", 4, 50),
                ("most_changed", 4, 50),
            ],
        ),
    ),
    (
        "HEADER-3",
        header(
            "HEADER-3", "Lineage — blast radius of a dataset change (get_dataset_usage)"
        ),
    ),
    ("ROW-5", row("ROW-5", [("dependents", 4, 60), ("lineage_table", 8, 60)])),
    (
        "HEADER-4",
        header(
            "HEADER-4", "Evidence — how the export is built and what the digest means"
        ),
    ),
    ("ROW-6", markdown_row("ROW-6", "MARKDOWN-1", EVIDENCE_MD.strip(), 60)),
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

dash_id = upsert(
    "dashboard",
    "slug",
    DASH_SLUG,
    {
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
                    "dataset": "#1FA8C9",
                    "chart": "#5AC189",
                    "dashboard": "#A868B7",
                    "add": "#5AC189",
                    "edit": "#FCC700",
                    "remove": "#E04355",
                },
            }
        ),
    },
)
for sid in charts.values():
    s.put(f"{BASE}/api/v1/chart/{sid}", json={"dashboards": [dash_id]})
print("dashboard", dash_id, f"{BASE}/dashboard/{DASH_SLUG}/")
