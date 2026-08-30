#!/usr/bin/env python3
"""Flash a Nordic Secure DFU package over USB CDC serial."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import TextIO

from nrfutil_compat import install_pc_ble_driver_exception_shim

install_pc_ble_driver_exception_shim()

from nordicsemi.dfu.dfu import Dfu  # noqa: E402
from nordicsemi.dfu.dfu_transport import DfuEvent  # noqa: E402
from nordicsemi.dfu.dfu_transport_serial import DfuTransportSerial  # noqa: E402


class ProgressReporter:
    """Render one live progress line, with sparse milestones for redirected logs."""

    BAR_WIDTH = 30

    def __init__(self, total: int, stream: TextIO = sys.stdout) -> None:
        self.total = total
        self.stream = stream
        self.interactive = stream.isatty()
        self.completed = 0
        self.next_percentage = 10
        self.started_at: float | None = None
        self.rendered = False
        self.finished = False

    @staticmethod
    def format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        return f"{size / 1024:.1f} KiB"

    def render_live_line(self, percentage: int) -> None:
        filled = self.BAR_WIDTH * percentage // 100
        bar = "#" * filled + "-" * (self.BAR_WIDTH - filled)
        elapsed = max(time.monotonic() - (self.started_at or time.monotonic()), 0.001)
        rate = (
            f"{self.format_size(int(self.completed / elapsed))}/s"
            if elapsed >= 0.25
            else "calculating..."
        )
        print(
            f"\r\033[2K  [{bar}] {percentage:3d}%  "
            f"{self.format_size(self.completed)} / {self.format_size(self.total)}  "
            f"{rate}",
            end="",
            file=self.stream,
            flush=True,
        )
        self.rendered = True

    def __call__(self, progress: int) -> None:
        if self.started_at is None:
            self.started_at = time.monotonic()
        self.completed = min(self.completed + progress, self.total)
        percentage = 100 if self.total == 0 else self.completed * 100 // self.total
        if self.interactive:
            self.render_live_line(percentage)
        elif percentage >= self.next_percentage or self.completed == self.total:
            print(
                f"  Progress: {percentage:3d}%  "
                f"{self.format_size(self.completed)} / {self.format_size(self.total)}",
                file=self.stream,
                flush=True,
            )
            self.next_percentage = (percentage // 10 + 1) * 10

    def finish(self) -> None:
        if self.finished:
            return
        if self.interactive and self.rendered:
            print(file=self.stream, flush=True)
        self.finished = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Flash a Nordic Secure DFU package over USB CDC serial."
    )
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud-rate", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--connect-delay", type=float, default=3.0)
    parser.add_argument("--prn", type=int, default=0)
    parser.add_argument(
        "--no-flow-control",
        action="store_true",
        help="Disable RTS/CTS flow control (enabled by Nordic's usb-serial default).",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    firmware = args.firmware.resolve()
    if not firmware.is_file():
        print(f"DFU package not found: {firmware}", file=sys.stderr)
        return 1
    if args.baud_rate <= 0 or args.timeout <= 0 or args.connect_delay < 0 or args.prn < 0:
        print("Invalid serial DFU timing or transport option.", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    transport = DfuTransportSerial(
        com_port=args.port,
        baud_rate=args.baud_rate,
        flow_control=not args.no_flow_control,
        timeout=args.timeout,
        prn=args.prn,
        do_ping=False,
    )
    dfu = Dfu(
        zip_file_path=str(firmware),
        dfu_transport=transport,
        connect_delay=args.connect_delay,
    )
    reporter = ProgressReporter(dfu.dfu_get_total_size())
    transport.register_events_callback(DfuEvent.PROGRESS_EVENT, reporter)

    print(f"  Serial transport: {args.port}")
    try:
        dfu.dfu_send_images()
    except KeyboardInterrupt:
        reporter.finish()
        print("DFU interrupted.", file=sys.stderr)
        return 130
    except Exception as error:  # pc-nrfutil exposes several transport exception types
        reporter.finish()
        if args.verbose:
            logging.exception("DFU failed")
        else:
            print(f"DFU failed: {error}", file=sys.stderr)
        return 1
    finally:
        serial_port = getattr(transport, "serial_port", None)
        if serial_port is not None and getattr(serial_port, "is_open", False):
            serial_port.close()

    reporter.finish()
    print("  Firmware transferred successfully; device is rebooting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
