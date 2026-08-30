import argparse
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

import chameleon_cli_unit as cli_module  # noqa: E402
from chameleon_cli_main import ChameleonCLI  # noqa: E402
from chameleon_cli_unit import (  # noqa: E402
    BaseCLIUnit,
    DataHexsamples,
    DataPlot,
    HF14AAntiCollArgsUnit,
    HFMFClone,
    HFMFELoad,
    HFMFView,
    load_dic_file,
    load_key_file,
    load_sector_key_pairs,
    mifare_sector_layout,
)
from chameleon_enum import MfcKeyType  # noqa: E402
from chameleon_utils import (  # noqa: E402
    ArgsParserError,
    UnexpectedResponseError,
    print_key_table,
)


class TestHF14AAntiCollArgsUnit(unittest.TestCase):
    def setUp(self):
        self.unit = HF14AAntiCollArgsUnit()
        self.unit._device_cmd = Mock()
        self.current = (b"\xde\xad\xbe\xef", b"\x04\x00", b"\x08", b"")

    @staticmethod
    def _args(**overrides):
        values = {
            "uid": None,
            "atqa": None,
            "sak": None,
            "ats": None,
            "delete_ats": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_accepts_complete_uid_bytes(self):
        result = self.unit.update_hf14a_anticoll(
            self._args(uid="01020304050607"), *self.current
        )

        self.assertEqual(result[2], bytes.fromhex("01020304050607"))
        self.unit.cmd.hf14a_set_anti_coll_data.assert_called_once()

    def test_rejects_partial_or_trailing_hex(self):
        for uid in ("0102", "DEADBEEF-not-hex"):
            with self.subTest(uid=uid), self.assertRaises(ArgsParserError):
                self.unit.update_hf14a_anticoll(self._args(uid=uid), *self.current)

    def test_rejects_odd_length_ats(self):
        with self.assertRaises(ArgsParserError):
            self.unit.update_hf14a_anticoll(self._args(ats="ABC"), *self.current)


class TestCLIHelpers(unittest.TestCase):
    def tearDown(self):
        cli_module._last_capture = None

    def test_hex_samples_prints_every_row(self):
        cli_module._last_capture = bytes(range(32))
        output = io.StringIO()
        with redirect_stdout(output):
            DataHexsamples().on_exec(argparse.Namespace(num=32))

        rendered = output.getvalue()
        self.assertIn(" 00 |", rendered)
        self.assertIn(" 01 |", rendered)

    def test_plot_handles_an_empty_selected_range(self):
        cli_module._last_capture = b"\x80"
        output = io.StringIO()
        with redirect_stdout(output):
            DataPlot().on_exec(argparse.Namespace(start=2, len=10, ascii=True))

        self.assertIn("No samples in the selected range", output.getvalue())

    def test_key_table_handles_empty_and_asymmetric_maps(self):
        output = io.StringIO()
        with redirect_stdout(output):
            print_key_table({"A": {1: "A0A1A2A3A4A5"}, "B": {2: "B0B1B2B3B4B5"}})
            print_key_table({"A": {}, "B": {}})

        rendered = output.getvalue()
        self.assertIn("01", rendered)
        self.assertIn("02", rendered)
        self.assertIn("A0A1A2A3A4A5", rendered)
        self.assertIn("B0B1B2B3B4B5", rendered)

    def test_key_file_loads_packed_six_byte_keys(self):
        first = bytes.fromhex("A0A1A2A3A4A5")
        second = bytes.fromhex("B0B1B2B3B4B5")
        keys = load_key_file(io.BytesIO(first + second), set())
        self.assertEqual(keys, {first, second})

        with self.assertRaises(ArgsParserError):
            load_key_file(io.BytesIO(b"short"), set())

    def test_dictionary_file_loads_keys_and_comments(self):
        keys = load_dic_file(
            io.StringIO("# defaults\nA0A1A2A3A4A5\nB0B1B2B3B4B5 # key B\n"),
            set(),
        )
        self.assertEqual(
            keys,
            {bytes.fromhex("A0A1A2A3A4A5"), bytes.fromhex("B0B1B2B3B4B5")},
        )

        with self.assertRaises(ArgsParserError):
            load_dic_file(io.StringIO("not-a-key\n"), set())

    def test_sector_key_pairs_load_binary_and_legacy_text(self):
        pairs = [
            (bytes.fromhex("A0A1A2A3A4A5"), bytes.fromhex("B0B1B2B3B4B5")),
            (bytes.fromhex("C0C1C2C3C4C5"), bytes.fromhex("D0D1D2D3D4D5")),
        ]
        packed = b"".join(key_a + key_b for key_a, key_b in pairs)
        legacy = "\n".join(f"{key_a.hex()}:{key_b.hex()}" for key_a, key_b in pairs)

        self.assertEqual(load_sector_key_pairs(io.BytesIO(packed), 2), pairs)
        self.assertEqual(load_sector_key_pairs(io.StringIO(legacy), 2), pairs)

    def test_sector_key_pairs_reject_invalid_content_or_sector_count(self):
        with self.assertRaises(ArgsParserError):
            load_sector_key_pairs(io.BytesIO(b"\xff"), 1)
        with self.assertRaises(ArgsParserError):
            load_sector_key_pairs(io.StringIO("A0A1A2A3A4A5:B0B1B2B3B4B5"), 2)

    def test_mifare_sector_layout_handles_4k_boundaries(self):
        self.assertEqual(mifare_sector_layout(0), (0, 4))
        self.assertEqual(mifare_sector_layout(31), (124, 4))
        self.assertEqual(mifare_sector_layout(32), (128, 16))
        self.assertEqual(mifare_sector_layout(39), (240, 16))
        for invalid_sector in (-1, 40):
            with self.subTest(sector=invalid_sector), self.assertRaises(ValueError):
                mifare_sector_layout(invalid_sector)

    def test_view_reads_all_256_blocks_of_a_4k_card(self):
        unit = HFMFView()
        unit._device_cmd = Mock()
        unit.cmd.mf1_read_one_block.side_effect = lambda block, _key_type, _key: (
            bytes((block,)) * 16
        )
        key_pair = bytes.fromhex("A0A1A2A3A4A5B0B1B2B3B4B5")
        args = argparse.Namespace(
            dump_file=None,
            key_file=io.BytesIO(key_pair * 40),
            maxSectors=40,
        )

        with (
            redirect_stdout(io.StringIO()),
            patch.object(cli_module, "print_mem_dump") as print_dump,
        ):
            unit.on_exec(args)

        read_blocks = [
            call.args[0] for call in unit.cmd.mf1_read_one_block.call_args_list
        ]
        self.assertEqual(read_blocks, list(range(256)))
        dumped_data, block_size = print_dump.call_args.args
        self.assertEqual(len(dumped_data), 256 * 16)
        self.assertEqual(block_size, 16)

    def test_subprocess_preserves_literal_arguments_and_output(self):
        argument = "$(echo must-not-run)"
        process = BaseCLIUnit.sub_process(
            [
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1])",
                argument,
            ],
            cwd=SCRIPT_ROOT,
        )

        self.assertEqual(process.wait_process(), 0)
        self.assertEqual(process.get_output_sync(), argument + "\n")

    def test_cli_closes_files_created_by_argument_parser(self):
        with tempfile.TemporaryFile() as argument_file:
            ChameleonCLI._close_argument_files(
                argparse.Namespace(first=argument_file, duplicate=argument_file)
            )
            self.assertTrue(argument_file.closed)

    def test_emulator_load_writes_a_single_final_block(self):
        payload = bytes(range(16))
        unit = HFMFELoad()
        unit._device_com = SimpleNamespace(data_max_length=4096)
        unit._device_cmd = Mock()

        with tempfile.TemporaryDirectory() as directory:
            dump_path = Path(directory) / "one-block.bin"
            dump_path.write_bytes(payload)
            args = argparse.Namespace(file=str(dump_path), type="bin")
            with redirect_stdout(io.StringIO()):
                unit.on_exec(args)

        unit.cmd.mf1_write_emu_block_data.assert_called_once_with(0, payload)

    def test_clone_supports_mini_dump_and_skips_missing_key_b(self):
        payload = b"".join(bytes((block,)) * 16 for block in range(20))
        dump_file = io.BytesIO(payload)
        dump_file.name = "mini.bin"
        key_file = io.StringIO("FFFFFFFFFFFF")
        unit = HFMFClone()
        unit._device_cmd = Mock()

        def read_block(_block, key_type, _key):
            if key_type is MfcKeyType.B:
                raise UnexpectedResponseError("key B unavailable")
            return bytes(16)

        unit.cmd.mf1_read_one_block.side_effect = read_block
        args = argparse.Namespace(
            dump_file_type=None,
            dump_file=dump_file,
            dic=key_file,
            clone_access=False,
        )
        with redirect_stdout(io.StringIO()):
            unit.on_exec(args)

        write_calls = unit.cmd.mf1_write_one_block.call_args_list
        self.assertEqual(len(write_calls), 20)
        self.assertEqual([call.args[0] for call in write_calls], list(range(20)))
        self.assertTrue(all(call.args[1] is MfcKeyType.A for call in write_calls))
        expected_trailer = bytes((3,)) * 6 + bytes.fromhex("ff0780") + bytes((3,)) * 7
        self.assertEqual(write_calls[3].args[3], expected_trailer)

    def test_clone_rejects_unknown_dump_size(self):
        dump_file = io.BytesIO(bytes(32 * 16))
        dump_file.name = "unsupported.bin"
        unit = HFMFClone()

        with self.assertRaises(ArgsParserError):
            unit.on_exec(
                argparse.Namespace(
                    dump_file_type=None,
                    dump_file=dump_file,
                    dic=io.StringIO("FFFFFFFFFFFF\n"),
                    clone_access=False,
                )
            )


if __name__ == "__main__":
    unittest.main()
