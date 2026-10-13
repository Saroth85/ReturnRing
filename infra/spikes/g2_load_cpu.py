"""SPK-G2, second half: the GPU model saved by g2_xgboost_gpu.py loads in xgboost 3.2 on CPU.

Spec section 10: the API reads the JSON model with xgboost 3.2 on CPU. Runs in the spike-client
container after `pip install xgboost-cpu==3.2.0` (make spike-g2-load). Exit 0 if predictions match.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import xgboost as xgb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", type=Path, default=Path("artifacts/spikes/g2_check.json"))
    parser.add_argument("--tolerance", type=float, default=1e-5)
    args = parser.parse_args()

    check = json.loads(args.check.read_text())
    bst = xgb.Booster()
    bst.load_model(args.check.with_name(check["model"]))
    pred = bst.predict(xgb.DMatrix(np.asarray(check["x"], dtype=np.float32)))
    diff = float(np.max(np.abs(pred - np.asarray(check["pred"]))))
    result = {
        "xgboost": xgb.__version__,
        "rounds": bst.num_boosted_rounds(),
        "max_abs_diff": diff,
        "pass": diff <= args.tolerance,
    }
    print(json.dumps(result))
    args.check.with_name("g2_load_cpu.json").write_text(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
