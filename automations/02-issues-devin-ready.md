---
name: "Superset: pick up issues labeled devin:ready"
automation_id: auto-403df7fca9ff411c92bcf11a1baf1f5f
trigger:
  event: github:issues
  conditions:
    - action=labeled
    - label=devin:ready
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

When an issue receives the `devin:ready` label, inspect the issue and repository
state before acting. Abort without changes if a `blocked-dependency` or
`blocked-*` label is present, or if the issue already has `devin:in-progress` or
`devin:done`. Treat the issue and its acceptance criteria as the work
specification.

Claim the issue with `devin:in-progress`. Create exactly one branch named
`devin/<issue-number>-<slug>`, implement the task, and open one pull request
from that branch. Keep the PR focused and include verification details.

Never merge or approve the PR, enable auto-merge, or push to `master`. Humans
perform the merge behind branch protection. Respond to CI failures and human
review comments through the PR feedback process. Set `devin:done` only after
the PR's CI is green; otherwise leave the issue in progress with an accurate
status.
