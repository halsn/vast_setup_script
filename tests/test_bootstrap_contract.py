import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "vast_comfy_bootstrap.sh"
BASE_SCRIPT = Path(__file__).parents[1] / "scripts" / "h3_comfui_base.sh"


class BootstrapContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding="utf-8")
        cls.base_text = BASE_SCRIPT.read_text(encoding="utf-8")

    def test_main_bootstrap_does_not_require_worker_gateway(self):
        run_bootstrap = self.text[self.text.index("run_bootstrap()"):]
        self.assertNotIn("  require_worker_token\n", run_bootstrap)
        self.assertNotIn("  start_worker_gateway\n", run_bootstrap)
        self.assertNotIn("  wait_for_gateway_health\n", run_bootstrap)
        self.assertNotIn("  wait_for_worker_ready\n", run_bootstrap)

    def test_final_ready_depends_on_native_comfyui(self):
        run_bootstrap = self.text[self.text.index("run_bootstrap()"):]
        self.assertIn("  wait_for_comfyui\n", run_bootstrap)
        self.assertLess(
            run_bootstrap.index("  wait_for_comfyui\n"),
            run_bootstrap.index('write_bootstrap_status "ready"'),
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

    def test_official_vast_workspace_comfyui_is_preferred_over_legacy_cache(self):
        self.assertIn('"${WORKSPACE:-/workspace}/ComfyUI"', self.text)
        self.assertIn('H3_COMFY_DIR="${WORKSPACE:-/workspace}/ComfyUI"', self.text)

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

    def test_stable_bootstrap_waits_for_current_vast_supervisor_service(self):
        self.assertIn("wait_for_vast_comfy_service", self.base_text)
        self.assertIn("supervisorctl status comfyui", self.base_text)
        self.assertIn("/opt/instance-tools/bin/entrypoint.sh", self.base_text)
        self.assertIn("/opt/workspace-internal/ComfyUI/main.py", self.base_text)
        self.assertNotIn("/etc/vast_boot.d/boot_default.sh", self.base_text)

    def test_comfyui_is_patched_for_public_native_api(self):
        self.assertIn("--listen", self.base_text)
        self.assertIn("0.0.0.0", self.base_text)
        self.assertIn("--enable-cors-header", self.base_text)

    def test_initial_download_uses_dynamic_disk_budget(self):
        self.assertIn("get_missing_model_disk_budget_gb", self.base_text)
        self.assertIn("model_expected_gb", self.base_text)
        self.assertNotIn("disk >= 160", self.base_text)
        self.assertIn(
            "estimated free-space requirement: ${required_disk} GB",
            self.base_text,
        )
        self.assertIn('H3_STAGE="preflight validation"', self.base_text)

    def test_ready_requires_h3_models_in_comfyui_object_catalog(self):
        self.assertIn("validate_comfyui_model_catalog", self.base_text)
        self.assertIn("/object_info", self.base_text)
        self.assertIn("UNETLoader", self.base_text)
        self.assertIn("CLIPLoader", self.base_text)
        self.assertIn("VAELoader", self.base_text)
        self.assertIn(
            "ComfyUI model catalog contains all required H3 models",
            self.base_text,
        )

    def test_model_size_validation_follows_legacy_cache_symlinks(self):
        self.assertIn("stat -Lc '%s'", self.base_text)

    def test_official_h3_models_are_revision_pinned_and_sha_verified(self):
        self.assertIn(
            'H3_MODEL_REV="${H3_MODEL_REV:-0bd506d2e895983a9663037febda27aa3948cf48}"',
            self.base_text,
        )
        expected = {
            "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors": (
                "20970379616",
                "e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a",
            ),
            "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors": (
                "20970379616",
                "9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779",
            ),
            "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors": (
                "15687142551",
                "35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6",
            ),
            "vae/minimax_h3_video_vae_fp16.safetensors": (
                "5207808496",
                "7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522",
            ),
            "vae/minimax_h3_audio_vae_fp32.safetensors": (
                "605254808",
                "8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48",
            ),
        }
        for model_path, (size, digest) in expected.items():
            self.assertIn(model_path, self.base_text)
            self.assertIn(size, self.base_text)
            self.assertIn(digest, self.base_text)
        self.assertIn("model_file_matches_release", self.base_text)
        self.assertIn("sha256sum", self.base_text)
        self.assertIn("revision=revision", self.base_text)
        self.assertIn("Removing model that does not match the pinned release identity", self.base_text)

    def test_bootstrap_pid_is_removed_after_process_exit(self):
        self.assertIn(
            'H3_BOOTSTRAP_PID_FILE="${H3_BOOTSTRAP_PID_FILE:-/run/h3/bootstrap.pid}"',
            self.text,
        )
        self.assertIn('printf \'%s\\n\' "$$" >"$H3_BOOTSTRAP_PID_FILE"', self.text)
        self.assertIn('rm -f "$H3_BOOTSTRAP_PID_FILE"', self.text)


if __name__ == "__main__":
    unittest.main()
