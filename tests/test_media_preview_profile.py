import unittest
from pathlib import Path


COMMON_SCRIPT = Path(__file__).parents[1] / "scripts" / "h3_profile_common.sh"


class MediaPreviewProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = COMMON_SCRIPT.read_text(encoding="utf-8")

    def test_video_helper_suite_is_pinned_for_h3_profiles(self):
        self.assertIn('VHS_NODE_NAME="ComfyUI-VideoHelperSuite"', self.text)
        self.assertIn(
            'VHS_NODE_REPO="https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git"',
            self.text,
        )
        self.assertIn('VHS_NODE_REV="4d907bee61e92c2e65af3bd6383a4e4d356126d1"', self.text)
        self.assertIn("h3_profile_install_video_helper_suite", self.text)
        prepare = self.text[
            self.text.index("h3_profile_prepare_base() {"):
            self.text.index("h3_profile_install_requirements() {")
        ]
        self.assertIn("h3_profile_install_video_helper_suite", prepare)

    def test_finish_requires_video_helper_suite_preview_routes(self):
        self.assertIn("h3_profile_verify_media_preview_routes", self.text)
        self.assertIn("/vhs/viewvideo", self.text)
        self.assertIn("/vhs/viewaudio", self.text)
        finish = self.text[self.text.index("h3_profile_finish() {"):]
        self.assertIn("h3_profile_verify_media_preview_routes", finish)


if __name__ == "__main__":
    unittest.main()
