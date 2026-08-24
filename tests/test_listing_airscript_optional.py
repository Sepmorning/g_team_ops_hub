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
        this._conditionalFormats = [];
        this.ProtectContents = false;
        this.ThrowWhenUnprotected = false;
        this.UnprotectMode = "normal";
        rows.forEach((values, rowIndex) => {
            values.forEach((value, columnIndex) => {
                this._cell(rowIndex + 1, columnIndex + 1).value = value;
            });
        });
        this.UsedRange = { Row: 1, Rows: { Count: rows.length } };
    }

    Protect() { this.ProtectContents = true; }

    Unprotect(value) {
        if (this.ThrowWhenUnprotected && !this.ProtectContents) {
            throw new Error("WPS rejects Unprotect on an unprotected sheet");
        }
        if (this.UnprotectMode === "never") {
            throw new Error("KDocs refuses every Unprotect signature");
        }
        if (this.UnprotectMode === "object" && (
            !value || typeof value !== "object" || value.Password !== ""
        )) {
            throw new Error("KDocs requires an object password argument");
        }
        this.ProtectContents = false;
    }

    _cell(row, column) {
        const key = row + ":" + column;
        if (!this._cells.has(key)) {
            this._cells.set(key, {
                value: "", formula: "", numberFormat: "", locked: true
            });
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

    numberFormat(header, headers) {
        const column = headers.indexOf(header) + 1;
        return column > 0 ? this._cell(2, column).numberFormat : undefined;
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
        Object.defineProperty(range, "Locked", {
            get() {
                const values = flattened.map(cell => cell.locked);
                return values.every(value => value === values[0]) ? values[0] : null;
            },
            set(value) { flattened.forEach(cell => { cell.locked = Boolean(value); }); }
        });
        range.FormatConditions = {
            get Count() { return this._owner._conditionalFormats.length; },
            _owner: this,
            Item(index) { return this._owner._conditionalFormats[index - 1]; },
            Add(type, operator, formula1, formula2) {
                const condition = {
                    Type: type,
                    Operator: operator,
                    Formula1: formula1,
                    Formula2: formula2,
                    Interior: { Color: 0 },
                    AppliesTo: range,
                    ModifyAppliesToRange(value) { this.AppliesTo = value; },
                    SetFirstPriority() { this.Priority = 1; }
                };
                this._owner._conditionalFormats.push(condition);
                return condition;
            }
        };
        return range;
    }
}

function validRuleSheet(name = "ListingRules") {
    const sheet = new FakeSheet(name, []);
    const values = {
        B4: "R1.0", B5: 10, B6: 30, B7: 90, B8: 10,
        B9: 0.20, B10: 0.50, B11: -0.20, B12: -0.50, B13: 0.25,
        B14: 5,
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
    globalThis.__lastListingSheets = sheets;
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
    discount_price: 15.99,
    rating_review: "5/4"
});
const priceAfterWrite = optionalSheet.value("优惠价", optionalHeaders);
const ratingAfterWrite = optionalSheet.value("评分/评论数", optionalHeaders);
const ratingNumberFormat = optionalSheet.numberFormat("评分/评论数", optionalHeaders);
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
const legacyFinalColumn = columnName(requiredHeaders.indexOf("最终补货月销") + 1);
const legacyConfidenceColumn = columnName(requiredHeaders.indexOf("预测可信度") + 1);
manualSheet.Range(legacyFinalColumn + "2").FormatConditions.Add(
    2, -1, "=$" + legacyConfidenceColumn + "2=\"中\"", ""
);
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
const manualFinalFormula = manualSheet.formula("最终补货月销", requiredHeaders);
const trendFormula = manualSheet.formula("趋势差异率", requiredHeaders);
const confidenceFormula = manualSheet.formula("预测可信度", requiredHeaders);
const exceptionFormula = manualSheet.formula("异常原因", requiredHeaders);
const finalFormula = manualSheet.formula("最终补货月销", requiredHeaders);
const linkStatusCell = columnName(requiredHeaders.indexOf("链接状态") + 1) + "2";
const confidenceCell = columnName(requiredHeaders.indexOf("预测可信度") + 1) + "2";
const highlightCondition = manualSheet._conditionalFormats[0];
const setupSheet = new FakeSheet("纯粹-美国", targetRows(requiredHeaders));
const legacyRuleSheet = validRuleSheet("规则配置");
legacyRuleSheet.Range("B5").Value2 = 11;
// 模拟金山文档在线接口只接受对象密码参数的情况。
legacyRuleSheet.ProtectContents = true;
legacyRuleSheet.UnprotectMode = "object";
const setupResult = executeWith(setupSheet, {
    action: "setup_rules",
    sheet_name: "纯粹-美国",
    items: []
}, legacyRuleSheet);
const lockedSetupSheet = new FakeSheet("锁定测试-美国", targetRows(requiredHeaders));
const lockedRuleSheet = validRuleSheet("ListingRules");
lockedRuleSheet.ProtectContents = true;
lockedRuleSheet.UnprotectMode = "never";
const lockedSetupResult = executeWith(lockedSetupSheet, {
    action: "setup_rules",
    sheet_name: "锁定测试-美国",
    items: []
}, lockedRuleSheet);
const replacementRuleSheet = globalThis.__lastListingSheets.find(
    sheet => sheet.Name === "ListingRules"
);
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
    ratingAfterWrite,
    ratingNumberFormat,
    regularPrice: requiredOnlySheet.value("价格", requiredHeaders),
    manualResult,
    manualFinal: manualSheet.value("最终补货月销", requiredHeaders),
    manualFinalFormula,
    trendFormula,
    systemFormula: manualSheet.formula("系统建议月销", requiredHeaders),
    replenishmentFormula: manualSheet.formula("建议补货量", requiredHeaders),
    confidenceFormula,
    exceptionFormula,
    finalFormula,
    linkStatusCell,
    confidenceCell,
    highlightFormula: highlightCondition ? highlightCondition.Formula1 : "",
    highlightColor: highlightCondition ? highlightCondition.Interior.Color : 0,
    setupResult,
    migratedRuleSheetName: legacyRuleSheet.Name,
    resetLowBoundary: legacyRuleSheet.Range("B5").Value2[0][0],
    ruleSheetProtected: legacyRuleSheet.ProtectContents,
    ruleTitleLocked: legacyRuleSheet.Range("A1").Locked,
    ruleParameterLocked: legacyRuleSheet.Range("B4").Locked,
    ruleWeightLocked: legacyRuleSheet.Range("B17").Locked,
    confidenceDocumentation: legacyRuleSheet.Range("A36:D38").Value2,
    protectionDocumentation: legacyRuleSheet.Range("A71:D71").Value2,
    lockedSetupResult,
    archivedLockedRuleSheetName: lockedRuleSheet.Name,
    replacementRuleSheetProtected: replacementRuleSheet.ProtectContents,
    replacementLowBoundary: replacementRuleSheet.Range("B5").Value2[0][0],
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

    assert payload["priced"]["schemaVersion"] == 10
    assert payload["priceAfterWrite"] == 15.99
    assert payload["priceAfterBlank"] == ""
    assert payload["ratingAfterWrite"] == "5/4"
    assert payload["ratingNumberFormat"] == "@"
    assert payload["priced"]["columns"]["discount_price"] == "D"
    assert "discount_price" not in payload["missingTarget"]["columns"]
    assert payload["regularPrice"] == 19.99
    assert payload["blanked"]["failures"] == []
    assert payload["priced"]["rules"]["version"] == "R1.0"
    assert payload["priced"]["rules"]["replenishmentMultiple"] == 5
    assert payload["priced"]["formulaErrorRows"] == 0
    assert payload["manualResult"]["manualOverrideRows"] == 0
    assert payload["manualFinal"] == ""
    assert payload["manualFinalFormula"].startswith("=IF(")
    assert payload["systemFormula"].startswith("=IF(")
    assert "'ListingRules'!$B$17" in payload["systemFormula"]
    assert "'ListingRules'!$D$19" in payload["systemFormula"]
    assert "ROUNDUP(MAX(0," in payload["replenishmentFormula"]
    assert "'ListingRules'!$B$14" in payload["replenishmentFormula"]
    assert "INT(" not in payload["replenishmentFormula"]
    assert payload["linkStatusCell"] not in payload["confidenceFormula"]
    assert payload["linkStatusCell"] not in payload["exceptionFormula"]
    assert '="数据不足"' in payload["confidenceFormula"]
    assert "(V2/7*30)" in payload["trendFormula"]
    assert "((X2-W2)/16*30)" in payload["trendFormula"]
    assert payload["confidenceCell"] not in payload["finalFormula"]
    assert '="清库存"' in payload["finalFormula"]
    assert '="停售"' in payload["finalFormula"]
    assert '="新品观察"' in payload["finalFormula"]
    assert '="暂缓补货"' in payload["finalFormula"]
    assert payload["highlightFormula"].endswith('="低"')
    assert payload["highlightColor"] == 15123357
    assert payload["setupResult"]["formulaRows"] == 1
    assert payload["setupResult"]["formulaErrorRows"] == 0
    assert payload["setupResult"]["lowConfidenceHighlightApplied"] is True
    assert payload["setupResult"]["rules"]["protected"] is False
    assert payload["setupResult"]["rules"]["protectionVerified"] is True
    assert payload["setupResult"]["rules"]["editableRangesApplied"] is False
    assert payload["migratedRuleSheetName"] == "ListingRules"
    assert payload["resetLowBoundary"] == 10
    assert payload["ruleSheetProtected"] is False
    assert [row[1] for row in payload["confidenceDocumentation"]] == ["低", "中", "高"]
    assert "FBA可售=0" in payload["confidenceDocumentation"][0][2]
    assert "广告状态=数据不足" in payload["confidenceDocumentation"][1][2]
    assert "同时满足" in payload["confidenceDocumentation"][2][2]
    assert payload["protectionDocumentation"][0][1] == "不设置保护"
    assert payload["lockedSetupResult"]["archivedRuleSheetName"].startswith(
        "ListingRules_旧保护"
    )
    assert payload["archivedLockedRuleSheetName"].startswith("ListingRules_旧保护")
    assert payload["replacementRuleSheetProtected"] is False
    assert payload["replacementLowBoundary"] == 10
    assert "规则版本在预览后发生变化" in payload["versionConflict"]
    assert payload["recoveryWithoutRules"]["success"] is True
    assert payload["recoveryWithoutRules"]["rules"]["valid"] is False
    assert payload["recoverySync"]["updated"] == ["SKU-1"]
    assert payload["recoveryChangeCount"] >= 2
    assert len(payload["recoveryPreview"]["ready"]) == payload["recoveryChangeCount"]
    assert len(payload["recoveryApplied"]["applied"]) == payload["recoveryChangeCount"]
    assert payload["discountAfterRecovery"] == 18.99
    assert len(payload["recoveryConflict"]["conflicts"]) == 1
