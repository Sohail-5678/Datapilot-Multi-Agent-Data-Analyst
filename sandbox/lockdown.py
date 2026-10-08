# Python-side lockdown shared by the browser worker and the Node runner (SPEC §8.1).
# Runs after pandas/numpy/scipy are loaded and before any model-written code.
# Defense in depth only: the real boundary is WebAssembly + a worker whose network APIs were replaced.
import sys

_BLOCKED = ("js", "pyodide_js", "pyodide", "pyodide.code", "pyodide.ffi", "pyodide.http", "micropip",
            "_pyodide", "_pyodide_core", "socket", "ssl", "urllib.request", "http.client", "subprocess")


class _BlockImports:
    def find_spec(self, name, path=None, target=None):
        if name in _BLOCKED or name.split(".")[0] in ("js", "pyodide_js", "pyodide", "micropip", "_pyodide", "_pyodide_core"):
            raise ImportError(f"import of '{name}' is disabled in the DataPilot sandbox")
        return None


for _name in list(sys.modules):
    if _name in _BLOCKED or _name.split(".")[0] in ("js", "pyodide_js", "micropip"):
        sys.modules.pop(_name, None)
sys.meta_path.insert(0, _BlockImports())

import builtins as _b  # noqa: E402

_real_import = _b.__import__


def _guarded_import(name, *args, **kwargs):
    if name in _BLOCKED or name.split(".")[0] in ("js", "pyodide_js", "pyodide", "micropip", "_pyodide", "_pyodide_core"):
        raise ImportError(f"import of '{name}' is disabled in the DataPilot sandbox")
    return _real_import(name, *args, **kwargs)


_b.__import__ = _guarded_import
# exec/compile stay: the import system needs them (the server-side precheck rejects them in model code).
for _fn in ("open", "input", "breakpoint"):
    if hasattr(_b, _fn):
        def _deny(*_a, _n=_fn, **_k):
            raise PermissionError(f"{_n}() is disabled in the DataPilot sandbox")
        setattr(_b, _fn, _deny)
del _fn
