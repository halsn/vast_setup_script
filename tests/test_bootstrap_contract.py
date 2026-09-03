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


if __name__ == "__main__":
    unittest.main()
