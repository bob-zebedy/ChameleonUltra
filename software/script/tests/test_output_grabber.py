import sys
import unittest
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

from tests.output_grabber import OutputGrabber  # noqa: E402


class TestOutputGrabber(unittest.TestCase):
    def test_captures_large_unicode_output(self):
        output = OutputGrabber()
        expected = "x" * 100_000 + " 中文"

        with output:
            print(expected, end="")

        self.assertEqual(output.captured_text, expected)

    def test_can_be_reused(self):
        output = OutputGrabber()
        with output:
            print("first", end="")
        self.assertEqual(output.captured_text, "first")

        with output:
            print("second", end="")
        self.assertEqual(output.captured_text, "second")


if __name__ == "__main__":
    unittest.main()
