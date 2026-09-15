import unittest
from pathlib import Path


BASE_SCRIPT = Path(__file__).parents[1] / "scripts" / "setupp_h3_comfui.sh"
VIDEO_HELPER_COMMIT = "4d907bee61e92c2e65af3bd6383a4e4d356126d1"


class MediaPreviewContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = BASE_SCRIPT.read_text(encoding="utf-8")

    def test_base_h3_installs_pinned_video_helper_for_transcoded_previews(self):
        self.assertIn("ComfyUI-VideoHelperSuite", self.text)
        self.assertIn("https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git", self.text)
        self.assertIn(VIDEO_HELPER_COMMIT, self.text)
        self.assertIn("install_video_helper_preview", self.text)

    def test_video_helper_is_installed_in_the_common_custom_node_stage(self):
        function_start = self.text.index("install_custom_nodes() {")
        function_end = self.text.index("detect_sageattention()", function_start)
        install_stage = self.text[function_start:function_end]
        self.assertIn("install_video_helper_preview", install_stage)


if __name__ == "__main__":
    unittest.main()
