import re

LFSR48_FILTER_A = 0x9E98
LFSR48_FILTER_B = 0xB48E
LFSR48_FILTER_C = 0xEC57E80A
LFSR48_POLY = 0xE882B0AD621
U8_TO_ODD4 = tuple(
    ((i & 0x80) >> 4) + ((i & 0x20) >> 3) + ((i & 0x08) >> 2) + ((i & 0x02) >> 1)
    for i in range(256)
)


def u8_to_odd4(u8):
    return U8_TO_ODD4[u8 & 0xFF]


def get_bit(num, x=0):
    return (num >> x) & 1


def even_parity_u8(u8):
    return (u8 & 0xFF).bit_count() & 1


def odd_parity_u8(u8):
    return even_parity_u8(u8) ^ 1


def even_parity_u16(u16):
    return (u16 & 0xFFFF).bit_count() & 1


def even_parity_u48(u48):
    return (u48 & 0xFFFFFFFFFFFF).bit_count() & 1


def swap_endian_u16(u16):
    return ((u16 & 0xFF) << 8) | ((u16 >> 8) & 0xFF)


def swap_endian_u32(u32):
    return swap_endian_u16(u32 & 0xFFFF) << 16 | swap_endian_u16((u32 >> 16) & 0xFFFF)


"""
ref: https://web.archive.org/web/20081010065744/http://sar.informatik.hu-berlin.de/research/publications/SAR-PR-2008-21/SAR-PR-2008-21_.pdf
"""


class Crypto1:
    def __init__(self, new_lfsr48: int = 0):
        self.lfsr48 = new_lfsr48

    @property
    def key(self) -> str:
        return (self.lfsr48 & 0xFFFFFFFFFFFF).to_bytes(6, "little").hex()

    @key.setter
    def key(self, key: str):
        if re.fullmatch(r"[a-fA-F0-9]{12}", key) is None:
            raise ValueError(f"Invalid hex format key: {key}")
        self.lfsr48 = int.from_bytes(bytes.fromhex(key), "little")

    def lfsr48_filter(self):
        f = 0
        f |= get_bit(LFSR48_FILTER_B, u8_to_odd4(self.lfsr48 >> 8))  # fb4
        f |= get_bit(LFSR48_FILTER_A, u8_to_odd4(self.lfsr48 >> 16)) << 1  # fa4
        f |= get_bit(LFSR48_FILTER_A, u8_to_odd4(self.lfsr48 >> 24)) << 2  # fa4
        f |= get_bit(LFSR48_FILTER_B, u8_to_odd4(self.lfsr48 >> 32)) << 3  # fb4
        f |= get_bit(LFSR48_FILTER_A, u8_to_odd4(self.lfsr48 >> 40)) << 4  # fa4
        return get_bit(LFSR48_FILTER_C, f)

    def lfsr48_bit(self, bit_in: int = 0, is_encrypted: bool = False) -> int:
        out_bit = self.lfsr48_filter()
        bit_feedback = (
            even_parity_u48(LFSR48_POLY & self.lfsr48)
            ^ (bit_in & 1)
            ^ (is_encrypted & out_bit)
        )
        self.lfsr48 = (bit_feedback << 47) | (self.lfsr48 >> 1)
        return out_bit

    def lfsr48_u8(self, u8_in: int = 0, is_encrypted: bool = False) -> int:
        out_u8 = 0
        for i in range(8):
            tmp = self.lfsr48_bit(u8_in >> i, is_encrypted) << i
            out_u8 |= tmp
        return out_u8

    def lfsr48_u32(self, u32_in: int = 0, is_encrypted: bool = False) -> int:
        out_u32 = 0
        for i in range(3, -1, -1):
            bit_offset = i << 3
            out_u32 |= self.lfsr48_u8(u32_in >> bit_offset, is_encrypted) << bit_offset
        return out_u32

    @staticmethod
    def prng_next(lfsr32: int, n: int = 1) -> int:
        lfsr32 = swap_endian_u32(lfsr32)
        for _ in range(n):
            lfsr32 = even_parity_u8(0x2D & (lfsr32 >> 16)) << 31 | (lfsr32 >> 1)
        return swap_endian_u32(lfsr32)

    @staticmethod
    def mfkey32_is_reader_has_key(
        uid: int, nt: int, nrEnc: int, arEnc: int, key: str
    ) -> bool:
        state = Crypto1()
        state.key = key
        state.lfsr48_u32(uid ^ nt, False)  # ks0
        state.lfsr48_u32(nrEnc, True)  # ks1
        ks2 = state.lfsr48_u32(0, False)  # ks2
        ar = arEnc ^ ks2
        return ar == Crypto1.prng_next(nt, 64)
