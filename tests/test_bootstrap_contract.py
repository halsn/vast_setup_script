import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "vast_comfy_bootstrap.sh"
BASE_SCRIPT = Path(__file__).parents[1] / "scripts" / "setupp_h3_comfui.sh"


class BootstrapContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding="utf-8")
        cls.base_text = BASE_SCRIPT.read_text(encoding="utf-8")

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

    def test_legacy_model_cache_is_reused_before_fast_bootstrap(self):
        self.assertIn("migrate_legacy_h3_models", self.text)
        for model_path in (
            "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
            "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
            "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
            "vae/minimax_h3_video_vae_fp16.safetensors",
            "vae/minimax_h3_audio_vae_fp32.safetensors",
        ):
            self.assertIn(model_path, self.text)
        self.assertIn("migrate_legacy_h3_models() {", self.text)
        migration = self.text.index("      migrate_legacy_h3_models\n")
        fast_bootstrap = self.text.index('H3_BOOTSTRAP_STAGE="fast_bootstrap"')
        self.assertLess(migration, fast_bootstrap)

    def test_legacy_model_migration_has_a_local_logger(self):
        self.assertIn("log_info() {", self.text)
        self.assertIn("log_warn() {", self.text)

    def test_model_size_validation_follows_legacy_cache_symlinks(self):
        self.assertIn("stat -Lc '%s'", self.base_text)

    def test_bootstrap_pid_is_removed_after_process_exit(self):
        self.assertIn(
            'H3_BOOTSTRAP_PID_FILE="${H3_BOOTSTRAP_PID_FILE:-/run/h3/bootstrap.pid}"',
            self.text,
        )
        self.assertIn('printf \'%s\\n\' "$$" >"$H3_BOOTSTRAP_PID_FILE"', self.text)
        self.assertIn('rm -f "$H3_BOOTSTRAP_PID_FILE"', self.text)


if __name__ == "__main__":
    unittest.main()
