# Builds `data` from the JSON payload, then user code runs, then `result` is serialized.
import json as _json

import numpy as np  # noqa: F401
import pandas as pd

_payload = _json.loads(_DP_PAYLOAD)  # noqa: F821 — injected by the runner
data = pd.DataFrame(_payload["rows"], columns=_payload["columns"])
del _payload
