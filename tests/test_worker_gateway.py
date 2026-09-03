import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
