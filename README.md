# Devin Automation

Devin Automations are event-driven: a GitHub issue, label, review, comment, or
check-run event can start a Devin session immediately. Event delivery is not
perfect, however. An automation can be rate-limited, a webhook can be dropped,
or a session can stop before it reaches the next workflow step.

This repository packages the collector and gap dispatcher that prove those
automations are working. Every six hours it records the state of `devin/*`
pull requests, CI, human reviews, review threads, Devin sessions, and
automations. It derives only the gaps that the event-driven automations could
not see, stores the snapshot in Postgres, and can start a bounded Devin session
to address each missed gap.

The implementation uses the GitHub REST and GraphQL APIs, the Devin v3 API,
stdlib HTTP/JSON handling, and an optional Postgres sink.

## Architecture

Event-driven work follows this path:

```text
GitHub events (issues, labels, reviews, comments, check runs)
                         |
                         v
                 Devin Automations
                         |
                         v
             devin/<issue>-<slug> branches
                         |
                         v
                         PRs
```

The periodic evidence and recovery path follows this path:

```text
GitHub Actions schedule / cron
              |
              v
          collector -------> JSON artifact
              |
              v
           Postgres -------> Superset dashboard
              ^
              |
 recurring Devin observability automation reads the snapshot
 and dispatches only missed CI/review gaps
              |
              v
       same-branch Devin sessions
```

The collector examines open, non-draft PRs whose branch starts with `devin/`.
It ignores informational checks, applies the configured grace period, and
suppresses findings when a recent commit or active Devin session shows that the
event-driven flow is already handling the PR. Dispatch records a hidden marker
comment on the PR, making retries idempotent.

## Quickstart with Docker

The default Compose file runs Postgres and a collector. The dispatcher is
explicitly placed behind a `manual` profile so it cannot run accidentally.

```bash
cp .env.example .env
docker compose up -d db
docker compose run --rm collector
docker compose --profile manual run --rm dispatcher --dry-run
```

The collector writes `out/snapshot.json` and loads the same snapshot into
Postgres when `DEVIN_OBS_DATABASE_URL` is configured. The Compose example uses
the `db` service hostname; change it when the database is external.

## Environment variables

| Variable | Required | Description | Default |
| --- | --- | --- | --- |
| `GITHUB_TOKEN` | Yes for API access | GitHub token with read access to pull requests, checks, reviews, and comments; dispatch also needs comment write access | empty |
| `GITHUB_REPOSITORY` | Yes | Target repository in `owner/repository` form; there is no repository fallback | — |
| `DEVIN_API_KEY` | No for GitHub-only collection | Devin v3 API key with the permissions needed to view sessions and create sessions | empty |
| `DEVIN_ORG_ID` | No for GitHub-only collection | Devin organization identifier | empty |
| `DEVIN_OBS_DATABASE_URL` | No | Postgres connection URL for the `devin_obs` schema | empty |
| `DEVIN_OBS_GRACE_HOURS` | No | Age before an unattended finding is eligible | `2` |
| `DEVIN_OBS_IGNORE_CHECKS` | No | Comma-separated informational check names | `actions-timeline` |
| `DEVIN_OBS_LOOKBACK_DAYS` | No | GitHub PR and Devin session lookback window | `60` |
| `DEVIN_CREATE_AS_USER_ID` | No | Optional Devin user identity for session creation; requires the corresponding organization permission | empty |
| `DEVIN_OBS_SOURCE` | No | Label stored with a database snapshot | `cli` for collect/findings |

Copy `.env.example` to `.env` and replace every placeholder. `.env` is
git-ignored. `GITHUB_REPOSITORY` is intentionally required so the same image
can be used for any repository without silently collecting the wrong target.

## Command reference

All commands are exposed through the `devin-automation` console script. They
use the current `GITHUB_REPOSITORY` configuration and do not contact live
services in dry-run mode.

### `collect`

Collect PRs, checks, review state, review threads, Devin sessions, and
automations, derive findings, print a summary, and optionally write a complete
snapshot:

```bash
devin-automation collect --json snapshot.json
devin-automation collect --json snapshot.json --database-url "$DEVIN_OBS_DATABASE_URL"
```

### `findings`

Collect the same source data but output only the derived findings:

```bash
devin-automation findings --json findings.json
```

### `dispatch`

Collect and dispatch each not-yet-dispatched finding up to the requested
limits. A real dispatch creates a Devin session and then posts the hidden
idempotency marker comment:

```bash
devin-automation dispatch --dry-run
devin-automation dispatch --max 3 --max-acu 15 --json dispatches.json
```

### `load`

Load a previously collected JSON snapshot into Postgres. This is useful when
collection runs in GitHub Actions and loading runs separately:

```bash
devin-automation load snapshot.json --database-url "$DEVIN_OBS_DATABASE_URL"
```

## GitHub Actions

`.github/workflows/observability.yml` runs every six hours and supports
`workflow_dispatch`. It installs the package with its Postgres extra, runs
`collect`, writes the JSON snapshot to the step summary, and uploads it as the
`devin-obs-snapshot` artifact. If `DEVIN_OBS_DATABASE_URL` is non-empty,
`collect` also upserts the snapshot into Postgres.

Configure these repository secrets before enabling the schedule:

* `DEVIN_API_KEY` — Devin v3 key, if session and automation data is wanted.
* `DEVIN_ORG_ID` — Devin organization identifier.
* `DEVIN_OBS_DATABASE_URL` — reachable Postgres URL, if database loading is
  wanted.

GitHub supplies `GITHUB_TOKEN`, and the workflow supplies
`GITHUB_REPOSITORY=${{ github.repository }}`. The workflow has read-only
contents, pull-request, and checks permissions because it only collects data.
Grant comment write access separately when running a real dispatch job.

## Installing the Devin Automations

The four files in `automations/` are reviewable checked-in mirrors, not an
automatic import format. Create the automations through the Devin webapp or
API, copy each file's `name`, trigger, limits, concurrency, and prompt, and
record the resulting automation identifier in the front matter. All four are
configured to run as `creator`.

Install these files as the four automations:

1. `01-task-spec-issues.md` starts implementation from the structured issue
   template.
2. `02-issues-devin-ready.md` picks up issues after a human applies
   `devin:ready`.
3. `03-pr-feedback-and-ci.md` addresses human review feedback and failed or
   timed-out CI on `devin/*` PRs.
4. `04-observability-review.md` is the recurring six-hour review that reads the
   latest snapshot and dispatches only missed gaps.

The automation configuration is the authoritative runtime copy; the markdown
files make the configuration and prompt inspectable in code review.

## Superset database and dashboard

Point the BI tool at the Postgres database named by
`DEVIN_OBS_DATABASE_URL`. The loader creates schema `devin_obs` and keeps the
DDL in both `src/devin_automation/db.py` and `sql/schema.sql`.

The tables support these dashboard panels:

| Panel | Table / query |
| --- | --- |
| Snapshot health over time | `devin_obs.snapshots`: `collected_at`, `pulls`, `open_pulls`, `failing_ci`, `sessions`, `findings`, and `acus_total` |
| PR delivery state | `devin_obs.pull_requests`: group by `remediation`, filter `branch LIKE 'devin/%'` |
| PR state trend | `devin_obs.pull_request_history`: order by `collected_at`, grouped by `number` |
| Current CI failures | `devin_obs.pull_requests` joined to `devin_obs.check_runs`, filtering `checks = 'failure'` and the current `head_sha` |
| Review backlog | `devin_obs.pull_requests`: `unresolved_threads`, `oldest_unresolved_at`, `changes_requested` |
| Devin consumption and activity | `devin_obs.sessions`: sum `acus_consumed` by `origin`, `automation_id`, `category`, or day |
| Automation health | `devin_obs.automations`: `enabled`, `last_status`, `last_fired_at`, and `event_types` |
| Missed-gap backlog | `devin_obs.findings`: open rows where `resolved_at IS NULL`, grouped by `kind` and `dispatched` |
| Dispatch audit | `devin_obs.dispatches`: session, finding, PR, timestamp, and marker comment URL |

For example, the current open-gap panel can use:

```sql
SELECT kind, count(*) AS findings, count(*) FILTER (WHERE dispatched) AS dispatched
FROM devin_obs.findings
WHERE resolved_at IS NULL
GROUP BY kind
ORDER BY kind;
```

## Safety guarantees

* Dispatch is idempotent. Every finding has a stable key, and the PR receives a
  hidden `devin-obs:dispatch` marker containing the key and created session.
* A dispatch reuses the existing PR branch; it does not create a second PR.
* Session prompts explicitly prohibit merging, approving, enabling auto-merge,
  and pushing to `master`.
* Devin adds `devin:done` only after CI is green, and humans merge behind branch
  protection and approval requirements.
* The collector never mutates GitHub state. Only an explicit real
  `dispatch` posts comments and creates sessions.
* The `dispatcher` Compose service is in the `manual` profile and never starts
  during a normal `docker compose up`.

## Limitations

* GitHub events performed by Devin's GitHub App
  (`devin-ai-integration[bot]`) do not fire Devin Automations. When testing a
  GitHub-triggered automation, a human must perform the triggering action
  (open the issue, add the label, or post the comment).
* The collector's CI state reflects the PR head at collection time, not the
  head that existed when `devin:done` was applied.
* GitHub review-thread resolution requires GraphQL access. A token lacking
  that access cannot provide the same review evidence.
* Devin API data is optional. Without both `DEVIN_API_KEY` and `DEVIN_ORG_ID`,
  GitHub collection still works, but sessions and automation health are absent.
* The lookback window limits historical collection. Sessions that do not
  contain a recognizable PR URL or PR number in their title can only be
  counted in aggregate.
* Scheduled workflows can be paused by GitHub for inactive forks; use
  `workflow_dispatch` to refresh a paused workflow.
