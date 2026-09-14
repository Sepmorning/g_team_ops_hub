"""Execute the actual deployed formulas against synthetic, non-business cases."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def test_listing_forecast_formulas_compute_business_cases():
    node = os.environ.get("FBA_TEST_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for formula regression")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, str(root / "tests/listing_formula_harness.cjs"),
         str(root / "airscripts/Listing库存销售自动回填.js")],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    assert json.loads(result.stdout)["status"] == "passed"
