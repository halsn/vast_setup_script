import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "vast_comfy_bootstrap.sh"


class BootstrapContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding="utf-8")

    def test_token_guard_precedes_gateway_start(self):
        self.assertIn('if [[ -z "${H3_WORKER_TOKEN:-}" ]]; then', self.text)
        run_bootstrap = self.text.index("run_bootstrap()")
        guard_call = self.text.index("  require_worker_token\n", run_bootstrap)
        self.assertLess(
            guard_call,
            self.text.index("  start_worker_gateway", run_bootstrap),
        )

    def test_final_ready_checks_gateway_and_worker(self):
        self.assertIn("wait_for_gateway_health", self.text)
        self.assertIn("wait_for_worker_ready", self.text)
        self.assertLess(
            self.text.index("wait_for_gateway_health"),
            self.text.index('write_bootstrap_status "ready"'),
        )
        self.assertLess(
            self.text.index("wait_for_worker_ready"),
            self.text.index('write_bootstrap_status "ready"'),
        )

    def test_bootstrap_starts_official_base_when_it_is_not_running(self):
        self.assertIn("start_vast_comfy_base", self.text)
        self.assertIn("/opt/instance-tools/bin/entrypoint.sh", self.text)
        self.assertLess(
            self.text.index("start_vast_comfy_base()"),
            self.text.index("wait_for_vast_comfy_base()"),
        )

    def test_existing_runtime_checkout_is_refreshed(self):
        self.assertIn('git -C "$H3_RUNTIME_ROOT" fetch', self.text)
        self.assertIn('git -C "$H3_RUNTIME_ROOT" reset --hard', self.text)

    def test_existing_gateway_is_replaced_before_start(self):
        self.assertIn("pkill -f '[r]untime.worker_gateway'", self.text)
        self.assertLess(
            self.text.index("pkill -f '[r]untime.worker_gateway'"),
            self.text.index('nohup "$H3_COMFY_PYTHON" -m runtime.worker_gateway'),
        )


if __name__ == "__main__":
    unittest.main()
