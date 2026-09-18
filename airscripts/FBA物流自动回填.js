const SCHEMA_VERSION = 14;
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
    fba: ["FBA号", "FBA单号", "FBA编号", "FBA"],
    carrier: ["货代", "物流商", "货代公司"],
    event_time: ["轨迹发生时间"],
    phase: ["标准阶段"],
    node: ["标准节点"],
    event_type: ["轨迹类型", "信息属性"],
    content: ["物流轨迹原文"],
    related_plan: ["涉及计划"],
    validity: ["有效状态"],
    exception_status: ["异常状态"],
    transport_info: ["运输信息"],
    updated_at: ["系统更新时间"]
};

const DETAIL_IDENTITY_FIELD_DEFINITIONS = {
    fba: DETAIL_FIELD_DEFINITIONS.fba,
    carrier: DETAIL_FIELD_DEFINITIONS.carrier,
    event_time: DETAIL_FIELD_DEFINITIONS.event_time,
    content: DETAIL_FIELD_DEFINITIONS.content
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
    "completion",
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
    signed_time: true,
    last_mile_time: true
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
    fba: true
};

const DETAIL_DATE_TIME_FIELDS = {
    event_time: true,
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

function returnResult(result) {
    console.log(JSON.stringify(result));
    return result;
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

// 明细表不再保存内部事件编号。可见且稳定的四个业务字段共同定位一条轨迹；
// 阶段、节点和状态会随解析规则更新，不能作为事件身份的一部分。
function detailEventKey(value) {
    const source = value && typeof value === "object" ? value : {};
    const fba = normalizeFba(source.fba);
    const content = displayText(source.content).replace(/\r\n?/g, "\n");
    if (!isValidFba(fba) || content === "") {
        return "";
    }
    return JSON.stringify([
        fba,
        displayText(source.carrier),
        comparableDetailValue("event_time", source.event_time),
        content
    ]);
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

function findOptionalColumnsByDefinitions(sheet, definitions, tableLabel) {
    const headers = readHeaders(sheet);
    const result = {};
    Object.keys(definitions).forEach(function (field) {
        const aliases = definitions[field].map(normalizeText);
        const matches = headers.filter(function (header) {
            return aliases.indexOf(header.normalized) >= 0;
        });
        if (matches.length > 1) {
            throw new Error(
                tableLabel + "存在重复含义的表头：" + definitions[field][0] +
                "。为防止写错列，已停止处理"
            );
        }
        result[field] = matches.length === 1 ? matches[0] : null;
    });
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
            normalizeText(completionValues[index]) === "" &&
            displayText(scalarValue(sheet.Range(columns.signed_time.columnLetter + (index + 2)))) === ""
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
    const existingEvents = buildDetailRowsByEventKey(
        detailSheet, detailColumns, detailLastRow
    );
    const mutableFields = [
        "phase",
        "node",
        "event_type",
        "related_plan",
        "validity",
        "exception_status",
        "transport_info"
    ];
    const mutableValues = Object.create(null);
    if (detailLastRow >= 2) {
        applyDetailFormats(detailSheet, detailColumns, 2, detailLastRow);
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
            const eventFba = normalizeFba(event.fba || item.fba);
            const content = displayText(event.content);
            const normalized = Object.assign({}, event, {
                fba: eventFba,
                content: content
            });
            const eventKey = detailEventKey(normalized);
            if (eventKey === "") {
                continue;
            }
            const existing = existingEvents[eventKey];
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
                    if (!writesByField.updated_at) writesByField.updated_at = [];
                    writesByField.updated_at.push({row: existing.row, value: displayText(event.updated_at)});
                    updatedCount++;
                } else {
                    unchangedCount++;
                }
                continue;
            }
            // 本批后续出现相同事件时，也会落入 existingEvents 分支。
            existingEvents[eventKey] = {
                row: detailLastRow + newEvents.length + 1,
                index: -1,
                isNew: true
            };
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
    // 只写12个标准明细列。
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

function buildDetailRowsByEventKey(sheet, columns, detailLastRow) {
    const result = Object.create(null);
    if (detailLastRow < 2) {
        return result;
    }
    const identityFields = ["fba", "carrier", "event_time", "content"];
    const values = {};
    identityFields.forEach(function (field) {
        values[field] = singleColumnValues(
            sheet.Range(
                columns[field].columnLetter + "2:" +
                columns[field].columnLetter + detailLastRow
            ).Value2
        );
    });
    for (let index = 0; index <= detailLastRow - 2; index++) {
        const rowValue = {};
        identityFields.forEach(function (field) {
            rowValue[field] = values[field][index];
        });
        const eventKey = detailEventKey(rowValue);
        if (eventKey === "") {
            continue;
        }
        if (result[eventKey]) {
            throw new Error(
                "轨迹明细存在重复事件：" + normalizeFba(rowValue.fba) +
                " / " + comparableDetailValue("event_time", rowValue.event_time) +
                "，已停止处理"
            );
        }
        result[eventKey] = {
            row: index + 2,
            index: index,
            isNew: false,
            fba: normalizeFba(rowValue.fba)
        };
    }
    return result;
}

function duplicateDetailRows(sheet, columns, detailLastRow) {
    if (detailLastRow < 2) {
        return [];
    }
    const identityFields = Object.keys(DETAIL_IDENTITY_FIELD_DEFINITIONS);
    const values = {};
    identityFields.forEach(function (field) {
        values[field] = columns[field]
            ? singleColumnValues(
                sheet.Range(
                    columns[field].columnLetter + "2:" +
                    columns[field].columnLetter + detailLastRow
                ).Value2
            )
            : Array(detailLastRow - 1).fill("");
    });
    const firstRows = Object.create(null);
    const duplicates = [];
    for (let index = 0; index <= detailLastRow - 2; index++) {
        const rowValue = {};
        identityFields.forEach(function (field) {
            rowValue[field] = values[field][index];
        });
        const eventKey = detailEventKey(rowValue);
        if (eventKey === "") {
            continue;
        }
        const row = index + 2;
        if (firstRows[eventKey]) {
            if (duplicates.indexOf(firstRows[eventKey]) < 0) {
                duplicates.push(firstRows[eventKey]);
            }
            duplicates.push(row);
        } else {
            firstRows[eventKey] = row;
        }
    }
    return duplicates.sort(function (left, right) { return left - right; });
}

function detailTargetKey(target) {
    const values = [
        target && target.value,
        target && target.oldValue,
        target && target.newValue
    ];
    for (let index = 0; index < values.length; index++) {
        const key = detailEventKey(values[index]);
        if (key !== "") {
            return key;
        }
    }
    return displayText(target && target.matchValue);
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
    eventKey,
    row,
    itemKey,
    reason
) {
    const value = row ? detailRowValue(detailSheet, detailColumns, row) : null;
    return {
        targetType: "row",
        sheetName: displayText(detailSheet.Name),
        matchHeader: "FBA号+货代+轨迹发生时间+物流轨迹原文",
        matchValue: eventKey,
        itemKey: itemKey,
        reason: reason || "event",
        field: "__row__",
        header: "轨迹事件行",
        cellAddress: row ? detailColumns.fba.columnLetter + row : "",
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
    const detailRows = buildDetailRowsByEventKey(
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
            const event = events[eventIndex] && typeof events[eventIndex] === "object"
                ? Object.assign({}, events[eventIndex]) : {};
            event.fba = event.fba || fba;
            const eventKey = detailEventKey(event);
            if (eventKey !== "" && !eventTargets[eventKey]) {
                eventTargets[eventKey] = {
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
        const detailKeys = Object.keys(detailRows);
        for (let index = 0; index < detailKeys.length; index++) {
            const eventKey = detailKeys[index];
            const fba = detailRows[eventKey].fba;
            if (
                isValidFba(fba) &&
                !activeFbas[fba] &&
                !eventTargets[eventKey]
            ) {
                eventTargets[eventKey] = {
                    itemKey: fba,
                    reason: "cleanup"
                };
            }
        }
    }

    const eventKeys = Object.keys(eventTargets);
    for (let index = 0; index < eventKeys.length; index++) {
        const eventKey = eventKeys[index];
        const target = eventTargets[eventKey];
        snapshots.push(
            trackingRowSnapshot(
                detailSheet,
                detailColumns,
                eventKey,
                detailRows[eventKey] ? detailRows[eventKey].row : 0,
                target.itemKey,
                target.reason
            )
        );
    }
    // A complete detail image preserves custom values and ordering during cleanup.
    const guarded = includeCleanup ? snapshots.filter(function (item) { return item.targetType === "cell"; }) : snapshots;
    if (includeCleanup) {
        Object.keys(rowsByFba).forEach(function (fba) {
            const rows = rowsByFba[fba];
            if (rows.length !== 1 || seenFbas[fba]) return;
            if (displayText(scalarValue(mainSheet.Range(mainColumns.signed_time.columnLetter + rows[0]))) === "") return;
            ["signed_time", "completion"].forEach(function (field) {
                const snapshot = trackingCellSnapshot(mainSheet, mainColumns, fba, rows[0], field);
                snapshot.reason = "cleanup";
                guarded.push(snapshot);
            });
        });
        guarded.push(inputSnapshot(mainSheet, mainColumns), tableSnapshot(detailSheet));
    }
    return guarded;
}

function tableImage(sheet) {
    try {
        if (Boolean(sheet.ProtectContents)) {
            throw new Error("轨迹明细已保护，请先取消工作表保护再整理");
        }
    } catch (error) {
        if (displayText(error && error.message).indexOf("轨迹明细已保护") >= 0) {
            throw error;
        }
        // 部分WPS运行环境不提供ProtectContents，继续使用后续只读安全检查。
    }
    const lastRow = lastUsedRow(sheet, sheet.Name);
    const used = sheet.UsedRange;
    const width = used && used.Columns ? Number(used.Column || 1) + Number(used.Columns.Count) - 1 : 100;
    if (!width || width > 100 || lastRow * width > 100000) {
        throw new Error("轨迹明细超出安全整理范围（100列、10万个单元格），已停止");
    }
    const range = sheet.Range("A1:" + columnNumberToName(width) + Math.max(1, lastRow));
    if (range.MergeCells === true || range.MergeCells === null) {
        throw new Error("轨迹明细含合并单元格，请先取消合并再整理");
    }
    const values = range.Value2;
    const rows = Array.isArray(values) ? values.map(function (row) {
        return Array.isArray(row) ? row.slice() : [row];
    }) : [[values]];
    // Formula-bearing detail sheets cannot be reordered without changing references.
    const formulas = range.Formula;
    if (Array.isArray(formulas) && formulas.some(function (row) {
        return (Array.isArray(row) ? row : [row]).some(function (cell) {
            return typeof cell === "string" && cell.charAt(0) === "=";
        });
    })) throw new Error("轨迹明细含公式，不能安全移动整行；请将公式移至其他子表");
    let right = 1;
    rows.forEach(function (row) {
        row.forEach(function (value, index) { if (value !== null && value !== undefined && value !== "") right = Math.max(right, index + 1); });
    });
    const trimmed = rows.map(function (row) { return row.slice(0, right).map(function (value) { return value == null ? "" : value; }); });
    while (trimmed.length > 1 && trimmed[trimmed.length - 1].every(function (v) { return v === ""; })) trimmed.pop();
    const formats = trimmed.map(function (row, index) {
        return row.map(function (_value, column) {
            return sheet.Range(columnNumberToName(column + 1) + (index + 1)).NumberFormat || "General";
        });
    });
    return { headers: trimmed[0], rows: trimmed.slice(1), formats: formats.slice(1) };
}

function tableGuard(sheet) {
    try {
        if (Boolean(sheet.ProtectContents)) {
            throw new Error("轨迹明细已保护，请先取消工作表保护再整理");
        }
    } catch (error) {
        if (displayText(error && error.message).indexOf("轨迹明细已保护") >= 0) {
            throw error;
        }
    }
    const lastRow = lastUsedRow(sheet, sheet.Name);
    const used = sheet.UsedRange;
    const width = used && used.Columns
        ? Number(used.Column || 1) + Number(used.Columns.Count) - 1 : 100;
    if (!width || width > 100 || lastRow * width > 100000) {
        throw new Error("轨迹明细超出安全整理范围（100列、10万个单元格），已停止");
    }
    const range = sheet.Range(
        "A1:" + columnNumberToName(width) + Math.max(1, lastRow)
    );
    if (range.MergeCells === true || range.MergeCells === null) {
        throw new Error("轨迹明细含合并单元格，请先取消合并再整理");
    }
    const formulas = range.Formula;
    if (Array.isArray(formulas) && formulas.some(function (row) {
        return (Array.isArray(row) ? row : [row]).some(function (cell) {
            return typeof cell === "string" && cell.charAt(0) === "=";
        });
    })) {
        throw new Error("轨迹明细含公式，不能安全删除列；请将公式移至其他子表");
    }
    const rawValues = range.Value2;
    const rows = Array.isArray(rawValues) ? rawValues.map(function (row) {
        return Array.isArray(row) ? row.slice() : [row];
    }) : [[rawValues]];
    let right = 0;
    rows.forEach(function (row) {
        row.forEach(function (value, index) {
            if (value !== null && value !== undefined && value !== "") {
                right = Math.max(right, index + 1);
            }
        });
    });
    const trimmed = rows.map(function (row) {
        return row.slice(0, right).map(function (value) {
            return value === null || value === undefined ? "" : value;
        });
    });
    while (
        trimmed.length > 1 &&
        trimmed[trimmed.length - 1].every(function (value) { return value === ""; })
    ) {
        trimmed.pop();
    }
    let hash1 = 104729;
    let hash2 = 130363;
    let characterCount = 0;
    function add(value) {
        const text = JSON.stringify(value === undefined ? null : value);
        const framed = text.length + ":" + text + ";";
        characterCount += framed.length;
        for (let index = 0; index < framed.length; index++) {
            const code = framed.charCodeAt(index);
            hash1 = (hash1 * 131 + code) % 2147483647;
            hash2 = (hash2 * 257 + code) % 2147483629;
        }
    }
    add("headers");
    (trimmed[0] || []).forEach(add);
    add("rows");
    trimmed.slice(1).forEach(function (row) {
        add("row");
        row.forEach(add);
    });
    const value = {
        columns: right,
        rows: Math.max(0, trimmed.length - 1),
        characters: characterCount,
        hash1: hash1,
        hash2: hash2
    };
    return {
        targetType: "tracking_table_guard",
        sheetName: sheet.Name,
        matchHeader: "轨迹明细校验",
        matchValue: "__detail_guard__",
        itemKey: "__detail_guard__",
        field: "__table_guard__",
        value: value,
        comparableValue: value
    };
}

function detailFieldsForHeaders(headers) {
    const result = [];
    const seen = Object.create(null);
    for (let index = 0; index < headers.length; index++) {
        const normalized = normalizeText(headers[index]);
        let matched = "";
        Object.keys(DETAIL_FIELD_DEFINITIONS).forEach(function (field) {
            if (
                DETAIL_FIELD_DEFINITIONS[field].map(normalizeText)
                    .indexOf(normalized) >= 0
            ) {
                matched = field;
            }
        });
        if (matched === "" || seen[matched]) {
            throw new Error("轨迹明细表头不能按12个必要字段安全恢复");
        }
        seen[matched] = true;
        result.push(matched);
    }
    if (
        result.length !== Object.keys(DETAIL_FIELD_DEFINITIONS).length ||
        Object.keys(DETAIL_FIELD_DEFINITIONS).some(function (field) {
            return !seen[field];
        })
    ) {
        throw new Error("轨迹明细表头不能按12个必要字段安全恢复");
    }
    return result;
}

function projectDetailImage(value, fieldOrder) {
    const source = value && typeof value === "object" ? value : {};
    const headers = Array.isArray(source.headers) ? source.headers : [];
    const sourceColumns = Object.create(null);
    for (let index = 0; index < headers.length; index++) {
        const normalized = normalizeText(headers[index]);
        Object.keys(DETAIL_FIELD_DEFINITIONS).forEach(function (field) {
            if (
                DETAIL_FIELD_DEFINITIONS[field].map(normalizeText)
                    .indexOf(normalized) >= 0
            ) {
                if (Object.prototype.hasOwnProperty.call(sourceColumns, field)) {
                    throw new Error("旧轨迹快照包含重复必要表头，不能安全恢复");
                }
                sourceColumns[field] = index;
            }
        });
    }
    if (fieldOrder.some(function (field) {
        return !Object.prototype.hasOwnProperty.call(sourceColumns, field);
    })) {
        throw new Error("旧轨迹快照缺少必要表头，不能安全恢复");
    }
    const rows = Array.isArray(source.rows) ? source.rows : [];
    const formats = Array.isArray(source.formats) ? source.formats : [];
    return {
        headers: fieldOrder.map(function (field) {
            return DETAIL_FIELD_DEFINITIONS[field][0];
        }),
        rows: rows.map(function (row) {
            const values = Array.isArray(row) ? row : [];
            return fieldOrder.map(function (field) {
                const value = values[sourceColumns[field]];
                return value === null || value === undefined ? "" : value;
            });
        }),
        formats: rows.map(function (_row, rowIndex) {
            const rowFormats = Array.isArray(formats[rowIndex])
                ? formats[rowIndex] : [];
            return fieldOrder.map(function (field) {
                return rowFormats[sourceColumns[field]] || "General";
            });
        })
    };
}

function assertUniqueDetailImage(value, fieldOrder) {
    const indexes = Object.create(null);
    fieldOrder.forEach(function (field, index) {
        indexes[field] = index;
    });
    const seen = Object.create(null);
    const duplicateRows = [];
    const rows = value && Array.isArray(value.rows) ? value.rows : [];
    rows.forEach(function (row, index) {
        const values = Array.isArray(row) ? row : [];
        const eventKey = detailEventKey({
            fba: values[indexes.fba],
            carrier: values[indexes.carrier],
            event_time: values[indexes.event_time],
            content: values[indexes.content]
        });
        if (eventKey === "") return;
        const rowNumber = index + 2;
        if (seen[eventKey]) {
            if (duplicateRows.indexOf(seen[eventKey]) < 0) {
                duplicateRows.push(seen[eventKey]);
            }
            duplicateRows.push(rowNumber);
        } else {
            seen[eventKey] = rowNumber;
        }
    });
    if (duplicateRows.length > 0) {
        throw new Error(
            "旧轨迹快照投影后存在无法区分的重复轨迹（行号：" +
            duplicateRows.slice(0, 10).join("、") +
            (duplicateRows.length > 10 ? " 等" : "") +
            "），不能安全恢复"
        );
    }
}

function detailImageForCurrent(current, value) {
    let fieldOrder = null;
    let headerError = null;
    try {
        fieldOrder = detailFieldsForHeaders(current.headers);
    } catch (error) {
        headerError = error;
    }
    if (fieldOrder) {
        const desired = projectDetailImage(value, fieldOrder);
        assertUniqueDetailImage(desired, fieldOrder);
        return desired;
    }
    if (sameComparable(current.headers, value.headers)) {
        const retainedFields = Object.keys(DETAIL_FIELD_DEFINITIONS);
        const projected = projectDetailImage(value, retainedFields);
        assertUniqueDetailImage(projected, retainedFields);
        return value;
    }
    throw headerError || new Error("轨迹明细表头不能安全恢复");
}

function comparableTableImage(value) {
    return projectDetailImage(value, Object.keys(DETAIL_FIELD_DEFINITIONS));
}

function tableSnapshot(sheet) {
    const value = tableImage(sheet);
    return { targetType: "tracking_table", sheetName: sheet.Name, matchHeader: "轨迹明细表", matchValue: "__detail__", itemKey: "__detail__", field: "__table__", value: value, comparableValue: value };
}

function inputSnapshot(sheet, columns) {
    const rows = [];
    const last = lastUsedRow(sheet, sheet.Name);
    for (let row = 2; row <= last; row++) {
        const fba = normalizeFba(scalarValue(sheet.Range(columns.fba.columnLetter + row)));
        if (fba) rows.push([fba, displayText(scalarValue(sheet.Range(columns.carrier.columnLetter + row))), normalizeText(scalarValue(sheet.Range(columns.completion.columnLetter + row))), comparableMainValue("signed_time", scalarValue(sheet.Range(columns.signed_time.columnLetter + row)))]);
    }
    return { targetType: "tracking_inputs", sheetName: sheet.Name, matchHeader: columns.fba.text, matchValue: "__inputs__", itemKey: "__inputs__", field: "__inputs__", value: rows, comparableValue: rows.map(function (row) { return row.slice(0, 2); }) };
}

function writeTableImage(sheet, value) {
    const current = tableImage(sheet);
    const desired = detailImageForCurrent(current, value);
    const width = current.headers.length;
    const count = Math.max(current.rows.length, desired.rows.length);
    if (count) {
        const rows = desired.rows.slice();
        while (rows.length < count) rows.push(Array(width).fill(""));
        sheet.Range("A2:" + columnNumberToName(width) + (count + 1)).Value2 = rows;
        desired.formats.forEach(function (row, index) {
            row.forEach(function (format, column) { sheet.Range(columnNumberToName(column + 1) + (index + 2)).NumberFormat = format; });
        });
    }
}

function organizeDetails(mainSheet, mainColumns, detailSheet, detailColumns) {
    const image = tableImage(detailSheet);
    const inputs = inputSnapshot(mainSheet, mainColumns).value;
    const ranks = Object.create(null);
    inputs.forEach(function (row, index) { if (row[2] === "" && row[3] === "" && !(row[0] in ranks)) ranks[row[0]] = index; });
    const fbaColumn = detailColumns.fba.columnNumber - 1;
    const timeColumn = detailColumns.event_time.columnNumber - 1;
    const records = image.rows.map(function (row, index) { return { row: row, format: image.formats[index], index: index }; });
    const kept = records.filter(function (item) {
        const fba = normalizeFba(item.row[fbaColumn]);
        return !isValidFba(fba) || fba in ranks;
    });
    const active = kept.filter(function (item) { return isValidFba(normalizeFba(item.row[fbaColumn])); });
    active.sort(function (a, b) {
        return ranks[normalizeFba(a.row[fbaColumn])] - ranks[normalizeFba(b.row[fbaColumn])] ||
            comparableDetailValue("event_time", a.row[timeColumn]).localeCompare(comparableDetailValue("event_time", b.row[timeColumn])) || a.index - b.index;
    });
    let index = 0;
    const ordered = kept.map(function (item) { return isValidFba(normalizeFba(item.row[fbaColumn])) ? active[index++] : item; });
    const next = { headers: image.headers, rows: ordered.map(function (item) { return item.row; }), formats: ordered.map(function (item) { return item.format; }) };
    if (!sameComparable(image, next)) writeTableImage(detailSheet, next);
    return records.length - kept.length;
}

function completeSignedRows(sheet, columns, updated, updatedCells) {
    const rows = buildRowsByFba(sheet, columns, lastUsedRow(sheet, sheet.Name));
    Object.keys(rows).forEach(function (fba) {
        // 重复FBA不能安全按业务键恢复，保持不写。
        if (rows[fba].length !== 1) return;
        const row = rows[fba][0];
        if (displayText(scalarValue(sheet.Range(columns.signed_time.columnLetter + row))) === "") return;
        const address = columns.completion.columnLetter + row;
        const previous = displayText(scalarValue(sheet.Range(address)));
        if (previous === "是") return;
        sheet.Range(address).Value2 = "是";
        pushUnique(updated, fba);
        updatedCells.push({ fba: fba, row: row, address: address, field: "completion", header: columns.completion.text, oldValue: previous, newValue: "是" });
    });
}

function headerSnapshot(sheet) {
    const values = firstRowValues(sheet.Range("A1:" + HEADER_END_COLUMN + "1").Value2).map(function (v) { return v == null ? "" : v; });
    const used = sheet.UsedRange;
    const usedRight = used && used.Columns ? Number(used.Column || 1) + Number(used.Columns.Count) - 1 : 0;
    return { targetType: "tracking_headers", sheetName: sheet.Name, matchHeader: "__headers__", matchValue: "__headers__", itemKey: "__headers__", field: "__headers__", value: values, comparableValue: values, usedRight: usedRight };
}

function headerPlan(sheet, definitions, exactColumns, exactRight) {
    const values = headerSnapshot(sheet).value;
    const additions = [];
    const removals = [];
    const renames = [];
    let right = 0;
    values.forEach(function (v, i) { if (displayText(v)) right = i + 1; });
    const used = sheet.UsedRange;
    if (used && used.Columns) {
        right = Math.max(
            right,
            Number(used.Column || 1) + Number(used.Columns.Count) - 1
        );
    }
    if (exactColumns) {
        right = Number(exactRight || 0);
    }
    const matchedColumns = Object.create(null);
    Object.keys(definitions).forEach(function (field) {
        const aliases = definitions[field].map(normalizeText);
        const found = [];
        for (let index = 0; index < right; index++) {
            if (aliases.indexOf(normalizeText(values[index])) >= 0) {
                found.push(index + 1);
            }
        }
        if (found.length > 1) {
            throw new Error(sheet.Name + "表头重复：" + definitions[field][0]);
        }
        if (found.length === 1) {
            matchedColumns[found[0]] = field;
            const currentHeader = displayText(values[found[0] - 1]);
            if (exactColumns && currentHeader !== definitions[field][0]) {
                renames.push({
                    field: field,
                    from: currentHeader,
                    header: definitions[field][0],
                    column: found[0]
                });
            }
        }
    });
    if (exactColumns) {
        for (let column = 1; column <= right; column++) {
            if (!matchedColumns[column]) {
                removals.push({
                    header: displayText(values[column - 1]) || "（无表头）",
                    column: column
                });
            }
        }
    }
    let nextColumn = exactColumns ? right - removals.length : right;
    Object.keys(definitions).forEach(function (field) {
        if (Object.keys(matchedColumns).some(function (column) {
            return matchedColumns[column] === field;
        })) {
            return;
        }
        nextColumn++;
        if (nextColumn > 100) {
            throw new Error("缺少表头无法在100列安全范围内追加");
        }
        additions.push({
            field: field,
            header: definitions[field][0],
            column: nextColumn
        });
    });
    return {
        sheetName: sheet.Name,
        additions: additions,
        removals: removals,
        renames: renames
    };
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
    if (targetType === "tracking_headers") {
        if (target.sheetName === mainSheet.Name) return headerSnapshot(mainSheet);
        if (target.sheetName === detailSheet.Name) return headerSnapshot(detailSheet);
        throw new Error("表头快照不属于当前站点");
    }
    if (targetType === "tracking_inputs" && target.sheetName === mainSheet.Name) return inputSnapshot(mainSheet, mainColumns);
    if (targetType === "tracking_table_guard" && target.sheetName === detailSheet.Name) return tableGuard(detailSheet);
    if (targetType === "tracking_table" && target.sheetName === detailSheet.Name) return tableSnapshot(detailSheet);
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
        const eventKey = detailTargetKey(target);
        if (eventKey === "") {
            throw new Error("恢复目标缺少轨迹业务键");
        }
        const detailRows = cachedDetailRows || buildDetailRowsByEventKey(
            detailSheet,
            detailColumns,
            lastUsedRow(detailSheet, detailSheet.Name)
        );
        const detailRow = detailRows[eventKey];
        return trackingRowSnapshot(
            detailSheet,
            detailColumns,
            eventKey,
            detailRow ? detailRow.row : 0,
            displayText(target.itemKey),
            displayText(target.reason)
        );
    }
    throw new Error("不支持的恢复目标类型");
}

function sameComparable(left, right) {
    function canonical(value) {
        if (Array.isArray(value)) return value.map(canonical);
        if (value && typeof value === "object") {
            const result = {};
            Object.keys(value).sort().forEach(function (key) { result[key] = canonical(value[key]); });
            return result;
        }
        return value;
    }
    return JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
}

function sameTableImage(left, right) {
    const leftHeaders = left && Array.isArray(left.headers) ? left.headers : [];
    const rightHeaders = right && Array.isArray(right.headers) ? right.headers : [];
    if (sameComparable(leftHeaders, rightHeaders)) {
        return sameComparable(left, right);
    }
    try {
        return sameComparable(
            comparableTableImage(left),
            comparableTableImage(right)
        );
    } catch (error) {
        return false;
    }
}

function expectedComparable(target, value) {
    if (displayText(target.targetType).indexOf("tracking_") === 0) return value;
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
    const detailRows = buildDetailRowsByEventKey(
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
        const changed = expected.targetType === "tracking_inputs"
            ? !sameComparable(current.value, expected.value)
            : expected.targetType === "tracking_table"
                ? !sameTableImage(current.value, expected.value)
                : !sameComparable(current.comparableValue, comparable);
        if (changed) {
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
    const detailRows = buildDetailRowsByEventKey(
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
            const matchesExpected = change.targetType === "tracking_table"
                ? sameTableImage(current.value, expectedValue)
                : sameComparable(current.comparableValue, expected);
            const matchesDesired = change.targetType === "tracking_table"
                ? sameTableImage(current.value, desiredValue)
                : sameComparable(current.comparableValue, desired);
            if (matchesExpected) {
                if (change.targetType === "tracking_table") {
                    detailImageForCurrent(current.value, desiredValue);
                }
                result.ready.push(item);
            } else if (matchesDesired) {
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
    if (displayText(change.targetType) === "tracking_table" && change.sheetName === detailSheet.Name) {
        writeTableImage(detailSheet, desiredValue);
        return;
    }
    if (displayText(change.targetType).indexOf("tracking_") === 0) throw new Error("输入及表头只用于核对，不通过数据恢复修改");
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
    const eventKey = detailTargetKey(change);
    const detailLastRow = lastUsedRow(detailSheet, detailSheet.Name);
    const detailRows = buildDetailRowsByEventKey(
        detailSheet, detailColumns, detailLastRow
    );
    const existing = detailRows[eventKey];
    const existingRow = existing ? existing.row : 0;
    if (desiredValue === null || desiredValue === undefined) {
        if (existingRow) {
            detailSheet.Range(
                detailColumns.fba.columnLetter + existingRow
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
    action !== "apply_changes" &&
    action !== "headers_preview" && action !== "headers_apply" && action !== "organize"
) {
    throw new Error("不支持的操作：" + action);
}

if (action === "discover") {
    const discoveryResult = {
        success: true,
        schemaVersion: SCHEMA_VERSION,
        sheets: workbookSheets()
    };
    return returnResult(discoveryResult);
}

if (sheetName === "" || detailSheetName === "") {
    throw new Error("未提供FBA主表或轨迹明细表名称");
}

const targetSheet = findTargetSheet(sheetName);
const detailSheet = findTargetSheet(detailSheetName);
if (action === "headers_preview" || action === "headers_apply") {
    const definitions = Object.assign({ shop: ["店铺"] }, MAIN_FIELD_DEFINITIONS, { note: ["备注"] });
    const detailGuard = tableGuard(detailSheet);
    const plans = [
        headerPlan(targetSheet, definitions, false),
        headerPlan(
            detailSheet,
            DETAIL_FIELD_DEFINITIONS,
            true,
            detailGuard.value.columns
        )
    ];
    const missingIdentityHeaders = plans[1].additions.filter(function (item) {
        return Object.prototype.hasOwnProperty.call(
            DETAIL_IDENTITY_FIELD_DEFINITIONS,
            item.field
        );
    });
    if (detailGuard.value.rows > 0 && missingIdentityHeaders.length > 0) {
        throw new Error(
            "轨迹明细已有历史数据但缺少用于轨迹定位的表头：" +
            missingIdentityHeaders.map(function (item) { return item.header; })
                .join("、") +
            "。请先在WPS人工补齐并核对；系统未修改表格"
        );
    }
    const duplicateRows = duplicateDetailRows(
        detailSheet,
        findOptionalColumnsByDefinitions(
            detailSheet,
            DETAIL_IDENTITY_FIELD_DEFINITIONS,
            detailSheetName
        ),
        lastUsedRow(detailSheet, detailSheetName)
    );
    if (duplicateRows.length > 0) {
        throw new Error(
            "轨迹明细有 " + duplicateRows.length +
            " 行在删除事件编号后无法区分（行号：" +
            duplicateRows.slice().sort(function (left, right) { return left - right; })
                .slice(0, 10).join("、") +
            (duplicateRows.length > 10 ? " 等" : "") +
            "）。请先在WPS人工核对这些重复轨迹；系统未修改表格"
        );
    }
    // 表头整理会物理删除明细列；轻量双摘要防止预览后的数据变化被误删。
    const snapshots = [
        headerSnapshot(targetSheet),
        headerSnapshot(detailSheet),
        detailGuard
    ];
    if (action === "headers_apply") {
        if (!sameComparable(snapshots, argv.preconditions)) {
            throw new Error("物流表在预览后已变化，请重新预览后再整理");
        }
        plans[1].removals.slice().sort(function (left, right) {
            return right.column - left.column;
        }).forEach(function (item) {
            detailSheet.Range(
                columnNumberToName(item.column) + "1"
            ).EntireColumn.Delete();
        });
        plans[0].additions.forEach(function (item) {
            targetSheet.Range(columnNumberToName(item.column) + "1").Value2 = item.header;
        });
        plans[1].additions.forEach(function (item) {
            detailSheet.Range(columnNumberToName(item.column) + "1").Value2 = item.header;
        });
        const mainCols = findColumnsByDefinitions(targetSheet, MAIN_FIELD_DEFINITIONS, sheetName);
        const detailCols = findColumnsByDefinitions(detailSheet, DETAIL_FIELD_DEFINITIONS, detailSheetName);
        Object.keys(DETAIL_FIELD_DEFINITIONS).forEach(function (field) {
            detailSheet.Range(detailCols[field].columnLetter + "1").Value2 =
                DETAIL_FIELD_DEFINITIONS[field][0];
        });
        const last = lastUsedRow(targetSheet, sheetName);
        if (last > 1) applyMainFormats(targetSheet, mainCols, [{ start: 2, end: last }]);
        const detailLast = lastUsedRow(detailSheet, detailSheetName);
        if (detailLast > 1) applyDetailFormats(detailSheet, detailCols, 2, detailLast);
        targetSheet.Range(mainCols.route.columnLetter + "1:" + mainCols.route.columnLetter + Math.max(2, last)).WrapText = true;
        detailSheet.Range(detailCols.content.columnLetter + "1:" + detailCols.content.columnLetter + Math.max(2, detailLast)).WrapText = true;
    }
    return returnResult({ success: true, schemaVersion: SCHEMA_VERSION, plans: plans, snapshots: snapshots });
}
if (action === "snapshot_targets" && Array.isArray(argv.targets) && argv.targets.every(function (item) { return item.targetType === "tracking_headers"; })) {
    return returnResult({ success: true, schemaVersion: SCHEMA_VERSION, snapshots: argv.targets.map(function (item) {
        if (item.sheetName === targetSheet.Name) return headerSnapshot(targetSheet);
        if (item.sheetName === detailSheet.Name) return headerSnapshot(detailSheet);
        throw new Error("表头快照不属于当前站点");
    }) });
}
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
    return returnResult(baseResult);
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
    return returnResult(snapshotResult);
}

if (action === "snapshot_targets") {
    const targets = Array.isArray(argv.targets) ? argv.targets : [];
    const mainRows = buildRowsByFba(
        targetSheet,
        columns,
        lastUsedRow(targetSheet, targetSheet.Name)
    );
    const detailRows = buildDetailRowsByEventKey(
        detailSheet,
        detailColumns,
        lastUsedRow(detailSheet, detailSheet.Name)
    );
    const snapshots = targets.map(function (target) {
        const current = currentTrackingSnapshot(
            targetSheet,
            columns,
            detailSheet,
            detailColumns,
            target,
            mainRows,
            detailRows
        );
        if (target.targetType === "tracking_inputs" && !sameComparable(current.comparableValue, target.comparableValue)) throw new Error("主表FBA顺序或货代已变化，请核对本次操作");
        return current;
    });
    const targetResult = Object.assign(baseResult, { snapshots: snapshots });
    return returnResult(targetResult);
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
    return returnResult(response);
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
            hasMore: false,
            inputGuard: inputSnapshot(targetSheet, columns)
        });
    }
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
    let completionPending = false;
    const seen = Object.create(null);
    for (let index = 0; index < allFbaValues.length; index++) {
        const fba = normalizeFba(allFbaValues[index]);
        const completed = normalizeText(completionValues[index]).toLowerCase();
        if (isValidFba(fba) && displayText(scalarValue(targetSheet.Range(columns.signed_time.columnLetter + (index + 2)))) !== "" && completed !== "是") completionPending = true;
        // 一键更新严格只查询“是否完成”为空的记录；任何非空状态都完全跳过。
        if (!isValidFba(fba) || completed !== "" || displayText(scalarValue(targetSheet.Range(columns.signed_time.columnLetter + (index + 2)))) !== "") {
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
        hasMore: nextOffset < pending.length,
        inputGuard: inputSnapshot(targetSheet, columns),
        completionPending: completionPending
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

// 调用方已持久化签收/完成单元格快照，并在上方校验全部主表输入。
completeSignedRows(targetSheet, columns, updated, updatedCells);

if (items.length === 0) {
    let detailRowsRemoved = 0;
    if (action === "sync_tracking" || action === "organize") {
        detailRowsRemoved = organizeDetails(
            targetSheet,
            columns,
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
        detailRowsRemoved: detailRowsRemoved,
        inputGuard: inputSnapshot(targetSheet, columns),
        detailGuard: tableSnapshot(detailSheet)
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
    if ((action === "sync_tracking" || action === "sync") && displayText(main.route) === "") {
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
        // 完成状态在签收单元格实际写入成功后生成，不能使用请求中的状态。
        if (field === "completion") continue;
        const incoming = displayText(main[field]);
        const column = columns[field];
        const currentRaw = (
            rowValues[column.columnNumber - bounds.minimum]
        );
        const current = displayText(currentRaw);
        const comparableIncoming = comparableMainValue(field, incoming);
        const comparableCurrent = comparableMainValue(field, currentRaw);

        const planNames = { estimated_departure: "预计出发", estimated_arrival: "预计到达", estimated_delivery: "预计送达" };
        const cancelled = displayText(main.cancelled_plans).split("|").indexOf(planNames[field]) >= 0 && !!planNames[field];
        if (incoming === "" && !CLEARABLE_FIELDS[field] && !cancelled) {
            continue;
        }
        if (comparableCurrent === comparableIncoming) {
            continue;
        }
        if (field === "signed_time" && current !== "") continue;
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
completeSignedRows(targetSheet, columns, updated, updatedCells);
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
        pushUnique(updated, fba);
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
        if (argv.defer_organize !== true) detailRowsRemoved = organizeDetails(
            targetSheet,
            columns,
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
    detailRowsRemoved: detailRowsRemoved,
    detailGuard: tableSnapshot(detailSheet),
    inputGuard: inputSnapshot(targetSheet, columns)
});

return returnResult(result);
