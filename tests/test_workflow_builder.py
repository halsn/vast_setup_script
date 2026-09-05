import unittest

from runtime.workflow_builder import build_h3_prompt


class WorkflowBuilderTests(unittest.TestCase):
    def test_default_random_seed_is_valid_for_comfy_random_noise(self):
        workflow = build_h3_prompt(
            "h3_t2v",
            "a paper boat on a calm lake",
            "",
            {"width": 768, "height": 512, "frames": 49, "seed": -1},
            [],
        )

        seed = workflow["noise"]["inputs"]["noise_seed"]
        self.assertIsInstance(seed, int)
        self.assertGreaterEqual(seed, 0)


if __name__ == "__main__":
    unittest.main()
