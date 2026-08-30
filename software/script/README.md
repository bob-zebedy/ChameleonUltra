# ChameleonUltra CLI

Python CLI for controlling ChameleonUltra and Chameleon Lite over USB serial.

## Requirements

- `uv`
- Python 3.13 or newer

## Setup

From the repository root:

```bash
cd software/script
uv sync --locked
```

## Run

```bash
uv run python chameleon_cli_main.py
```

Connect to a device automatically:

```text
hw connect
```

To select a serial port manually:

```text
hw connect -p /dev/cu.usbmodem1234561
```

## Common commands

```text
hw version       # Show firmware version
hw battery       # Show battery status
hw mode          # Show the current device mode
hw slot list     # List emulation slots
hf 14a scan      # Scan an ISO14443-A tag
dump_help        # List available commands
hw disconnect    # Disconnect the device
exit             # Exit the CLI
```

Use `-h` to view help for a command:

```text
hf 14a scan -h
```

## Test

```bash
uv run python -m unittest discover -s tests
```

Set `CHAMELEON_TEST_PORT` to include hardware tests.

Only use RFID/NFC operations on devices and systems you own or are authorized to test.
