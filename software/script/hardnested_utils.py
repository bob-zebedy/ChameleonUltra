hardnested_sums = (
    0,
    32,
    56,
    64,
    80,
    96,
    104,
    112,
    120,
    128,
    136,
    144,
    152,
    160,
    176,
    192,
    200,
    224,
    256,
)
hardnested_nonces_sum_map = bytearray(256)
hardnested_first_byte_num = 0
hardnested_first_byte_sum = 0


def evenparity32(n):
    """
    calc evenparity32, can replace to any fast native impl...
    @param n - NT_ENC
    """
    return (n & 0xFFFFFFFF).bit_count() & 1


def check_nonce_unique_sum(nt, par):
    """
    Check nt_enc is unique and calc first byte sum
    Pay attention: thread unsafe!!!
    @param nt - NT_ENC
    @param par - parity of NT_ENC
    """
    global hardnested_first_byte_sum, hardnested_first_byte_num
    first_byte = (nt >> 24) & 0xFF
    if not hardnested_nonces_sum_map[first_byte]:
        hardnested_first_byte_sum += evenparity32((nt & 0xFF000000) | (par & 0x08))
        hardnested_nonces_sum_map[first_byte] = True
        hardnested_first_byte_num += 1


def reset():
    global hardnested_first_byte_num, hardnested_first_byte_sum
    global hardnested_nonces_sum_map
    # clear the history
    hardnested_nonces_sum_map = bytearray(256)
    hardnested_first_byte_sum = 0
    hardnested_first_byte_num = 0
