#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$script_dir/.." && pwd)"
uv_project="$repo_dir/software/script"
dfu_uv_project="$script_dir/tools/dfu"
enter_dfu_script="$repo_dir/resource/tools/enter_dfu.py"
flash_package_script="$dfu_uv_project/flash_package.py"

usage() {
  cat <<'EOF'
Usage: firmware/flash-dfu-app.sh
       firmware/flash-dfu-full.sh

Environment variables:
  CURRENT_DEVICE_TYPE      ultra or lite; normally detected automatically
  DFU_WAIT_TIMEOUT_SECONDS seconds to wait for DFU mode (default: 60)
EOF
}

print_step() {
  local number=$1
  local message=$2
  if (( number > 1 )); then
    printf '\n'
  fi
  printf '[%s/4] %s\n' "$number" "$message"
}

interactive_output=false
if [[ -t 1 ]]; then
  interactive_output=true
fi
wait_line_active=false

clear_wait_line() {
  if [[ "$wait_line_active" == true ]]; then
    printf '\r\033[2K'
    wait_line_active=false
  fi
}

package_kind=${1:-}
if [[ -n "$package_kind" ]]; then
  shift
fi

case "$package_kind" in
  app|full) ;;
  *)
    usage >&2
    exit 2
    ;;
esac

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

if (( $# > 0 )); then
  echo "Unexpected argument: $1" >&2
  usage >&2
  exit 2
fi

print_step 1 "Checking tools and firmware package"

for required_command in uv nrfutil; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "Required command not found: $required_command" >&2
    exit 1
  fi
done

if [[ ! -f "$uv_project/pyproject.toml" || ! -f "$uv_project/uv.lock" ]]; then
  echo "uv project is incomplete: $uv_project" >&2
  exit 1
fi

if [[ ! -f "$enter_dfu_script" ]]; then
  echo "DFU entry script not found: $enter_dfu_script" >&2
  exit 1
fi

if [[ ! -f "$dfu_uv_project/pyproject.toml" \
   || ! -f "$dfu_uv_project/uv.lock" \
   || ! -f "$flash_package_script" ]]; then
  echo "Native serial DFU project is incomplete: $dfu_uv_project" >&2
  exit 1
fi

requested_device_type=${CURRENT_DEVICE_TYPE:-}
case "$requested_device_type" in
  ""|ultra|lite) ;;
  *)
    echo "Unknown CURRENT_DEVICE_TYPE: $requested_device_type" >&2
    exit 1
    ;;
esac

wait_timeout=${DFU_WAIT_TIMEOUT_SECONDS:-60}
if ! [[ "$wait_timeout" =~ ^[1-9][0-9]*$ ]]; then
  echo "DFU_WAIT_TIMEOUT_SECONDS must be a positive integer: $wait_timeout" >&2
  exit 1
fi

if [[ -n "$requested_device_type" ]]; then
  requested_package="$script_dir/objects/${requested_device_type}-dfu-${package_kind}.zip"
  if [[ ! -f "$requested_package" ]]; then
    echo "DFU package not found: $requested_package" >&2
    echo "Build it with: CURRENT_DEVICE_TYPE=$requested_device_type bash firmware/build.sh" >&2
    exit 1
  fi
elif [[ ! -f "$script_dir/objects/ultra-dfu-${package_kind}.zip" \
     && ! -f "$script_dir/objects/lite-dfu-${package_kind}.zip" ]]; then
  echo "No ${package_kind} DFU package found in $script_dir/objects" >&2
  echo "Build it with: CURRENT_DEVICE_TYPE=ultra bash firmware/build.sh" >&2
  exit 1
fi

printf '  Prerequisites are ready.\n'

print_step 2 "Switching Chameleon to DFU mode"
if ! uv run \
  --project "$uv_project" \
  --locked \
  python "$enter_dfu_script"; then
  printf '  Automatic switch was unavailable; waiting for manual DFU mode.\n'
  printf '  Hold button B while connecting USB; LEDs 4 and 5 should blink.\n'
fi

print_step 3 "Detecting one Chameleon in DFU mode"
if [[ "$interactive_output" == false ]]; then
  printf '  Waiting up to %ss for the USB device...\n' "$wait_timeout"
fi
deadline=$((SECONDS + wait_timeout))
dfu_devices_json=""

while (( SECONDS < deadline )); do
  if [[ "$interactive_output" == true ]]; then
    elapsed=$((wait_timeout - (deadline - SECONDS)))
    printf '\r  Waiting for USB device... %2ss / %ss' "$elapsed" "$wait_timeout"
    wait_line_active=true
  fi

  if ! dfu_devices_json=$(nrfutil device list \
    --traits nordicDfu \
    --json \
    --skip-overhead \
    --log-level error \
    --log-output stdout); then
    clear_wait_line
    echo "nrfutil failed while enumerating DFU devices." >&2
    exit 1
  fi

  if [[ "$dfu_devices_json" == *'"nordicDfu":true'* ]]; then
    break
  fi
  sleep 1
done

clear_wait_line
if [[ "$dfu_devices_json" != *'"nordicDfu":true'* ]]; then
  echo "Timed out after ${wait_timeout}s waiting for a Chameleon DFU device." >&2
  exit 1
fi

if ! device_metadata=$(uv run \
  --quiet \
  --project "$uv_project" \
  --locked \
  python -c '
import json
import sys

devices = json.loads(sys.argv[1]).get("devices", [])
if len(devices) != 1:
    print(f"Expected exactly one DFU device, found {len(devices)}.", file=sys.stderr)
    raise SystemExit(1)

device = devices[0]
serial_number = device.get("serialNumber")
product = (device.get("usb") or {}).get("product") or ""
serial_ports = device.get("serialPorts") or []
port = serial_ports[0].get("path") if serial_ports else None
if not serial_number:
    print("The DFU device did not report a serial number.", file=sys.stderr)
    raise SystemExit(1)
if not port:
    print("The DFU device did not report a serial port.", file=sys.stderr)
    raise SystemExit(1)

print(f"{serial_number}\t{product}\t{port}")
' "$dfu_devices_json"); then
  exit 1
fi

IFS=$'\t' read -r device_serial device_product device_port <<< "$device_metadata"
if [[ -z "$device_serial" || -z "$device_product" || -z "$device_port" ]]; then
  echo "Unable to read complete DFU device metadata." >&2
  exit 1
fi

printf '  Device: %s\n' "$device_product"
printf '  Serial: %s\n' "$device_serial"
printf '  Port:   %s\n' "$device_port"

case "$device_product" in
  *ChameleonUltra*) detected_device_type=ultra ;;
  *ChameleonLite*)  detected_device_type=lite ;;
  *)
    echo "Unable to determine device type from USB product: $device_product" >&2
    echo "Set CURRENT_DEVICE_TYPE=ultra or CURRENT_DEVICE_TYPE=lite explicitly." >&2
    exit 1
    ;;
esac

if [[ -n "$requested_device_type" && "$requested_device_type" != "$detected_device_type" ]]; then
  echo "Device type mismatch: requested $requested_device_type, detected $detected_device_type." >&2
  exit 1
fi

device_type=${requested_device_type:-$detected_device_type}
dfu_package="$script_dir/objects/${device_type}-dfu-${package_kind}.zip"
if [[ ! -f "$dfu_package" ]]; then
  echo "DFU package for $device_type not found: $dfu_package" >&2
  echo "Build it with: CURRENT_DEVICE_TYPE=$device_type bash firmware/build.sh" >&2
  exit 1
fi

print_step 4 "Flashing $(basename "$dfu_package")"
if ! uv run \
  --project "$dfu_uv_project" \
  --locked \
  python "$flash_package_script" \
  --firmware "$dfu_package" \
  --port "$device_port"; then
  printf '\nFirmware update failed. The device may still be in DFU mode.\n' >&2
  exit 1
fi
