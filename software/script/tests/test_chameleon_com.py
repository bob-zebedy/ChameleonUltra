import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

from chameleon_com import (  # noqa: E402
    ChameleonCom,
    NotOpenException,
    OpenFailException,
)
from chameleon_enum import Status  # noqa: E402


class TestChameleonCom(unittest.TestCase):
    def setUp(self):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        port = self.listener.getsockname()[1]

        self.com = ChameleonCom().open(f"tcp:127.0.0.1:{port}")
        self.peer, _ = self.listener.accept()
        self.peer.settimeout(1)

    def tearDown(self):
        self.com.close()
        self.peer.close()
        self.listener.close()

    def _receive_frame(self) -> bytes:
        frame = bytearray()
        while len(frame) < 9:
            frame.extend(self.peer.recv(4096))
        payload_length = struct.unpack_from("!H", frame, 6)[0]
        frame_length = 10 + payload_length
        while len(frame) < frame_length:
            frame.extend(self.peer.recv(4096))
        return bytes(frame[:frame_length])

    def test_lrc_and_frame_encoding(self):
        self.assertEqual(ChameleonCom.lrc_calc(b"\x01\x02\xfd"), 0)
        self.assertEqual(
            self.com.make_data_frame_bytes(0x1234, b"\x01\x02").hex(),
            "11ef123400000002b80102fd",
        )

    def test_sync_command_accepts_fragmented_response(self):
        command = 0x1234
        errors = []

        def emulate_device():
            try:
                request = self._receive_frame()
                self.assertEqual(request[2:4], command.to_bytes(2, "big"))
                response = self.com.make_data_frame_bytes(
                    command, b"response", Status.SUCCESS
                )
                for byte in response:
                    self.peer.sendall(bytes((byte,)))
            except Exception as exc:
                errors.append(exc)

        device_thread = threading.Thread(target=emulate_device)
        device_thread.start()
        response = self.com.send_cmd_sync(command, b"request", timeout=1)
        device_thread.join(1)

        self.assertFalse(device_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(response.cmd, command)
        self.assertEqual(response.status, Status.SUCCESS)
        self.assertEqual(response.data, b"response")

    def test_command_timeout_does_not_busy_wait(self):
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "exec timeout"):
            self.com.send_cmd_sync(0x1235, timeout=0.05)
        elapsed = time.monotonic() - started

        self.assertGreaterEqual(elapsed, 0.04)
        self.assertLess(elapsed, 0.5)
        self.assertEqual(self.com.wait_response_map, {})

    def test_peer_disconnect_releases_waiting_command(self):
        def disconnect_after_request():
            self._receive_frame()
            self.peer.shutdown(socket.SHUT_RDWR)
            self.peer.close()

        device_thread = threading.Thread(target=disconnect_after_request)
        device_thread.start()
        started = time.monotonic()
        with self.assertRaisesRegex(NotOpenException, "Connection closed"):
            self.com.send_cmd_sync(0x1236, timeout=1)
        device_thread.join(1)

        self.assertLess(time.monotonic() - started, 0.5)
        self.assertFalse(device_thread.is_alive())

    def test_duplicate_pending_command_is_rejected(self):
        self.com.send_cmd_auto(0x1237, timeout=1)
        with self.assertRaisesRegex(RuntimeError, "already has a pending request"):
            self.com.send_cmd_auto(0x1237, timeout=1)

    def test_instance_can_reopen_after_close(self):
        self.com.close()
        self.peer.close()
        self.listener.close()

        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        port = self.listener.getsockname()[1]
        self.com.open(f"tcp:127.0.0.1:{port}")
        self.peer, _ = self.listener.accept()
        self.peer.settimeout(1)

        command = 0x1238

        def emulate_device():
            self._receive_frame()
            self.peer.sendall(
                self.com.make_data_frame_bytes(command, b"reopened", Status.SUCCESS)
            )

        device_thread = threading.Thread(target=emulate_device)
        device_thread.start()
        response = self.com.send_cmd_sync(command, timeout=1)
        device_thread.join(1)

        self.assertEqual(response.data, b"reopened")
        self.assertFalse(device_thread.is_alive())

    def test_invalid_tcp_address_and_payload_are_rejected(self):
        with self.assertRaises(OpenFailException):
            ChameleonCom().open("tcp:missing-port")
        with self.assertRaisesRegex(ValueError, "maximum"):
            self.com.make_data_frame_bytes(1, bytes(4097))

    def test_tcp_address_parser_supports_ipv4_hostnames_and_ipv6(self):
        parse = ChameleonCom._parse_tcp_address
        self.assertEqual(parse("tcp:localhost:1234"), ("localhost", 1234))
        self.assertEqual(parse("tcp:[::1]:4321"), ("::1", 4321))
        self.assertEqual(parse("tcp:::1:9876"), ("::1", 9876))
        for address in ("tcp:localhost", "tcp::1234", "tcp:localhost:0"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                parse(address)


if __name__ == "__main__":
    unittest.main()
