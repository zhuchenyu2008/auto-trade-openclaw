from __future__ import annotations

from pathlib import Path


_SRC_PACKAGE = Path(__file__).resolve().parent.parent / "src" / "tg_okx_auto_trade"

if not _SRC_PACKAGE.is_dir():
    raise ImportError(f"Missing source package directory: {_SRC_PACKAGE}")

__path__ = [str(_SRC_PACKAGE)]
__file__ = str(_SRC_PACKAGE / "__init__.py")

with open(__file__, "r", encoding="utf-8") as handle:
    exec(compile(handle.read(), __file__, "exec"))
