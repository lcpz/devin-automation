---
name: "Superset: 6-hourly observability review — dispatch missed CI/review gaps on devin/* PRs"
automation_id: auto-09e2abe6589942f2991fa9fc89d4630b
trigger:
  event: schedule:recurring
  schedule: "DTSTART;TZID=UTC:19700101T003000 + RRULE:FREQ=HOURLY;INTERVAL=6"
limits:
  max_acu_limit: 15
  invocations: 2
  window_seconds: 21600
concurrency:
  runs: 1
  queue: 1
run_as: creator
---

> Authoritative copy lives in the Devin automation; this file is the reviewable checked-in mirror.

## Prompt

Read the latest observability snapshot and review open, non-draft PRs on
`devin/*` branches. Use the snapshot's finding and dispatch state to identify
only missed gaps: failed CI with no active Devin work, unresolved human review
threads, or changes requested with no follow-up commit. Respect the grace
period and existing hidden dispatch markers; do not dispatch a finding that is
already resolved or already handled.

For each genuine gap, dispatch at most the work permitted by the snapshot and
the automation limits. A dispatched session must re-check the PR's current
state, work on the same branch, and leave a concise PR comment. It must never
merge or approve, enable auto-merge, or push to `master`; humans merge behind
branch protection. Mark the owning issue `devin:done` only when the PR's CI is
green.
