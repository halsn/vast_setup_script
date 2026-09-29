from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[1]
STORE_PATH = ROOT / "patches" / "timeline-director" / "checkpoint_store.py"
PLUGIN_PATCH_PATH = ROOT / "patches" / "timeline-director" / "two-phase-checkpoints.patch"


def load_checkpoint_store():
    assert STORE_PATH.is_file(), "Timeline Director checkpoint store is missing"
    spec = importlib.util.spec_from_file_location("timeline_director_checkpoint_store", STORE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pinned_plugin_patch_exposes_phase_resume_and_manifest_commit_contract():
    assert PLUGIN_PATCH_PATH.is_file(), "Pinned Timeline Director source patch is missing"
    patch = PLUGIN_PATCH_PATH.read_text(encoding="utf-8")
    for contract in (
        '"execution_stage"',
        '"sampling_fingerprint"',
        '"checkpoint_id"',
        "resume_progressive_high_resolution",
        "MiniMaxH3TimelineCheckpointCommit",
        "register_checkpoint_routes",
    ):
        assert contract in patch


def test_preview_commits_first_segment_av_checkpoint_only_after_state_is_written(tmp_path):
    store = load_checkpoint_store()
    checkpoint_id = str(uuid4())
    fingerprint = "a" * 64

    state_path = store.stage_first_segment(tmp_path, checkpoint_id, b"serialized AV resume tensors")
    assert state_path.is_file()
    assert not (state_path.parent / "manifest.json").exists()

    store.commit_preview(
        tmp_path,
        checkpoint_id,
        fingerprint=fingerprint,
        segment_count=4,
        metadata={"model": "timeline-director-test", "transition_step": 6},
    )

    status = store.checkpoint_status(
        tmp_path, checkpoint_id, fingerprint=fingerprint, segment_count=4
    )
    assert status == {
        "status": "ready",
        "schema_version": store.CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": checkpoint_id,
        "fingerprint": fingerprint,
        "segment_count": 4,
        "resume_segment": 1,
    }


def test_finalize_resumes_first_segment_then_samples_later_segments_in_order():
    store = load_checkpoint_store()
    assert store.segment_stages("finalize", 4) == ("finalize", "full", "full", "full")
    assert store.segment_stages("preview", 4) == ("preview", "preview", "preview", "preview")
    assert store.segment_stages("full", 4) == ("full", "full", "full", "full")


def test_finalize_rejects_mismatch_before_loading_any_tensor(tmp_path):
    store = load_checkpoint_store()
    checkpoint_id = str(uuid4())
    store.stage_first_segment(tmp_path, checkpoint_id, b"serialized AV resume tensors")
    store.commit_preview(
        tmp_path,
        checkpoint_id,
        fingerprint="a" * 64,
        segment_count=3,
        metadata={"model": "timeline-director-test"},
    )
    loaded = []

    with pytest.raises(store.CheckpointError, match="fingerprint"):
        store.load_first_segment(
            tmp_path,
            checkpoint_id,
            fingerprint="b" * 64,
            segment_count=3,
            loader=lambda path: loaded.append(path),
        )

    assert loaded == []


def test_finalize_rejects_missing_or_corrupt_state_before_loading(tmp_path):
    store = load_checkpoint_store()
    checkpoint_id = str(uuid4())
    state_path = store.stage_first_segment(tmp_path, checkpoint_id, b"resume tensors")
    store.commit_preview(
        tmp_path,
        checkpoint_id,
        fingerprint="d" * 64,
        segment_count=2,
        metadata={"model": "timeline-director-test"},
    )
    state_path.write_bytes(b"xxxxxx tensors")
    loaded = []

    with pytest.raises(store.CheckpointError, match="checksum"):
        store.load_first_segment(
            tmp_path,
            checkpoint_id,
            fingerprint="d" * 64,
            segment_count=2,
            loader=lambda path: loaded.append(path),
        )

    assert loaded == []


def test_staged_preview_is_not_ready_until_all_preview_segments_commit(tmp_path):
    store = load_checkpoint_store()
    checkpoint_id = str(uuid4())
    store.stage_first_segment(tmp_path, checkpoint_id, b"resume tensors")

    with pytest.raises(store.CheckpointError, match="manifest is missing"):
        store.checkpoint_status(
            tmp_path, checkpoint_id, fingerprint="e" * 64, segment_count=3
        )


def test_checkpoint_routes_validate_and_discard_only_the_managed_checkpoint(tmp_path):
    import asyncio

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    store = load_checkpoint_store()
    checkpoint_id = str(uuid4())
    fingerprint = "c" * 64
    state_path = store.stage_first_segment(tmp_path, checkpoint_id, b"serialized AV resume tensors")
    store.commit_preview(
        tmp_path,
        checkpoint_id,
        fingerprint=fingerprint,
        segment_count=2,
        metadata={"model": "timeline-director-test"},
    )
    routes = web.RouteTableDef()
    store.register_checkpoint_routes(routes, lambda: tmp_path)
    app = web.Application()
    app.add_routes(routes)

    async def request_routes():
        async with TestClient(TestServer(app)) as client:
            status_response = await client.get(
                f"/minimax_h3_timeline/checkpoints/{checkpoint_id}",
                params={"fingerprint": fingerprint, "segment_count": "2"},
            )
            assert status_response.status == 200
            assert (await status_response.json())["resume_segment"] == 1

            invalid_response = await client.get(
                "/minimax_h3_timeline/checkpoints/not-a-uuid",
                params={"fingerprint": fingerprint, "segment_count": "2"},
            )
            assert invalid_response.status == 400

            delete_response = await client.delete(
                f"/minimax_h3_timeline/checkpoints/{checkpoint_id}"
            )
            assert delete_response.status == 200
            assert (await delete_response.json())["status"] == "deleted"

    asyncio.run(request_routes())
    assert not state_path.parent.exists()


@pytest.mark.parametrize("checkpoint_id", ["../escape", "not-a-uuid", "\\\\server\\share"])
def test_checkpoint_routes_reject_invalid_ids_and_outside_paths(tmp_path, checkpoint_id):
    store = load_checkpoint_store()
    with pytest.raises(store.CheckpointError):
        store.checkpoint_status(
            tmp_path, checkpoint_id, fingerprint="a" * 64, segment_count=1
        )
    with pytest.raises(store.CheckpointError):
        store.discard_checkpoint(tmp_path, checkpoint_id)
