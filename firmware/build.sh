#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$script_dir"

softdevice=s140
softdevice_version=7.2.0
softdevice_id=0x0100


# TODO: find a way to manage this automatically, I don't want to rely on action build #.
application_version=1
bootloader_version=1

device_type=${CURRENT_DEVICE_TYPE:-ultra}
case "$device_type" in
  "ultra") hw_version=0 ;;
  "lite")  hw_version=1 ;;
  *)       echo "Unknown CURRENT_DEVICE_TYPE: $device_type" >&2; exit 1 ;;
esac
export CURRENT_DEVICE_TYPE="$device_type"

artifact_backend=${FIRMWARE_ARTIFACT_BACKEND:-auto}
if [[ "$artifact_backend" == "auto" ]]; then
  if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
    artifact_backend=native
  else
    artifact_backend=nrfutil
  fi
fi

case "$artifact_backend" in
  native|nrfutil) ;;
  *)
    echo "Unknown FIRMWARE_ARTIFACT_BACKEND: $artifact_backend (expected auto, native, or nrfutil)" >&2
    exit 1
    ;;
esac

echo "Building firmware for $device_type (hw_version=$hw_version, artifact_backend=$artifact_backend)"

set -x

rm -rf "objects"

(
  cd bootloader
  make -j
)

(
  cd application
  make -j
)

(
  cd objects

  cp ../nrf52_sdk/components/softdevice/${softdevice}/hex/${softdevice}_nrf52_${softdevice_version}_softdevice.hex softdevice.hex

  if [[ "$artifact_backend" == "native" ]]; then
    if ! command -v uv >/dev/null 2>&1; then
      echo "uv is required by the native firmware artifact backend." >&2
      echo "Install uv, or use FIRMWARE_ARTIFACT_BACKEND=nrfutil with Rosetta and Nordic's legacy tools." >&2
      exit 1
    fi

    native_tool_dir="$script_dir/tools/dfu"
    uv run \
      --project "$native_tool_dir" \
      --locked \
      python "$native_tool_dir/build_artifacts.py" \
      --objects-dir "$PWD" \
      --device-type "$device_type" \
      --hw-version "$hw_version" \
      --application-version "$application_version" \
      --bootloader-version "$bootloader_version" \
      --softdevice-id "$softdevice_id" \
      --key-file "$script_dir/../resource/dfu_key/chameleon.pem"
  else
    nrfutil nrf5sdk-tools pkg generate \
      --hw-version "$hw_version" \
      --bootloader bootloader.hex --bootloader-version "$bootloader_version" --key-file ../../resource/dfu_key/chameleon.pem \
      --application application.hex --application-version "$application_version" \
      --softdevice softdevice.hex \
      --sd-req "$softdevice_id" --sd-id "$softdevice_id" \
      "${device_type}-dfu-full.zip"

    nrfutil nrf5sdk-tools pkg generate \
      --hw-version "$hw_version" --key-file ../../resource/dfu_key/chameleon.pem \
      --application application.hex --application-version "$application_version" \
      --sd-req "$softdevice_id" \
      "${device_type}-dfu-app.zip"

    nrfutil nrf5sdk-tools settings generate \
      --family NRF52840 \
      --application application.hex --application-version "$application_version" \
      --softdevice softdevice.hex \
      --bootloader-version "$bootloader_version" --bl-settings-version 2 \
      settings.hex
    mergehex \
      --merge \
      settings.hex \
      application.hex \
      --output application_merged.hex

    mergehex \
      --merge \
        bootloader.hex \
        application_merged.hex \
        softdevice.hex \
      --output fullimage.hex

    tmp_dir=$(mktemp -d -t cu_binaries_XXXXXXXXXX)
    cp ./*.hex "$tmp_dir"
    mv "$tmp_dir/application_merged.hex" "$tmp_dir/application.hex"
    rm "$tmp_dir/settings.hex"
    zip -j "${device_type}-binaries.zip" "$tmp_dir"/*.hex
    rm -rf "$tmp_dir"
  fi
)
