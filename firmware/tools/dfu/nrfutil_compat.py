"""Compatibility helpers for pc-nrfutil without its binary BLE driver."""

from __future__ import annotations

import sys
import types


def install_pc_ble_driver_exception_shim() -> None:
    """Provide the one exception type used by non-BLE pc-nrfutil modules."""
    try:
        from pc_ble_driver_py.exceptions import NordicSemiException  # noqa: F401
    except ModuleNotFoundError as error:
        if error.name not in {"pc_ble_driver_py", "pc_ble_driver_py.exceptions"}:
            raise

        driver_module = types.ModuleType("pc_ble_driver_py")
        driver_module.__path__ = []
        exceptions_module = types.ModuleType("pc_ble_driver_py.exceptions")

        class NordicSemiException(Exception):
            pass

        exceptions_module.NordicSemiException = NordicSemiException
        driver_module.exceptions = exceptions_module
        sys.modules["pc_ble_driver_py"] = driver_module
        sys.modules["pc_ble_driver_py.exceptions"] = exceptions_module
