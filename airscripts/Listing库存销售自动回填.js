const SCHEMA_VERSION = 2;
const HEADER_END_COLUMN = "CZ";
const MAX_HEADER_ROW = 12;
const MAX_SCAN_ROW = 20000;
const MAX_ITEMS = 50;

// Listing脚本与物流脚本完全独立。所有列都按表头名称识别，顺序可以任意，
// 也允许保留自定义列；同一标准字段出现两次时会停止，避免写错位置。
const FIELD_DEFINITIONS = {
    msku: ["MSKU", "SKU"],
    product_name: ["品名", "产品名"],
    asin: ["ASIN"],
    listing_status: ["Listing状态"],
    price: ["价格"],
    current_data_date: ["本次数据日期"],
    previous_data_date: ["上次数据日期"],
    rating: ["评分"],
    review_count: ["评论数"],
    previous_rating: ["上次评分"],
    previous_review_count: ["上次评论数"],
    yesterday_ad_spend: ["昨日广告费"],
    previous_yesterday_ad_spend: ["上次昨日广告费"],
    fba_available: ["FBA可售", "FBA库存"],
    reserved: ["预留"],
    inbound: ["在途"],
    previous_fba_available: ["上次FBA可售", "上次FBA库存"],
    previous_reserved: ["上次预留"],
    previous_inbound: ["上次在途"],
    sales_7d: ["7日销量"],
    sales_14d: ["14日销量"],
    sales_30d: ["30日销量"],
    previous_sales_7d: ["上次7日销量"],
    previous_sales_14d: ["上次14日销量"],
    previous_sales_30d: ["上次30日销量"],
    average_sales_7d: ["7日均销"],
    average_sales_14d: ["14日均销"],
    average_sales_30d: ["30日均销"],
    converted_monthly_sales_7d: ["7日折算月销"],
    monthly_difference: ["月销差异"],
    monthly_difference_rate: ["月销差异率"],
    sales_trend: ["销量趋势"],
    system_monthly_sales: ["系统建议月销"],
    final_monthly_sales: ["最终补货月销"],
    stock_coverage_months: ["在库覆盖月数"],
    total_coverage_months: ["含在途覆盖月数", "在途覆盖月数"],
    suggested_replenishment: ["建议补货量"],
    link_status: ["链接情况"],
    inventory_status: ["库存情况"],
    ad_status: ["广告情况"],
    operation_notes: ["运营备注"],
    updated_at: ["本次更新时间"]
};

const ROLL_FIELDS = [
    { incoming: "rating", current: "rating", previous: "previous_rating" },
    {
        incoming: "review_count",
        current: "review_count",
        previous: "previous_review_count"
    },
    {
        incoming: "yesterday_ad_spend",
        current: "yesterday_ad_spend",
        previous: "previous_yesterday_ad_spend"
    },
    {
        incoming: "fba_available",
        current: "fba_available",
        previous: "previous_fba_available"
    },
    { incoming: "reserved", current: "reserved", previous: "previous_reserved" },
    { incoming: "inbound", current: "inbound", previous: "previous_inbound" },
    { incoming: "sales_7d", current: "sales_7d", previous: "previous_sales_7d" },
    {
        incoming: "sales_14d",
        current: "sales_14d",
        previous: "previous_sales_14d"
    },
    {
        incoming: "sales_30d",
        current: "sales_30d",
        previous: "previous_sales_30d"
    }
];

function displayText(value) {
    return String(value === null || value === undefined ? "" : value).trim();
}

function normalizeHeader(value) {
    return displayText(value).replace(/\s+/g, "");
}

function normalizeMsku(value) {
    return displayText(value).toUpperCase();
}

function pad(value) {
    const text = String(value);
    return text.length < 2 ? "0" + text : text;
}

function currentTimeText() {
    const now = new Date();
    return (
        now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" +
        pad(now.getDate()) + " " + pad(now.getHours()) + ":" +
        pad(now.getMinutes()) + ":" + pad(now.getSeconds())
    );
}

function excelDateText(value) {
    if (typeof value !== "number" || !isFinite(value)) {
        return "";
    }
    const date = new Date(Math.round((value - 25569) * 86400000));
    if (isNaN(date.getTime())) {
        return "";
    }
    return (
        date.getUTCFullYear() + "-" + pad(date.getUTCMonth() + 1) + "-" +
        pad(date.getUTCDate())
    );
}

function normalizedDate(value) {
    const serial = excelDateText(value);
    if (serial !== "") {
        return serial;
    }
    const text = displayText(value);
    const match = text.match(/^(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?$/);
    if (!match) {
        return text;
    }
    return match[1] + "-" + pad(match[2]) + "-" + pad(match[3]);
}

function isIsoDate(value) {
    return /^20\d{2}-\d{2}-\d{2}$/.test(value);
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

function findTargetSheet(sheetName) {
    const sheets = Application.Sheets;
    const matches = [];
    for (let index = 1; index <= sheets.Count; index++) {
        const sheet = sheets.Item(index);
        if (normalizeHeader(sheet.Name) === normalizeHeader(sheetName)) {
            matches.push(sheet);
        }
    }
    if (matches.length === 0) {
        throw new Error("没有找到Listing子表：" + sheetName);
    }
    if (matches.length > 1) {
        throw new Error("存在多个同名Listing子表：" + sheetName);
    }
    return matches[0];
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

function headersAtRow(sheet, rowNumber) {
    const values = firstRowValues(
        sheet.Range(
            "A" + rowNumber + ":" + HEADER_END_COLUMN + rowNumber
        ).Value2
    );
    const result = [];
    for (let index = 0; index < values.length; index++) {
        const original = displayText(values[index]);
        if (original !== "") {
            result.push({
                columnNumber: index + 1,
                columnLetter: columnNumberToName(index + 1),
                text: original,
                normalized: normalizeHeader(original)
            });
        }
    }
    return result;
}

function locateHeaders(sheet) {
    const keys = Object.keys(FIELD_DEFINITIONS);
    let best = { count: -1, row: 1, missing: [], duplicates: [] };
    for (let rowNumber = 1; rowNumber <= MAX_HEADER_ROW; rowNumber++) {
        const headers = headersAtRow(sheet, rowNumber);
        const columns = {};
        const missing = [];
        const duplicates = [];
        for (let keyIndex = 0; keyIndex < keys.length; keyIndex++) {
            const key = keys[keyIndex];
            const aliases = FIELD_DEFINITIONS[key].map(normalizeHeader);
            const matches = headers.filter(function (header) {
                return aliases.indexOf(header.normalized) >= 0;
            });
            if (matches.length === 0) {
                missing.push(FIELD_DEFINITIONS[key][0]);
            } else if (matches.length > 1) {
                duplicates.push(
                    FIELD_DEFINITIONS[key][0] + "（" +
                    matches.map(function (item) { return item.text; }).join("、") +
                    "）"
                );
            } else {
                columns[key] = matches[0];
            }
        }
        const count = keys.length - missing.length;
        if (count > best.count) {
            best = {
                count: count,
                row: rowNumber,
                missing: missing,
                duplicates: duplicates
            };
        }
        if (missing.length === 0 && duplicates.length === 0) {
            return { row: rowNumber, columns: columns };
        }
    }
    if (best.duplicates.length > 0) {
        throw new Error(
            "Listing表第" + best.row + "行存在重复含义的表头：" +
            best.duplicates.join("、")
        );
    }
    throw new Error(
        "前" + MAX_HEADER_ROW + "行未找到完整Listing表头；最接近的第" +
        best.row + "行仍缺少：" + best.missing.join("、")
    );
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

function lastUsedRow(sheet, headerRow) {
    const usedRange = sheet.UsedRange;
    const lastRow = Math.max(
        headerRow,
        usedRange.Row + usedRange.Rows.Count - 1
    );
    if (lastRow > MAX_SCAN_ROW) {
        throw new Error(
            "Listing表使用区域达到第" + lastRow + "行，超过安全上限" +
            MAX_SCAN_ROW + "。请清理底部多余格式"
        );
    }
    return lastRow;
}

function buildRowsByMsku(sheet, columns, headerRow, lastRow) {
    const result = Object.create(null);
    if (lastRow <= headerRow) {
        return result;
    }
    const values = singleColumnValues(
        sheet.Range(
            columns.msku.columnLetter + (headerRow + 1) + ":" +
            columns.msku.columnLetter + lastRow
        ).Value2
    );
    for (let index = 0; index < values.length; index++) {
        const msku = normalizeMsku(values[index]);
        if (msku === "") {
            continue;
        }
        if (!result[msku]) {
            result[msku] = [];
        }
        result[msku].push(headerRow + index + 1);
    }
    return result;
}

function readFieldValues(sheet, columns, fields, headerRow, lastRow) {
    const result = Object.create(null);
    for (let index = 0; index < fields.length; index++) {
        const field = fields[index];
        result[field] = lastRow <= headerRow ? [] : singleColumnValues(
            sheet.Range(
                columns[field].columnLetter + (headerRow + 1) + ":" +
                columns[field].columnLetter + lastRow
            ).Value2
        );
    }
    return result;
}

function hasOwn(source, key) {
    return Object.prototype.hasOwnProperty.call(source, key);
}

function pushWrite(writesByField, field, row, value, msku) {
    if (!writesByField[field]) {
        writesByField[field] = [];
    }
    writesByField[field].push({ row: row, value: value, msku: msku });
}

function writeFields(sheet, columns, writesByField, states) {
    const fields = Object.keys(writesByField);
    for (let fieldIndex = 0; fieldIndex < fields.length; fieldIndex++) {
        const field = fields[fieldIndex];
        const writes = writesByField[field].sort(function (left, right) {
            return left.row - right.row;
        });
        const groups = [];
        for (let index = 0; index < writes.length; index++) {
            const group = groups[groups.length - 1];
            if (group && writes[index].row === group[group.length - 1].row + 1) {
                group.push(writes[index]);
            } else {
                groups.push([writes[index]]);
            }
        }
        for (let groupIndex = 0; groupIndex < groups.length; groupIndex++) {
            const group = groups[groupIndex];
            const start = group[0].row;
            const end = group[group.length - 1].row;
            const range = sheet.Range(
                columns[field].columnLetter + start + ":" +
                columns[field].columnLetter + end
            );
            if (
                field === "current_data_date" ||
                field === "previous_data_date" ||
                field === "updated_at"
            ) {
                range.NumberFormat = "@";
            }
            try {
                range.Value2 = group.length === 1
                    ? group[0].value
                    : group.map(function (item) { return [item.value]; });
                for (let itemIndex = 0; itemIndex < group.length; itemIndex++) {
                    states[group[itemIndex].msku].written = true;
                }
            } catch (error) {
                for (let itemIndex = 0; itemIndex < group.length; itemIndex++) {
                    const item = group[itemIndex];
                    try {
                        sheet.Range(
                            columns[field].columnLetter + item.row
                        ).Value2 = item.value;
                        states[item.msku].written = true;
                    } catch (singleError) {
                        states[item.msku].failed = true;
                    }
                }
            }
        }
    }
}

const argv = Context && Context.argv ? Context.argv : {};
const action = normalizeHeader(argv.action || "validate").toLowerCase();
const sheetName = displayText(argv.sheet_name);
const dataDate = normalizedDate(argv.data_date);
const items = Array.isArray(argv.items) ? argv.items : [];

if (action !== "discover" && action !== "validate" && action !== "sync") {
    throw new Error("不支持的Listing操作：" + action);
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
if (sheetName === "") {
    throw new Error("未提供Listing子表名称");
}
if (items.length > MAX_ITEMS) {
    throw new Error("单次最多处理" + MAX_ITEMS + "个Listing");
}
if (action === "sync" && !isIsoDate(dataDate)) {
    throw new Error("数据日期无效，应为YYYY-MM-DD");
}

const targetSheet = findTargetSheet(sheetName);
const located = locateHeaders(targetSheet);
const headerRow = located.row;
const columns = located.columns;
const baseResult = {
    success: true,
    schemaVersion: SCHEMA_VERSION,
    sheetName: targetSheet.Name,
    headerRow: headerRow,
    columns: columnLetters(columns),
    headers: columnHeaders(columns)
};

if (action === "validate") {
    console.log(JSON.stringify(baseResult));
    return baseResult;
}

const updated = [];
const sameDateUpdated = [];
const stale = [];
const notInSheet = [];
const duplicateRows = [];
const conflicts = [];
const failures = [];
const lastRow = lastUsedRow(targetSheet, headerRow);
const rowsByMsku = buildRowsByMsku(targetSheet, columns, headerRow, lastRow);
const readFields = [
    "asin",
    "current_data_date",
    "rating",
    "review_count",
    "yesterday_ad_spend",
    "fba_available",
    "reserved",
    "inbound",
    "sales_7d",
    "sales_14d",
    "sales_30d"
];
const existing = readFieldValues(
    targetSheet, columns, readFields, headerRow, lastRow
);
const seenInput = Object.create(null);
const writesByField = Object.create(null);
const states = Object.create(null);
const updateTime = currentTimeText();

for (let index = 0; index < items.length; index++) {
    const source = items[index] && typeof items[index] === "object"
        ? items[index] : {};
    const msku = normalizeMsku(source.msku);
    if (msku === "" || seenInput[msku]) {
        if (msku !== "") {
            duplicateRows.push(displayText(source.msku));
        }
        continue;
    }
    seenInput[msku] = true;
    const rows = rowsByMsku[msku] || [];
    if (rows.length === 0) {
        notInSheet.push(displayText(source.msku));
        continue;
    }
    if (rows.length > 1) {
        duplicateRows.push(displayText(source.msku));
        continue;
    }
    const row = rows[0];
    const valueIndex = row - headerRow - 1;
    const targetAsin = displayText(existing.asin[valueIndex]).toUpperCase();
    const sourceAsin = displayText(source.asin).toUpperCase();
    if (targetAsin !== "" && sourceAsin !== "" && targetAsin !== sourceAsin) {
        conflicts.push(
            displayText(source.msku) + "（表内" + targetAsin +
            "，上传" + sourceAsin + "）"
        );
        continue;
    }
    const currentDate = normalizedDate(
        existing.current_data_date[valueIndex]
    );
    if (currentDate !== "" && !isIsoDate(currentDate)) {
        failures.push(
            displayText(source.msku) + "（表内本次数据日期格式无效）"
        );
        continue;
    }
    if (currentDate !== "" && dataDate < currentDate) {
        stale.push(displayText(source.msku));
        continue;
    }
    const mode = currentDate === dataDate ? "same" : "new";
    states[msku] = {
        label: displayText(source.msku),
        mode: mode,
        written: false,
        failed: false
    };
    if (mode === "new") {
        if (currentDate !== "") {
            pushWrite(
                writesByField,
                "previous_data_date",
                row,
                currentDate,
                msku
            );
        }
        pushWrite(
            writesByField, "current_data_date", row, dataDate, msku
        );
    }
    for (let fieldIndex = 0; fieldIndex < ROLL_FIELDS.length; fieldIndex++) {
        const definition = ROLL_FIELDS[fieldIndex];
        if (!hasOwn(source, definition.incoming)) {
            continue;
        }
        if (mode === "new" && currentDate !== "") {
            pushWrite(
                writesByField,
                definition.previous,
                row,
                existing[definition.current][valueIndex],
                msku
            );
        }
        pushWrite(
            writesByField,
            definition.current,
            row,
            source[definition.incoming],
            msku
        );
    }
    if (hasOwn(source, "system_monthly_sales")) {
        // “系统建议月销”由共享表公式显示；每次导入都重置人工可改的最终值。
        pushWrite(
            writesByField,
            "final_monthly_sales",
            row,
            source.system_monthly_sales,
            msku
        );
    }
    pushWrite(writesByField, "updated_at", row, updateTime, msku);
}

writeFields(targetSheet, columns, writesByField, states);
const stateKeys = Object.keys(states);
for (let index = 0; index < stateKeys.length; index++) {
    const state = states[stateKeys[index]];
    if (state.failed || !state.written) {
        failures.push(state.label);
    } else if (state.mode === "same") {
        sameDateUpdated.push(state.label);
    } else {
        updated.push(state.label);
    }
}

const result = Object.assign(baseResult, {
    updated: updated,
    sameDateUpdated: sameDateUpdated,
    stale: stale,
    notInSheet: notInSheet,
    duplicateRows: duplicateRows,
    conflicts: conflicts,
    failures: failures
});
console.log(JSON.stringify(result));
return result;
