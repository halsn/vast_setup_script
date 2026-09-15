import unittest
from pathlib import Path


BASE_SCRIPT = Path(__file__).parents[1] / "scripts" / "setupp_h3_comfui.sh"


class MediaPreviewProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = BASE_SCRIPT.read_text(encoding="utf-8")

    def test_video_helper_suite_is_pinned_and_installed_for_all_h3_profiles(self):
        self.assertIn('VHS_NODE_NAME="ComfyUI-VideoHelperSuite"', self.text)
        self.assertIn(
            'VHS_NODE_REPO="https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git"',
            self.text,
        )
        self.assertIn('VHS_NODE_REV="4d907bee61e92c2e65af3bd6383a4e4d356126d1"', self.text)
        self.assertIn("install_pinned_video_helper_suite", self.text)
        install_body = self.text[self.text.index("install_custom_nodes() {"):self.text.index("detect_sageattention() {")]
        self.assertIn("install_pinned_video_helper_suite", install_body)

    def test_health_checks_require_video_helper_suite_preview_route(self):
        self.assertIn("verify_media_preview_routes", self.text)
        self.assertIn("/vhs/viewvideo", self.text)
        self.assertIn("/vhs/viewaudio", self.text)
        health = self.text[self.text.index("run_health_checks() {"):self.text.index("print_summary() {")]
        self.assertIn("verify_media_preview_routes", health)


if __name__ == "__main__":
    unittest.main()
