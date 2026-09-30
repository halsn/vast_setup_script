# Timeline two-phase deployment release

> **For agentic workers:** Execute inline with test-driven development and verification before completion.

**Goal:** Publish the existing checkpoint patch to deployment `main` and fail deployment if preview/finalize capabilities are unavailable.

**Architecture:** Preserve the pinned upstream revision and managed patch installer. Extend the existing deployment health check to validate the same sampler inputs, checkpoint node, and read-only route contract as the GUI.

**Tech Stack:** Bash, Python, pytest, Git.

**Spec:** User-approved repair: publish setup two-phase support, prevent false successful deployments, preserve running GPU jobs.

## Constraints

- Work in the existing setup feature branch; preserve unrelated worktrees and workspace edits.
- No instance replacement, generation submission, or ComfyUI restart while a job runs.
- Fast-forward deployment main only after tests and remote ancestry verification; never force-push.

## Review focus

- Missing checkpoint node or sampler inputs must fail deployment.
- A generic 400 or HTML response must not count as a working checkpoint API.
- HTTP 404 must fail; the intended invalid-ID HTTP 400 must succeed.
- Repeated patch installation must preserve unrelated plugin edits.
- Published entry script and companions must resolve to available commits.

## Task 1: Deployment regression coverage

**Files:** `tests/test_h3_studio_smoke.py`, `scripts/setupp_h3_studio.sh`.

- [ ] Execute the actual embedded deployment Python checker against local HTTP fixtures.
- [ ] Watch missing node/input and unavailable/wrong route regressions fail before implementation.
- [ ] Add the minimal node/input/output and invalid-ID endpoint checks.
- [ ] Verify focused tests and the full setup suite.

## Task 2: Publish the complete patch chain

**Files:** Existing three checkpoint commits plus Task 1 changes.

- [ ] Review the patch installer and release diff; verify Bash syntax and patch application/idempotence.
- [ ] Commit only scoped changes, fetch remote main, and fast-forward main without discarding work.
- [ ] Push main and verify the remote SHA and published script/companion files.

## Task 3: Existing-instance follow-up

- [ ] Read queue/capabilities without interrupting generation.
- [ ] Report that updating an already deployed instance remains necessary; defer its restart until the job completes.
