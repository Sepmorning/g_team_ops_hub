const SCHEMA_VERSION = 5;
const DEFAULT_SHEET_NAME = "US-FBA";
const DETAIL_SHEET_NAME = "US-轨迹明细";
const HEADER_END_COLUMN = "CV";
const MAX_SCAN_ROW = 20000;
const MAX_ITEMS = 50;

// 主表与明细表均按第一行表头识别，不依赖列号，也不限制店铺、备注等其他列。
// 每个标准字段只能出现一次；数组中的其他名称用于兼容旧表头。
const MAIN_FIELD_DEFINITIONS = {
    fba: ["FBA号", "FBA单号", "FBA编号", "FBA"],
    carrier: ["货代", "物流商", "货代公司"],
    transport_ref: ["运输工具/班次", "船名航次/航班", "船名航次", "航班号"],
    current_phase: ["当前阶段"],
    current_node: ["当前节点"],
    latest_time: ["最新轨迹时间", "最新物流时间"],
    route: ["货代最新路由信息", "最新物流信息", "最新路由信息", "路由"],
    current_exception: ["当前异常", "物流异常"],
    pickup_time: ["提货", "提货时间"],
    estimated_departure: ["预计出发", "预计开船（机）时间", "预计开船(机)时间"],
    actual_departure: ["开船（机）时间", "开船(机)时间", "实际出发"],
    estimated_arrival: ["预计到达", "预计到港"],
    actual_arrival: ["到港", "到港时间", "实际到达"],
    estimated_delivery: ["预计送达", "预计派送"],
    last_mile_time: ["提取派送", "提取派送时间"],
    signed_time: ["签收时间", "签收"],
    pod_status: ["POD状态", "POD"],
    completion: ["是否完成"],
    data_status: ["数据状态"],
    updated_time: ["物流最后更新时间", "物流更新时间"]
};

const DETAIL_FIELD_DEFINITIONS = {
    event_id: ["事件编号"],
    fba: ["FBA号", "FBA单号", "FBA编号", "FBA"],
    carrier: ["货代", "物流商", "货代公司"],
    carrier_order_no: ["货代订单号"],
    event_time: ["轨迹发生时间"],
    phase: ["标准阶段"],
    node: ["标准节点"],
    event_type: ["信息属性"],
    content: ["物流轨迹原文"],
    related_plan: ["涉及计划"],
    validity: ["有效状态"],
    exception_status: ["异常状态"],
    transport_info: ["运输信息"],
    source_status: ["官网原始状态"],
    first_seen: ["首次获取时间"],
    last_confirmed: ["最后确认时间"],
    updated_at: ["系统更新时间"]
};

const MAIN_VALUE_FIELDS = [
    "transport_ref",
    "current_phase",
    "current_node",
    "latest_time",
    "route",
    "current_exception",
    "pickup_time",
    "estimated_departure",
    "actual_departure",
    "estimated_arrival",
    "actual_arrival",
    "estimated_delivery",
    "last_mile_time",
    "signed_time",
    "pod_status",
    "data_status",
    "updated_time"
];

const PROTECTED_ACTUAL_FIELDS = {
    pickup_time: true,
    actual_departure: true,
    actual_arrival: true,
    signed_time: true
};

const CLEARABLE_FIELDS = {
    current_exception: true
};

const DATE_ONLY_FIELDS = {
    pickup_time: true,
    estimated_departure: true,
    actual_departure: true,
    estimated_arrival: true,
    actual_arrival: true,
    estimated_delivery: true,
    last_mile_time: true,
    signed_time: true
};

const DATE_TIME_FIELDS = {
    latest_time: true,
    updated_time: true
};

const DETAIL_TEXT_FIELDS = {
    event_id: true,
    fba: true,
    carrier_order_no: true
};

const DETAIL_DATE_TIME_FIELDS = {
    event_time: true,
    first_seen: true,
    last_confirmed: true,
    updated_at: true
};

const MAIN_FIELD_LABELS = {
    pickup_time: "提货",
    actual_departure: "开船（机）时间",
    actual_arrival: "到港",
    last_mile_time: "提取派送",
    signed_time: "签收时间"
};

function normalizeText(value) {
    return String(value === null || value === undefined ? "" : value)
        .replace(/\s+/g, "")
        .trim();
}

function displayText(value) {
    return String(value === null || value === undefined ? "" : value).trim();
}

function padNumber(value) {
    const text = String(value);
    return text.length < 2 ? "0" + text : text;
}

function excelSerialText(value, includeTime) {
    if (typeof value !== "number" || !isFinite(value)) {
        return "";
    }
    const milliseconds = Math.round((value - 25569) * 86400000);
    const date = new Date(milliseconds);
    if (isNaN(date.getTime())) {
        return "";
    }
    const datePart = (
        date.getUTCFullYear() + "-" +
        padNumber(date.getUTCMonth() + 1) + "-" +
        padNumber(date.getUTCDate())
    );
    if (!includeTime) {
        return datePart;
    }
    return (
        datePart + " " +
        padNumber(date.getUTCHours()) + ":" +
        padNumber(date.getUTCMinutes()) + ":" +
        padNumber(date.getUTCSeconds())
    );
}

function normalizedDateText(value, includeTime) {
    const serial = excelSerialText(value, includeTime);
    if (serial !== "") {
        return serial;
    }
    const text = displayText(value);
    const match = text.match(
        /^(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?/
    );
    if (!match) {
        return text;
    }
    const datePart = (
        match[1] + "-" + padNumber(match[2]) + "-" + padNumber(match[3])
    );
    if (!includeTime) {
        return datePart;
    }
    return (
        datePart + " " +
        padNumber(match[4] || 0) + ":" +
        padNumber(match[5] || 0) + ":" +
        padNumber(match[6] || 0)
    );
}

function comparableMainValue(field, value) {
    if (DATE_ONLY_FIELDS[field]) {
        return normalizedDateText(value, false);
    }
    if (DATE_TIME_FIELDS[field]) {
        return normalizedDateText(value, true);
    }
    return displayText(value);
}

function normalizeFba(value) {
    return normalizeText(value).toUpperCase();
}

function isValidFba(value) {
    return /^FBA[A-Z0-9-]{5,}$/.test(value);
}

function columnNumberToName(columnNumber) {
    let result = "";
    let number = columnNumber;
    while (number > 0) {
        const remainder = (number - 1) % 26;
        result = String.fromCharCode(65 + remainder) + result;
        number = Math.floor((number - 1) / 26);
    }
    return result;
}

function firstRowValues(rawValues) {
    if (!Array.isArray(rawValues)) {
        return [rawValues];
    }
    if (rawValues.length > 0 && Array.isArray(rawValues[0])) {
        return rawValues[0];
    }
    return rawValues;
}

function singleColumnValues(rawValues) {
    if (!Array.isArray(rawValues)) {
        return [rawValues];
    }
    if (rawValues.length > 0 && Array.isArray(rawValues[0])) {
        return rawValues.map(function (row) {
            return row.length > 0 ? row[0] : "";
        });
    }
    return rawValues;
}

function singleRowValues(rawValues) {
    return firstRowValues(rawValues);
}

function pushUnique(values, value) {
    if (values.indexOf(value) < 0) {
        values.push(value);
    }
}

function findTargetSheet(sheetName) {
    const sheets = Application.Sheets;
    const matches = [];
    for (let index = 1; index <= sheets.Count; index++) {
        const sheet = sheets.Item(index);
        if (normalizeText(sheet.Name) === normalizeText(sheetName)) {
            matches.push(sheet);
        }
    }
    if (matches.length === 0) {
        throw new Error("没有找到子表：" + sheetName);
    }
    if (matches.length > 1) {
        throw new Error("存在多个同名子表：" + sheetName);
    }
    return matches[0];
}

function readHeaders(sheet) {
    const values = firstRowValues(
        sheet.Range("A1:" + HEADER_END_COLUMN + "1").Value2
    );
    const headers = [];
    for (let index = 0; index < values.length; index++) {
        const original = displayText(values[index]);
        const normalized = normalizeText(original);
        if (normalized !== "") {
            headers.push({
                columnNumber: index + 1,
                columnLetter: columnNumberToName(index + 1),
                text: original,
                normalized: normalized
            });
        }
    }
    return headers;
}

function findColumnsByDefinitions(sheet, definitions, tableLabel) {
    const headers = readHeaders(sheet);
    const result = {};
    const missing = [];
    const duplicates = [];
    const keys = Object.keys(definitions);

    for (let keyIndex = 0; keyIndex < keys.length; keyIndex++) {
        const key = keys[keyIndex];
        const aliases = definitions[key].map(normalizeText);
        const matches = headers.filter(function (header) {
            return aliases.indexOf(header.normalized) >= 0;
        });
        if (matches.length === 0) {
            missing.push(definitions[key][0]);
        } else if (matches.length > 1) {
            duplicates.push(
                definitions[key][0] + "（" +
                matches.map(function (item) { return item.text; }).join("、") +
                "）"
            );
        } else {
            result[key] = matches[0];
        }
    }

    if (missing.length > 0) {
        throw new Error(
            tableLabel + "第一行缺少表头：" + missing.join("、") +
            "。表头顺序可任意，店铺、备注等其他列可保留"
        );
    }
    if (duplicates.length > 0) {
        throw new Error(
            tableLabel + "存在重复含义的表头：" + duplicates.join("、") +
            "。为防止写错列，已停止处理"
        );
    }
    return result;
}

function columnLetters(columns) {
    const result = {};
    const keys = Object.keys(columns);
    for (let index = 0; index < keys.length; index++) {
        result[keys[index]] = columns[keys[index]].columnLetter;
    }
    return result;
}

function columnHeaders(columns) {
    const result = {};
    const keys = Object.keys(columns);
    for (let index = 0; index < keys.length; index++) {
        result[keys[index]] = columns[keys[index]].text;
    }
    return result;
}

function lastUsedRow(sheet, tableLabel) {
    const usedRange = sheet.UsedRange;
    const lastRow = usedRange.Row + usedRange.Rows.Count - 1;
    if (lastRow > MAX_SCAN_ROW) {
        throw new Error(
            tableLabel + "使用区域达到第" + lastRow +
            "行，超过安全上限" + MAX_SCAN_ROW +
            "。请清理表格底部多余格式后重试"
        );
    }
    return lastRow;
}

function applyMainFormats(sheet, columns, rowRanges) {
    const dateFields = Object.keys(DATE_ONLY_FIELDS);
    const dateTimeFields = Object.keys(DATE_TIME_FIELDS);
    for (let rangeIndex = 0; rangeIndex < rowRanges.length; rangeIndex++) {
        const rowRange = rowRanges[rangeIndex];
        for (let fieldIndex = 0; fieldIndex < dateFields.length; fieldIndex++) {
            const field = dateFields[fieldIndex];
            sheet.Range(
                columns[field].columnLetter + rowRange.start + ":" +
                columns[field].columnLetter + rowRange.end
            ).NumberFormat = "yyyy-mm-dd";
        }
        for (
            let fieldIndex = 0;
            fieldIndex < dateTimeFields.length;
            fieldIndex++
        ) {
            const field = dateTimeFields[fieldIndex];
            sheet.Range(
                columns[field].columnLetter + rowRange.start + ":" +
                columns[field].columnLetter + rowRange.end
            ).NumberFormat = "yyyy-mm-dd hh:mm:ss";
        }
    }
}

function applyDetailFormats(sheet, columns, startRow, endRow) {
    if (endRow < startRow) {
        return;
    }
    const textFields = Object.keys(DETAIL_TEXT_FIELDS);
    const dateTimeFields = Object.keys(DETAIL_DATE_TIME_FIELDS);
    for (let fieldIndex = 0; fieldIndex < textFields.length; fieldIndex++) {
        const field = textFields[fieldIndex];
        sheet.Range(
            columns[field].columnLetter + startRow + ":" +
            columns[field].columnLetter + endRow
        ).NumberFormat = "@";
    }
    for (let fieldIndex = 0; fieldIndex < dateTimeFields.length; fieldIndex++) {
        const field = dateTimeFields[fieldIndex];
        sheet.Range(
            columns[field].columnLetter + startRow + ":" +
            columns[field].columnLetter + endRow
        ).NumberFormat = "yyyy-mm-dd hh:mm:ss";
    }
}

function consecutiveRowRanges(rows) {
    const unique = [];
    for (let index = 0; index < rows.length; index++) {
        if (unique.indexOf(rows[index]) < 0) {
            unique.push(rows[index]);
        }
    }
    unique.sort(function (left, right) { return left - right; });
    const ranges = [];
    for (let index = 0; index < unique.length; index++) {
        const row = unique[index];
        const current = ranges[ranges.length - 1];
        if (current && row === current.end + 1) {
            current.end = row;
        } else {
            ranges.push({ start: row, end: row });
        }
    }
    return ranges;
}

function buildRowsByFba(sheet, columns, lastRow) {
    const values = lastRow < 2 ? [] : singleColumnValues(
        sheet.Range(
            columns.fba.columnLetter + "2:" +
            columns.fba.columnLetter + lastRow
        ).Value2
    );
    const result = Object.create(null);
    for (let index = 0; index < values.length; index++) {
        const fba = normalizeFba(values[index]);
        if (fba === "") {
            continue;
        }
        if (!result[fba]) {
            result[fba] = [];
        }
        result[fba].push(index + 2);
    }
    return result;
}

function mainColumnBounds(columns) {
    let minimum = columns.fba.columnNumber;
    let maximum = columns.fba.columnNumber;
    const keys = Object.keys(columns);
    for (let index = 0; index < keys.length; index++) {
        const number = columns[keys[index]].columnNumber;
        minimum = Math.min(minimum, number);
        maximum = Math.max(maximum, number);
    }
    return { minimum: minimum, maximum: maximum };
}

function writeGroupsForColumn(sheet, columnLetter, writes, states) {
    writes.sort(function (left, right) { return left.row - right.row; });
    const groups = [];
    for (let index = 0; index < writes.length; index++) {
        const item = writes[index];
        const group = groups[groups.length - 1];
        if (group && item.row === group[group.length - 1].row + 1) {
            group.push(item);
        } else {
            groups.push([item]);
        }
    }

    function markSuccess(group) {
        for (let index = 0; index < group.length; index++) {
            states[group[index].fba].written = true;
        }
    }

    function writeSingle(item) {
        try {
            sheet.Range(columnLetter + item.row).Value2 = item.value;
            states[item.fba].written = true;
        } catch (error) {
            states[item.fba].failed = true;
        }
    }

    for (let groupIndex = 0; groupIndex < groups.length; groupIndex++) {
        const group = groups[groupIndex];
        if (group.length === 1) {
            writeSingle(group[0]);
            continue;
        }
        try {
            sheet.Range(
                columnLetter + group[0].row + ":" +
                columnLetter + group[group.length - 1].row
            ).Value2 = group.map(function (item) { return [item.value]; });
            markSuccess(group);
        } catch (error) {
            for (let itemIndex = 0; itemIndex < group.length; itemIndex++) {
                writeSingle(group[itemIndex]);
            }
        }
    }
}

function writeDetailUpdates(detailSheet, detailColumns, writesByField) {
    const fields = Object.keys(writesByField);
    for (let fieldIndex = 0; fieldIndex < fields.length; fieldIndex++) {
        const field = fields[fieldIndex];
        const writes = writesByField[field];
        writes.sort(function (left, right) { return left.row - right.row; });
        const groups = [];
        for (let index = 0; index < writes.length; index++) {
            const item = writes[index];
            const group = groups[groups.length - 1];
            if (group && item.row === group[group.length - 1].row + 1) {
                group.push(item);
            } else {
                groups.push([item]);
            }
        }
        for (let groupIndex = 0; groupIndex < groups.length; groupIndex++) {
            const group = groups[groupIndex];
            const columnLetter = detailColumns[field].columnLetter;
            const targetRange = detailSheet.Range(
                columnLetter + group[0].row + ":" +
                columnLetter + group[group.length - 1].row
            );
            if (DETAIL_TEXT_FIELDS[field]) {
                targetRange.NumberFormat = "@";
            } else if (DETAIL_DATE_TIME_FIELDS[field]) {
                targetRange.NumberFormat = "yyyy-mm-dd hh:mm:ss";
            }
            if (group.length === 1) {
                detailSheet.Range(columnLetter + group[0].row).Value2 =
                    group[0].value;
            } else {
                targetRange.Value2 = group.map(function (item) {
                    return [item.value];
                });
            }
        }
    }
}

function syncEvents(detailSheet, detailColumns, acceptedItems) {
    const detailLastRow = lastUsedRow(detailSheet, DETAIL_SHEET_NAME);
    const existingIds = Object.create(null);
    const mutableFields = [
        "carrier_order_no",
        "phase",
        "node",
        "event_type",
        "related_plan",
        "validity",
        "exception_status",
        "transport_info",
        "source_status",
        "last_confirmed",
        "updated_at"
    ];
    const mutableValues = Object.create(null);
    if (detailLastRow >= 2) {
        applyDetailFormats(detailSheet, detailColumns, 2, detailLastRow);
        const values = singleColumnValues(
            detailSheet.Range(
                detailColumns.event_id.columnLetter + "2:" +
                detailColumns.event_id.columnLetter + detailLastRow
            ).Value2
        );
        for (let index = 0; index < values.length; index++) {
            const eventId = displayText(values[index]);
            if (eventId !== "") {
                if (existingIds[eventId]) {
                    throw new Error(
                        "事件编号 " + eventId + " 在明细表中重复，已停止写入"
                    );
                }
                existingIds[eventId] = {
                    row: index + 2,
                    index: index,
                    isNew: false
                };
            }
        }
        for (let fieldIndex = 0; fieldIndex < mutableFields.length; fieldIndex++) {
            const field = mutableFields[fieldIndex];
            mutableValues[field] = singleColumnValues(
                detailSheet.Range(
                    detailColumns[field].columnLetter + "2:" +
                    detailColumns[field].columnLetter + detailLastRow
                ).Value2
            );
        }
    } else {
        for (let fieldIndex = 0; fieldIndex < mutableFields.length; fieldIndex++) {
            mutableValues[mutableFields[fieldIndex]] = [];
        }
    }

    const writesByField = Object.create(null);
    for (let fieldIndex = 0; fieldIndex < mutableFields.length; fieldIndex++) {
        writesByField[mutableFields[fieldIndex]] = [];
    }
    const newEvents = [];
    let unchangedCount = 0;
    let updatedCount = 0;
    for (let itemIndex = 0; itemIndex < acceptedItems.length; itemIndex++) {
        const item = acceptedItems[itemIndex];
        const events = (Array.isArray(item.events) ? item.events : [])
            .map(function (event, originalIndex) {
                return { event: event, originalIndex: originalIndex };
            })
            .sort(function (left, right) {
                const timeDifference = displayText(
                    left.event && left.event.event_time
                ).localeCompare(
                    displayText(right.event && right.event.event_time)
                );
                return timeDifference || left.originalIndex - right.originalIndex;
            })
            .map(function (item) { return item.event; });
        for (let eventIndex = 0; eventIndex < events.length; eventIndex++) {
            const source = events[eventIndex];
            const event = source && typeof source === "object" ? source : {};
            const eventId = displayText(event.event_id);
            const eventFba = normalizeFba(event.fba || item.fba);
            const content = displayText(event.content);
            if (eventId === "" || !isValidFba(eventFba) || content === "") {
                continue;
            }
            const existing = existingIds[eventId];
            if (existing) {
                unchangedCount++;
                if (existing.isNew) {
                    continue;
                }
                let eventChanged = false;
                for (
                    let fieldIndex = 0;
                    fieldIndex < mutableFields.length;
                    fieldIndex++
                ) {
                    const field = mutableFields[fieldIndex];
                    const incoming = displayText(event[field]);
                    const current = displayText(
                        mutableValues[field][existing.index]
                    );
                    if (incoming !== "" && incoming !== current) {
                        writesByField[field].push({
                            row: existing.row,
                            value: incoming
                        });
                        eventChanged = true;
                    }
                }
                if (eventChanged) {
                    updatedCount++;
                }
                continue;
            }
            // 本批后续出现相同事件时，也会落入 existingIds 分支。
            existingIds[eventId] = {
                row: detailLastRow + newEvents.length + 1,
                index: -1,
                isNew: true
            };
            const normalized = Object.assign({}, event, {
                event_id: eventId,
                fba: eventFba
            });
            newEvents.push(normalized);
        }
    }
    writeDetailUpdates(detailSheet, detailColumns, writesByField);

    if (newEvents.length === 0) {
        return {
            added: 0,
            updated: updatedCount,
            unchanged: unchangedCount
        };
    }

    const orderedColumns = Object.keys(detailColumns).map(function (key) {
        return {
            key: key,
            columnNumber: detailColumns[key].columnNumber
        };
    }).sort(function (left, right) {
        return left.columnNumber - right.columnNumber;
    });
    const columnGroups = [];
    for (let index = 0; index < orderedColumns.length; index++) {
        const item = orderedColumns[index];
        const group = columnGroups[columnGroups.length - 1];
        if (
            group &&
            item.columnNumber === group[group.length - 1].columnNumber + 1
        ) {
            group.push(item);
        } else {
            columnGroups.push([item]);
        }
    }
    const startRow = Math.max(2, detailLastRow + 1);
    const endRow = startRow + newEvents.length - 1;
    applyDetailFormats(detailSheet, detailColumns, startRow, endRow);
    // 只写标准明细列。若用户在标准表头之间插入自定义列，不会用空值覆盖。
    for (let groupIndex = 0; groupIndex < columnGroups.length; groupIndex++) {
        const group = columnGroups[groupIndex];
        const rows = newEvents.map(function (event) {
            return group.map(function (item) {
                return displayText(event[item.key]);
            });
        });
        detailSheet.Range(
            columnNumberToName(group[0].columnNumber) + startRow + ":" +
            columnNumberToName(group[group.length - 1].columnNumber) + endRow
        ).Value2 = rows;
    }
    return {
        added: newEvents.length,
        updated: updatedCount,
        unchanged: unchangedCount
    };
}

const argv = Context && Context.argv ? Context.argv : {};
const action = normalizeText(argv.action || "validate").toLowerCase();
const sheetName = normalizeText(argv.sheet_name || DEFAULT_SHEET_NAME);
const items = Array.isArray(argv.items) ? argv.items : [];

if (sheetName !== DEFAULT_SHEET_NAME) {
    throw new Error("当前脚本只允许处理 " + DEFAULT_SHEET_NAME);
}
if (
    action !== "validate" &&
    action !== "sync" &&
    action !== "sync_tracking" &&
    action !== "list_pending"
) {
    throw new Error("不支持的操作：" + action);
}

const targetSheet = findTargetSheet(sheetName);
const detailSheet = findTargetSheet(DETAIL_SHEET_NAME);
const columns = findColumnsByDefinitions(
    targetSheet,
    MAIN_FIELD_DEFINITIONS,
    DEFAULT_SHEET_NAME
);
const detailColumns = findColumnsByDefinitions(
    detailSheet,
    DETAIL_FIELD_DEFINITIONS,
    DETAIL_SHEET_NAME
);
const baseResult = {
    success: true,
    schemaVersion: SCHEMA_VERSION,
    sheetName: targetSheet.Name,
    detailSheetName: detailSheet.Name,
    columns: columnLetters(columns),
    detailColumns: columnLetters(detailColumns),
    headers: columnHeaders(columns),
    detailHeaders: columnHeaders(detailColumns)
};

if (action === "validate") {
    console.log(JSON.stringify(baseResult));
    return baseResult;
}

const lastRow = lastUsedRow(targetSheet, DEFAULT_SHEET_NAME);

if (action === "list_pending") {
    const offset = Math.max(0, Number(argv.offset) || 0);
    const limit = Math.max(1, Math.min(500, Number(argv.limit) || 500));
    if (lastRow < 2) {
        return Object.assign(baseResult, {
            fbas: [],
            total: 0,
            offset: offset,
            nextOffset: offset,
            hasMore: false
        });
    }
    // WPS会把日期保存为Excel序号；统一显示格式，兼容升级前已写入的历史行。
    applyMainFormats(
        targetSheet,
        columns,
        [{ start: 2, end: lastRow }]
    );

    const allFbaValues = singleColumnValues(
        targetSheet.Range(
            columns.fba.columnLetter + "2:" + columns.fba.columnLetter + lastRow
        ).Value2
    );
    const completionValues = singleColumnValues(
        targetSheet.Range(
            columns.completion.columnLetter + "2:" +
            columns.completion.columnLetter + lastRow
        ).Value2
    );
    const carrierValues = singleColumnValues(
        targetSheet.Range(
            columns.carrier.columnLetter + "2:" +
            columns.carrier.columnLetter + lastRow
        ).Value2
    );
    const updatedValues = singleColumnValues(
        targetSheet.Range(
            columns.updated_time.columnLetter + "2:" +
            columns.updated_time.columnLetter + lastRow
        ).Value2
    );
    const pending = [];
    const seen = Object.create(null);
    for (let index = 0; index < allFbaValues.length; index++) {
        const fba = normalizeFba(allFbaValues[index]);
        const completed = normalizeText(completionValues[index]).toLowerCase();
        const hasSnapshot = normalizeText(updatedValues[index]) !== "";
        // 已完成的旧记录仍会补写一次完整快照和历史明细，之后不再重复查询。
        if (!isValidFba(fba) || (completed === "是" && hasSnapshot)) {
            continue;
        }
        if (!seen[fba]) {
            seen[fba] = true;
            pending.push({
                fba: fba,
                carrier: displayText(carrierValues[index])
            });
        }
    }
    const page = pending.slice(offset, offset + limit);
    const nextOffset = offset + page.length;
    return Object.assign(baseResult, {
        fbas: page,
        total: pending.length,
        offset: offset,
        nextOffset: nextOffset,
        hasMore: nextOffset < pending.length
    });
}

if (items.length > MAX_ITEMS) {
    throw new Error("单次最多处理 " + MAX_ITEMS + " 个FBA");
}

const updated = [];
const unchanged = [];
const notInSheet = [];
const duplicateRows = [];
const failures = [];
const conflicts = [];

if (items.length === 0) {
    return Object.assign(baseResult, {
        updated: updated,
        unchanged: unchanged,
        notInSheet: notInSheet,
        duplicateRows: duplicateRows,
        failures: failures,
        conflicts: conflicts,
        eventsAdded: 0,
        eventsUpdated: 0,
        eventsUnchanged: 0
    });
}

const rowsByFba = buildRowsByFba(targetSheet, columns, lastRow);
const seenInput = Object.create(null);
const states = Object.create(null);
const acceptedItems = [];
const writesByColumn = Object.create(null);
const bounds = mainColumnBounds(columns);

for (let itemIndex = 0; itemIndex < items.length; itemIndex++) {
    const sourceItem = items[itemIndex];
    const item = sourceItem && typeof sourceItem === "object" ? sourceItem : {};
    const fba = normalizeFba(item.fba);
    if (!isValidFba(fba)) {
        failures.push(fba || "第" + (itemIndex + 1) + "项");
        continue;
    }
    if (seenInput[fba]) {
        continue;
    }
    seenInput[fba] = true;

    const matches = rowsByFba[fba] || [];
    if (matches.length === 0) {
        notInSheet.push(fba);
        continue;
    }
    if (matches.length > 1) {
        duplicateRows.push(fba);
        continue;
    }

    const main = item.main && typeof item.main === "object"
        ? Object.assign({}, item.main)
        : {};
    if (displayText(main.route) === "" && displayText(item.route) !== "") {
        main.route = item.route;
    }
    if (action === "sync_tracking" && displayText(main.route) === "") {
        failures.push(fba);
        continue;
    }
    if (action === "sync" && displayText(main.route) === "") {
        failures.push(fba);
        continue;
    }

    const row = matches[0];
    const rowValues = singleRowValues(
        targetSheet.Range(
            columnNumberToName(bounds.minimum) + row + ":" +
            columnNumberToName(bounds.maximum) + row
        ).Value2
    );
    states[fba] = {
        changed: false,
        written: false,
        failed: false
    };

    for (let fieldIndex = 0; fieldIndex < MAIN_VALUE_FIELDS.length; fieldIndex++) {
        const field = MAIN_VALUE_FIELDS[fieldIndex];
        const incoming = displayText(main[field]);
        const column = columns[field];
        const currentRaw = (
            rowValues[column.columnNumber - bounds.minimum]
        );
        const current = displayText(currentRaw);
        const comparableIncoming = comparableMainValue(field, incoming);
        const comparableCurrent = comparableMainValue(field, currentRaw);

        if (incoming === "" && !CLEARABLE_FIELDS[field]) {
            continue;
        }
        if (comparableCurrent === comparableIncoming) {
            continue;
        }
        if (PROTECTED_ACTUAL_FIELDS[field] && current !== "" && incoming !== "") {
            conflicts.push(fba + "：" + MAIN_FIELD_LABELS[field]);
            continue;
        }

        states[fba].changed = true;
        if (!writesByColumn[field]) {
            writesByColumn[field] = [];
        }
        writesByColumn[field].push({
            fba: fba,
            row: row,
            value: incoming
        });
    }

    acceptedItems.push({
        fba: fba,
        row: row,
        events: Array.isArray(item.events) ? item.events : []
    });
}

const writeFields = Object.keys(writesByColumn);
for (let fieldIndex = 0; fieldIndex < writeFields.length; fieldIndex++) {
    const field = writeFields[fieldIndex];
    writeGroupsForColumn(
        targetSheet,
        columns[field].columnLetter,
        writesByColumn[field],
        states
    );
}
// 无论查询服务内部如何并发，明细都按主表实际行号排列。
acceptedItems.sort(function (left, right) {
    return left.row - right.row;
});
applyMainFormats(
    targetSheet,
    columns,
    consecutiveRowRanges(
        acceptedItems.map(function (item) { return item.row; })
    )
);

const stateFbas = Object.keys(states);
for (let index = 0; index < stateFbas.length; index++) {
    const fba = stateFbas[index];
    const state = states[fba];
    if (state.failed) {
        pushUnique(failures, fba);
    }
    if (state.written) {
        updated.push(fba);
    } else if (!state.failed) {
        unchanged.push(fba);
    }
}

let eventSummary = { added: 0, updated: 0, unchanged: 0 };
if (action === "sync_tracking") {
    try {
        eventSummary = syncEvents(
            detailSheet,
            detailColumns,
            acceptedItems
        );
    } catch (error) {
        for (let index = 0; index < acceptedItems.length; index++) {
            pushUnique(failures, acceptedItems[index].fba);
        }
        throw new Error(
            "主表已按可写内容处理，但物流轨迹明细追加失败；" +
            "可重新执行一键更新安全补写。原因：" + error.message
        );
    }
}

const result = Object.assign(baseResult, {
    updated: updated,
    unchanged: unchanged,
    notInSheet: notInSheet,
    duplicateRows: duplicateRows,
    failures: failures,
    conflicts: conflicts,
    eventsAdded: eventSummary.added,
    eventsUpdated: eventSummary.updated,
    eventsUnchanged: eventSummary.unchanged
});

console.log(JSON.stringify(result));
return result;
