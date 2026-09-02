---
name: "Superset: implement task-spec issues (Devin task template)"
automation_id: auto-dec7c6f88d4241e593a9d9125276ccd7
trigger:
  event: github:issues
  conditions:
    - action=opened
    - issue.title starts_with "[devin-task]"
    - issue.user.login not_contains "[bot]"
limits:
  max_acu_limit: 30
  invocations: 10
  window_seconds: 3600
concurrency:
  runs: 2
  queue: 10
run_as: creator
---

> Authoritative copy lives in the Devin automation; this file is the reviewable checked-in mirror.

## Prompt

When a human opens a task-spec issue, read the complete issue form as the task
specification: goal, scope, acceptance criteria, constraints, and expected size.
Check the repository state and the issue before doing any work. Do not act if the
issue is blocked or otherwise not actionable.

Claim the issue with `devin:in-progress`, then create a branch named
`devin/<issue-number>-<slug>` and implement the requested work within the stated
scope. Open one pull request from that branch, linking the issue and describing
the changes and verification. Do not open multiple PRs for the same task.

Never merge or approve the PR, enable auto-merge, or push to `master`. Humans
perform the merge behind branch protection. Address CI failures and human review
comments on the PR when they arrive. Add `devin:done` only when the PR's CI is
green; leave the PR for human review and merge.
