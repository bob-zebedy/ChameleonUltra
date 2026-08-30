import sys
import unittest
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

import hardnested_utils  # noqa: E402


class TestHardnestedUtils(unittest.TestCase):
    def setUp(self):
        hardnested_utils.reset()

    def test_duplicate_first_byte_is_counted_once(self):
        hardnested_utils.check_nonce_unique_sum(0xAA000001, 0)
        hardnested_utils.check_nonce_unique_sum(0xAAFFFFFF, 1)

        self.assertEqual(hardnested_utils.hardnested_first_byte_num, 1)
        self.assertEqual(sum(hardnested_utils.hardnested_nonces_sum_map), 1)

    def test_nonce_is_treated_as_u32(self):
        hardnested_utils.check_nonce_unique_sum((0x1FF << 24) | 1, 0)

        self.assertTrue(hardnested_utils.hardnested_nonces_sum_map[0xFF])
        self.assertEqual(hardnested_utils.hardnested_first_byte_num, 1)

    def test_reset_clears_all_state(self):
        hardnested_utils.check_nonce_unique_sum(0x01000000, 0)
        hardnested_utils.reset()

        self.assertEqual(hardnested_utils.hardnested_first_byte_num, 0)
        self.assertEqual(hardnested_utils.hardnested_first_byte_sum, 0)
        self.assertFalse(any(hardnested_utils.hardnested_nonces_sum_map))


if __name__ == "__main__":
    unittest.main()
