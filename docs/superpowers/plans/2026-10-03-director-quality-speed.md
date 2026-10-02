# Director Quality Preserving Speed Improvements

> For agentic workers: use subagent-driven-development for isolated tasks and independent review. Functional verification uses mock models and transports; do not submit GPU renders.

**Goal:** Reduce repeated Director reference/text encoding and provide lightweight live previews without changing model weights, sampler, steps, resolution, final VAE, or segment continuation.

**Architecture:** Add bounded process-local CPU caches around deterministic H3 reference and text encoding calls. Cache keys include exact input contents, model identity and patch state, and encoder parameters. Extend the setup-managed patch contract to install the cache helper and safely upgrade the previously shipped patch. Consume native ComfyUI preview messages in the Director, and pin the author's TAEH3 weights for preview decoding only.

**Tech Stack:** Python, pinned ComfyUI 0.38 and Timeline Director 309b626, shell setup scripts, React/TypeScript, pytest and Vitest mocks.

## Task 1: Exact encoding reuse and measured stages

- [x] Add failing mock tests in `tests/test_h3_timeline_encoding_cache.py` for identical-input hits; input content, crop/shape, dtype, model and patch-state invalidation; mutation isolation; bounded CPU storage; unsupported inputs bypass; failed computations never cached; unmodified RNG progression.
- [x] Implement `patches/timeline-director/reference_cache.py` and add narrow calls in the pinned Director's `_execute_h3_independent_first` to reuse video/image VAE, audio VAE, and text/vision conditioning results independently. Prompt changes must not invalidate unchanged VAE results. Log cache hit/miss and elapsed encode stages without synchronizing CUDA for diagnostics.
- [x] Regenerate `two-phase-checkpoints.patch` from the pinned upstream plus existing patch. Preserve the first-segment-only resume and attention safety guard exactly.
- [x] Update `scripts/h3_timeline_director_patch.py` and Studio companion fetching to include the new helper and upgrade an exactly matching prior managed patch. Retain refusal of unrelated local edits. Verify install, rerun, reverse, and old-to-new upgrade using mock fixtures and the actual pinned source checkout.
- [x] Run focused pytest, review scope compliance, then review correctness and cache safety.

## Task 2: Live preview transport and Director display

- [x] Add failing mock tests covering legacy binary JPEG/PNG preview messages, invalid messages, late messages after stop/unmount, prompt ownership, and URL revocation.
- [x] Extend `comfyui.ts` preview parsing without changing JSON event behavior. Preserve other workflows' subscriptions and ignore unsupported binary event kinds.
- [x] Add Director preview state/display with the label `生成中预览，最终细节以成片为准`; release object URLs on replacement, stop and unmount. Show node/stage timing derived from actual execution events without inventing an overall segment percentage.
- [x] Run affected Vitest suites and frontend build, then independent scope and quality review.

## Task 3: Pinned preview decoder and delivery

- [x] Pin author's `madebyollin/taehv` TAEH3 artifact at an immutable revision, record bytes and SHA-256, and download to `models/vae_approx` during Studio setup. Verify source configuration through mocked downloads. Enable the preview path at Studio scope while keeping final INT8 VAE selection unchanged.
- [x] Confirm sampler callback/preview support in pinned ComfyUI and SelfLift. Preview errors must not silently modify sampling or swallow CUDA failures.
- [x] Run setup regression checks, patch syntax/application checks and client build. Record actual mock hit/miss compute counts; do not label them as measured GPU speedups.
- [x] Publish reviewed commits, update standalone companion pin, and synchronize only changed files into the user's working source without overwriting existing edits. Report how existing installations receive the update.

## Acceptance

- Cached and uncached mock outputs remain exactly equal and isolated from mutation.
- Changed reference bytes or model patches never produce a stale hit.
- Encoding work is skipped on a repeated request and reused between preview/final when its exact inputs match.
- Cache storage is bounded CPU memory and does not retain GPU model objects.
- Model, schedule, resolution, final decoder and existing continuation semantics remain unchanged.
- Mock preview/terminal/recovery tests and builds pass; real 5090 speed and image quality remain explicitly unmeasured.

## Verification record

- Client commit `b1be205`: 123 focused Vitest mocks passed in the isolated branch; frontend build passed. Independent scope and correctness reviews passed.
- Current client source: merged six files while preserving existing local changes; 166 Director/transport mocks and 64 T8 Relay mocks passed; frontend build passed.
- Pinned preview artifact: author revision `62f7591f59dfbb4c3c02b7a621d180a9eeaba26c`, 22,709,752 bytes, SHA-256 `4fd022bfcab08772fe0536b17ea1a3bbb5625be11e397868d1c5d891863d4c13`.
- All pre-existing patch sections (`__init__.py`, finite sampler, checkpoint store, SelfLift sampler/attention guard) were compared against baseline `35a62ba` and are byte-for-byte unchanged.
- Broader Windows setup run: 236 passed, 18 failed because the unchanged Linux T8 smoke launcher imports `fcntl`. The baseline launcher at `35a62ba` reproduces that import failure. This run is not a clean full-suite result.
- No GPU renders or remote service restarts were performed. First unique-input generation speed and 5090 image quality are unmeasured.

- Final setup combination: 99 mocks passed after updating the standalone companion pin, both in the isolated branch and in the synchronized working source. The actual patched encoder was exercised with CPU mock models: requests red/blue/blue computed image VAE once, audio VAE once, tokenizer twice and scheduled CLIP twice; cached blue matched fresh blue.
- Pinned plugin migration at `309b626973d049b073e93557ff94603efc2d1272`: exact prior patch reversal, new apply, idempotent apply, and final reversal passed. Both helper sources match their embedded patch content; drift is rejected before file mutation.
- Setup payload `9e7f378` and companion-pin commit `03e2156` were published to `main` and `codex/h3-director-quality-speed`. Downloaded public payload/helper/script bytes matched their Git objects. Client commit `b1be205` was published to its feature branch and its six changed files were merged into the existing client source.
- Source synchronization used three-way merges with backups, preserving existing edits. Shell syntax and Python compilation checks passed after synchronization.

## Receiving the update

- The local client source and built frontend already contain the changes; reload or reopen the client page to load the new frontend.
- New Studio installations through the current setup `main` receive the exact encoding helper and pinned TAEH3 preview decoder. Existing servers need the updated Studio setup to install the backend changes and start ComfyUI with the preview option.
- No registered remote instance was available during delivery, so no existing server was upgraded or restarted. Functional validation used mocks only; GPU speed and final image quality remain unmeasured.
