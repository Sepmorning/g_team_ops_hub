import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


AIRSCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "airscripts" / "FBA物流自动回填.js"
)


def test_logistics_airscript_highlights_only_current_business_changes():
    node = os.environ.get("FBA_TEST_NODE") or shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the AirScript behavior harness")

    harness = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");

function columnNumber(name) {
    let result = 0;
    for (const character of name) {
        result = result * 26 + character.charCodeAt(0) - 64;
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
            this._cells.set(key, {
                value: "",
                colorIndex: -4142,
                themeColor: null,
                tint: 0,
                numberFormat: ""
            });
        }
        return this._cells.get(key);
    }

    cell(address) {
        const parsed = cellAddress(address);
        return this._cell(parsed.row, parsed.column);
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
            get() {
                return cells.map(row => row.map(cell => cell.value));
            },
            set(value) {
                if (Array.isArray(value)) {
                    cells.forEach((row, rowIndex) => {
                        row.forEach((cell, columnIndex) => {
                            const sourceRow = Array.isArray(value[rowIndex])
                                ? value[rowIndex]
                                : [];
                            cell.value = sourceRow[columnIndex] ?? "";
                        });
                    });
                } else {
                    flattened.forEach(cell => { cell.value = value; });
                }
            }
        });
        Object.defineProperty(range, "NumberFormat", {
            set(value) {
                flattened.forEach(cell => { cell.numberFormat = value; });
            }
        });
        const interior = {};
        Object.defineProperty(interior, "ColorIndex", {
            get() { return flattened[0].colorIndex; },
            set(value) {
                flattened.forEach(cell => {
                    cell.colorIndex = value;
                    if (value === -4142) {
                        cell.themeColor = null;
                        cell.tint = 0;
                    }
                });
            }
        });
        Object.defineProperty(interior, "ThemeColor", {
            get() { return flattened[0].themeColor; },
            set(value) {
                flattened.forEach(cell => {
                    cell.themeColor = value;
                    cell.colorIndex = 1;
                });
            }
        });
        Object.defineProperty(interior, "TintAndShade", {
            get() { return flattened[0].tint; },
            set(value) {
                flattened.forEach(cell => { cell.tint = value; });
            }
        });
        range.Interior = interior;
        return range;
    }
}

const mainHeaders = [
    "FBA号", "货代", "运输工具/班次", "当前阶段", "当前节点",
    "最新轨迹时间", "货代最新路由信息", "当前异常", "提货", "预计出发",
    "开船（机）时间", "预计到达", "到港", "预计送达", "提取派送",
    "签收时间", "POD状态", "是否完成", "数据状态", "物流最后更新时间"
];
const mainRow = [
    "FBA12345", "安达", "", "干线运输", "已到港",
    "2026-07-29 09:00:00", "2026-07-29 09:00 已到港", "", "", "",
    "", "", "", "", "", "", "", "", "正常", "2026-07-29 10:00:00"
];
const detailHeaders = [
    "事件编号", "FBA号", "货代", "货代订单号", "轨迹发生时间", "标准阶段",
    "标准节点", "信息属性", "物流轨迹原文", "涉及计划", "有效状态",
    "异常状态", "运输信息", "官网原始状态", "首次获取时间",
    "最后确认时间", "系统更新时间"
];

const mainSheet = new FakeSheet("US-FBA", [mainHeaders, mainRow]);
const detailSheet = new FakeSheet("US-轨迹明细", [detailHeaders]);
for (const address of ["D2", "G2"]) {
    mainSheet.cell(address).colorIndex = 1;
    mainSheet.cell(address).themeColor = 5;
    mainSheet.cell(address).tint = 0.8;
}
mainSheet.cell("E2").colorIndex = 1;
mainSheet.cell("E2").themeColor = 6;
mainSheet.cell("E2").tint = 0.8;
detailSheet.cell("A1").colorIndex = 1;
detailSheet.cell("A1").themeColor = 5;
detailSheet.cell("A1").tint = 0.8;

const sheets = [mainSheet, detailSheet];
const Application = {
    Sheets: {
        Count: sheets.length,
        Item(index) { return sheets[index - 1]; }
    }
};
function executeWith(application, argv) {
    const runner = new Function("Application", "Context", source);
    return runner(application, { argv });
}
function execute(main) {
    return executeWith(Application, {
            action: "sync",
            sheet_name: "US-FBA",
            items: [{ fba: "FBA12345", main }]
    });
}

const first = execute({
    route: "2026-07-30 10:00 已签收",
    updated_time: "2026-07-30 10:00:00"
});
const firstStyles = {
    oldBusiness: mainSheet.cell("D2"),
    customBusiness: mainSheet.cell("E2"),
    changedBusiness: mainSheet.cell("G2"),
    auditTime: mainSheet.cell("T2")
};
const firstStyleSnapshot = JSON.parse(JSON.stringify(firstStyles));

const second = execute({
    route: "2026-07-30 10:00 已签收",
    updated_time: "2026-07-30 11:00:00"
});

function pendingRow(fba, completion, updatedTime) {
    const row = new Array(mainHeaders.length).fill("");
    row[0] = fba;
    row[1] = "安达";
    row[17] = completion;
    row[19] = updatedTime;
    return row;
}
const pendingMainSheet = new FakeSheet("US-FBA", [
    mainHeaders,
    pendingRow("FBA00001", "", ""),
    pendingRow("FBA00002", "", "2026-07-30 10:00:00"),
    pendingRow("FBA00003", "是", ""),
    pendingRow("FBA00004", "是", "2026-07-30 10:00:00"),
    pendingRow("FBA00005", "否", ""),
    pendingRow("FBA00006", "  ", "")
]);
const pendingDetailSheet = new FakeSheet("US-轨迹明细", [detailHeaders]);
const pendingSheets = [pendingMainSheet, pendingDetailSheet];
const pending = executeWith({
    Sheets: {
        Count: pendingSheets.length,
        Item(index) { return pendingSheets[index - 1]; }
    }
}, {
    action: "list_pending",
    sheet_name: "US-FBA",
    items: [],
    offset: 0,
    limit: 500
});

function excelSerial(dateTime) {
    return Date.parse(dateTime.replace(" ", "T") + "Z") / 86400000 + 25569;
}
const detailTimestamp = "2026-07-30 10:00:00";
const detailMainRow = mainRow.slice();
const detailExistingRow = [
    "event-1", "FBA12345", "安达", "ORDER-1",
    excelSerial("2026-07-29 09:00:00"), "干线运输", "实际到达", "实际",
    "已到港", "", "有效", "", "", "已到港",
    excelSerial(detailTimestamp), excelSerial(detailTimestamp),
    excelSerial(detailTimestamp)
];
const compareMainSheet = new FakeSheet("US-FBA", [mainHeaders, detailMainRow]);
const compareDetailSheet = new FakeSheet(
    "US-轨迹明细",
    [detailHeaders, detailExistingRow]
);
const compareSheets = [compareMainSheet, compareDetailSheet];
const compareApplication = {
    Sheets: {
        Count: compareSheets.length,
        Item(index) { return compareSheets[index - 1]; }
    }
};
const detailEvent = {
    event_id: "event-1",
    fba: "FBA12345",
    carrier: "安达",
    carrier_order_no: "ORDER-1",
    event_time: "2026-07-29 09:00:00",
    phase: "干线运输",
    node: "实际到达",
    event_type: "实际",
    content: "已到港",
    related_plan: "",
    validity: "有效",
    exception_status: "",
    transport_info: "",
    source_status: "已到港",
    first_seen: detailTimestamp,
    last_confirmed: detailTimestamp,
    updated_at: detailTimestamp
};
function syncDetail(event) {
    return executeWith(compareApplication, {
        action: "sync_tracking",
        sheet_name: "US-FBA",
        items: [{
            fba: "FBA12345",
            main: { route: detailMainRow[6] },
            events: [event]
        }]
    });
}
const detailSame = syncDetail(detailEvent);
const detailDatesAfterSame = {
    lastConfirmed: compareDetailSheet.cell("P2").value,
    updatedAt: compareDetailSheet.cell("Q2").value
};
const detailChanged = syncDetail(Object.assign({}, detailEvent, {
    validity: "已被更新"
}));
const detailDatesAfterChanged = {
    lastConfirmed: compareDetailSheet.cell("P2").value,
    updatedAt: compareDetailSheet.cell("Q2").value
};
console.log(JSON.stringify({
    first,
    firstStyles: firstStyleSnapshot,
    second,
    secondStyles: {
        changedBusiness: mainSheet.cell("G2"),
        auditTime: mainSheet.cell("T2")
    },
    detailHeader: detailSheet.cell("A1"),
    pending,
    detailSame,
    detailChanged,
    detailDatesAfterSame,
    detailDatesAfterChanged
}));
"""
    completed = subprocess.run(
        [node, "-e", harness, str(AIRSCRIPT_PATH)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])

    assert payload["first"]["schemaVersion"] == 9
    assert payload["first"]["updated"] == ["FBA12345"]
    assert payload["first"]["auditOnly"] == []
    assert payload["first"]["formatFailures"] == []
    assert [
        (item["address"], item["header"])
        for item in payload["first"]["updatedCells"]
    ] == [("G2", "货代最新路由信息")]
    assert payload["firstStyles"]["oldBusiness"]["colorIndex"] == -4142
    assert payload["firstStyles"]["customBusiness"]["themeColor"] == 6
    assert payload["firstStyles"]["changedBusiness"]["themeColor"] == 5
    assert payload["firstStyles"]["changedBusiness"]["tint"] == pytest.approx(0.8)
    assert payload["firstStyles"]["auditTime"]["colorIndex"] == -4142

    assert payload["second"]["updated"] == []
    assert payload["second"]["auditOnly"] == ["FBA12345"]
    assert payload["second"]["updatedCells"] == []
    assert payload["secondStyles"]["changedBusiness"]["colorIndex"] == -4142
    assert payload["secondStyles"]["auditTime"]["colorIndex"] == -4142
    assert payload["detailHeader"]["themeColor"] == 5
    assert payload["pending"]["total"] == 3
    assert [item["fba"] for item in payload["pending"]["fbas"]] == [
        "FBA00001",
        "FBA00002",
        "FBA00006",
    ]
    assert payload["detailSame"]["eventsUpdated"] == 0
    assert payload["detailSame"]["eventsUnchanged"] == 1
    assert payload["detailChanged"]["eventsUpdated"] == 1
    assert payload["detailChanged"]["eventsUnchanged"] == 0
    assert isinstance(payload["detailDatesAfterSame"]["lastConfirmed"], (int, float))
    assert isinstance(payload["detailDatesAfterSame"]["updatedAt"], (int, float))
    assert isinstance(
        payload["detailDatesAfterChanged"]["lastConfirmed"], (int, float)
    )
    assert isinstance(payload["detailDatesAfterChanged"]["updatedAt"], (int, float))
