import platform
import queue
import socket
import struct
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

import serial

from chameleon_enum import Command, Status
from chameleon_utils import CC, CG, CR, CY, color_string

ANDROID = "android" in platform.release()

# each thread is waiting for its data for 100 ms before looping again
THREAD_BLOCKING_TIMEOUT = 0.1

# TODO: client settings
DEBUG = False

_FRAME_METADATA_FORMAT = "!BBHHH"
_FRAME_METADATA_SIZE = struct.calcsize(_FRAME_METADATA_FORMAT)
_FRAME_HEADER_SIZE = struct.calcsize("!BBHHHB")


class TransportType(Enum):
    NONE = auto()
    SERIAL = auto()
    SOCKET = auto()


class NotOpenException(Exception):
    """
    Chameleon err status
    """


class OpenFailException(Exception):
    """
    Chameleon open fail(serial port may be error)
    """


class CMDInvalidException(Exception):
    """
    CMD invalid(Unsupported)
    """


@dataclass
class Response:
    """Response returned by a Chameleon command."""

    cmd: int
    status: int
    data: bytes = b""
    parsed: Any = None


@dataclass(slots=True)
class _PendingRequest:
    deadline: float
    callback: Callable[[int, int | None, bytes | None], None] | None = None
    response: Response | None = None
    timed_out: bool = False
    connection_closed: bool = False
    completed: threading.Event = field(default_factory=threading.Event)


class ChameleonCom:
    """
    Chameleon device base class
    Communication and Data frame implemented
    """

    data_frame_sof = 0x11
    data_max_length = 4096

    def __init__(self):
        """
        Create a chameleon device instance
        """
        self.transport: serial.Serial | socket.socket | None = None
        self.transport_type = TransportType.NONE
        self.send_data_queue = queue.Queue()
        self.wait_response_map: dict[int, _PendingRequest] = {}
        self.event_closing = threading.Event()
        self.commands: list[int] = []
        self._state_lock = threading.RLock()
        self._worker_threads: list[threading.Thread] = []

    def isOpen(self) -> bool:
        """
            Chameleon is connected and init.

        :return:
        """
        with self._state_lock:
            return self._is_open_unlocked()

    def _is_open_unlocked(self) -> bool:
        if self.transport is None:
            return False
        if self.transport_type is TransportType.SOCKET:
            return True
        return bool(getattr(self.transport, "is_open", False))

    def open(self, port: str) -> "ChameleonCom":
        """
            Open chameleon port to communication
            And init some variables

        :param port: com port, comXXX or ttyXXX
        :return:
        """
        if self.isOpen():
            return self

        # A previous connection may have closed asynchronously. Make sure its
        # workers are gone before reusing this instance for another transport.
        if self.transport is not None or any(
            worker.is_alive() for worker in self._worker_threads
        ):
            self.close()
            self._join_worker_threads()

        transport: serial.Serial | socket.socket | None = None
        transport_type = TransportType.NONE
        try:
            if port.startswith("tcp:"):
                host, tcp_port = self._parse_tcp_address(port)
                if DEBUG:
                    print("Connecting to", host, tcp_port)
                transport = socket.create_connection((host, tcp_port), timeout=5)
                transport.settimeout(THREAD_BLOCKING_TIMEOUT)
                transport_type = TransportType.SOCKET
            else:
                if ANDROID:
                    raise OSError(
                        "COM ports are unavailable on Android; use a USB-serial to TCP bridge"
                    )
                transport = serial.Serial(
                    port=port,
                    baudrate=115200,
                    timeout=THREAD_BLOCKING_TIMEOUT,
                )
                transport_type = TransportType.SERIAL
                try:
                    transport.dtr = True
                except (AttributeError, OSError, ValueError):
                    # Not every serial implementation supports DTR.
                    pass
        except (OSError, ValueError) as exc:
            if transport is not None:
                with suppress(OSError):
                    transport.close()
            raise OpenFailException(str(exc)) from exc

        with self._state_lock:
            self.transport = transport
            self.transport_type = transport_type
            self.send_data_queue = queue.Queue()
            self.wait_response_map.clear()
            self.event_closing = threading.Event()
        self._start_worker_threads()
        return self

    @staticmethod
    def _parse_tcp_address(address: str) -> tuple[str, int]:
        if not address.startswith("tcp:"):
            raise ValueError("TCP address must start with tcp:")
        host, separator, port_text = address.removeprefix("tcp:").rpartition(":")
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        if not separator or not host or not port_text:
            raise ValueError("TCP address must use tcp:<host>:<port>")
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError("TCP port must be between 1 and 65535")
        return host, port

    def _join_worker_threads(self) -> None:
        current_thread = threading.current_thread()
        if current_thread in self._worker_threads:
            raise OpenFailException(
                "Cannot reopen a connection from a communication worker"
            )
        for worker in self._worker_threads:
            worker.join(THREAD_BLOCKING_TIMEOUT * 2)
        alive_workers = [
            worker.name for worker in self._worker_threads if worker.is_alive()
        ]
        if alive_workers:
            raise OpenFailException(
                f"Previous communication workers did not stop: {', '.join(alive_workers)}"
            )
        self._worker_threads.clear()

    def _start_worker_threads(self) -> None:
        workers = (
            ("chameleon-receive", self.thread_data_receive),
            ("chameleon-transfer", self.thread_data_transfer),
            ("chameleon-timeout", self.thread_check_timeout),
        )
        self._worker_threads = [
            threading.Thread(name=name, target=target, daemon=True)
            for name, target in workers
        ]
        for worker in self._worker_threads:
            worker.start()

    def check_open(self) -> None:
        """

        :return:
        """
        if not self.isOpen():
            raise NotOpenException("Please call open() function to start device.")

    @staticmethod
    def lrc_calc(array: bytearray | bytes) -> int:
        """
            Calc lrc and auto cut byte.

        :param array: value array
        :return: u8 result
        """
        # add and cut byte and return
        return (-sum(array)) & 0xFF

    def close(self) -> None:
        """
            Close chameleon and clear variable.

        :return:
        """
        with self._state_lock:
            self.event_closing.set()
            transport = self.transport
            transport_type = self.transport_type
            self.transport = None
            self.transport_type = TransportType.NONE
            pending_requests = list(self.wait_response_map.items())
            for _, pending in pending_requests:
                pending.connection_closed = True
                pending.completed.set()
            self.wait_response_map.clear()

        if transport is not None:
            if transport_type is TransportType.SOCKET:
                with suppress(OSError):
                    transport.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                transport.close()

        for cmd, pending in pending_requests:
            if pending.callback is not None:
                try:
                    pending.callback(cmd, None, None)
                except Exception as exc:
                    print(f"Command close callback failed: {exc}")

        while True:
            try:
                self.send_data_queue.get_nowait()
            except queue.Empty:
                break
            else:
                self.send_data_queue.task_done()

    def thread_data_receive(self):
        """
            Sub thread to receive data from chameleon device.

        :return:
        """
        data_buffer = bytearray()
        data_position = 0
        data_cmd = 0x0000
        data_status = 0x0000
        data_length = 0x0000

        while self.isOpen():
            # receive
            with self._state_lock:
                transport = self.transport
                transport_type = self.transport_type
            if transport is None:
                break
            if transport_type is TransportType.SERIAL:
                try:
                    data_bytes = transport.read(
                        max(1, min(transport.in_waiting, self.data_max_length))
                    )
                except OSError as exc:
                    if not self.event_closing.is_set():
                        print(f"Serial Error {exc}, thread for receiver exit.")
                    self.close()
                    break
            else:  # SOCKET
                try:
                    data_bytes = transport.recv(1024)
                except TimeoutError:
                    continue
                except OSError:
                    if not self.event_closing.is_set():
                        print(color_string((CR, "Socket closed")))
                    self.close()
                    break
                if not data_bytes:
                    self.close()
                    break

            for data_byte in data_bytes:
                data_buffer.append(data_byte)
                if data_position < 2:  # start of frame + lrc1
                    if (
                        data_position == 0
                        and data_buffer[data_position] != self.data_frame_sof
                    ):
                        print("Data frame no sof byte.")
                        data_position = 0
                        data_buffer.clear()
                        continue
                    if data_position == 1 and data_buffer[
                        data_position
                    ] != self.lrc_calc(data_buffer[:data_position]):
                        data_position = 0
                        data_buffer.clear()
                        print("Data frame sof lrc error.")
                        continue
                elif data_position == _FRAME_METADATA_SIZE:  # frame head lrc
                    if data_buffer[data_position] != self.lrc_calc(
                        data_buffer[:data_position]
                    ):
                        data_position = 0
                        data_buffer.clear()
                        print("Data frame head lrc error.")
                        continue
                    # frame head complete, cache info
                    _, _, data_cmd, data_status, data_length = struct.unpack(
                        _FRAME_METADATA_FORMAT, data_buffer[:data_position]
                    )
                    if data_length > self.data_max_length:
                        data_position = 0
                        data_buffer.clear()
                        print("Data frame data length larger than max.")
                        continue
                elif data_position > _FRAME_METADATA_SIZE:  # frame data
                    if data_position == _FRAME_HEADER_SIZE + data_length:
                        if data_buffer[data_position] == self.lrc_calc(
                            data_buffer[:data_position]
                        ):
                            # ok, lrc for data is correct.
                            # and we are receive completed
                            # print(f"Buffer data = {data_buffer.hex()}")
                            data_response = bytes(
                                data_buffer[
                                    _FRAME_HEADER_SIZE : _FRAME_HEADER_SIZE
                                    + data_length
                                ]
                            )
                            if DEBUG:
                                try:
                                    command = Command(data_cmd)
                                    command_string = f"{data_cmd} {command.name}"
                                except ValueError:
                                    command_string = f"{data_cmd} (unknown)"
                                try:
                                    status_string = str(Status(data_status))
                                    if data_status == Status.SUCCESS:
                                        status_string = color_string(
                                            (CG, status_string.ljust(30))
                                        )
                                    else:
                                        status_string = color_string(
                                            (CR, status_string.ljust(30))
                                        )
                                except ValueError:
                                    status_string = f"{data_status:30x}"
                                response = data_response.hex()
                                print(
                                    f"<={color_string((CC, command_string.ljust(40)), (CR, status_string), (CY, response))}"
                                )
                            self._complete_request(data_cmd, data_status, data_response)
                        else:
                            print("Data frame global lrc error.")
                        data_position = 0
                        data_buffer.clear()
                        continue
                data_position += 1

    def _complete_request(self, cmd: int, status: int, data: bytes) -> None:
        with self._state_lock:
            pending = self.wait_response_map.pop(cmd, None)
            if pending is not None:
                pending.response = Response(cmd, status, data)
                pending.completed.set()
        if pending is None:
            print(f"No task waiting for response: {cmd}")
            return

        if pending.callback is not None:
            try:
                pending.callback(cmd, status, data)
            except Exception as exc:
                print(f"Command callback failed: {exc}")

    def thread_data_transfer(self):
        """
            Sub thread to transfer data to chameleon device.

        :return:
        """
        while self.isOpen():
            # get a task from queue(if exists)
            try:
                task = self.send_data_queue.get(
                    block=True, timeout=THREAD_BLOCKING_TIMEOUT
                )
            except queue.Empty:
                continue
            task_close = task["close"]
            with self._state_lock:
                transport = self.transport
                transport_type = self.transport_type
            if transport is None:
                self.send_data_queue.task_done()
                self.close()
                break
            if transport_type is TransportType.SERIAL:
                try:
                    transport.write(task["frame"])
                except OSError as exc:
                    print(f"Serial Error {exc}, thread for transfer exit.")
                    self.send_data_queue.task_done()
                    self.close()
                    break
            else:  # SOCKET
                try:
                    transport.sendall(task["frame"])
                except OSError as exc:
                    print(f"Socket error {exc}, thread for transfer exit.")
                    self.send_data_queue.task_done()
                    self.close()
                    break
            # update queue status
            self.send_data_queue.task_done()
            # disconnect if DFU command has been sent
            if task_close:
                self.close()

    def thread_check_timeout(self):
        """
            Check task timeout.

        :return:
        """
        while not self.event_closing.wait(THREAD_BLOCKING_TIMEOUT):
            now = time.monotonic()
            expired: list[tuple[int, _PendingRequest]] = []
            with self._state_lock:
                for cmd, pending in list(self.wait_response_map.items()):
                    if now >= pending.deadline:
                        self.wait_response_map.pop(cmd, None)
                        pending.timed_out = True
                        pending.completed.set()
                        expired.append((cmd, pending))
            for cmd, pending in expired:
                if pending.callback is not None:
                    try:
                        pending.callback(cmd, None, None)
                    except Exception as exc:
                        print(f"Command timeout callback failed: {exc}")

    def make_data_frame_bytes(
        self, cmd: int, data: bytes | None = None, status: int = 0
    ) -> bytes:
        """
            Make data frame

        :return: frame
        """
        if data is None:
            data = b""
        else:
            data = bytes(data)
        if len(data) > self.data_max_length:
            raise ValueError(
                f"Command payload is {len(data)} bytes; maximum is {self.data_max_length}"
            )
        frame = bytearray(
            struct.pack(
                "!BBHHHB",
                self.data_frame_sof,
                0,
                cmd,
                status,
                len(data),
                0,
            )
        )
        frame.extend(data)
        frame.append(0)
        # lrc1
        frame[1] = self.lrc_calc(frame[:1])
        # lrc2
        frame[_FRAME_METADATA_SIZE] = self.lrc_calc(frame[:_FRAME_METADATA_SIZE])
        # lrc3
        frame[-1] = self.lrc_calc(frame[:-1])
        return bytes(frame)

    def send_cmd_auto(
        self,
        cmd: int,
        data: bytes | None = None,
        status: int = 0,
        callback=None,
        timeout: float = 3,
        close: bool = False,
    ) -> _PendingRequest:
        """
            Send cmd to device

        :param cmd: cmd
        :param data: bytes data (optional)
        :param status: status (optional)
        :param callback: call on response
        :param timeout: wait response timeout
        :param close: close connection after executing
        :return:
        """
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        # make data frame
        if DEBUG:
            try:
                command = Command(cmd)
                command_name = f"{command.name}"
            except ValueError:
                command_name = "(UNKNOWN)"
            cmd_string = (
                f"{cmd:4} {command_name}{f'[{status:04x}]' if status != 0 else ''}"
            )
            hexdata = data.hex() if data is not None else ""
            print(f"<={color_string((CC, cmd_string.ljust(40)), (CY, hexdata))}")
        data_frame = self.make_data_frame_bytes(cmd, data, status)
        pending = _PendingRequest(
            deadline=time.monotonic() + timeout,
            callback=callback if callable(callback) else None,
        )
        with self._state_lock:
            if not self._is_open_unlocked():
                raise NotOpenException("Please call open() function to start device.")
            if cmd in self.wait_response_map:
                raise RuntimeError(f"Command {cmd} already has a pending request")
            self.wait_response_map[cmd] = pending
            task = {"cmd": cmd, "frame": data_frame, "close": close}
            self.send_data_queue.put_nowait(task)
        return pending

    def send_cmd_sync(
        self,
        cmd: int,
        data: bytes | None = None,
        status: int = 0,
        timeout: float = 3,
    ) -> Response:
        """
            Send cmd to device, and block receive data.

        :param cmd: cmd
        :param data: bytes data (optional)
        :param status: status (optional)
        :param timeout: wait response timeout
        :return: response data
        """
        if self.commands and cmd not in self.commands:
            raise CMDInvalidException(
                f"This device doesn't declare that it can support this command: {cmd}.\n"
                "Make sure firmware is up to date and matches client"
            )

        pending = self.send_cmd_auto(cmd, data, status, None, timeout)
        if not pending.completed.wait(timeout):
            with self._state_lock:
                if self.wait_response_map.get(cmd) is pending:
                    self.wait_response_map.pop(cmd, None)
                    pending.timed_out = True
                    pending.completed.set()
        if pending.timed_out:
            raise TimeoutError(f"CMD {cmd} exec timeout")
        if pending.connection_closed:
            raise NotOpenException(f"Connection closed while waiting for CMD {cmd}")
        if pending.response is None:
            raise RuntimeError(f"CMD {cmd} completed without a response")
        if pending.response.status == Status.INVALID_CMD:
            raise CMDInvalidException(f"Device unsupported cmd: {cmd}")
        return pending.response


if __name__ == "__main__":
    try:
        cml = ChameleonCom().open("com19")
    except OpenFailException:
        cml = ChameleonCom().open("/dev/ttyACM0")
    resp = cml.send_cmd_sync(0x03E8, None, 0)
    print(resp.status)
    print(resp.data)
    cml.close()
