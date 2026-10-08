import json as _json
import math as _math


def _clean(o, depth=0):
    if depth > 6:
        return str(o)
    try:
        import numpy as _np
        import pandas as _pd
        if isinstance(o, _np.generic):
            o = o.item()
        elif isinstance(o, _np.ndarray):
            o = o.tolist()
        elif isinstance(o, _pd.DataFrame):
            o = o.head(50).to_dict(orient="records")
        elif isinstance(o, _pd.Series):
            o = o.head(50).to_dict()
    except Exception:
        pass
    if isinstance(o, float):
        return None if (_math.isnan(o) or _math.isinf(o)) else round(o, 6)
    if isinstance(o, dict):
        return {str(k): _clean(v, depth + 1) for k, v in list(o.items())[:100]}
    if isinstance(o, (list, tuple)):
        return [_clean(v, depth + 1) for v in list(o)[:200]]
    if o is None or isinstance(o, (str, int, bool)):
        return o
    return str(o)


if "result" not in globals():
    raise NameError("The code must assign the final answer to a variable named `result`.")
_DP_OUT = _json.dumps(_clean(result))  # noqa: F821
if len(_DP_OUT) > 50_000:
    raise ValueError("`result` is larger than 50 KB; return a summary instead.")
_DP_OUT
