# Native firmware artifact generation

This helper generates Nordic Secure DFU packages, bootloader settings, merged
HEX images, and the release binaries archive without executing the legacy
`nrfutil-legacy` binary or `mergehex`.

On Apple Silicon, `firmware/build.sh` selects this backend automatically. It
uses `uv` to provide the pinned Python 3.10 environment and `pc-nrfutil` 6.1.7
package-generation API:

```sh
CURRENT_DEVICE_TYPE=ultra bash firmware/build.sh
```

The first run may download Python and the locked Python dependencies. To force
the backend on another platform, set `FIRMWARE_ARTIFACT_BACKEND=native`. Set it
to `nrfutil` to use the original Nordic CLI pipeline.

The unused `pc-ble-driver-py` dependency is intentionally excluded because
Nordic did not publish a native Apple Silicon wheel. Package generation and
USB CDC serial DFU use the remaining pure Python APIs. A small compatibility
shim supplies the exception class imported by those non-BLE modules.

The repository flashing wrappers run `enter_dfu.py` through the separate
`software/script` uv project, use the standalone native `nrfutil device`
plugin only for USB discovery, and transfer the package through the pinned
Python `usb-serial` implementation:

```sh
bash firmware/flash-dfu-app.sh
# Or, when replacing the complete SoftDevice + bootloader + application image:
bash firmware/flash-dfu-full.sh
```
