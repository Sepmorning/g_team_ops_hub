const SCHEMA_VERSION = 11;
const DEFAULT_SHEET_NAME = "US-FBA";
const DEFAULT_DETAIL_SHEET_NAME = "US-轨迹明细";
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

// “矢车菊蓝，着色1，浅色80%”使用工作簿主题色，随当前WPS主题保持一致。
// 物流最后更新时间只是审计时间：继续写入，但不作为业务变化着色。
const SYSTEM_HIGHLIGHT_THEME_COLOR = 5;
const SYSTEM_HIGHLIGHT_TINT = 0.8;
const SYSTEM_NO_FILL_COLOR_INDEX = -4142;
const BUSINESS_HIGHLIGHT_FIELDS = MAIN_VALUE_FIELDS.filter(function (field) {
    return field !== "updated_time";
});

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

function workbookSheets() {
    const sheets = Application.Sheets;
    const result = [];
    for (let index = 1; index <= sheets.Count; index++) {
        const sheet = sheets.Item(index);
        result.push({
            id: displayText(sheet.Id),
            name: displayText(sheet.Name)
        });
    }
    return result;
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

function comparableDetailValue(field, value) {
    if (DETAIL_DATE_TIME_FIELDS[field]) {
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

function errorText(error) {
    const text = displayText(error && error.message ? error.message : error);
    return text.length > 120 ? text.slice(0, 120) + "…" : text;
}

function pushFormatFailure(values, fba, address, operation, error) {
    pushUnique(
        values,
        fba + "：" + address + "（" + operation + "失败：" + errorText(error) + "）"
    );
}

function isSystemHighlight(cell) {
    let interior;
    try {
        interior = cell.Interior;
    } catch (error) {
        return false;
    }
    try {
        if (Number(interior.ColorIndex) === SYSTEM_NO_FILL_COLOR_INDEX) {
            return false;
        }
    } catch (error) {
        // 部分无填充单元格读取ColorIndex时可能返回空值，继续检查主题属性。
    }
    try {
        return (
            Number(interior.ThemeColor) === SYSTEM_HIGHLIGHT_THEME_COLOR &&
            Math.abs(Number(interior.TintAndShade) - SYSTEM_HIGHLIGHT_TINT) < 0.02
        );
    } catch (error) {
        // 手工RGB填充不一定具有主题属性，不把它当作系统旧高亮。
        return false;
    }
}

function clearPreviousSystemHighlights(
    sheet,
    columns,
    acceptedItems,
    formatFailures
) {
    for (let itemIndex = 0; itemIndex < acceptedItems.length; itemIndex++) {
        const item = acceptedItems[itemIndex];
        for (
            let fieldIndex = 0;
            fieldIndex < BUSINESS_HIGHLIGHT_FIELDS.length;
            fieldIndex++
        ) {
            const field = BUSINESS_HIGHLIGHT_FIELDS[fieldIndex];
            const address = columns[field].columnLetter + item.row;
            try {
                const cell = sheet.Range(address);
                if (isSystemHighlight(cell)) {
                    cell.Interior.ColorIndex = SYSTEM_NO_FILL_COLOR_INDEX;
                }
            } catch (error) {
                pushFormatFailure(
                    formatFailures,
                    item.fba,
                    address,
                    "清除旧高亮",
                    error
                );
            }
        }
    }
}

function applySystemHighlight(range) {
    range.Interior.ThemeColor = SYSTEM_HIGHLIGHT_THEME_COLOR;
    range.Interior.TintAndShade = SYSTEM_HIGHLIGHT_TINT;
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

function activeFbasFromMain(sheet, columns, lastRow) {
    const result = Object.create(null);
    if (lastRow < 2) {
        return result;
    }
    const fbaValues = singleColumnValues(
        sheet.Range(
            columns.fba.columnLetter + "2:" +
            columns.fba.columnLetter + lastRow
        ).Value2
    );
    const completionValues = singleColumnValues(
        sheet.Range(
            columns.completion.columnLetter + "2:" +
            columns.completion.columnLetter + lastRow
        ).Value2
    );
    for (let index = 0; index < fbaValues.length; index++) {
        const fba = normalizeFba(fbaValues[index]);
        // 与一键查询保持同一口径：只有“是否完成”为空才属于活跃货件。
        if (
            isValidFba(fba) &&
            normalizeText(completionValues[index]) === ""
        ) {
            result[fba] = true;
        }
    }
    return result;
}

function removeInactiveDetailRows(
    mainSheet,
    mainColumns,
    mainLastRow,
    detailSheet,
    detailColumns
) {
    const activeFbas = activeFbasFromMain(
        mainSheet,
        mainColumns,
        mainLastRow
    );
    const detailLastRow = lastUsedRow(detailSheet, detailSheet.Name);
    if (detailLastRow < 2) {
        return 0;
    }
    const detailFbas = singleColumnValues(
        detailSheet.Range(
            detailColumns.fba.columnLetter + "2:" +
            detailColumns.fba.columnLetter + detailLastRow
        ).Value2
    );
    const removableRows = [];
    for (let index = 0; index < detailFbas.length; index++) {
        const fba = normalizeFba(detailFbas[index]);
        // 无FBA或格式异常的说明/自定义行不自动删除。
        if (isValidFba(fba) && !activeFbas[fba]) {
            removableRows.push(index + 2);
        }
    }
    const ranges = consecutiveRowRanges(removableRows);
    // 从底部删除，避免前面的行号因上移而失效。
    for (let index = ranges.length - 1; index >= 0; index--) {
        const rowRange = ranges[index];
        detailSheet.Range(
            detailColumns.fba.columnLetter + rowRange.start + ":" +
            detailColumns.fba.columnLetter + rowRange.end
        ).EntireRow.Delete();
    }
    return removableRows.length;
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

function writeGroupsForColumn(
    sheet,
    columnLetter,
    writes,
    states,
    updatedCells,
    formatFailures
) {
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

    function recordSuccess(item) {
        const state = states[item.fba];
        state.written = true;
        if (item.business) {
            state.businessWritten = true;
            updatedCells.push({
                fba: item.fba,
                row: item.row,
                address: item.address,
                field: item.field,
                header: item.header,
                oldValue: item.oldValue,
                newValue: item.newValue
            });
        } else if (item.field === "updated_time") {
            state.auditWritten = true;
        }
    }

    function markSuccess(group) {
        for (let index = 0; index < group.length; index++) {
            recordSuccess(group[index]);
        }
    }

    function highlightSingle(item) {
        if (!item.business) {
            return;
        }
        try {
            applySystemHighlight(sheet.Range(item.address));
        } catch (error) {
            pushFormatFailure(
                formatFailures,
                item.fba,
                item.address,
                "设置本次更新高亮",
                error
            );
        }
    }

    function highlightGroup(group) {
        if (!group[0].business) {
            return;
        }
        try {
            applySystemHighlight(
                sheet.Range(
                    columnLetter + group[0].row + ":" +
                    columnLetter + group[group.length - 1].row
                )
            );
        } catch (error) {
            for (let index = 0; index < group.length; index++) {
                highlightSingle(group[index]);
            }
        }
    }

    function writeSingle(item) {
        try {
            sheet.Range(columnLetter + item.row).Value2 = item.value;
            recordSuccess(item);
            highlightSingle(item);
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
            highlightGroup(group);
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
    const detailLastRow = lastUsedRow(detailSheet, detailSheet.Name);
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
                if (existing.isNew) {
                    unchangedCount++;
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
                    const currentRaw = mutableValues[field][existing.index];
                    if (
                        incoming !== "" &&
                        comparableDetailValue(field, incoming) !==
                            comparableDetailValue(field, currentRaw)
                    ) {
                        writesByField[field].push({
                            row: existing.row,
                            value: incoming
                        });
                        eventChanged = true;
                    }
                }
                if (eventChanged) {
                    updatedCount++;
                } else {
                    unchangedCount++;
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

function scalarValue(range) {
    const values = firstRowValues(range.Value2);
    return values.length > 0 ? values[0] : "";
}

function comparableDetailRow(value) {
    if (value === null || value === undefined) {
        return null;
    }
    const result = {};
    const fields = Object.keys(DETAIL_FIELD_DEFINITIONS);
    for (let index = 0; index < fields.length; index++) {
        const field = fields[index];
        result[field] = comparableDetailValue(field, value[field]);
    }
    return result;
}

function buildDetailRowsByEventId(sheet, columns, detailLastRow) {
    const result = Object.create(null);
    if (detailLastRow < 2) {
        return result;
    }
    const values = singleColumnValues(
        sheet.Range(
            columns.event_id.columnLetter + "2:" +
            columns.event_id.columnLetter + detailLastRow
        ).Value2
    );
    for (let index = 0; index < values.length; index++) {
        const eventId = displayText(values[index]);
        if (eventId === "") {
            continue;
        }
        if (result[eventId]) {
            throw new Error(
                "事件编号 " + eventId + " 在明细表中重复，已停止处理"
            );
        }
        result[eventId] = index + 2;
    }
    return result;
}

function detailRowValue(sheet, columns, row) {
    const result = {};
    const fields = Object.keys(DETAIL_FIELD_DEFINITIONS);
    for (let index = 0; index < fields.length; index++) {
        const field = fields[index];
        result[field] = scalarValue(
            sheet.Range(columns[field].columnLetter + row)
        );
    }
    return result;
}

function trackingCellSnapshot(sheet, columns, fba, row, field) {
    const column = columns[field];
    const address = column.columnLetter + row;
    const value = scalarValue(sheet.Range(address));
    return {
        targetType: "cell",
        sheetName: displayText(sheet.Name),
        matchHeader: columns.fba.text,
        matchValue: fba,
        itemKey: fba,
        field: field,
        header: column.text,
        cellAddress: address,
        value: value,
        comparableValue: comparableMainValue(field, value)
    };
}

function trackingRowSnapshot(
    detailSheet,
    detailColumns,
    eventId,
    row,
    itemKey,
    reason
) {
    const value = row ? detailRowValue(detailSheet, detailColumns, row) : null;
    return {
        targetType: "row",
        sheetName: displayText(detailSheet.Name),
        matchHeader: detailColumns.event_id.text,
        matchValue: eventId,
        itemKey: itemKey,
        reason: reason || "event",
        field: "__row__",
        header: "轨迹事件行",
        cellAddress: row ? detailColumns.event_id.columnLetter + row : "",
        value: value,
        comparableValue: comparableDetailRow(value)
    };
}

function collectTrackingSnapshots(
    mainSheet,
    mainColumns,
    detailSheet,
    detailColumns,
    snapshotItems,
    includeCleanup
) {
    const mainLastRow = lastUsedRow(mainSheet, mainSheet.Name);
    const rowsByFba = buildRowsByFba(mainSheet, mainColumns, mainLastRow);
    const detailLastRow = lastUsedRow(detailSheet, detailSheet.Name);
    const detailRows = buildDetailRowsByEventId(
        detailSheet, detailColumns, detailLastRow
    );
    const eventTargets = Object.create(null);
    const snapshots = [];
    const seenFbas = Object.create(null);

    for (let itemIndex = 0; itemIndex < snapshotItems.length; itemIndex++) {
        const item = snapshotItems[itemIndex] &&
            typeof snapshotItems[itemIndex] === "object"
            ? snapshotItems[itemIndex] : {};
        const fba = normalizeFba(item.fba);
        if (isValidFba(fba) && !seenFbas[fba]) {
            seenFbas[fba] = true;
            const rows = rowsByFba[fba] || [];
            if (rows.length === 1) {
                for (
                    let fieldIndex = 0;
                    fieldIndex < MAIN_VALUE_FIELDS.length;
                    fieldIndex++
                ) {
                    snapshots.push(
                        trackingCellSnapshot(
                            mainSheet,
                            mainColumns,
                            fba,
                            rows[0],
                            MAIN_VALUE_FIELDS[fieldIndex]
                        )
                    );
                }
            }
        }
        const events = Array.isArray(item.events) ? item.events : [];
        for (let eventIndex = 0; eventIndex < events.length; eventIndex++) {
            const eventId = displayText(events[eventIndex] && events[eventIndex].event_id);
            if (eventId !== "" && !eventTargets[eventId]) {
                eventTargets[eventId] = {
                    itemKey: fba,
                    reason: "event"
                };
            }
        }
    }

    if (includeCleanup && detailLastRow >= 2) {
        const activeFbas = activeFbasFromMain(
            mainSheet, mainColumns, mainLastRow
        );
        const eventIds = singleColumnValues(
            detailSheet.Range(
                detailColumns.event_id.columnLetter + "2:" +
                detailColumns.event_id.columnLetter + detailLastRow
            ).Value2
        );
        const detailFbas = singleColumnValues(
            detailSheet.Range(
                detailColumns.fba.columnLetter + "2:" +
                detailColumns.fba.columnLetter + detailLastRow
            ).Value2
        );
        for (let index = 0; index < eventIds.length; index++) {
            const eventId = displayText(eventIds[index]);
            const fba = normalizeFba(detailFbas[index]);
            if (
                eventId !== "" &&
                isValidFba(fba) &&
                !activeFbas[fba] &&
                !eventTargets[eventId]
            ) {
                eventTargets[eventId] = {
                    itemKey: fba,
                    reason: "cleanup"
                };
            }
        }
    }

    const eventIds = Object.keys(eventTargets);
    for (let index = 0; index < eventIds.length; index++) {
        const eventId = eventIds[index];
        const target = eventTargets[eventId];
        snapshots.push(
            trackingRowSnapshot(
                detailSheet,
                detailColumns,
                eventId,
                detailRows[eventId] || 0,
                target.itemKey,
                target.reason
            )
        );
    }
    return snapshots;
}

function currentTrackingSnapshot(
    mainSheet,
    mainColumns,
    detailSheet,
    detailColumns,
    target,
    cachedMainRows,
    cachedDetailRows
) {
    const targetType = displayText(target && target.targetType).toLowerCase();
    if (targetType === "cell") {
        if (
            normalizeText(target.sheetName) !== normalizeText(mainSheet.Name)
        ) {
            throw new Error("恢复单元格不属于当前FBA主表");
        }
        const field = displayText(target.field);
        if (MAIN_VALUE_FIELDS.indexOf(field) < 0 || !mainColumns[field]) {
            throw new Error("恢复目标不是物流系统可写字段：" + field);
        }
        const fba = normalizeFba(target.matchValue);
        const mainRows = cachedMainRows || buildRowsByFba(
            mainSheet,
            mainColumns,
            lastUsedRow(mainSheet, mainSheet.Name)
        );
        const rows = mainRows[fba] || [];
        if (rows.length !== 1) {
            throw new Error("FBA在主表中不是唯一一行：" + displayText(target.matchValue));
        }
        return trackingCellSnapshot(
            mainSheet, mainColumns, fba, rows[0], field
        );
    }
    if (targetType === "row") {
        if (
            normalizeText(target.sheetName) !== normalizeText(detailSheet.Name) ||
            displayText(target.field) !== "__row__"
        ) {
            throw new Error("恢复行不属于当前轨迹明细表");
        }
        const eventId = displayText(target.matchValue);
        if (eventId === "") {
            throw new Error("恢复目标缺少事件编号");
        }
        const detailRows = cachedDetailRows || buildDetailRowsByEventId(
            detailSheet,
            detailColumns,
            lastUsedRow(detailSheet, detailSheet.Name)
        );
        return trackingRowSnapshot(
            detailSheet,
            detailColumns,
            eventId,
            detailRows[eventId] || 0,
            displayText(target.itemKey),
            displayText(target.reason)
        );
    }
    throw new Error("不支持的恢复目标类型");
}

function sameComparable(left, right) {
    return JSON.stringify(left) === JSON.stringify(right);
}

function expectedComparable(target, value) {
    return displayText(target.targetType).toLowerCase() === "row"
        ? comparableDetailRow(value)
        : comparableMainValue(displayText(target.field), value);
}

function verifyTrackingPreconditions(
    mainSheet,
    mainColumns,
    detailSheet,
    detailColumns,
    preconditions
) {
    const mainRows = buildRowsByFba(
        mainSheet,
        mainColumns,
        lastUsedRow(mainSheet, mainSheet.Name)
    );
    const detailRows = buildDetailRowsByEventId(
        detailSheet,
        detailColumns,
        lastUsedRow(detailSheet, detailSheet.Name)
    );
    for (let index = 0; index < preconditions.length; index++) {
        const expected = preconditions[index];
        const current = currentTrackingSnapshot(
            mainSheet,
            mainColumns,
            detailSheet,
            detailColumns,
            expected,
            mainRows,
            detailRows
        );
        const comparable = Object.prototype.hasOwnProperty.call(
            expected, "comparableValue"
        ) ? expected.comparableValue : expectedComparable(
            expected, expected.value
        );
        if (!sameComparable(current.comparableValue, comparable)) {
            throw new Error(
                "共享表在写前快照后发生变化：" +
                displayText(expected.itemKey || expected.matchValue) +
                " / " + displayText(expected.header || expected.field) +
                "。本次已停止，未覆盖新值"
            );
        }
    }
}

function inspectTrackingChanges(
    mainSheet,
    mainColumns,
    detailSheet,
    detailColumns,
    changes,
    direction,
    indexOffset
) {
    const result = {
        ready: [],
        alreadyApplied: [],
        conflicts: [],
        failures: []
    };
    const mainRows = buildRowsByFba(
        mainSheet,
        mainColumns,
        lastUsedRow(mainSheet, mainSheet.Name)
    );
    const detailRows = buildDetailRowsByEventId(
        detailSheet,
        detailColumns,
        lastUsedRow(detailSheet, detailSheet.Name)
    );
    for (let index = 0; index < changes.length; index++) {
        const change = changes[index] && typeof changes[index] === "object"
            ? changes[index] : {};
        const globalIndex = indexOffset + index;
        try {
            const current = currentTrackingSnapshot(
                mainSheet,
                mainColumns,
                detailSheet,
                detailColumns,
                change,
                mainRows,
                detailRows
            );
            const expectedValue = direction === "rollback"
                ? change.newValue : change.oldValue;
            const desiredValue = direction === "rollback"
                ? change.oldValue : change.newValue;
            const item = {
                index: globalIndex,
                itemKey: displayText(change.itemKey || change.matchValue),
                targetType: displayText(change.targetType),
                matchValue: displayText(change.matchValue),
                field: displayText(change.field),
                header: displayText(change.header || current.header),
                cellAddress: current.cellAddress,
                currentValue: current.value,
                expectedValue: expectedValue,
                desiredValue: desiredValue
            };
            const expected = expectedComparable(change, expectedValue);
            const desired = expectedComparable(change, desiredValue);
            if (sameComparable(current.comparableValue, expected)) {
                result.ready.push(item);
            } else if (sameComparable(current.comparableValue, desired)) {
                result.alreadyApplied.push(item);
            } else {
                result.conflicts.push(item);
            }
        } catch (error) {
            result.failures.push({
                index: globalIndex,
                itemKey: displayText(change.itemKey || change.matchValue),
                matchValue: displayText(change.matchValue),
                field: displayText(change.field),
                message: displayText(error && error.message ? error.message : error)
            });
        }
    }
    return result;
}

function applyTrackingChange(
    mainSheet,
    mainColumns,
    detailSheet,
    detailColumns,
    change,
    desiredValue
) {
    if (displayText(change.targetType).toLowerCase() === "cell") {
        const current = currentTrackingSnapshot(
            mainSheet,
            mainColumns,
            detailSheet,
            detailColumns,
            change
        );
        const range = mainSheet.Range(current.cellAddress);
        const field = displayText(change.field);
        if (DATE_ONLY_FIELDS[field]) {
            range.NumberFormat = "yyyy-mm-dd";
        } else if (DATE_TIME_FIELDS[field]) {
            range.NumberFormat = "yyyy-mm-dd hh:mm:ss";
        }
        range.Value2 = desiredValue;
        return;
    }
    const eventId = displayText(change.matchValue);
    const detailLastRow = lastUsedRow(detailSheet, detailSheet.Name);
    const detailRows = buildDetailRowsByEventId(
        detailSheet, detailColumns, detailLastRow
    );
    const existingRow = detailRows[eventId] || 0;
    if (desiredValue === null || desiredValue === undefined) {
        if (existingRow) {
            detailSheet.Range(
                detailColumns.event_id.columnLetter + existingRow
            ).EntireRow.Delete();
        }
        return;
    }
    const row = existingRow || Math.max(2, detailLastRow + 1);
    applyDetailFormats(detailSheet, detailColumns, row, row);
    const fields = Object.keys(DETAIL_FIELD_DEFINITIONS);
    for (let index = 0; index < fields.length; index++) {
        const field = fields[index];
        const value = Object.prototype.hasOwnProperty.call(desiredValue, field)
            ? desiredValue[field] : "";
        detailSheet.Range(detailColumns[field].columnLetter + row).Value2 = value;
    }
}

function applyTrackingChanges(
    mainSheet,
    mainColumns,
    detailSheet,
    detailColumns,
    changes,
    direction,
    indexOffset
) {
    const inspected = inspectTrackingChanges(
        mainSheet,
        mainColumns,
        detailSheet,
        detailColumns,
        changes,
        direction,
        indexOffset
    );
    const result = {
        applied: [],
        alreadyApplied: inspected.alreadyApplied,
        conflicts: inspected.conflicts,
        failures: inspected.failures
    };
    for (let readyIndex = 0; readyIndex < inspected.ready.length; readyIndex++) {
        const ready = inspected.ready[readyIndex];
        const localIndex = ready.index - indexOffset;
        const change = changes[localIndex];
        try {
            applyTrackingChange(
                mainSheet,
                mainColumns,
                detailSheet,
                detailColumns,
                change,
                ready.desiredValue
            );
            result.applied.push(ready);
        } catch (error) {
            result.failures.push({
                index: ready.index,
                itemKey: ready.itemKey,
                matchValue: ready.matchValue,
                field: ready.field,
                message: displayText(error && error.message ? error.message : error)
            });
        }
    }
    return result;
}

const argv = Context && Context.argv ? Context.argv : {};
const action = normalizeText(argv.action || "validate").toLowerCase();
const sheetName = normalizeText(argv.sheet_name || DEFAULT_SHEET_NAME);
const detailSheetName = normalizeText(
    argv.detail_sheet_name || DEFAULT_DETAIL_SHEET_NAME
);
const items = Array.isArray(argv.items) ? argv.items : [];

if (
    action !== "discover" &&
    action !== "validate" &&
    action !== "sync" &&
    action !== "sync_tracking" &&
    action !== "list_pending" &&
    action !== "snapshot" &&
    action !== "snapshot_targets" &&
    action !== "inspect_changes" &&
    action !== "apply_changes"
) {
    throw new Error("不支持的操作：" + action);
}

if (action === "discover") {
    const discoveryResult = {
        success: true,
        schemaVersion: SCHEMA_VERSION,
        sheets: workbookSheets()
    };
    console.log(JSON.stringify(discoveryResult));
    return discoveryResult;
}

if (sheetName === "" || detailSheetName === "") {
    throw new Error("未提供FBA主表或轨迹明细表名称");
}

const targetSheet = findTargetSheet(sheetName);
const detailSheet = findTargetSheet(detailSheetName);
const columns = findColumnsByDefinitions(
    targetSheet,
    MAIN_FIELD_DEFINITIONS,
    sheetName
);
const detailColumns = findColumnsByDefinitions(
    detailSheet,
    DETAIL_FIELD_DEFINITIONS,
    detailSheetName
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

if (action === "snapshot") {
    if (items.length > MAX_ITEMS) {
        throw new Error("单次最多快照 " + MAX_ITEMS + " 个FBA");
    }
    const snapshotResult = Object.assign(baseResult, {
        snapshots: collectTrackingSnapshots(
            targetSheet,
            columns,
            detailSheet,
            detailColumns,
            items,
            argv.include_cleanup === true
        )
    });
    console.log(JSON.stringify(snapshotResult));
    return snapshotResult;
}

if (action === "snapshot_targets") {
    const targets = Array.isArray(argv.targets) ? argv.targets : [];
    const mainRows = buildRowsByFba(
        targetSheet,
        columns,
        lastUsedRow(targetSheet, targetSheet.Name)
    );
    const detailRows = buildDetailRowsByEventId(
        detailSheet,
        detailColumns,
        lastUsedRow(detailSheet, detailSheet.Name)
    );
    const snapshots = targets.map(function (target) {
        return currentTrackingSnapshot(
            targetSheet,
            columns,
            detailSheet,
            detailColumns,
            target,
            mainRows,
            detailRows
        );
    });
    const targetResult = Object.assign(baseResult, { snapshots: snapshots });
    console.log(JSON.stringify(targetResult));
    return targetResult;
}

if (action === "inspect_changes" || action === "apply_changes") {
    const changes = Array.isArray(argv.changes) ? argv.changes : [];
    const direction = displayText(argv.direction).toLowerCase();
    if (direction !== "rollback" && direction !== "forward") {
        throw new Error("变更方向无效");
    }
    const indexOffset = Math.max(0, Number(argv.index_offset) || 0);
    const changeResult = action === "inspect_changes"
        ? inspectTrackingChanges(
            targetSheet,
            columns,
            detailSheet,
            detailColumns,
            changes,
            direction,
            indexOffset
        )
        : applyTrackingChanges(
            targetSheet,
            columns,
            detailSheet,
            detailColumns,
            changes,
            direction,
            indexOffset
        );
    const response = Object.assign(baseResult, changeResult);
    console.log(JSON.stringify(response));
    return response;
}

const lastRow = lastUsedRow(targetSheet, sheetName);

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
    const pending = [];
    const seen = Object.create(null);
    for (let index = 0; index < allFbaValues.length; index++) {
        const fba = normalizeFba(allFbaValues[index]);
        const completed = normalizeText(completionValues[index]).toLowerCase();
        // 一键更新严格只查询“是否完成”为空的记录；任何非空状态都完全跳过。
        if (!isValidFba(fba) || completed !== "") {
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

verifyTrackingPreconditions(
    targetSheet,
    columns,
    detailSheet,
    detailColumns,
    Array.isArray(argv.preconditions) ? argv.preconditions : []
);

const updated = [];
const auditOnly = [];
const unchanged = [];
const notInSheet = [];
const duplicateRows = [];
const failures = [];
const conflicts = [];
const updatedCells = [];
const formatFailures = [];

if (items.length === 0) {
    let detailRowsRemoved = 0;
    if (action === "sync_tracking") {
        detailRowsRemoved = removeInactiveDetailRows(
            targetSheet,
            columns,
            lastRow,
            detailSheet,
            detailColumns
        );
    }
    return Object.assign(baseResult, {
        updated: updated,
        auditOnly: auditOnly,
        unchanged: unchanged,
        notInSheet: notInSheet,
        duplicateRows: duplicateRows,
        failures: failures,
        conflicts: conflicts,
        updatedCells: updatedCells,
        formatFailures: formatFailures,
        eventsAdded: 0,
        eventsUpdated: 0,
        eventsUnchanged: 0,
        detailRowsRemoved: detailRowsRemoved
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
        failed: false,
        businessWritten: false,
        auditWritten: false
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
            value: incoming,
            field: field,
            address: column.columnLetter + row,
            header: column.text,
            oldValue: comparableCurrent,
            newValue: comparableIncoming,
            business: BUSINESS_HIGHLIGHT_FIELDS.indexOf(field) >= 0
        });
    }

    acceptedItems.push({
        fba: fba,
        row: row,
        events: Array.isArray(item.events) ? item.events : []
    });
}

// 只清理物流标准字段中由本脚本留下的旧主题高亮；其他业务列和手工填充不动。
clearPreviousSystemHighlights(
    targetSheet,
    columns,
    acceptedItems,
    formatFailures
);

const writeFields = Object.keys(writesByColumn);
for (let fieldIndex = 0; fieldIndex < writeFields.length; fieldIndex++) {
    const field = writeFields[fieldIndex];
    writeGroupsForColumn(
        targetSheet,
        columns[field].columnLetter,
        writesByColumn[field],
        states,
        updatedCells,
        formatFailures
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
    if (state.businessWritten) {
        updated.push(fba);
    } else if (state.auditWritten && !state.failed) {
        auditOnly.push(fba);
    } else if (!state.failed) {
        unchanged.push(fba);
    }
}

updatedCells.sort(function (left, right) {
    if (left.row !== right.row) {
        return left.row - right.row;
    }
    return columns[left.field].columnNumber - columns[right.field].columnNumber;
});

let eventSummary = { added: 0, updated: 0, unchanged: 0 };
let detailRowsRemoved = 0;
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
    try {
        detailRowsRemoved = removeInactiveDetailRows(
            targetSheet,
            columns,
            lastRow,
            detailSheet,
            detailColumns
        );
    } catch (error) {
        throw new Error(
            "主表和本次轨迹已处理，但清理非活跃FBA明细失败；" +
            "原有活跃轨迹不会被清空，可重新执行安全补写。原因：" +
            error.message
        );
    }
}

const result = Object.assign(baseResult, {
    updated: updated,
    auditOnly: auditOnly,
    unchanged: unchanged,
    notInSheet: notInSheet,
    duplicateRows: duplicateRows,
    failures: failures,
    conflicts: conflicts,
    updatedCells: updatedCells,
    formatFailures: formatFailures,
    eventsAdded: eventSummary.added,
    eventsUpdated: eventSummary.updated,
    eventsUnchanged: eventSummary.unchanged,
    detailRowsRemoved: detailRowsRemoved
});

console.log(JSON.stringify(result));
return result;
