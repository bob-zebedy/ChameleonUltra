import sys
import unittest
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

from crypto1 import (  # noqa: E402
    Crypto1,
    even_parity_u8,
    even_parity_u16,
    even_parity_u48,
    odd_parity_u8,
)


class TestCrypto1(unittest.TestCase):
    def test_key_getter_setter(self):
        state = Crypto1()
        state.key = "a0a1a2a3a4a5"
        self.assertEqual(state.key, "a0a1a2a3a4a5")

    def test_invalid_key_is_rejected(self):
        state = Crypto1()
        for key in ("", "0011", "00112233445Z", "00112233445566"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                state.key = key

    def test_parity_helpers_mask_to_their_declared_width(self):
        self.assertEqual(even_parity_u8(0x101), 1)
        self.assertEqual(odd_parity_u8(0x101), 0)
        self.assertEqual(even_parity_u16(0x10001), 1)
        self.assertEqual(even_parity_u48((1 << 48) | 0b1011), 1)

    def test_prng_next(self):
        self.assertEqual(Crypto1.prng_next(0x2C198BE4, 64), 0xCC14C013)

    def test_reader_three_pass_auth(self):
        uid, nt, nr, atEnc = 0x65535D33, 0xBE2B7B5D, 0x0B4271BA, 0x36081500
        reader = Crypto1()
        reader.key = "974C262B9278"
        ks0 = reader.lfsr48_u32(uid ^ nt, False)
        self.assertEqual(ks0, 0xAC93C1A4, "ks0 assert failed")
        ks1 = reader.lfsr48_u32(nr, False)
        self.assertEqual(ks1, 0xBAA3C92B, "ks1 assert failed")
        nrEnc = nr ^ ks1
        self.assertEqual(nrEnc, 0xB1E1B891, "nrEnc assert failed")
        ar = Crypto1.prng_next(nt, 64)
        self.assertEqual(ar, 0xF0928568, "ar assert failed")
        ks2 = reader.lfsr48_u32(0, False)
        self.assertEqual(ks2, 0xDC652720, "ks2 assert failed")
        arEnc = ar ^ ks2
        self.assertEqual(arEnc, 0x2CF7A248, "arEnc assert failed")
        ks3 = reader.lfsr48_u32(0, False)
        self.assertEqual(ks3, 0xC6F4A093, "ks3 assert failed")
        at = atEnc ^ ks3
        nt96 = Crypto1.prng_next(nt, 96)
        self.assertEqual(at, nt96, "at assert failed")

    def test_tag_three_pass_auth(self):
        uid, nt, nrEnc, arEnc = 0x65535D33, 0xBE2B7B5D, 0xB1E1B891, 0x2CF7A248
        tag = Crypto1()
        tag.key = "974C262B9278"
        ks0 = tag.lfsr48_u32(uid ^ nt, False)
        self.assertEqual(ks0, 0xAC93C1A4, "ks0 assert failed")
        ks1 = tag.lfsr48_u32(nrEnc, True)
        self.assertEqual(ks1, 0xBAA3C92B, "ks1 assert failed")
        nr = ks1 ^ nrEnc
        self.assertEqual(nr, 0x0B4271BA, "nr assert failed")
        ks2 = tag.lfsr48_u32(0, False)
        self.assertEqual(ks2, 0xDC652720, "ks2 assert failed")
        ar = ks2 ^ arEnc
        self.assertEqual(ar, 0xF0928568, "ar assert failed")
        at = Crypto1.prng_next(nt, 96)
        self.assertEqual(at, 0xF0FCB593, "at assert failed")
        ks3 = tag.lfsr48_u32(0, False)
        self.assertEqual(ks3, 0xC6F4A093, "ks3 assert failed")
        atEnc = at ^ ks3
        self.assertEqual(atEnc, 0x36081500, "atEnc assert failed")

    def test_mfkey32_is_reader_has_key_true(self):
        self.assertTrue(
            Crypto1.mfkey32_is_reader_has_key(
                uid=0x65535D33,
                nt=0x2C198BE4,
                nrEnc=0xFEDAC6D2,
                arEnc=0xCF0A3C7E,
                key="A9AC67832330",
            )
        )

    def test_mfkey32_is_reader_has_key_false(self):
        self.assertFalse(
            Crypto1.mfkey32_is_reader_has_key(
                uid=0x65535D33,
                nt=0x2C198BE4,
                nrEnc=0xFEDAC6D2,
                arEnc=0xCF0A3C7E,
                key="FFFFFFFFFFFF",
            )
        )


if __name__ == "__main__":
    unittest.main()
