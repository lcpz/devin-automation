---
name: "Superset: address PR feedback & failed CI on devin/* PRs"
automation_id: auto-f4523d7e054c4fee8e2edaac6915c869
trigger:
  events:
    - human PR review comment
    - review with changes_requested
    - issue comment containing @devin
    - failed/timed-out check_run on devin/* branches
limits:
  max_acu_limit: 20
  invocations: 8
  window_seconds: 3600
concurrency:
  runs: 1
  queue: 5
run_as: creator
---

> Authoritative copy lives in the Devin automation; this file is the reviewable checked-in mirror.

## Prompt

For a triggered `devin/*` pull request, first read the current PR, its checks,
review comments, and branch state. Address every human review request with code
or a reasoned reply, reply in-thread where appropriate, and resolve a thread
when its request has been fulfilled. For failed or timed-out CI, reproduce the
failure locally, fix the root cause on the same branch, and push the fix without
weakening or skipping tests. Watch CI until it is green.

Do not rely on another event to chain the work: inspect all current feedback and
failed checks in the PR. Never merge or approve the PR, enable auto-merge, or
push to `master`; humans perform the merge behind branch protection. Finish
with one PR comment summarizing what changed and what remains. The owning issue
may receive `devin:done` only when CI is green.
