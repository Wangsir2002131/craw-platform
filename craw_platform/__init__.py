"""Crawler platform package.

This project package intentionally uses the name ``platform``. Re-export the
standard-library ``platform`` module attributes so third-party imports such as
``platform.python_implementation()`` keep working when this package shadows it.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import os as _os
import sysconfig as _sysconfig


def _load_stdlib_platform_module():
    stdlib_dir = _sysconfig.get_path("stdlib")
    if not stdlib_dir:
        return None

    platform_path = _os.path.join(stdlib_dir, "platform.py")
    spec = _importlib_util.spec_from_file_location("_stdlib_platform", platform_path)
    if spec is None or spec.loader is None:
        return None

    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_stdlib_platform = _load_stdlib_platform_module()
_reserved_names = {
    "__cached__",
    "__file__",
    "__loader__",
    "__name__",
    "__package__",
    "__path__",
    "__spec__",
}

if _stdlib_platform is not None:
    for _name in dir(_stdlib_platform):
        if _name not in _reserved_names:
            globals().setdefault(_name, getattr(_stdlib_platform, _name))

del _load_stdlib_platform_module
del _importlib_util
del _os
del _sysconfig
