import json
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from runtime.worker_gateway import create_app


class WorkerGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        (root / "runtime.json").write_text(
            json.dumps({"status": "not_ready"}), encoding="utf-8"
        )
        (root / "templates.json").write_text(
            json.dumps({"templates": []}), encoding="utf-8"
        )
        self.client = TestClient(
            TestServer(
                create_app(
                    token="test-token",
                    runtime_config_path=str(root / "runtime.json"),
                    template_catalog_path=str(root / "templates.json"),
                    comfyui_dir=str(root / "ComfyUI"),
                    bootstrap_status_path=str(root / "bootstrap.json"),
                )
            )
        )
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tempdir.cleanup()

    async def test_healthz_requires_bearer_token(self):
        unauthorized = await self.client.get("/healthz")
        self.assertEqual(unauthorized.status, 401)
        healthy = await self.client.get(
            "/healthz", headers={"Authorization": "Bearer test-token"}
        )
        self.assertEqual(healthy.status, 200)
        self.assertEqual((await healthy.json())["status"], "healthy")

    async def test_ready_stays_unavailable_until_runtime_is_ready(self):
        response = await self.client.get(
            "/ready", headers={"Authorization": "Bearer test-token"}
        )
        self.assertEqual(response.status, 503)

    async def test_ready_reports_live_comfy_attention_and_keeps_worker_profile_separate(self):
        root = Path(self.tempdir.name)
        (root / "runtime.json").write_text(json.dumps({
            "status": "ready", "backend": "sdpa", "models": [],
            "gpu": {"name": "RTX 5090", "total_memory_mib": 32768, "compute_capability": [12, 0]},
        }))
        observed = {"configured_backend": "sage", "last_backend": "pytorch",
                    "last_fallback_reason": "int32 guard", "versions": {"sageattention": "2.2.0"}}
        async def status(request):
            return web.json_response(observed)
        upstream = web.Application()
        upstream.router.add_get("/h3/runtime_status", status)
        server = TestServer(upstream)
        await server.start_server()
        gateway = TestClient(TestServer(create_app(
            token="test-token", upstream_url=str(server.make_url("")),
            runtime_config_path=str(root / "runtime.json"),
            template_catalog_path=str(root / "templates.json"), comfyui_dir=str(root / "ComfyUI"),
        )))
        await gateway.start_server()
        try:
            for last in ("pytorch", "sage", None):
                observed["last_backend"] = last
                response = await gateway.get("/ready", headers={"Authorization": "Bearer test-token"})
                self.assertEqual(response.status, 200)
                payload = await response.json()
                self.assertEqual(payload["backend"], last or "sage")
                self.assertEqual(payload["worker_backend"], "sdpa")
                self.assertEqual(payload["attention"], observed)
            observed.clear()
            response = await gateway.get("/ready", headers={"Authorization": "Bearer test-token"})
            self.assertEqual((await response.json())["backend"], "unknown")
        finally:
            await gateway.close()
            await server.close()

    async def test_logical_job_history_and_interrupt_use_remote_prompt_id(self):
        interrupt_payloads = []
        comfy = Path(self.tempdir.name) / "ComfyUI"
        comfy.mkdir()
        (comfy / ".h3-video-vae.json").write_text(json.dumps({
            "video_vae": "minimax_h3_video_vae_int8_convrot.safetensors"}))

        async def prompt(request):
            body = await request.json()
            self.assertIn("prompt", body)
            self.assertEqual(body["prompt"]["video_vae"]["inputs"]["vae_name"],
                             "minimax_h3_video_vae_int8_convrot.safetensors")
            return web.json_response({"prompt_id": "remote-job"})

        async def history(request):
            self.assertEqual(request.path, "/history/remote-job")
            return web.json_response(
                {
                    "remote-job": {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {"save": {"videos": [{"filename": "video.mp4"}]}},
                    }
                }
            )

        async def interrupt(request):
            interrupt_payloads.append(await request.json())
            return web.json_response({"ok": True})

        upstream_app = web.Application()
        upstream_app.router.add_post("/prompt", prompt)
        upstream_app.router.add_get("/history/remote-job", history)
        upstream_app.router.add_post("/interrupt", interrupt)
        upstream_server = TestServer(upstream_app)
        await upstream_server.start_server()
        gateway = TestClient(
            TestServer(
                create_app(
                    token="test-token",
                    upstream_url=str(upstream_server.make_url("")),
                    runtime_config_path=str(Path(self.tempdir.name) / "runtime.json"),
                    template_catalog_path=str(Path(self.tempdir.name) / "templates.json"),
                    comfyui_dir=str(Path(self.tempdir.name) / "ComfyUI"),
                )
            )
        )
        await gateway.start_server()
        headers = {"Authorization": "Bearer test-token"}
        try:
            submitted = await gateway.post(
                "/prompt",
                headers=headers,
                json={
                    "job_id": "local-job",
                    "template_id": "h3_t2v",
                    "prompt": "fox",
                    "negative_prompt": "",
                    "parameters": {"width": 512, "height": 512, "frames": 49, "seed": -1},
                    "assets": [],
                },
            )
            self.assertEqual(submitted.status, 200)
            self.assertEqual((await submitted.json())["remote_job_id"], "remote-job")

            history_response = await gateway.get("/history/local-job", headers=headers)
            self.assertEqual(history_response.status, 200)
            self.assertEqual((await history_response.json())["remote-job"]["status"]["status_str"], "success")

            interrupted = await gateway.post(
                "/interrupt", headers=headers, json={"prompt": "local-job"}
            )
            self.assertEqual(interrupted.status, 200)
            self.assertEqual(interrupt_payloads, [{"prompt": "remote-job"}])
        finally:
            await gateway.close()
            await upstream_server.close()


if __name__ == "__main__":
    unittest.main()
