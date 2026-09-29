# Timeline Director two-phase sampling patch

This patch targets only `Songssx/ComfyUI-MiniMaxH3-TimelineDirector` revision
`309b626973d049b073e93557ff94603efc2d1272`.

It adds optional `full`, `preview`, and `finalize` execution stages to the
finite-segment sampler. Omitting the stage keeps the original full-generation
path. Preview performs the low-resolution pass for every segment and stores a
tensor-only SelfLift transition checkpoint for segment 1. A finalization
resumes segment 1's high-resolution suffix and regenerates later segments in
order, carrying each preceding final AV latent forward.

Checkpoint state lives beneath ComfyUI's output directory in
`timeline_director_checkpoints/<uuid-v4>/`. The JSON manifest is committed only
after the preview graph completes; it records the state size and SHA-256. The
status route verifies that metadata and file integrity without loading tensors
or scheduling a prompt. Finalization revalidates before safe tensor loading.
The GET and DELETE endpoints accept only canonical UUID-v4 IDs and never
resolve user-supplied paths.

Setup must verify the exact base revision and apply this patch atomically. It
must not rewrite unknown local plugin changes. See
`tests/test_h3_timeline_director_two_phase.py` for the checkpoint and route
contract tests.
