import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from g_team_ops.listing import TARGET_HEADERS


LISTING_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "airscripts"
    / "Listing库存销售自动回填.js"
)


def test_listing_airscript_updates_optional_discount_only_when_target_exists():
    node = os.environ.get("FBA_TEST_NODE") or shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the Listing AirScript harness")

    harness = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const requiredHeaders = JSON.parse(process.argv[2]);

function columnNumber(name) {
    let result = 0;
    for (const character of name) {
        result = result * 26 + character.charCodeAt(0) - 64;
    }
    return result;
}

function columnName(number) {
    let result = "";
    while (number > 0) {
        const remainder = (number - 1) % 26;
        result = String.fromCharCode(65 + remainder) + result;
        number = Math.floor((number - 1) / 26);
    }
    return result;
}

function cellAddress(value) {
    const match = /^([A-Z]+)(\d+)$/.exec(value);
    if (!match) throw new Error("Unsupported range address: " + value);
    return { column: columnNumber(match[1]), row: Number(match[2]) };
}

class FakeSheet {
    constructor(name, rows) {
        this.Name = name;
        this._cells = new Map();
        rows.forEach((values, rowIndex) => {
            values.forEach((value, columnIndex) => {
                this._cell(rowIndex + 1, columnIndex + 1).value = value;
            });
        });
        this.UsedRange = { Row: 1, Rows: { Count: rows.length } };
    }

    _cell(row, column) {
        const key = row + ":" + column;
        if (!this._cells.has(key)) {
            this._cells.set(key, { value: "", formula: "", numberFormat: "" });
        }
        return this._cells.get(key);
    }

    value(header, headers) {
        const column = headers.indexOf(header) + 1;
        return column > 0 ? this._cell(2, column).value : undefined;
    }

    formula(header, headers) {
        const column = headers.indexOf(header) + 1;
        return column > 0 ? this._cell(2, column).formula : undefined;
    }

    Range(address) {
        const parts = address.split(":");
        const start = cellAddress(parts[0]);
        const end = cellAddress(parts[1] || parts[0]);
        const cells = [];
        for (let row = start.row; row <= end.row; row++) {
            const values = [];
            for (let column = start.column; column <= end.column; column++) {
                values.push(this._cell(row, column));
            }
            cells.push(values);
        }
        const flattened = cells.flat();
        const range = {};
        Object.defineProperty(range, "Value2", {
            get() { return cells.map(row => row.map(cell => cell.value)); },
            set(value) {
                if (Array.isArray(value)) {
                    cells.forEach((row, rowIndex) => {
                        row.forEach((cell, columnIndex) => {
                            const sourceRow = Array.isArray(value[rowIndex])
                                ? value[rowIndex] : [];
                            cell.value = sourceRow[columnIndex] ?? "";
                            cell.formula = "";
                        });
                    });
                } else {
                    flattened.forEach(cell => {
                        cell.value = value;
                        cell.formula = "";
                    });
                }
            }
        });
        Object.defineProperty(range, "Formula", {
            get() {
                return cells.map(row => row.map(cell => cell.formula || cell.value));
            },
            set(value) {
                flattened.forEach(cell => {
                    cell.formula = value;
                    cell.value = "";
                });
            }
        });
        Object.defineProperty(range, "NumberFormat", {
            set(value) { flattened.forEach(cell => { cell.numberFormat = value; }); }
        });
        return range;
    }
}

function validRuleSheet() {
    const sheet = new FakeSheet("规则配置", []);
    const values = {
        B4: "R1.0", B5: 10, B6: 30, B7: 90, B8: 10,
        B9: 0.20, B10: 0.50, B11: -0.20, B12: -0.50, B13: 0.25,
        B17: 0.30, C17: 0.25, D17: 0.45,
        B18: 0.40, C18: 0.30, D18: 0.30,
        B19: 0.50, C19: 0.30, D19: 0.20
    };
    Object.entries(values).forEach(([address, value]) => {
        sheet.Range(address).Value2 = value;
    });
    return sheet;
}

function targetRows(headers) {
    const row = new Array(headers.length).fill("");
    row[headers.indexOf("MSKU")] = "SKU-1";
    row[headers.indexOf("ASIN")] = "B012345678";
    if (headers.indexOf("价格") >= 0) row[headers.indexOf("价格")] = 19.99;
    if (headers.indexOf("优惠价") >= 0) row[headers.indexOf("优惠价")] = 18.99;
    return [headers, row];
}

function executeWith(sheet, argv, ruleSheet) {
    const sheets = [sheet, ruleSheet || validRuleSheet()];
    const Application = {
        Sheets: {
            get Count() { return sheets.length; },
            Item(index) { return sheets[index - 1]; },
            Add() {
                const created = new FakeSheet("Sheet" + (sheets.length + 1), []);
                sheets.push(created);
                return created;
            }
        }
    };
    const runner = new Function("Application", "Context", source);
    return runner(Application, { argv });
}

function execute(sheet, item) {
    return executeWith(sheet, {
        action: "sync",
        sheet_name: "纯粹-美国",
        data_date: "2026-08-06",
        items: [item]
    });
}

// 故意把所有标准列倒序，并把优惠价插在任意位置，防止实现依赖推荐列顺序。
const optionalHeaders = requiredHeaders.slice().reverse();
optionalHeaders.splice(3, 0, "优惠价");
const optionalSheet = new FakeSheet("纯粹-美国", targetRows(optionalHeaders));
const priced = execute(optionalSheet, {
    msku: "SKU-1",
    asin: "B012345678",
    discount_price: 15.99
});
const priceAfterWrite = optionalSheet.value("优惠价", optionalHeaders);
const blanked = execute(optionalSheet, {
    msku: "SKU-1",
    asin: "B012345678",
    discount_price: ""
});
const priceAfterBlank = optionalSheet.value("优惠价", optionalHeaders);

const requiredOnlySheet = new FakeSheet(
    "纯粹-美国",
    targetRows(requiredHeaders)
);
const missingTarget = execute(requiredOnlySheet, {
    msku: "SKU-1",
    asin: "B012345678",
    discount_price: 12.99
});

const manualSheet = new FakeSheet("纯粹-美国", targetRows(requiredHeaders));
manualSheet._cell(2, requiredHeaders.indexOf("最终补货月销") + 1).value = 55;
const manualResult = execute(manualSheet, {
    msku: "SKU-1",
    asin: "B012345678",
    price: 20.99,
    sales_7d: 7,
    sales_14d: 15,
    sales_30d: 31,
    ad_spend_7d: 10,
    ad_spend_14d: 20,
    ad_spend_30d: 40,
    revenue_7d: 140,
    revenue_14d: 300,
    revenue_30d: 620
});
const setupSheet = new FakeSheet("纯粹-美国", targetRows(requiredHeaders));
const setupResult = executeWith(setupSheet, {
    action: "setup_rules",
    sheet_name: "纯粹-美国",
    items: []
});
let versionConflict = "";
try {
    executeWith(manualSheet, {
        action: "sync",
        sheet_name: "纯粹-美国",
        data_date: "2026-08-07",
        expected_rule_version: "R0.9",
        items: [{ msku: "SKU-1", asin: "B012345678" }]
    });
} catch (error) {
    versionConflict = String(error.message || error);
}
const recoveryWithoutRules = executeWith(
    new FakeSheet("纯粹-美国", targetRows(requiredHeaders)),
    {
        action: "snapshot_targets",
        sheet_name: "纯粹-美国",
        items: [],
        targets: []
    },
    new FakeSheet("规则配置", [])
);

const recoverySheet = new FakeSheet("纯粹-美国", targetRows(optionalHeaders));
const recoveryItem = {
    msku: "SKU-1",
    asin: "B012345678",
    discount_price: 15.99
};
const recoveryBefore = executeWith(recoverySheet, {
    action: "snapshot",
    sheet_name: "纯粹-美国",
    items: [recoveryItem]
});
const recoverySync = executeWith(recoverySheet, {
    action: "sync",
    sheet_name: "纯粹-美国",
    data_date: "2026-08-06",
    items: [recoveryItem],
    preconditions: recoveryBefore.snapshots
});
const recoveryAfter = executeWith(recoverySheet, {
    action: "snapshot_targets",
    sheet_name: "纯粹-美国",
    items: [],
    targets: recoveryBefore.snapshots
});
const recoveryChanges = recoveryBefore.snapshots.map((oldItem, index) => ({
    targetType: oldItem.targetType,
    sheetName: oldItem.sheetName,
    matchHeader: oldItem.matchHeader,
    matchValue: oldItem.matchValue,
    itemKey: oldItem.itemKey,
    field: oldItem.field,
    cellAddress: oldItem.cellAddress,
    oldValue: oldItem.value,
    newValue: recoveryAfter.snapshots[index].value
})).filter((item, index) =>
    JSON.stringify(recoveryBefore.snapshots[index].comparableValue) !==
    JSON.stringify(recoveryAfter.snapshots[index].comparableValue)
);
const recoveryPreview = executeWith(recoverySheet, {
    action: "inspect_changes",
    sheet_name: "纯粹-美国",
    items: [],
    changes: recoveryChanges,
    direction: "rollback"
});
const recoveryApplied = executeWith(recoverySheet, {
    action: "apply_changes",
    sheet_name: "纯粹-美国",
    items: [],
    changes: recoveryChanges,
    direction: "rollback"
});
const discountAfterRecovery = recoverySheet.value("优惠价", optionalHeaders);
recoverySheet._cell(2, optionalHeaders.indexOf("优惠价") + 1).value = 17.88;
const recoveryConflict = executeWith(recoverySheet, {
    action: "inspect_changes",
    sheet_name: "纯粹-美国",
    items: [],
    changes: recoveryChanges,
    direction: "rollback"
});

console.log(JSON.stringify({
    priced,
    blanked,
    missingTarget,
    priceAfterWrite,
    priceAfterBlank,
    regularPrice: requiredOnlySheet.value("价格", requiredHeaders),
    manualResult,
    manualFinal: manualSheet.value("最终补货月销", requiredHeaders),
    systemFormula: manualSheet.formula("系统建议月销", requiredHeaders),
    setupResult,
    versionConflict,
    recoveryWithoutRules,
    recoverySync,
    recoveryChangeCount: recoveryChanges.length,
    recoveryPreview,
    recoveryApplied,
    discountAfterRecovery,
    recoveryConflict
}));
"""
    completed = subprocess.run(
        [
            node,
            "-e",
            harness,
            str(LISTING_SCRIPT_PATH),
            json.dumps(TARGET_HEADERS, ensure_ascii=False),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])

    assert payload["priced"]["schemaVersion"] == 5
    assert payload["priceAfterWrite"] == 15.99
    assert payload["priceAfterBlank"] == ""
    assert payload["priced"]["columns"]["discount_price"] == "D"
    assert "discount_price" not in payload["missingTarget"]["columns"]
    assert payload["regularPrice"] == 19.99
    assert payload["blanked"]["failures"] == []
    assert payload["priced"]["rules"]["version"] == "R1.0"
    assert payload["manualResult"]["manualOverrideRows"] == 1
    assert payload["manualFinal"] == 55
    assert payload["systemFormula"].startswith("=IF(")
    assert "'规则配置'!$B$17" in payload["systemFormula"]
    assert "'规则配置'!$D$19" in payload["systemFormula"]
    assert payload["setupResult"]["formulaRows"] == 1
    assert "规则版本在预览后发生变化" in payload["versionConflict"]
    assert payload["recoveryWithoutRules"]["success"] is True
    assert payload["recoveryWithoutRules"]["rules"]["valid"] is False
    assert payload["recoverySync"]["updated"] == ["SKU-1"]
    assert payload["recoveryChangeCount"] >= 2
    assert len(payload["recoveryPreview"]["ready"]) == payload["recoveryChangeCount"]
    assert len(payload["recoveryApplied"]["applied"]) == payload["recoveryChangeCount"]
    assert payload["discountAfterRecovery"] == 18.99
    assert len(payload["recoveryConflict"]["conflicts"]) == 1
