const DEFAULT_SHEET_NAME = "US-FBA";
const HEADER_END_COLUMN = "CV";
const MAX_SCAN_ROW = 20000;
const MAX_ITEMS = 50;

const FBA_HEADER_NAMES = ["FBA单号", "FBA号", "FBA编号", "FBA"];
const ROUTE_HEADER_NAMES = ["货代最新路由信息", "最新路由信息", "路由"];
const COMPLETION_HEADER_NAMES = ["是否完成"];

function normalizeText(value) {
    return String(value === null || value === undefined ? "" : value)
        .replace(/\s+/g, "")
        .trim();
}

function normalizeFba(value) {
    return normalizeText(value).toUpperCase();
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

function findColumns(sheet) {
    const values = firstRowValues(sheet.Range("A1:" + HEADER_END_COLUMN + "1").Value2);
    const headers = [];
    for (let index = 0; index < values.length; index++) {
        const text = normalizeText(values[index]);
        if (text !== "") {
            headers.push({
                columnNumber: index + 1,
                columnLetter: columnNumberToName(index + 1),
                text: text
            });
        }
    }

    const fbaMatches = headers.filter(function (header) {
        return FBA_HEADER_NAMES.indexOf(header.text) >= 0;
    });
    const routeMatches = headers.filter(function (header) {
        return ROUTE_HEADER_NAMES.indexOf(header.text) >= 0;
    });
    const completionMatches = headers.filter(function (header) {
        return COMPLETION_HEADER_NAMES.indexOf(header.text) >= 0;
    });

    if (fbaMatches.length !== 1) {
        throw new Error(
            fbaMatches.length === 0
                ? "第一行没有找到FBA表头"
                : "第一行存在多个FBA表头，已停止写入"
        );
    }
    if (routeMatches.length !== 1) {
        throw new Error(
            routeMatches.length === 0
                ? "第一行没有找到货代最新路由信息表头"
                : "第一行存在多个路由信息表头，已停止写入"
        );
    }
    if (completionMatches.length !== 1) {
        throw new Error(
            completionMatches.length === 0
                ? "第一行没有找到是否完成表头"
                : "第一行存在多个是否完成表头，已停止读取"
        );
    }
    if (fbaMatches[0].columnNumber === routeMatches[0].columnNumber) {
        throw new Error("FBA列和路由信息列不能是同一列");
    }

    return {
        fba: fbaMatches[0],
        route: routeMatches[0],
        completion: completionMatches[0]
    };
}

const argv = Context && Context.argv ? Context.argv : {};
const action = normalizeText(argv.action || "validate").toLowerCase();
const sheetName = normalizeText(argv.sheet_name || DEFAULT_SHEET_NAME);
const items = Array.isArray(argv.items) ? argv.items : [];

if (sheetName !== DEFAULT_SHEET_NAME) {
    throw new Error("当前脚本只允许处理 " + DEFAULT_SHEET_NAME);
}
// 单文档单脚本分发：未来增加ETA、实际接收日期等功能时，在这里增加
// 新action及对应处理函数，不需要为每一列另建脚本。
if (
    action !== "validate" && action !== "sync" &&
    action !== "sync_tracking" && action !== "list_pending"
) {
    throw new Error("不支持的操作：" + action);
}

const targetSheet = findTargetSheet(sheetName);
const columns = findColumns(targetSheet);
const baseResult = {
    success: true,
    sheetName: targetSheet.Name,
    columns: {
        fba: columns.fba.columnLetter,
        route: columns.route.columnLetter,
        completion: columns.completion.columnLetter
    },
    headers: {
        fba: columns.fba.text,
        route: columns.route.text,
        completion: columns.completion.text
    }
};

if (action === "validate") {
    console.log(JSON.stringify(baseResult));
    return baseResult;
}

const usedRange = targetSheet.UsedRange;
const lastRow = usedRange.Row + usedRange.Rows.Count - 1;
if (lastRow > MAX_SCAN_ROW) {
    throw new Error(
        "US-FBA使用区域达到第" + lastRow +
        "行，超过安全上限" + MAX_SCAN_ROW +
        "。请清理表格底部多余格式后重试"
    );
}

if (action === "list_pending") {
    const offset = Math.max(0, Number(argv.offset) || 0);
    const limit = Math.max(1, Math.min(500, Number(argv.limit) || 500));
    if (lastRow < 2) {
        return Object.assign(baseResult, {
            fbas: [], total: 0, offset: offset,
            nextOffset: offset, hasMore: false
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
    const pending = [];
    const seen = Object.create(null);
    for (let index = 0; index < allFbaValues.length; index++) {
        const fba = normalizeFba(allFbaValues[index]);
        const completed = normalizeText(completionValues[index]).toLowerCase();
        if (!/^FBA[A-Z0-9-]{5,}$/.test(fba) || completed === "是") {
            continue;
        }
        if (!seen[fba]) {
            seen[fba] = true;
            pending.push(fba);
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

if (lastRow < 2 || items.length === 0) {
    return Object.assign(baseResult, {
        updated: updated,
        unchanged: unchanged,
        notInSheet: items.map(function (item) { return normalizeFba(item.fba); }),
        duplicateRows: duplicateRows,
        failures: failures
    });
}

// 两次批量读取整列业务数据，避免逐格读取造成脚本长时间运行。
const fbaValues = singleColumnValues(
    targetSheet.Range(
        columns.fba.columnLetter + "2:" + columns.fba.columnLetter + lastRow
    ).Value2
);
const routeValues = singleColumnValues(
    targetSheet.Range(
        columns.route.columnLetter + "2:" + columns.route.columnLetter + lastRow
    ).Value2
);

const rowsByFba = Object.create(null);
for (let index = 0; index < fbaValues.length; index++) {
    const fba = normalizeFba(fbaValues[index]);
    if (fba === "") {
        continue;
    }
    if (!rowsByFba[fba]) {
        rowsByFba[fba] = [];
    }
    rowsByFba[fba].push(index + 2);
}

const seenInput = Object.create(null);
for (let index = 0; index < items.length; index++) {
    const fba = normalizeFba(items[index] && items[index].fba);
    const route = String(
        items[index] && items[index].route !== undefined ? items[index].route : ""
    ).trim();

    if (!/^FBA[A-Z0-9-]{5,}$/.test(fba) || route === "") {
        failures.push(fba || "第" + (index + 1) + "项");
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

    const row = matches[0];
    const currentRoute = String(routeValues[row - 2] || "").trim();
    if (currentRoute === route) {
        unchanged.push(fba);
        continue;
    }

    try {
        targetSheet.Range(columns.route.columnLetter + row).Value2 = route;
        updated.push(fba);
    } catch (error) {
        failures.push(fba);
    }
}

const result = Object.assign(baseResult, {
    updated: updated,
    unchanged: unchanged,
    notInSheet: notInSheet,
    duplicateRows: duplicateRows,
    failures: failures
});

console.log(JSON.stringify(result));
return result;
