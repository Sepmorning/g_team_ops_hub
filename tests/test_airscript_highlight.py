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

    _deleteRows(startRow, endRow) {
        const count = endRow - startRow + 1;
        const shifted = new Map();
        for (const [key, cell] of this._cells.entries()) {
            const [rowText, columnText] = key.split(":");
            const row = Number(rowText);
            const column = Number(columnText);
            if (row < startRow) {
                shifted.set(key, cell);
            } else if (row > endRow) {
                shifted.set((row - count) + ":" + column, cell);
            }
        }
        this._cells = shifted;
        this.UsedRange.Rows.Count = Math.max(
            1,
            this.UsedRange.Rows.Count - count
        );
    }

    Range(address) {
        const sheet = this;
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
                sheet.UsedRange.Rows.Count = Math.max(
                    sheet.UsedRange.Rows.Count,
                    end.row
                );
            }
        });
        Object.defineProperty(range, "NumberFormat", {
            get() { return flattened[0].numberFormat; },
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
        range.EntireRow = {
            Delete() { sheet._deleteRows(start.row, end.row); }
        };
        range.EntireColumn = {};
        Object.defineProperty(range.EntireColumn, "Hidden", {
            set(value) { sheet._cell(1, start.column).hidden = value; }
        });
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
const compareMainSheet = new FakeSheet("US-FBA", [
    mainHeaders,
    detailMainRow,
    pendingRow("FBA77777", "", ""),
    pendingRow("FBA99999", "是", "")
]);
const compareDetailSheet = new FakeSheet(
    "US-轨迹明细",
    [
        detailHeaders,
        detailExistingRow,
        ["event-active-failed", "FBA77777", "安达", "", "", "", "", "", "旧轨迹"],
        ["event-completed", "FBA99999", "安达", "", "", "", "", "", "已完成轨迹"],
        ["event-orphan", "FBA88888", "安达", "", "", "", "", "", "已移除货件"],
        ["manual-note", "说明", "", "", "", "", "", "", "人工说明行"]
    ]
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

const recoveryMainSheet = new FakeSheet("US-FBA", [mainHeaders, mainRow.slice()]);
const recoveryDetailSheet = new FakeSheet("US-轨迹明细", [detailHeaders]);
const recoverySheets = [recoveryMainSheet, recoveryDetailSheet];
const recoveryApplication = {
    Sheets: {
        Count: recoverySheets.length,
        Item(index) { return recoverySheets[index - 1]; }
    }
};
const recoveryItem = {
    fba: "FBA12345",
    main: { route: "2026-08-09 12:00 已签收" }
};
const recoveryBefore = executeWith(recoveryApplication, {
    action: "snapshot",
    sheet_name: "US-FBA",
    detail_sheet_name: "US-轨迹明细",
    items: [recoveryItem],
    include_cleanup: false
});
const recoverySync = executeWith(recoveryApplication, {
    action: "sync",
    sheet_name: "US-FBA",
    detail_sheet_name: "US-轨迹明细",
    items: [recoveryItem],
    preconditions: recoveryBefore.snapshots
});
const recoveryAfter = executeWith(recoveryApplication, {
    action: "snapshot_targets",
    sheet_name: "US-FBA",
    detail_sheet_name: "US-轨迹明细",
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
const recoveryPreview = executeWith(recoveryApplication, {
    action: "inspect_changes",
    sheet_name: "US-FBA",
    detail_sheet_name: "US-轨迹明细",
    items: [],
    changes: recoveryChanges,
    direction: "rollback"
});
const recoveryApplied = executeWith(recoveryApplication, {
    action: "apply_changes",
    sheet_name: "US-FBA",
    detail_sheet_name: "US-轨迹明细",
    items: [],
    changes: recoveryChanges,
    direction: "rollback"
});
const routeAfterRecovery = recoveryMainSheet.cell("G2").value;
recoveryMainSheet.cell("G2").value = "人工后续修改";
const recoveryConflict = executeWith(recoveryApplication, {
    action: "inspect_changes",
    sheet_name: "US-FBA",
    detail_sheet_name: "US-轨迹明细",
    items: [],
    changes: recoveryChanges,
    direction: "rollback"
});

const rowMainSheet = new FakeSheet("US-FBA", [mainHeaders, mainRow.slice()]);
const rowDetailSheet = new FakeSheet("US-轨迹明细", [detailHeaders]);
const rowSheets = [rowMainSheet, rowDetailSheet];
const rowApplication = {
    Sheets: {
        Count: rowSheets.length,
        Item(index) { return rowSheets[index - 1]; }
    }
};
const rowEvent = Object.assign({}, detailEvent, {
    event_id: "event-restore",
    updated_at: "2026-08-09 12:00:00"
});
const rowItem = {
    fba: "FBA12345",
    main: { route: mainRow[6] },
    events: [rowEvent]
};
const rowBefore = executeWith(rowApplication, {
    action: "snapshot",
    sheet_name: "US-FBA",
    detail_sheet_name: "US-轨迹明细",
    items: [rowItem],
    include_cleanup: false
});
const rowSync = executeWith(rowApplication, {
    action: "sync_tracking",
    sheet_name: "US-FBA",
    detail_sheet_name: "US-轨迹明细",
    items: [rowItem],
    preconditions: rowBefore.snapshots
});
const rowAfter = executeWith(rowApplication, {
    action: "snapshot_targets",
    sheet_name: "US-FBA",
    detail_sheet_name: "US-轨迹明细",
    items: [],
    targets: rowBefore.snapshots
});
const rowChanges = rowBefore.snapshots.map((oldItem, index) => ({
    targetType: oldItem.targetType,
    sheetName: oldItem.sheetName,
    matchHeader: oldItem.matchHeader,
    matchValue: oldItem.matchValue,
    itemKey: oldItem.itemKey,
    field: oldItem.field,
    cellAddress: oldItem.cellAddress,
    oldValue: oldItem.value,
    newValue: rowAfter.snapshots[index].value
})).filter((item, index) =>
    JSON.stringify(rowBefore.snapshots[index].comparableValue) !==
    JSON.stringify(rowAfter.snapshots[index].comparableValue)
);
const rowRestore = executeWith(rowApplication, {
    action: "apply_changes",
    sheet_name: "US-FBA",
    detail_sheet_name: "US-轨迹明细",
    items: [],
    changes: rowChanges,
    direction: "rollback"
});
const detailFbasAfterCleanup = [
    compareDetailSheet.cell("B2").value,
    compareDetailSheet.cell("B3").value,
    compareDetailSheet.cell("B4").value
];
// Header setup appends after custom columns and never alters manual business data.
const headerMain = new FakeSheet("US-FBA", [["自定义", "FBA号", "AMZ签收登记日期"], ["保留", "FBA12345", "2026-08-01"]]);
const headerDetail = new FakeSheet("US-轨迹明细", [["人工备注"], ["保留明细"]]);
const headerApp = { Sheets: { Count: 2, Item(i) { return [headerMain, headerDetail][i - 1]; } } };
const headerArgs = {sheet_name:"US-FBA",detail_sheet_name:"US-轨迹明细",items:[]};
const headerPreview = executeWith(headerApp, {...headerArgs, action:"headers_preview"});
const headerOriginal = [headerMain.cell("A1").value, headerMain.cell("C1").value, headerMain.cell("A2").value, headerMain.cell("C2").value];
executeWith(headerApp, {...headerArgs, action:"headers_apply", preconditions:headerPreview.snapshots});
const headerRemaining = executeWith(headerApp, {...headerArgs, action:"headers_preview"});
const headerPreserved = [headerMain.cell("A1").value, headerMain.cell("C1").value, headerMain.cell("A2").value, headerMain.cell("C2").value];
let headerConflict = false;
try { executeWith(headerApp, {...headerArgs, action:"headers_apply", preconditions:headerPreview.snapshots}); } catch(e) { headerConflict = true; }

// Organize active FBA groups in main-sheet order, retaining failed-query details and custom values.
const orderMain = new FakeSheet("US-FBA", [mainHeaders,
    ["FBA22222","安达"], ["FBA11111","易通"], Object.assign(Array(20).fill(""), {0:"FBA33333",1:"超鸿",17:"已完成"})]);
function historyRow(id,fba,time,note) { const row=Array(18).fill(""); row[0]=id;row[1]=fba;row[4]=time;row[17]=note;return row; }
const orderDetail = new FakeSheet("US-轨迹明细", [[...detailHeaders,"人工备注"],
    historyRow("a","FBA11111","2026-07-01","一"), historyRow("b","FBA33333","2026-07-01","三"),
    historyRow("c","FBA22222","2026-07-03","后"), historyRow("d","FBA22222","2026-07-02","先")]);
const orderApp = {Sheets:{Count:2,Item(i){return [orderMain,orderDetail][i-1]}}};
const beforeOrder = executeWith(orderApp,{...headerArgs,action:"snapshot",include_cleanup:true});
const organized = executeWith(orderApp,{...headerArgs,action:"organize",preconditions:beforeOrder.snapshots});
const afterOrder = executeWith(orderApp,{...headerArgs,action:"snapshot_targets",targets:beforeOrder.snapshots});
const orderedRows = [2,3,4].map(i=>[orderDetail.cell("B"+i).value,orderDetail.cell("R"+i).value]);
const orderChanges = beforeOrder.snapshots.map((old,i)=>({...old,oldValue:old.value,newValue:afterOrder.snapshots[i].value})).filter(v=>JSON.stringify(v.oldValue)!==JSON.stringify(v.newValue));
orderDetail.cell("R2").value="人工修改";
const orderConflict=executeWith(orderApp,{...headerArgs,action:"inspect_changes",changes:orderChanges,direction:"rollback"});
orderDetail.cell("R2").value="先";
const orderRestore=executeWith(orderApp,{...headerArgs,action:"apply_changes",changes:orderChanges,direction:"rollback"});
const restoredOrder=[2,3,4,5].map(i=>orderDetail.cell("A"+i).value);
// Emulate the deployed v11 JSON-text comparator with reordered webhook keys.
const legacySource = source.replace(/function sameComparable\(left, right\) \{[\s\S]*?\nfunction expectedComparable/, 'function sameComparable(left, right) { return JSON.stringify(left) === JSON.stringify(right); }\nfunction expectedComparable');
function legacyRun(args) { return new Function("Application", "Context", legacySource)(compareApplication, {argv:args}); }
const guardArgs = {sheet_name:"US-FBA",detail_sheet_name:"US-轨迹明细",items:[]};
const targetRow = {targetType:"row",sheetName:"US-轨迹明细",matchValue:"event-1",itemKey:"FBA12345",field:"__row__"};
const rowGuard = legacyRun({...guardArgs,action:"snapshot_targets",targets:[targetRow]}).snapshots[0];
function sortedKeys(value) { if(Array.isArray(value))return value.map(sortedKeys); if(value&&typeof value==="object")return Object.fromEntries(Object.keys(value).sort().map(k=>[k,sortedKeys(value[k])]));return value; }
const reorderedGuard = sortedKeys(rowGuard);
let legacyOrderConflict = false;
try { legacyRun({...guardArgs,action:"sync",preconditions:[reorderedGuard]}); } catch(e) { legacyOrderConflict = e.message.includes("写前快照后发生变化"); }
const normalizedGuard = {...reorderedGuard};
delete normalizedGuard.comparableValue;
legacyRun({...guardArgs,action:"sync",preconditions:[normalizedGuard]});
const previousContent = compareDetailSheet.cell("I2").value;
compareDetailSheet.cell("I2").value = "人工修改后的新轨迹";
let realChangeBlocked = false;
try { legacyRun({...guardArgs,action:"sync",preconditions:[normalizedGuard]}); } catch(e) { realChangeBlocked = e.message.includes("写前快照后发生变化"); }
compareDetailSheet.cell("I2").value = previousContent;
// Manual signatures are authoritative; completion and cleanup are reversible.
function assertRule(value, message) { if (!value) throw new Error(message); }
const signMain = new FakeSheet("US-FBA", [mainHeaders,
    Object.assign(Array(20).fill(""), {0:"FBA11111",1:"安达",15:"2026-09-01",17:"否"}),
    Object.assign(Array(20).fill(""), {0:"FBA22222",1:"安达"}),
    Object.assign(Array(20).fill(""), {0:"FBA33333",1:"易通",17:"是"})]);
const signDetail = new FakeSheet("US-轨迹明细", [detailHeaders,
    historyRow("signed-old","FBA11111","2026-09-01",""),
    historyRow("active-old","FBA22222","2026-08-30","")]);
const signApp = {Sheets:{Count:2,Item(i){return [signMain,signDetail][i-1]}}};
const signPending = executeWith(signApp,{...headerArgs,action:"list_pending"});
assertRule(signPending.completionPending && signPending.fbas.length===1 && signPending.fbas[0].fba==="FBA22222", "签收必须本轮跳过");
const signBefore = executeWith(signApp,{...headerArgs,action:"snapshot",include_cleanup:true});
const signDone = executeWith(signApp,{...headerArgs,action:"sync_tracking",preconditions:signBefore.snapshots});
assertRule(signMain.cell("P2").value==="2026-09-01" && signMain.cell("R2").value==="是", "保留人工签收并补齐完成");
assertRule(signDone.updatedCells.some(c=>c.field==="completion") && signDetail.cell("B2").value==="FBA22222", "完成写入和明细清理必须记录");
const signAfter = executeWith(signApp,{...headerArgs,action:"snapshot_targets",targets:signBefore.snapshots});
const signChanges = signBefore.snapshots.map((s,i)=>({...s,oldValue:s.value,newValue:signAfter.snapshots[i].value})).filter((s,i)=>JSON.stringify(signBefore.snapshots[i].comparableValue)!==JSON.stringify(signAfter.snapshots[i].comparableValue));
assertRule(signChanges.length===2 && signChanges.every(s=>s.targetType!=="tracking_inputs"), "恢复只记录完成单元格和明细");
signMain.cell("R2").value="人工修改";
const signConflict=executeWith(signApp,{...headerArgs,action:"inspect_changes",changes:signChanges,direction:"rollback"});
assertRule(signConflict.conflicts.length===1, "人工改完成值必须阻止恢复覆盖");
signMain.cell("R2").value="是";
const signRollback=executeWith(signApp,{...headerArgs,action:"apply_changes",changes:signChanges,direction:"rollback"});
assertRule(signRollback.applied.length===2 && signMain.cell("R2").value==="否", "完成和明细可以恢复");

const autoItem={fba:"FBA22222",main:{route:"已签收",signed_time:"2026-09-12"},events:[]};
const autoBefore=executeWith(signApp,{...headerArgs,action:"snapshot",items:[autoItem],include_cleanup:true});
signMain.cell("P3").value="2026-09-13";
let signatureConflict=false;
try { executeWith(signApp,{...headerArgs,action:"sync_tracking",items:[autoItem],preconditions:autoBefore.snapshots}); } catch(e) { signatureConflict=e.message.includes("写前快照后发生变化"); }
assertRule(signatureConflict, "查询期间填写签收必须触发冲突");
signMain.cell("P3").value="";
const autoDone=executeWith(signApp,{...headerArgs,action:"sync_tracking",items:[autoItem],preconditions:autoBefore.snapshots});
assertRule(signMain.cell("P3").value==="2026-09-12" && signMain.cell("R3").value==="是", "系统实际签收补空并完成");
assertRule(signDetail.cell("B2").value==="", "系统新签收必须清理全部明细");
const repeatedSign=executeWith(signApp,{...headerArgs,action:"sync_tracking",items:[{...autoItem,main:{route:"签收更新",signed_time:"2026-09-14"}}]});
assertRule(signMain.cell("P3").value==="2026-09-12", "后续系统时间不覆盖签收");
signMain.cell("P3").value=""; signMain.cell("R3").value="";
executeWith(signApp,{...headerArgs,action:"sync_tracking",items:[{...autoItem,main:{route:"预计签收，POD已上传",estimated_delivery:"2026-09-18",pod_status:"已提供"}}]});
assertRule(signMain.cell("P3").value==="" && signMain.cell("R3").value==="", "预计时间和POD不能替代实际签收");
assertRule(["A1","D1","N1","O1","P1"].every(a=>headerDetail.cell(a).value!==undefined), "表头整理没有丢失历史数据");

// Audit-only event timestamps must not create a business update.
const stableEvent={...detailEvent, validity:"已被更新", updated_at:"2026-09-15 12:00:00",last_confirmed:"2026-09-15 12:00:00"};
const oldSystemTime=compareDetailSheet.cell("Q2").value;
const auditOnlyEvent=executeWith(compareApplication,{...headerArgs,action:"sync_tracking",items:[{fba:"FBA12345",main:{route:"已到港"},events:[stableEvent]}]});
assertRule(auditOnlyEvent.eventsUpdated===0 && compareDetailSheet.cell("Q2").value===oldSystemTime, "重复获取不刷新系统更新时间");
console.log(JSON.stringify({
    legacyOrderConflict, realChangeBlocked,
    headerOriginal,headerPreserved,headerRemaining,headerConflict,
    organized,orderedRows,orderConflict,orderRestore,restoredOrder,
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
    detailFbasAfterCleanup,
    detailDatesAfterSame,
    detailDatesAfterChanged,
    recoverySync,
    recoveryChangeCount: recoveryChanges.length,
    recoveryPreview,
    recoveryApplied,
    routeAfterRecovery,
    recoveryConflict,
    rowSync,
    rowChangeCount: rowChanges.length,
    rowRestore,
    rowEventAfterRestore: rowDetailSheet.cell("A2").value
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

    assert payload["legacyOrderConflict"] is True
    assert payload["realChangeBlocked"] is True
    assert payload["first"]["schemaVersion"] == 13
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
    assert payload["detailSame"]["detailRowsRemoved"] == 2
    assert payload["detailChanged"]["eventsUpdated"] == 1
    assert payload["detailChanged"]["eventsUnchanged"] == 0
    assert payload["detailChanged"]["detailRowsRemoved"] == 0
    assert payload["detailFbasAfterCleanup"] == [
        "FBA12345",
        "FBA77777",
        "说明",
    ]
    assert isinstance(payload["detailDatesAfterSame"]["lastConfirmed"], (int, float))
    assert isinstance(payload["detailDatesAfterSame"]["updatedAt"], (int, float))
    assert isinstance(
        payload["detailDatesAfterChanged"]["lastConfirmed"], (int, float)
    )
    assert payload["detailDatesAfterChanged"]["updatedAt"] == "2026-07-30 10:00:00"
    assert payload["recoverySync"]["updated"] == ["FBA12345"]
    assert payload["recoveryChangeCount"] == 1
    assert len(payload["recoveryPreview"]["ready"]) == 1
    assert len(payload["recoveryApplied"]["applied"]) == 1
    assert payload["routeAfterRecovery"] == "2026-07-29 09:00 已到港"
    assert len(payload["recoveryConflict"]["conflicts"]) == 1
    assert payload["rowSync"]["eventsAdded"] == 1
    assert payload["rowChangeCount"] == 1
    assert len(payload["rowRestore"]["applied"]) == 1
    assert payload["rowEventAfterRestore"] == ""
    assert payload["headerPreserved"] == payload["headerOriginal"]
    assert all(not plan["additions"] for plan in payload["headerRemaining"]["plans"])
    assert payload["headerConflict"] is True
    assert payload["organized"]["detailRowsRemoved"] == 1
    assert payload["orderedRows"] == [["FBA22222", "先"], ["FBA22222", "后"], ["FBA11111", "一"]]
    assert len(payload["orderConflict"]["conflicts"]) == 1
    assert len(payload["orderRestore"]["applied"]) == 1
    assert payload["restoredOrder"] == ["a", "b", "c", "d"]
