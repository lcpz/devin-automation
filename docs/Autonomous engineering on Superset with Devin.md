# Autonomous engineering on Superset with Devin

---

## 1. WHAT: problem, what we built, why it matters

### The leadership question

"If I were an engineering leader, how would I know this is working?" An autonomous coding agent is only
useful if (a) humans stay in control of what lands, and (b) its output is measurable with the same
signals used for any team: work opened and merged, CI health, review latency, security posture, and
whether anything falls through the cracks. The work below was organised around producing those signals
as a by-product of doing real engineering on Superset, not as a separate reporting exercise.

### What we implemented

**A governed delivery loop.** Work enters through GitHub only: a human opens an issue from a task
template, or applies a `devin:ready` label. Devin implements the task on a `devin/*` branch and opens
exactly one pull request. A second automation watches every Devin PR for human review comments,
changes-requested reviews, `@devin` mentions and failed checks, and fixes or replies in-thread. Devin
never merges, approves, or enables auto-merge; the branch ruleset on `master` requires green CI and one
human approval with no bypass. Every automation runs as the repository owner, not as an organisation
identity, which keeps this experiment fast and personal.

**A measurement layer that proves the loop is closed.** Event delivery is never perfect: webhooks drop,
automations rate-limit, sessions stop early. So a periodic collector records the state of every Devin
PR (checks, review threads, Devin activity, dispatch markers), classifies each PR (awaiting review,
awaiting Devin, failed CI, pending CI, merged, closed), derives only the gaps the event-driven path
could not see, and stores everything in a Postgres schema that Superset reads. A scheduled Devin
review reads the same data, re-verifies each gap against the repository, and dispatches one bounded fix
session per PR, idempotently. This is the difference between "we have automation" and "we can show
the automation is working".

**A product feature set on Superset's versioning and lineage.**

*The gap this addresses.* Superset connects to a warehouse, lets users define datasets, metrics and
SQL, turns those into charts and dashboards that people use to run operations, and may execute
scheduled reports and alerts. Along the way it stores a great deal of metadata about itself: dashboard
and chart definitions, saved queries, users, execution records and action logs. But that information
lives in different places with different rules. Asset history has limited retention and capture scope;
query history is kept apart from asset history and execution evidence; datasets and the charts that
depend on them are related internally and over REST but have no equivalent tools for an AI agent; and
import/export was designed to move assets between environments, not to produce reviewable evidence of a
change. So when something important changes, an engineer ends up opening several pages and correlating
entries by object id, user and timestamp to answer basic questions: what was the previous definition,
who changed it, which query ran and did it succeed, which dashboards depend on the changed dataset, is
the history I am looking at complete, and could another reviewer retrieve the same entries and reach
the same conclusion. The scope is deliberately Superset's own metadata; changes inside the connected
databases are out of scope.

*What was built.* Five capabilities, specified as one story and delivered through the loop above:

- Dataset identity on chart information: a stable `dataset_uuid` next to the internal integer id, so a
  chart can be followed to its dataset across export, import and environment moves.
- Paginated version and activity history that says when it is incomplete (`count`, `truncated`) and
  how far back it is guaranteed to reach (`retention`), so "no change" is never confused with "pruned".
- Reverse lineage: which charts and dashboards depend on a dataset, filtered to what the caller may
  see and bounded in the same way.
- Per-asset version and activity tools for agents, returning the same envelope as the REST routes plus
  an asset header, so a person, a script and an assistant read identical evidence.
- A bounded migration-evidence export: inventory of dependents, before/after snapshots, in-window
  activity and executions, a coverage statement, and a SHA-256 digest the reviewer can recompute.

*The scenario it serves.* A platform team wants to retire a widely used dataset and move its charts and
dashboards to a replacement. Before the migration, Superset lists the dependent assets and preserves
their current definitions; an assistant can prepare the inventory and flag remaining dependencies while
people decide how and when to update each asset. During the migration, Superset shows which assets
changed, who changed them and whether anything in scope still points at the old dataset, each statement
linked to the relevant versions, relationships and activity entries, with any retention or result limit
disclosed. Afterwards, a second reviewer verifies the migration from the same metadata instead of
reconstructing it from chat messages, tickets and screenshots.

*Why it matters.* Lower operational cost, because versions, user actions and executions are brought
together and incident reviews and audit preparation stop being a manual correlation exercise.
Coordinated change to shared assets, because dependents are known before a dataset is updated or
retired. And scalable AI-assisted review, because machine-readable, bounded, access-filtered metadata
lets assistants triage and draft explanations for many more events, while every conclusion links to
specific definitions, executions and actions a person can inspect.

**Two verified security issues found and fixed.** The security scans were run as part of exploring the
Superset codebase before the product work began, and they are a good example of what an autonomous
agent can help with: finding, verifying and fixing an issue as one continuous piece of work.

*How they were found.* We ran two Devin Security Swarm scans on the fork. The first was a generic
security scan. The second used a scan profile written for this codebase, `Superset indirect
object access`, whose stated objective is "backend authorization bypass via relationships or alternate
endpoints in Superset objects, evaluated against `SECURITY.md`". Instead of looking for generic
vulnerability patterns, the profile asks one question: can a non-admin user read or modify a protected
object through a side door (a related object, or a different endpoint) that the role and capability
matrix in `SECURITY.md` does not entitle them to? It sets the bar for a finding accordingly: name the
exact principal assumed, the matrix row violated, a direct negative control (the front door that does
refuse), the indirect request path, and the data exposed or the mutation achieved. It tells the swarm
to evaluate route decorators, DAO ownership filters and object-level checks together rather than in
isolation, and not to flag a missing per-object check on resources where the codebase deliberately
relies on route-level permissions (tags, reports, annotations, CSS templates). It scopes the search to
backend routes, commands, DAOs, serialisers and relationships under `superset/`, excludes tests,
migrations, frontend and docs, and rules out intended chart/dashboard data access, authorised guest
access, Admin and operator behaviour, compromise of trusted backends and harmless enumeration. Findings
sharing one root cause are aggregated, severity follows demonstrated impact, and neighbouring topics
(row-level-security or guest-token bypass, query-context datasource substitution) are left to separate
profiles. In short, the profile encodes the repository's own security model and the reporting
requirements its `AGENTS.md` places on automated tooling, so that what comes out is testable against the
published matrix rather than speculative. The two issues below were the confirmed high-severity results.

Some background for both issues. Superset ships with a small set of built-in roles. `Admin` is fully
trusted and is the only role allowed to register or manage connections to external data sources.
`Alpha` and `Gamma` are the everyday analyst roles: they can build charts and dashboards, but only
over data they have been explicitly granted. `SECURITY.md` writes these rules down as a table of "who
may do what" (the role and capability matrix); a security issue is anything that lets a role do
something its row in that table does not allow.

*Issue 1: ordinary users could create a new data connection (finding `sfind-5be55ac7…`, high).*
Superset has a newer feature called a semantic layer: a saved configuration that tells Superset how to
reach an external data system and what it exposes. Functionally it is a data connection, and the
matrix says only `Admin` may create those. Superset derives each role's permissions automatically from
the list of screens and API objects it knows about, and keeps a separate list of the objects that must
stay admin-only. The two semantic-layer objects had never been added to that admin-only list, so the
automatic derivation quietly gave `Gamma` and `Alpha` permission to create and edit them. Any analyst
could therefore send a request to the semantic-layer API with a connection configuration of their
choosing; the server checked that the request was well-formed, but not who was sending it. Because
Superset later contacts whatever address that configuration points to, an analyst could also make the
server issue outbound requests to a destination they controlled. *Fix:* the two semantic-layer objects
were added to the admin-only list, so after permissions are regenerated `Gamma` and `Alpha` can still
read the objects they are allowed to see, while creating and editing is reserved for `Admin`, matching
the matrix. Tests assert the classification of both objects.

*Issue 2: re-pointing a saved query bypassed the data-access check (finding `sfind-6e3340aa…`, high).*
A virtual dataset is a saved SQL query that Superset treats like a table. Because the SQL can reference
any table, Superset must check, whenever the SQL is saved, that the author is allowed to read every
table it touches. That check ran only when the SQL text itself changed. But the SQL is interpreted
relative to a binding: the database, catalog and schema (namespace) the dataset points at. A query
such as `SELECT * FROM t` means a different physical table depending on that binding. A user with
rights to edit datasets, but data access to only one schema, could write the query against the schema
they were allowed to see, then edit the dataset to point at a different schema while leaving the SQL
untouched. No check ran, because the text had not changed; at query time the unqualified `t` now
resolved to a table in a schema they had never been granted, and the query-time gate passed because
the user owned the dataset. This violates the "Read data: only granted datasets" row for such a role.
*Fix:* the data-access check now runs whenever either the SQL or its binding changes, evaluating the
SQL against the new binding. Regression tests cover both directions: re-pointing to a schema the user has not been granted is
rejected, and a purely descriptive edit (description, owners) still does not trigger a SQL check.

*Why this matters.* The scanner and the fixer are the same actor. Each finding arrived with the violated
matrix row, the assumed principal, an attack path and a proposed fix; the remediation sessions checked
the documented capability matrix (rejecting an earlier assumption that Alpha should keep write access),
wrote the fix and the tests, and opened PRs that went through the same CI and human approval as any
other change. Nothing was lost between "security report" and "engineering backlog".

**Two Superset dashboards.** The observability dashboard answers the leadership question (open, merged,
red CI, unattended gaps, engineering time saved, PR status board, CI conclusions, dispatch audit, collector
health including whether the Devin API was reachable). The governed-evidence dashboard shows what the
product work itself delivers, read directly from Superset's own metadata and versioning tables.

---

## 2. HOW: architecture and flow, and how to demo it

### The hybrid architecture

Two independent paths, joined by GitHub and by the observability database. The event path does the
work; the periodic path proves it and recovers what the event path missed.

```text
                         HUMANS (GitHub)
   open task-spec issue | apply devin:ready | review PR | approve + merge
                                 |
                                 v
 +-------------------------------------------------------------------+
 |                    EVENT-DRIVEN PATH (Devin Automations)          |
 |  github:issues ---------> "implement task" session -> devin/* PR  |
 |  github:pull_request_review / review_comment / issue_comment /    |
 |  github:check_run (failed) -> "fix + reply in thread" session     |
 +-------------------------------+-----------------------------------+
                                 |
                                 v
                    CI (GitHub Actions)  +  Devin Review  +  human approval
                    branch ruleset on master: CI green + 1 approval, no bypass
                                 |
                                 v
                              master
                                 |
   ==============================+====================================
                                 |
 +-------------------------------v-----------------------------------+
 |                PERIODIC PATH (every 6 h, GitHub Actions)          |
 |  collector: PRs, check runs, review threads, Devin comments,      |
 |             Devin sessions + automations (v3 API), dispatch marks |
 |     |-> classify PRs, derive gaps (grace period, activity-aware)  |
 |     |-> status-board issue comment + JSON artifact                |
 |     '-> Postgres schema devin_obs (8 tables)                      |
 +-------------------------------+-----------------------------------+
                                 |
              +------------------+------------------+
              v                                     v
   Superset "Devin Observability"        Scheduled Devin review (every 6 h)
   dashboard (KPIs, PR board, CI,        reads the snapshot, re-verifies each gap
   findings, dispatch audit, health)     against the repo, dispatches at most one
                                         bounded fix session per PR, idempotent
                                         via a marker comment written first
```

Reading the diagram top to bottom: a human creates demand; Devin turns it into a PR; GitHub enforces
the gate; the collector records what happened; Superset makes it visible; and a scheduled Devin session
acts only on what the event path demonstrably missed. Governance never moves out of GitHub.

Notable design points:

- **Idempotent dispatch.** The dispatcher writes a hidden marker comment on the PR before it creates a
  session, then rewrites the comment with the session URL. A failed session creation cannot leave a
  marker that hides the gap forever; markers expire.
- **Activity-aware suppression.** A gap is not a gap if a recent commit, an active Devin session, or a
  Devin progress comment shows the event path is already on it. Grace periods are anchored on the failed
  check, not on the PR.
- **Graceful degradation.** If the Devin API key is absent or rejected, the collector still records
  GitHub state and flags `devin_api=false` in the snapshot, so the dashboard shows the degradation rather
  than an empty chart.
- **Verify before acting.** The scheduled review is a Devin session, not a retry loop: on 2026-09-03 at
  06:30 UTC it found a red PR, checked that the same check was red on `master`, left a marker and made no
  push.

### Demo 1: the delivery loop and the observability dashboard (UI)

**Architectural decisions**

- **Humans fire, Devin executes.** Every entry point is a human act (an issue template, a label, a
  review comment, a failed check). Events raised by the Devin GitHub App never trigger automations, so
  the system cannot feed itself.
- **Event-driven first, schedule as safety net.** Native Devin automations react to GitHub events in
  seconds; the six-hourly review only handles what the events missed. The two never dispatch for the
  same gap because the collector marks every dispatch in the PR itself.
- **GitHub Actions writes, Devin reads.** Collection runs in the repository's own CI on a schedule and
  persists to Postgres; the Devin schedule reads that data rather than re-polling GitHub. Measurement
  and action are separate components with separate credentials.
- **PR-centric, timestamped observability.** The unit of record is the pull request, every snapshot
  carries its collection time, and dashboards only read the database. Nothing on screen is typed in.
- **Idempotent remediation.** One session per PR per run, a marker comment written before the session
  is created, so a failed dispatch cannot double-charge and a rerun cannot double-dispatch.
- **Governance stays in GitHub.** Branch protection, required CI and human approval are unchanged; the
  automation never merges.

1. **GitHub, Issues, New, "Devin task" template.** Show the task-spec form. Say: only a human can fire
   this; events raised by the Devin GitHub App never trigger automations.
2. **Devin, Automations page.** Four entries (section 2b). Open the PR feedback one and read the four
   trigger types.
3. **A recent Devin PR.** Devin Review comments, Devin's per-thread replies, the `risk:*` label, the CI
   list, the human approval. On an older PR, the hidden `devin-obs` marker comment: the audit trail
   survives the merge.
4. **GitHub Actions, the status-board run** (section 2c).
5. **The scheduled review session** (06:30 UTC on 2026-09-03): read the final message, an evidence-based
   no-op.
6. **Superset, "Devin Autonomous Delivery: Observability" dashboard**, top to bottom:
   - KPI row: open, merged, open with failing CI, unattended gaps, engineering time saved (2 hours
     per merged PR or task).
   - PR status board filtered by workstream (product story, security fixes, automation): every row
     merged or closed.
   - Collector snapshot health: two snapshots side by side (10:59 UTC with 8 open PRs and 3 findings;
     12:00 UTC with 0 and 0). The delta is the story.
   - Findings by kind, coloured by status (green resolved, red open).
   - Devin sessions and their PRs: one row per session (code-scan swarm sessions excluded), each
     linking to the session in Devin and to every pull request it produced.
7. **SQL Lab** on the "Devin Observability" connection: select a few columns from
   `devin_obs.pull_requests`. Say: this is the same database the collector writes; nothing here is typed in.

### Demo 2: the product itself, the governed-evidence story

The observability dashboard shows *that* the work shipped; this shows *what* shipped. The story: "I want
to change the `birth_names` dataset. Who breaks, what changed, and can I hand a reviewer proof they can
verify?"

**Architectural decisions**

- **UUIDs as the public identity.** Charts and datasets expose `dataset_uuid`/`chart_uuid` next to the
  legacy integer ids; the versioning routes take UUIDs only, so identity survives export, import and
  environment moves.
- **Bounded collections everywhere.** Every list carries `count`, `truncated` and `page`; nothing is
  silently cut, and the evidence bundle is bounded before it is hashed so the digest covers exactly what
  the reviewer sees.
- **Retention is disclosed, not assumed.** The `retention` block (`version_history_days`,
  `pruning_enabled`, `history_begins_at`) travels with every history response, so "no change" is never
  confused with "pruned"; the pruning flag reflects the actual schedule, not the config alone.
- **One contract for REST and MCP.** The MCP tools return the same envelope as the REST routes plus an
  `asset` header, so a human, a script and an agent read identical evidence.
- **No new privileges.** Routes reuse the existing `can_read` on Dataset and per-object access checks;
  the inventory excludes assets the caller cannot read.
- **Tamper evidence, not truth claims.** The SHA-256 digest proves the bundle was not altered after
  export and states its own coverage (`coverage.complete`); it does not claim the database is truthful.
- **The dashboard reads the same tables as the API.** The UI counterpart is built on Superset's own
  metadata and versioning tables, so the JSON and the panels are visibly the same evidence.

**Terminal flow:**

| Step | Call | What to point at |
|---|---|---|
| Reverse lineage | MCP `get_dataset_usage(dataset_uuid)` | `charts.count=11, truncated=true, page_size=5`, `dashboards.count=1` ("births"). It says it is incomplete rather than silently cutting the list. |
| Identity | MCP `get_chart_info(55)` | `dataset_uuid` next to the legacy integer `datasource_id`. UUIDs survive export, import and environment moves. |
| A governed change | `PUT /api/v1/dataset/17 {description}` | An ordinary edit; everything below is produced from it automatically. |
| Bounded history | `GET .../dataset/<uuid>/versions/?page_size=3` and `/activity/` | `count`, `truncated`, `page`, and a `retention` block (`version_history_days=30`, `pruning_enabled`, `history_begins_at`). Newest activity row is the edit, with `changed_by`, `path`, `from_value`, `to_value`. |
| Same contract for agents | MCP `get_dataset_versions`, `get_dataset_activity`, `get_chart_versions` | Identical envelope plus an `asset` header (`kind/id/uuid/name`); the model never guesses which object the history belongs to. |
| Evidence bundle | `GET .../migration_evidence/` and MCP `export_dataset_migration_evidence` | `inventory` (11 charts, 1 dashboard), `assets[]` with before/after snapshots and activity, `report_executions`, `query_executions`, `coverage.complete=false` with notes, `digest = {sha256, covers: evidence}`. |
| Verify | `sha256(json.dumps(evidence, sort_keys=True))` | `MATCH` with the server digest; flip one count and it differs. The digest proves the bundle was not altered after export; it does not prove the database is truthful, and the bundle says so. |

Governance properties to name: every collection is bounded before hashing so the digest covers exactly
what the reviewer sees; retention is disclosed so "no change" is never confused with "pruned"; access is
enforced per object (the inventory excludes assets the caller cannot read); the REST route is a normal
`can_read` on Dataset, no new privilege was invented.

**UI counterpart: the "Governed Evidence: Versioning, Lineage & Retention" dashboard.** For an
audience that will not read JSON. It reads Superset's own metadata and versioning tables (the same rows
the endpoints read), so it is evidence, not a mock-up. Walk it top to bottom:

- **KPI row** (observed 2026-09-03): 170 assets with version history, 222 retained version rows,
  424 field-level change rows, 2 distinct editors. Proves versioning is on for the whole instance, not
  just the demo dataset.
- **Retention disclosure**: 30-day window, retention cutoff, oldest and newest retained transaction,
  `nothing_pruned_yet=true`. This is the `retention` block from the API, as a panel; it is what the
  schedule-aware pruning fix keeps honest.
- **Activity feed**: who changed what. After the terminal step "A governed change", refresh: the
  description edit is the top row with `path`, `from_value`, `to_value`.
- **Versions by day** and **most-versioned assets**: where change concentrates; `birth_names` climbs by
  one after the edit.
- **Reverse lineage and blast radius**: dataset to chart to dashboard with `dataset_uuid` and
  `chart_uuid`; the bar for `birth_names` reads 11 charts, matching `get_dataset_usage`.
- **Evidence explanation panel**: maps each dashboard section to the product capability it demonstrates
  and shows the latest export digest, so the JSON and the UI are visibly the same evidence.

Demo flow: run the terminal steps, switch to this dashboard, refresh, and point at the new top activity
row and the incremented version count.

### 2b. The automations we created

Listed at https://app.devin.ai/org/luca-capezzuto-demo/automations. All four are enabled, all run as the
creator, all start a session per event, and all are limited to opening PRs and commenting; none can merge.
Each prompt is mirrored as a reviewable markdown file in `lcpz/superset-devin-automation/automations/`.

| # | Automation | Trigger | What the session does |
|---|---|---|---|
| 1 | **Implement task-spec issues** (`auto-dec7c…`) | `github:issues` opened, title starts with `[devin-task]` (the issue template) | Reads the spec, implements on `devin/<n>-<slug>`, opens exactly one PR with `Closes #n`, replies on the issue. |
| 2 | **Pick up issues labelled `devin:ready`** (`auto-403d…`) | `github:issues` labelled `devin:ready` by a human | Gate check (aborts on `blocked-*`, `devin:in-progress`, `devin:done`), claims with `devin:in-progress`, implements, opens one PR, flips to `devin:done` only when CI is green. |
| 3 | **Address PR feedback and failed CI on `devin/*` PRs** (`auto-f452…`) | `github:pull_request_review_comment`, `github:pull_request_review` (changes requested), `github:issue_comment` (`@devin`), `github:check_run` (failed or timed out) | Reproduces, fixes on the same branch, replies to each thread individually, re-runs pre-commit. |
| 4 | **6-hourly observability review** (`auto-09e2…`) | `schedule:recurring`, every 6 h from 00:30 UTC; ACU cap 15 per session; at most 2 invocations per window; concurrency 1 | Reads the latest snapshot and findings, re-verifies each gap against the repository, dispatches missed CI or review fixes, records a marker; does nothing when the failure is pre-existing on `master`. |

How they relate: 1 and 2 are the two intake paths (template-driven or label-gated); both end in one
Devin PR. 3 is the delivery-feedback loop on every Devin PR. 4 is the safety net for whatever 3 missed.
Two behaviours that shape the design: GitHub events raised by the Devin GitHub App itself do not fire
Devin Automations, so chaining always goes through a human action; and the automations are
intentionally paired with the built-in Devin GitHub integration (which wakes the originating session on
PR comments and CI failures) and Devin Review (which reviews every PR), not duplicated against them.

**What `lcpz/superset-devin-automation` adds on top of stock Devin.** Out of the box, Devin gives you the
GitHub integration, Devin Review, and event-driven Automations (the four rows above are ordinary
Automations). What it does not give you is proof that they are working, recovery when they are not, or
a place where a technical audience can query the result. The standalone repository packages exactly
that layer, repo-agnostic and Dockerised:

- **Collector** (`devin-automation collect`): GitHub REST and GraphQL plus the Devin v3 API; records
  PRs, check runs, review threads, Devin comments, dispatch markers, Devin sessions (status, origin,
  automation id, ACUs, linked PRs) and automation records; classifies every PR and derives findings
  (`ci-failed-unattended`, `review-unaddressed`, `changes-requested-unaddressed`) with grace periods and
  activity-aware suppression. Emits a JSON snapshot and, optionally, loads Postgres.
- **Gap dispatcher** (`devin-automation dispatch`, behind a manual Compose profile and `--dry-run` by
  default): one session per PR per run, `--max` and `--max-acu` limits, marker-before-session
  idempotency, optional `DEVIN_CREATE_AS_USER_ID`.
- **Postgres schema** `devin_obs` (`sql/schema.sql`): snapshots, pull requests and their history, check
  runs, sessions, automations, findings, dispatches. This is the "observable output for a technical
  audience": plain tables any BI tool can read.
- **GitHub Actions workflow** and the **status-board issue comment**: the periodic job that runs the
  collector, posts the board, and uploads the snapshot artifact (section 2c).
- **Automation prompt mirrors** (`automations/01…04.md`) with their triggers, limits and `run_as`, so the
  automation configuration is reviewable in git rather than only in the Devin UI.
- **Superset builders**: `superset/build_dashboard.py` (observability dashboard: connection, virtual
  datasets, 13 charts) and `superset/build_evidence_dashboard.py` (governed-evidence dashboard over the
  versioning tables), plus `superset/governed_evidence_demo.py` for the terminal demo.
- **Tests and tooling**: pytest, ruff, `mypy --strict`, Docker image and Compose; `GITHUB_REPOSITORY` is
  mandatory so the same image cannot silently collect the wrong repository.

In one sentence: stock Devin does the work; this repository measures it, recovers it, and makes it
queryable.

### 2c. Example: the Devin status dashboard run

https://github.com/lcpz/superset/actions/runs/33753133600 is a manually dispatched run of the "Devin
status board" workflow on `master`. It completed successfully and produced two artifacts:

**`devin-status-board`** (generated 2026-09-03 12:12 UTC). A markdown board posted as a pinned issue
comment and uploaded as an artifact. For that run it showed: issues tracked 1 (the original smoke-test
task), by status `closed=1`, controlled replays 1, sessions observed 1, ACUs consumed 0.0; and one row
linking the issue, its PR, the successful CI, the human approval, the Devin session and the PR checks.
Its automation-health line read "unknown (automation record not readable)": the workflow tried to fetch
the configured automation id and the Devin API returned 404. The organisation's automation list
independently shows the four enabled automations, so this is a lookup problem in the workflow's
configuration or the key's `ViewOrgAutomations` permission, not a missing automation; it is left visible
on the board rather than hidden.

**`devin-obs-snapshot`** (collected 2026-09-03 12:25:33 UTC). The observability job of the same run,
the first with the live Devin API wired in Actions:

```json
{"devin_api": true, "pulls": 18, "open": 1, "failing_ci": 0,
 "sessions": 146, "automations": 0, "findings": []}
```

`devin_api: true` and 146 sessions confirm the credentials work for session listing (the masked
`DEVIN_API_KEY` and `DEVIN_ORG_ID` are visible in the job environment without their values).
`automations: 0` is the same lookup problem as above, not evidence of zero automations. This snapshot
also surfaced a collector bug: the v3 sessions API returns `created_at` and `updated_at` as integer
epochs, which the loader passed straight into `TIMESTAMPTZ` columns. The fix (normalise to ISO with
tests, in both repositories) is in review; with it, this artifact loads into the local `devin_obs`
database: 146 sessions, 6 of them linked to PRs, and the dashboard's collector-health panel now shows
the 12:25 UTC snapshot with `devin_api=true`.

In the demo, this run is the "observable automation" beat: a scheduled job, a board a manager can
read, a JSON artifact an engineer can load, and an honest "unknown" where the pipeline could not verify.

---

## 3. WHY: why an autonomous coding agent, specifically

- **It closes the loop, not just the ticket.** A code generator produces a diff. Devin owns the PR
  lifecycle: it reads CI logs, reproduces failures, fixes, replies to each review thread individually,
  and re-runs pre-commit and mypy strict. The observability PR alone had five review findings fixed and
  answered in-thread without a human touching the branch.
- **Judgement in the safety net.** The 6-hourly review is not a cron job that retries CI. It decided that
  a red PR's failure was pre-existing on `master` and did nothing; it also noticed the collector was
  mislabelling Devin Review threads as human comments and reported it. A script cannot make that call; a
  human would not be awake at 06:30 to make it.
- **Curated security scanning to verified fixes.** Two swarm scans (129 investigation batches in total)
  were distilled to 16 ranked findings, each tied to the published capability matrix. Two high-severity
  boundary violations were then fixed and tested by the same agent, through the same governed PR flow.
  Scan, triage, fix and test are one actor with one context.
- **Meta-work is also delegable.** This report, the dashboards, the collector, the standalone repository
  and the Superset demo were produced by Devin sessions reading other Devin sessions, the Actions logs
  and the scan results.
- **Governance is not weakened; it is made visible.** Branch protection and human approval are
  unchanged. What changed is that every autonomous action leaves a marker, a session URL and a row in
  Postgres.
- **It caught its own mistake.** Re-verifying after the merges showed that two stacked PRs marked
  "merged" had never reached `master` (the stack was merged top-down after the base was squashed). The
  missing diff was re-applied; Devin Review on that re-apply then caught two earlier review fixes that
  had been dropped, and a further PR restored them with a stronger ordering guarantee and tests.

### Organisational benefits (general)

Throughput without headcount for well-specified work; toil removal (CI babysitting, review ping-pong,
status reporting); consistent remediation cadence (the 6-hour review); auditability (session link in
every PR, markers, database rows); controlled autonomy (template and label gates, `devin/*` branches,
no merge rights, ACU caps per automation); and a reusable pattern, since the standalone repository is
repo-agnostic.

---

## 4. WHEN: next steps for a real engagement

1. **Live Devin API in CI: done.** `DEVIN_API_KEY` and `DEVIN_ORG_ID` are configured as Actions secrets;
   the 2026-09-03 12:25 UTC collection returned `devin_api: true` with 146 sessions, and the epoch
   timestamp loader fix (in review) lets that snapshot populate the session and ACU panels. Remaining
   polish: make the automation record readable to the workflow (correct id or `ViewOrgAutomations` on
   the key) so the board's health line stops saying "unknown".

2. **Launch and control Devin from Slack.** Everything below is from the Devin Slack and Automations
   documentation and the v3 API reference.
   - *Setup.* An admin connects the workspace at Settings, Connections, Slack; each user links their own
     account (Slack email must match the Devin email). Devin must be invited to any channel it should
     act in.
   - *Launching.* Mention `@Devin <task>` in a channel or thread to start a session; Devin answers in a
     thread and the conversation continues there. `/ask-devin <question>` gives a quick codebase answer
     without a session. A message shortcut ("Create a new session") opens a pre-filled modal where you
     pick the channel and optionally attach a playbook. `!<macro>` attaches a playbook inline, so the
     Superset task-spec prompt could be a playbook invoked from Slack.
   - *Controlling.* Inline keywords work in the thread: `sleep`, `archive`, `EXIT`; `mute` and `unmute`;
     `(aside)` for messages Devin should ignore; `!new` to force a new thread or a dedicated code channel;
     `!channel #name` to post elsewhere; mode switches `!fast`, `!lite`, `!ultra`, `!normal` mid-session.
     Sessions started anywhere can be synced bidirectionally to a Slack thread from the session composer;
     `unsync` stops it. When Devin proposes an environment change, Slack shows the diff with an Apply
     button.
   - *Automations.* Automations accept `slack:message` and `slack:reaction` triggers (for example, react
     with a chosen emoji to a bug report to start an investigation), can bind the spawned session to the
     triggering thread (`attach_thread`) or post a breadcrumb (`notify_thread`), and can post results to a
     designated channel. A persistent triage session can watch a channel and spawn investigations. For
     the Superset flow this means: a reaction on a message in a team channel could open the same
     task-spec issue path, and the 6-hourly review could post its board to a channel instead of only to a
     GitHub issue.
   - *API.* Slack-originated sessions appear in `GET /v3/organizations/{org}/sessions` with
     `origin: "slack"`, so the collector already attributes them; automations with Slack triggers are
     managed through the same `/v3/organizations/{org}/automations` endpoints (`ViewOrgAutomations`,
     `ManageOrgAutomations`).
   - *What stays human.* Slack is another intake and steering surface; merge rights, branch protection
     and approval do not change. Caveat: the sidebar assistant experience needs a paid Slack plan; mentions,
     slash commands and shortcuts work on any plan. Code channels depend on Slack's own rollout.

3. **The sixth product capability**: a correlation id linking SQL Lab `Query` rows and
   `ReportExecutionLog` rows to a migration or review event, so `query_executions` in the evidence
   bundle stops being a heuristic match. It needs a SIP-59 database migration (atomic, reversible,
   backwards compatible), so it is the first change that needs the customer's DBA in the review loop.

4. **Customer rollout.** Point the standalone repository at their repository, run the collector in their
   CI, connect their Superset (or any SQL BI tool) to `devin_obs`; start with label-gated intake only,
   then enable task-spec issues and the periodic dispatcher once trust is established; keep ACU caps
   per automation and add per-PR ACU and review-latency KPIs to the dashboard.

