# H3 runtime updates implementation plan

> Execute inline in the existing clean release worktree. The user authorized the updates except a 5090 machine preset. Preserve Python 3.12, Torch/CUDA, the pinned T8/Director revisions, and the user's choice to perform video tests themselves.

**Goal:** Upgrade the setup's ComfyUI and Sage installation, add a verified INT8 VAE option, protect every common Sage call, and report observed acceleration.

**Architecture:** Keep the existing profile/base structure. Pin ComfyUI to `6b747c0428c343e1417219641db93a4fb7cb69ae` (v0.38.0). Install a small setup-owned status module and common attention guard before restarting ComfyUI. Preserve the Director guard. Keep FP16 VAE as default and add the official INT8 file with revision, size and digest.

**Tech stack:** Bash, Python 3.12, pytest, existing aiohttp Gateway and ComfyUI routes.

## 1. Baseline and RED

- [x] Run `uv run --with pytest --with aiohttp pytest -q tests/test_bootstrap_contract.py tests/test_h3_profiles.py tests/test_h3_timeline_attention.py tests/test_worker_gateway.py tests/test_workflow_builder.py`: 38 pass.
- [x] Add `tests/test_h3_runtime_updates.py`: pinned core, version override, real kernel probe, VAE identity, common guard, fatal errors and reporting.
- [x] Run the new file before changing production code; expected missing pin, skipped installation, missing INT8 metadata and missing guard failures.

## 2. Core and accelerator installation

- [x] Modify `scripts/h3_comfui_base.sh` defaults:
  ```bash
  H3_COMFYUI_REF="${H3_COMFYUI_REF:-6b747c0428c343e1417219641db93a4fb7cb69ae}"
  H3_MIN_COMFYUI_VERSION="${H3_MIN_COMFYUI_VERSION:-0.38.0}"
  H3_SAGE_VERSION="${H3_SAGE_VERSION:-2.2.0}"
  ```
- [x] Update core only when its commit differs; preserve local changes via the existing stash policy, fetch the selected ref and detach at it. Install only non-Torch dependencies.
- [x] Compare installed Sage metadata with the selected version even on Vast. Install with `--no-deps --no-build-isolation`; reject a mismatched result. Execute a small finite FP16 Sage kernel in a separate process before marking it verified.

## 3. Common safety and live status

- [x] Add `scripts/h3_attention_runtime.py`. Validate the AST of `attention_sage` before inserting a managed block. Make inputs contiguous immediately before Sage, avoid `numel >= 2**31`, record successful calls and fallback reasons, and rethrow illegal-access/device-assert errors.
- [x] Install `comfy/h3_attention_status.py` and the setup-owned `/h3/runtime_status` route. Report configured backend, last observed backend, fallback count/reason and package versions. Never describe import success as GPU execution proof.
- [x] Update `runtime/worker_gateway.py` readiness to read this live route with a bounded timeout, preserve legacy worker backend separately, and report unknown when the route is unavailable.

## 4. INT8 video VAE

- [x] Add `vae/minimax_h3_video_vae_int8_convrot.safetensors` to base and Worker manifests:
  ```text
  revision=e5eb578a89295337b8ff433a035929ce0279e0b6
  bytes=2811065184
  sha256=52a2c8c73583c86e4f41cdcce3a6ad0ea562987bc0bf3d60a0cef5f5c8e60c0e
  ```
- [x] Add `H3_VIDEO_VAE_VARIANT=fp16|int8`, keeping fp16 as default. Rewrite setup-owned native/Studio aliases to the selected VAE; preserve the pinned Stock20 smoke baseline and upstream source templates.

## 5. Verification and publication

- [x] Run the new tests, then the complete repository suite. Parse every shell file, compile modified Python and run `git diff --check`.
- [x] Review the diff for scope and dependency changes. Verify official VAE metadata at its immutable revision.
- [ ] Commit/push the update, then pin standalone Studio companion URLs to that commit and publish the pin. Sync only changed release files into the source checkout without replacing unrelated work.
- [x] Report CPU verification separately from an actual 5090 render. No active remote connection is saved, so deployment and GPU timing require an available instance.

## Verification evidence

- RED: 10 new checks failed before implementation; VAE integration added 3 failing checks; dependency retry added one failing check.
- Applicable Windows suite: 208 passed in 57.69 seconds. The 18 tests in test_h3_t8_smoke_job.py require Linux fcntl and cannot run here.
- All setup shell scripts parsed and modified Python compiled; official v0.38.0 attention source patched, compiled, and reapplied identically.
- Official INT8 VAE immutable revision, size and SHA-256 verified.
- Dependency installs retry even when the core revision already matches, with installed Torch package constraints.
- No GPU renders submitted and no remote setup applied.
