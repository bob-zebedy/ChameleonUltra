#!/usr/bin/env python3
"""Generate Chameleon Nordic Secure DFU and merged HEX artifacts."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from intelhex import IntelHex
from nordicsemi.dfu.package import Package
from nordicsemi.dfu.signing import Signing
from nrfutil_compat import install_pc_ble_driver_exception_shim


def integer(value: str) -> int:
    """Parse decimal and 0x-prefixed command-line integers."""
    try:
        return int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid integer: {value}") from error


def require_file(path: Path) -> Path:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"required input file not found: {path}")
    return path


def load_settings_generator():
    """Load Nordic's settings generator without its unused binary BLE driver."""
    install_pc_ble_driver_exception_shim()

    from nordicsemi.dfu.bl_dfu_sett import BLDFUSettings

    return BLDFUSettings


def generate_package(
    output: Path,
    *,
    signer: Signing,
    hardware_version: int,
    application_version: int,
    bootloader_version: int,
    softdevice_id: int,
    application: Path,
    bootloader: Path | None = None,
    softdevice: Path | None = None,
) -> None:
    package = Package(
        debug_mode=False,
        hw_version=hardware_version,
        app_version=application_version,
        bl_version=bootloader_version,
        sd_req=[softdevice_id],
        sd_id=[softdevice_id],
        app_fw=str(application),
        bootloader_fw=str(bootloader) if bootloader else None,
        softdevice_fw=str(softdevice) if softdevice else None,
        signer=signer,
    )
    package.generate_package(str(output))


def generate_settings(
    output: Path,
    *,
    application: Path,
    softdevice: Path,
    application_version: int,
    bootloader_version: int,
) -> None:
    settings_type = load_settings_generator()
    settings = settings_type()

    # pc-nrfutil writes an intermediate SoftDevice file to the current directory
    # and creates multiple temporary directories. Keep all of them out of the repo.
    original_cwd = Path.cwd()
    original_tempdir = tempfile.tempdir
    with tempfile.TemporaryDirectory(prefix="chameleon_dfu_settings_") as work_dir:
        try:
            os.chdir(work_dir)
            tempfile.tempdir = work_dir
            settings.generate(
                arch="NRF52840",
                app_file=str(application),
                app_ver=application_version,
                bl_ver=bootloader_version,
                bl_sett_ver=2,
                custom_bl_sett_addr=None,
                no_backup=False,
                backup_address=None,
                app_boot_validation_type="VALIDATE_GENERATED_CRC",
                sd_boot_validation_type="VALIDATE_GENERATED_CRC",
                sd_file=str(softdevice),
                signer=None,
            )
        finally:
            # The outer TemporaryDirectory owns all nested pc-nrfutil paths.
            # Prevent its destructor from trying to remove one of them twice.
            settings.temp_dir = None
            tempfile.tempdir = original_tempdir
            os.chdir(original_cwd)

    settings.tohexfile(str(output))


def merge_hex(output: Path, *inputs: Path) -> None:
    merged = IntelHex()
    for input_path in inputs:
        image = IntelHex(str(input_path))
        # Start-address records describe a process entry point, not flash data.
        # Independent firmware images naturally contain different values; Nordic
        # mergehex ignores that conflict when combining address ranges.
        image.start_addr = None
        merged.merge(image, overlap="error")
    merged.write_hex_file(str(output))


def create_binaries_archive(
    output: Path,
    *,
    application: Path,
    bootloader: Path,
    softdevice: Path,
    fullimage: Path,
) -> None:
    files = {
        "application.hex": application,
        "bootloader.hex": bootloader,
        "softdevice.hex": softdevice,
        "fullimage.hex": fullimage,
    }
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for archive_name, source in files.items():
            archive.write(source, archive_name)


def verify_dfu_archive(path: Path, expected_manifest_entries: set[str]) -> None:
    with ZipFile(path) as archive:
        corrupt_file = archive.testzip()
        if corrupt_file is not None:
            raise RuntimeError(f"corrupt file in {path.name}: {corrupt_file}")
        manifest = json.loads(archive.read("manifest.json"))["manifest"]
        actual_entries = {name for name, value in manifest.items() if value is not None}
        if actual_entries != expected_manifest_entries:
            raise RuntimeError(
                f"unexpected manifest entries in {path.name}: "
                f"expected {sorted(expected_manifest_entries)}, got {sorted(actual_entries)}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate signed DFU packages, bootloader settings, and merged HEX files."
    )
    parser.add_argument("--objects-dir", type=Path, required=True)
    parser.add_argument("--device-type", choices=("ultra", "lite"), required=True)
    parser.add_argument("--hw-version", type=integer, required=True)
    parser.add_argument("--application-version", type=integer, required=True)
    parser.add_argument("--bootloader-version", type=integer, required=True)
    parser.add_argument("--softdevice-id", type=integer, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    objects_dir = args.objects_dir.resolve()
    if not objects_dir.is_dir():
        raise NotADirectoryError(f"objects directory not found: {objects_dir}")

    application = require_file(objects_dir / "application.hex")
    bootloader = require_file(objects_dir / "bootloader.hex")
    softdevice = require_file(objects_dir / "softdevice.hex")
    key_file = require_file(args.key_file)

    signer = Signing()
    signer.load_key(str(key_file))

    app_package = objects_dir / f"{args.device_type}-dfu-app.zip"
    full_package = objects_dir / f"{args.device_type}-dfu-full.zip"
    settings = objects_dir / "settings.hex"
    application_merged = objects_dir / "application_merged.hex"
    fullimage = objects_dir / "fullimage.hex"
    binaries = objects_dir / f"{args.device_type}-binaries.zip"

    generate_package(
        full_package,
        signer=signer,
        hardware_version=args.hw_version,
        application_version=args.application_version,
        bootloader_version=args.bootloader_version,
        softdevice_id=args.softdevice_id,
        application=application,
        bootloader=bootloader,
        softdevice=softdevice,
    )
    generate_package(
        app_package,
        signer=signer,
        hardware_version=args.hw_version,
        application_version=args.application_version,
        bootloader_version=args.bootloader_version,
        softdevice_id=args.softdevice_id,
        application=application,
    )

    generate_settings(
        settings,
        application=application,
        softdevice=softdevice,
        application_version=args.application_version,
        bootloader_version=args.bootloader_version,
    )
    merge_hex(application_merged, settings, application)
    merge_hex(fullimage, bootloader, application_merged, softdevice)
    create_binaries_archive(
        binaries,
        application=application_merged,
        bootloader=bootloader,
        softdevice=softdevice,
        fullimage=fullimage,
    )

    verify_dfu_archive(app_package, {"application"})
    verify_dfu_archive(full_package, {"application", "softdevice_bootloader"})

    for artifact in (app_package, full_package, binaries, application_merged, fullimage):
        print(f"Generated: {artifact}")


if __name__ == "__main__":
    main()
