#!/usr/bin/env python3

import sys
import time

import serial
import serial.tools.list_ports as list_ports

DFUCMD = b"\x11\xef\x03\xf2\x00\x00\x00\x00\x0b\x00"
APPLICATION_VID_PID = (0x6868, 0x8686)
DFU_VID_PID = (0x1915, 0x521F)
PORT_SETTLE_SECONDS = 0.25
REENUMERATION_TIMEOUT_SECONDS = 2.0
MAX_ATTEMPTS = 3


def matching_ports(vid_pid):
    return [port for port in list_ports.comports() if (port.vid, port.pid) == vid_pid]


def wait_for_dfu(timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ports = matching_ports(DFU_VID_PID)
        if ports:
            return ports[0]
        time.sleep(0.1)
    return None


def send_dfu_command(port):
    serial_instance = None
    try:
        serial_instance = serial.Serial(
            port=port,
            baudrate=115200,
            timeout=0.2,
            write_timeout=1,
        )
        serial_instance.dtr = True
        # macOS can report the CDC port before the firmware has processed the
        # port-open/DTR event. An immediate first write can therefore be lost.
        time.sleep(PORT_SETTLE_SECONDS)
        bytes_written = serial_instance.write(DFUCMD)
        if bytes_written != len(DFUCMD):
            raise serial.SerialTimeoutException(
                f"Only wrote {bytes_written} of {len(DFUCMD)} bytes"
            )
        serial_instance.flush()
        # Keep the port alive long enough for the firmware command parser to
        # consume the frame and initiate the reset.
        time.sleep(PORT_SETTLE_SECONDS)
    finally:
        if serial_instance is not None:
            serial_instance.close()


dfu_ports = matching_ports(DFU_VID_PID)
if dfu_ports:
    print("Chameleon already in DFU mode")
    sys.exit(0)

application_ports = matching_ports(APPLICATION_VID_PID)
if not application_ports:
    print("Chameleon not found")
    sys.exit(1)
if len(application_ports) > 1:
    print(
        f"Expected exactly one Chameleon in application mode, found {len(application_ports)}."
    )
    sys.exit(1)

application_port = application_ports[0]
for attempt in range(1, MAX_ATTEMPTS + 1):
    try:
        send_dfu_command(application_port.device)
    except (OSError, serial.SerialException) as error:
        print(f"Serial error on attempt {attempt}/{MAX_ATTEMPTS}: {error}")
    else:
        dfu_port = wait_for_dfu(REENUMERATION_TIMEOUT_SECONDS)
        if dfu_port is not None:
            print(f"Chameleon entered DFU mode: {dfu_port.device}")
            sys.exit(0)

    application_ports = matching_ports(APPLICATION_VID_PID)
    if not application_ports:
        # The application USB device has disconnected and may still be in the
        # middle of re-enumerating. The caller performs the longer DFU wait.
        print("DFU command accepted; waiting for USB re-enumeration")
        sys.exit(0)
    application_port = application_ports[0]

print(f"Chameleon remained in application mode after {MAX_ATTEMPTS} attempts.")
sys.exit(1)
