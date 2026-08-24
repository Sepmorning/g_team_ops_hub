const SCHEMA_VERSION = 10;
const HEADER_END_COLUMN = "CZ";
const MAX_HEADER_ROW = 12;
const MAX_SCAN_ROW = 20000;
const MAX_ITEMS = 50;
// WPS会把中文工作表名的跨表公式引用保存成无法识别的名称，导致#NAME?。
// 标签使用ASCII，工作表内部标题仍保持中文；setup_rules会迁移版本5的旧标签。
const RULE_SHEET_NAME = "ListingRules";
const LEGACY_RULE_SHEET_NAME = "规则配置";

// Listing脚本与物流脚本完全独立。所有列都按表头名称识别，顺序可以任意，
// 也允许保留自定义列；同一标准字段出现两次时会停止，避免写错位置。
const FIELD_DEFINITIONS = {
    msku: ["MSKU", "SKU"],
    product_name: ["品名", "产品名"],
    asin: ["ASIN"],
    replenishment_status: ["补货状态"],
    price: ["价格"],
    current_data_date: ["本次数据日期"],
    previous_data_date: ["上次数据日期"],
    rating_review: ["评分/评论数"],
    previous_rating_review: ["上次评分/评论数"],
    yesterday_ad_spend: ["昨日广告费"],
    previous_yesterday_ad_spend: ["上次昨日广告费"],
    ad_spend_7d: ["7日广告费"],
    ad_spend_14d: ["14日广告费"],
    ad_spend_30d: ["30日广告费"],
    ad_rate_30d: ["30日广告费率"],
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
    revenue_7d: ["7日销售额"],
    revenue_14d: ["14日销售额"],
    revenue_30d: ["30日销售额"],
    average_price_30d: ["30日实际成交均价"],
    trend_difference_rate: ["趋势差异率"],
    sales_status: ["销量状态"],
    forecast_confidence: ["预测可信度"],
    exception_reason: ["异常原因"],
    system_monthly_sales: ["系统建议月销"],
    final_monthly_sales: ["最终补货月销"],
    stock_coverage_days: ["在库覆盖天数"],
    total_coverage_days: ["含在途覆盖天数"],
    suggested_replenishment: ["建议补货量"],
    link_status: ["链接状态"],
    inventory_status: ["库存状态"],
    ad_status: ["广告状态"],
    updated_at: ["本次更新时间"]
};

// 可选字段不参与完整表头校验；出现时仍要求名称唯一。
const OPTIONAL_FIELD_DEFINITIONS = {
    discount_price: ["优惠价"],
    operation_notes: ["运营备注"]
};

const ROLL_FIELDS = [
    {
        incoming: "rating_review",
        current: "rating_review",
        previous: "previous_rating_review"
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

// 这些字段每次按上传文件的当前值直接更新，不参与“本次/上次”滚动。
const DIRECT_FIELDS = [
    "price",
    "ad_spend_7d",
    "ad_spend_14d",
    "ad_spend_30d",
    "revenue_7d",
    "revenue_14d",
    "revenue_30d"
];

const LISTING_WRITABLE_FIELDS = [
    "current_data_date",
    "previous_data_date",
    "price",
    "rating_review",
    "previous_rating_review",
    "yesterday_ad_spend",
    "previous_yesterday_ad_spend",
    "ad_spend_7d",
    "ad_spend_14d",
    "ad_spend_30d",
    "fba_available",
    "reserved",
    "inbound",
    "previous_fba_available",
    "previous_reserved",
    "previous_inbound",
    "sales_7d",
    "sales_14d",
    "sales_30d",
    "previous_sales_7d",
    "previous_sales_14d",
    "previous_sales_30d",
    "revenue_7d",
    "revenue_14d",
    "revenue_30d",
    "discount_price",
    "updated_at",
    "ad_rate_30d",
    "average_price_30d",
    "trend_difference_rate",
    "sales_status",
    "forecast_confidence",
    "exception_reason",
    "system_monthly_sales",
    "final_monthly_sales",
    "stock_coverage_days",
    "total_coverage_days",
    "suggested_replenishment",
    "inventory_status",
    "ad_status"
];

const FORMULA_FIELDS = [
    "ad_rate_30d",
    "average_price_30d",
    "trend_difference_rate",
    "sales_status",
    "forecast_confidence",
    "exception_reason",
    "system_monthly_sales",
    "final_monthly_sales",
    "stock_coverage_days",
    "total_coverage_days",
    "suggested_replenishment",
    "inventory_status",
    "ad_status"
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
    const optionalKeys = Object.keys(OPTIONAL_FIELD_DEFINITIONS);
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
        for (let keyIndex = 0; keyIndex < optionalKeys.length; keyIndex++) {
            const key = optionalKeys[keyIndex];
            const aliases = OPTIONAL_FIELD_DEFINITIONS[key].map(normalizeHeader);
            const matches = headers.filter(function (header) {
                return aliases.indexOf(header.normalized) >= 0;
            });
            if (matches.length > 1) {
                duplicates.push(
                    OPTIONAL_FIELD_DEFINITIONS[key][0] + "（" +
                    matches.map(function (item) { return item.text; }).join("、") +
                    "）"
                );
            } else if (matches.length === 1) {
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
                field === "rating_review" ||
                field === "previous_rating_review" ||
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

function scalarValue(range) {
    const values = firstRowValues(range.Value2);
    return values.length > 0 ? values[0] : "";
}

function scalarFormula(range) {
    try {
        const values = firstRowValues(range.Formula);
        const formula = values.length > 0 ? displayText(values[0]) : "";
        // Excel/WPS 对静态单元格读取 Formula 时可能返回常量本身。
        // 只有以“=”开头的内容才是真正可恢复的公式。
        return formula.charAt(0) === "=" ? formula : "";
    } catch (error) {
        return "";
    }
}

function findSheetByName(name) {
    const target = normalizeHeader(name);
    const sheets = Application.Sheets;
    for (let index = 1; index <= sheets.Count; index++) {
        const sheet = sheets.Item(index);
        if (normalizeHeader(sheet.Name) === target) {
            return sheet;
        }
    }
    return null;
}

function ensureRuleSheet() {
    const existing = findSheetByName(RULE_SHEET_NAME);
    if (existing) {
        return existing;
    }
    const legacy = findSheetByName(LEGACY_RULE_SHEET_NAME);
    if (legacy) {
        try {
            legacy.Name = RULE_SHEET_NAME;
            return legacy;
        } catch (error) {
            throw new Error(
                "无法把旧“" + LEGACY_RULE_SHEET_NAME + "”工作表迁移为“" +
                RULE_SHEET_NAME + "”，请确认工作簿结构未被保护后重试"
            );
        }
    }
    let created = null;
    try {
        created = Application.Sheets.Add();
    } catch (error) {
        throw new Error(
            "缺少“" + RULE_SHEET_NAME + "”工作表，且AirScript无法自动新建。" +
            "请手工新建同名工作表后重试"
        );
    }
    if (!created && Application.ActiveSheet) {
        created = Application.ActiveSheet;
    }
    if (!created) {
        throw new Error("无法创建“" + RULE_SHEET_NAME + "”工作表");
    }
    created.Name = RULE_SHEET_NAME;
    return created;
}

function writeRuleValue(sheet, address, value) {
    sheet.Range(address).Value2 = value;
}

function ruleSheetProtectionState(sheet) {
    const properties = ["ProtectContents", "ProtectDrawingObjects", "ProtectScenarios"];
    let known = false;
    let protectedState = false;
    try {
        for (let index = 0; index < properties.length; index++) {
            const value = sheet[properties[index]];
            if (typeof value !== "undefined" && value !== null) {
                known = true;
                protectedState = protectedState || Boolean(value);
            }
        }
    } catch (error) {
        return null;
    }
    return known ? protectedState : null;
}

function unprotectRuleSheet(sheet) {
    // 部分WPS环境在未受保护的工作表上调用Unprotect()会抛错，
    // 因此先读取保护状态；旧版升级时规则表通常正处于未保护状态。
    if (ruleSheetProtectionState(sheet) === false) {
        return false;
    }
    if (typeof sheet.Unprotect !== "function") {
        throw new Error("当前WPS环境不支持取消规则工作表保护，请升级WPS后重试");
    }
    // AirScript、金山文档WebOffice对可选密码参数存在实现差异，依次兼容
    // 无参数、空字符串和对象参数三种调用方式。
    const attempts = [
        function () { sheet.Unprotect(); },
        function () { sheet.Unprotect(""); },
        function () { sheet.Unprotect({ Password: "" }); }
    ];
    for (let index = 0; index < attempts.length; index++) {
        try {
            attempts[index]();
            if (ruleSheetProtectionState(sheet) !== true) {
                return true;
            }
        } catch (error) {
            // 继续尝试下一种在线表格兼容签名。
        }
    }
    throw new Error("无法取消ListingRules保护");
}

function archivedRuleSheetName() {
    const base = "ListingRules_旧保护";
    for (let index = 0; index < 100; index++) {
        const candidate = index === 0 ? base : base + "_" + index;
        if (!findSheetByName(candidate)) {
            return candidate;
        }
    }
    throw new Error("无法为旧ListingRules生成安全备份标签");
}

function replaceLockedSystemRuleSheet(sheet) {
    const title = displayText(scalarValue(sheet.Range("A1")));
    if (displayText(sheet.Name) !== RULE_SHEET_NAME ||
        (title !== "" && title !== "G组运营工作台｜库存销售规则配置")) {
        throw new Error("ListingRules不是系统生成的规则表，不能自动替换");
    }
    const archivedName = archivedRuleSheetName();
    try {
        sheet.Name = archivedName;
    } catch (error) {
        throw new Error("无法重命名旧ListingRules，请检查是否有工作簿结构权限限制");
    }
    return {
        sheet: ensureRuleSheet(),
        archivedRuleSheetName: archivedName
    };
}

function setupRuleConfig() {
    let sheet = ensureRuleSheet();
    let archivedName = "";
    try {
        unprotectRuleSheet(sheet);
    } catch (error) {
        // 金山文档在线表格没有“审阅/撤销工作表保护”入口。若系统旧规则表
        // 无法通过API解除保护，则保留旧表并新建同名规则表，避免破坏旧数据。
        const replacement = replaceLockedSystemRuleSheet(sheet);
        sheet = replacement.sheet;
        archivedName = replacement.archivedRuleSheetName;
    }
    // “初始化 / 检查规则配置”是显式恢复默认动作。日常同步只读取当前规则，
    // 不会执行这里的重置。按用户最终决定，ListingRules不再设置任何保护。
    sheet.Range("A1:E80").Value2 = "";
    const defaults = [
        ["A1", "G组运营工作台｜库存销售规则配置"],
        ["A3", "参数"], ["B3", "当前值"], ["C3", "说明"],
        ["A4", "规则版本号"], ["B4", "R1.0"],
        ["C4", "参数变更时升级版本，系统审计会记录导入时版本"],
        ["A5", "低销量分界"], ["B5", 10],
        ["C5", "30日销量低于该值时使用低销量权重"],
        ["A6", "高销量分界"], ["B6", 30],
        ["C6", "30日销量达到该值时使用高销量权重"],
        ["A7", "目标覆盖天数"], ["B7", 90],
        ["C7", "正常补货时的含在途目标覆盖天数"],
        ["A8", "趋势判断最低销量"], ["B8", 10],
        ["C8", "低于该销量只显示低销量观察"],
        ["A9", "增长门槛"], ["B9", 0.20],
        ["A10", "快速增长门槛"], ["B10", 0.50],
        ["A11", "下降门槛"], ["B11", -0.20],
        ["A12", "快速下降门槛"], ["B12", -0.50],
        ["A13", "高广告费率门槛"], ["B13", 0.25],
        ["C13", "30日广告费 ÷ 30日销售额"],
        ["A14", "补货取整倍数"], ["B14", 5],
        ["C14", "建议补货量向上取整到该正整数的倍数"],
        ["A16", "销量档位"], ["B16", "M1 最近7日"],
        ["C16", "M2 第8—14日"], ["D16", "M3 第15—30日"],
        ["E16", "权重合计"],
        ["A17", "低销量"], ["B17", 0.30], ["C17", 0.25], ["D17", 0.45],
        ["A18", "中销量"], ["B18", 0.40], ["C18", 0.30], ["D18", 0.30],
        ["A19", "高销量"], ["B19", 0.50], ["C19", 0.30], ["D19", 0.20]
    ];
    for (let index = 0; index < defaults.length; index++) {
        writeRuleValue(sheet, defaults[index][0], defaults[index][1]);
    }
    const documentation = [
        ["工作列", "结果或项目", "精确触发条件 / 计算公式", "维护方式"],
        ["30日广告费率", "百分比 / 空", "30日广告费÷30日销售额；销售额为0或缺失时留空", "公式；门槛使用B13"],
        ["30日实际成交均价", "金额 / 空", "30日销售额÷30日销量；销量为0或缺失时留空", "公式；仅供价格诊断，不参与补货预测"],
        ["趋势差异率", "百分比", "(M1-M3)÷MAX(M3,1)；M1=7日销量÷7×30；M3=(30日销量-14日销量)÷16×30", "先换算为相同30天速度；14/25/35对应220%，再使用B8:B12"],
        ["趋势差异率", "空", "7/14/30日销量缺失、负数、7日>14日、14日>30日，或30日销量<B8", "公式"],
        ["销量状态", "数据异常", "7/14/30日销量任一缺失、负数，或7日销量>14日销量，或14日销量>30日销量", "公式；优先级1"],
        ["销量状态", "无销量", "销量窗口有效且30日销量=0", "公式；优先级2"],
        ["销量状态", "低销量观察", "销量窗口有效、30日销量>0且30日销量<B8", "公式；优先级3"],
        ["销量状态", "近期启动", "30日销量>=B8，且30日销量-14日销量=0，且7日销量>0", "公式；优先级4"],
        ["销量状态", "快速增长", "未命中前述状态，且趋势差异率>B10", "公式"],
        ["销量状态", "增长", "未命中前述状态，且B9<趋势差异率<=B10", "公式"],
        ["销量状态", "稳定", "未命中前述状态，且B11<=趋势差异率<=B9", "公式"],
        ["销量状态", "下降", "未命中前述状态，且B12<=趋势差异率<B11", "公式"],
        ["销量状态", "快速下降", "未命中前述状态，且趋势差异率<B12", "公式"],
        ["预测可信度", "低", "任一项成立：销量状态=数据异常/无销量/近期启动/快速增长/快速下降；补货状态非空且不等于正常补货；FBA可售=0且30日销量>0", "公式；低优先于中、高；最终补货月销蓝色提示"],
        ["预测可信度", "中", "未命中低，且任一项成立：30日销量<B6；销量状态=增长/下降；广告状态=数据不足/高广告费/广告费上涨/广告费下降", "公式；仅提醒，不阻止最终月销"],
        ["预测可信度", "高", "同时满足：30日销量>=B6；销量状态=稳定；补货状态为空或正常补货；FBA可售>0；广告状态=正常或无广告", "公式；任一条件不满足都不是高"],
        ["异常原因", "销量窗口异常；", "销量状态=数据异常", "公式；多项命中时按本区顺序拼接"],
        ["异常原因", "无销量；", "销量状态=无销量", "公式"],
        ["异常原因", "低销量；", "销量状态=低销量观察", "公式"],
        ["异常原因", "趋势突变；", "销量状态=近期启动、快速增长或快速下降", "公式"],
        ["异常原因", "当前无FBA可售；", "FBA可售=0且30日销量>0", "公式"],
        ["异常原因", "广告依赖高；", "广告状态=高广告费", "公式"],
        ["异常原因", "补货状态文本；", "补货状态非空且不等于正常补货；直接拼接实际补货状态文字", "公式；链接状态不参与"],
        ["系统建议月销", "低销量档", "30日销量<B5：M1×B17+M2×C17+M3×D17，结果四舍五入为整数", "M1=7日/7×30；M2=(14日-7日)/7×30；M3=(30日-14日)/16×30"],
        ["系统建议月销", "中销量档", "B5<=30日销量<B6：M1×B18+M2×C18+M3×D18，结果四舍五入为整数", "公式"],
        ["系统建议月销", "高销量档", "30日销量>=B6：M1×B19+M2×C19+M3×D19，结果四舍五入为整数", "公式；销量窗口异常时留空"],
        ["最终补货月销", "0", "补货状态=清库存或停售", "公式；最高优先级"],
        ["最终补货月销", "空", "补货状态=新品观察或暂缓补货", "公式；等待人工判断"],
        ["最终补货月销", "系统建议月销", "补货状态为其他值或留空；高/中/低可信度都自动填写", "可人工覆盖；下次数据更新恢复标准公式"],
        ["在库覆盖天数", "天数 / 空", "最终补货月销>0时：(FBA可售+预留)÷最终补货月销×30；否则留空", "公式"],
        ["含在途覆盖天数", "天数 / 空", "最终补货月销>0时：(FBA可售+预留+在途)÷最终补货月销×30；否则留空", "公式"],
        ["建议补货量", "0", "补货状态=清库存或停售", "公式；最高优先级"],
        ["建议补货量", "空", "补货状态=新品观察/暂缓补货，或最终补货月销为空/为0", "公式；等待人工判断或无有效需求"],
        ["建议补货量", "非负整数", "缺口=最终补货月销÷30×B7-FBA可售-预留-在途；MAX(0,缺口)按B14向上取整", "例如B14=5时，1→5、13→15"],
        ["库存状态", "数据不足", "最终补货月销为空或为0", "公式"],
        ["库存状态", "缺货", "最终补货月销>0且FBA可售+预留=0", "公式"],
        ["库存状态", "库存紧张", "未命中数据不足/缺货，且在库覆盖天数<30", "公式"],
        ["库存状态", "需要补货", "在库覆盖天数>=30，且含在途覆盖天数<B7", "公式"],
        ["库存状态", "库存健康", "B7<=含在途覆盖天数<=B7+30", "公式"],
        ["库存状态", "库存偏高", "含在途覆盖天数>B7+30", "公式"],
        ["广告状态", "数据不足", "7日或30日广告费缺失；或30日广告费>0但30日销售额缺失/为0导致广告费率为空", "公式"],
        ["广告状态", "无广告", "广告数据完整且30日广告费=0", "公式"],
        ["广告状态", "高广告费", "广告数据完整、30日广告费>0，且30日广告费率>=B13", "公式；优先于广告费涨跌"],
        ["广告状态", "广告费上涨", "未命中高广告费；此前23日广告费<=0且7日广告费>0，或最近7日日均÷此前23日日均>1.2", "此前23日广告费=30日广告费-7日广告费"],
        ["广告状态", "广告费下降", "未命中高广告费，且最近7日日均÷此前23日日均<0.8", "公式"],
        ["广告状态", "正常", "数据完整且未命中无广告、高广告费、广告费上涨或广告费下降", "公式"],
        ["链接状态", "人工自由填写", "例如高退标签、链接异常、促销中；系统不读取该列做判断", "不参与任何公式"],
        ["运营备注", "人工自由填写", "可按需添加该表头；不是必填列", "不参与任何公式"],
        ["规则表保护", "不设置保护", "ListingRules保持未保护，所有单元格可直接编辑；系统不再调用Protect或设置锁定区域", "修改B4:B14或B17:D19后请同步升级B4规则版本"]
    ];
    for (let index = 0; index < documentation.length; index++) {
        const row = 22 + index;
        sheet.Range("A" + row + ":D" + row).Value2 = [documentation[index]];
    }
    sheet.Range("E17").Formula = "=SUM(B17:D17)";
    sheet.Range("E18").Formula = "=SUM(B18:D18)";
    sheet.Range("E19").Formula = "=SUM(B19:D19)";
    try {
        sheet.Range("B9:B13").NumberFormat = "0.0%";
        sheet.Range("B17:E19").NumberFormat = "0.0%";
    } catch (error) {
        // 格式失败不影响参数和公式的安全性。
    }
    return {
        sheet: sheet,
        archivedRuleSheetName: archivedName,
        protected: false,
        protectionVerified: ruleSheetProtectionState(sheet) === false,
        editableRangesApplied: false
    };
}

function numericRule(sheet, address, label) {
    const raw = scalarValue(sheet.Range(address));
    if (displayText(raw) === "" || !Number.isFinite(Number(raw))) {
        throw new Error("规则配置“" + label + "”不是有效数字");
    }
    return Number(raw);
}

function validateRuleConfig() {
    const sheet = findSheetByName(RULE_SHEET_NAME);
    if (!sheet) {
        throw new Error(
            "缺少“" + RULE_SHEET_NAME + "”工作表，请在网页点击“初始化 / 检查规则配置”"
        );
    }
    const version = displayText(scalarValue(sheet.Range("B4")));
    const low = numericRule(sheet, "B5", "低销量分界");
    const high = numericRule(sheet, "B6", "高销量分界");
    const targetDays = numericRule(sheet, "B7", "目标覆盖天数");
    const trendMinimum = numericRule(sheet, "B8", "趋势判断最低销量");
    const growth = numericRule(sheet, "B9", "增长门槛");
    const fastGrowth = numericRule(sheet, "B10", "快速增长门槛");
    const decline = numericRule(sheet, "B11", "下降门槛");
    const fastDecline = numericRule(sheet, "B12", "快速下降门槛");
    const highAdRate = numericRule(sheet, "B13", "高广告费率门槛");
    const replenishmentMultiple = numericRule(sheet, "B14", "补货取整倍数");
    const weights = [];
    for (let row = 17; row <= 19; row++) {
        const values = [
            numericRule(sheet, "B" + row, "M1权重"),
            numericRule(sheet, "C" + row, "M2权重"),
            numericRule(sheet, "D" + row, "M3权重")
        ];
        const total = values[0] + values[1] + values[2];
        if (values.some(function (value) { return value < 0 || value > 1; }) ||
            Math.abs(total - 1) > 0.0001) {
            throw new Error("规则配置第" + row + "行权重必须为0到100%且合计100%");
        }
        weights.push(values);
    }
    if (version === "") {
        throw new Error("规则版本号不能为空");
    }
    if (low <= 0 || high <= low || targetDays <= 0 || trendMinimum < 0) {
        throw new Error("销量分界或目标覆盖天数配置无效");
    }
    if (growth < 0 || fastGrowth <= growth || decline > 0 || fastDecline >= decline) {
        throw new Error("销量趋势门槛配置无效");
    }
    if (highAdRate < 0 || highAdRate > 1) {
        throw new Error("高广告费率门槛必须在0到100%之间");
    }
    if (replenishmentMultiple <= 0 || Math.floor(replenishmentMultiple) !== replenishmentMultiple) {
        throw new Error("补货取整倍数必须是正整数");
    }
    let protectedContents = false;
    try {
        protectedContents = Boolean(sheet.ProtectContents);
    } catch (error) {
        // 旧WPS可能无法读取保护状态，不影响规则值校验。
    }
    return {
        valid: true,
        version: version,
        lowSalesBoundary: low,
        highSalesBoundary: high,
        targetCoverageDays: targetDays,
        trendMinimumSales: trendMinimum,
        growthThreshold: growth,
        fastGrowthThreshold: fastGrowth,
        declineThreshold: decline,
        fastDeclineThreshold: fastDecline,
        highAdRateThreshold: highAdRate,
        replenishmentMultiple: replenishmentMultiple,
        sheetName: displayText(sheet.Name),
        protected: protectedContents,
        weights: weights
    };
}

function formulaCell(columns, field, row) {
    return columns[field].columnLetter + row;
}

function ruleReference(address) {
    return "'" + RULE_SHEET_NAME + "'!$" + address.charAt(0) + "$" + address.slice(1);
}

function listingFormulas(columns, row) {
    const c = function (field) { return formulaCell(columns, field, row); };
    const sales7 = c("sales_7d");
    const sales14 = c("sales_14d");
    const sales30 = c("sales_30d");
    const m1 = "(" + sales7 + "/7*30)";
    const m2 = "((" + sales14 + "-" + sales7 + ")/7*30)";
    const m3 = "((" + sales30 + "-" + sales14 + ")/16*30)";
    const invalid = "OR(" + sales7 + "=\"\"," + sales14 + "=\"\"," +
        sales30 + "=\"\"," + sales7 + ">" + sales14 + "," +
        sales14 + ">" + sales30 + "," + sales7 + "<0," + sales14 +
        "<0," + sales30 + "<0)";
    const trend = c("trend_difference_rate");
    const salesStatus = c("sales_status");
    const systemMonthly = c("system_monthly_sales");
    const finalMonthly = c("final_monthly_sales");
    const replenishmentStatus = c("replenishment_status");
    const fba = c("fba_available");
    const reserved = c("reserved");
    const inbound = c("inbound");
    const ad7 = c("ad_spend_7d");
    const ad30 = c("ad_spend_30d");
    const revenue30 = c("revenue_30d");
    const adRate = c("ad_rate_30d");
    const adStatus = c("ad_status");
    const stockDays = c("stock_coverage_days");
    const totalDays = c("total_coverage_days");
    const low = ruleReference("B5");
    const high = ruleReference("B6");
    const target = ruleReference("B7");
    const trendMinimum = ruleReference("B8");
    const growth = ruleReference("B9");
    const fastGrowth = ruleReference("B10");
    const decline = ruleReference("B11");
    const fastDecline = ruleReference("B12");
    const highAd = ruleReference("B13");
    const replenishmentMultiple = ruleReference("B14");
    const lowForecast = m1 + "*" + ruleReference("B17") + "+" +
        m2 + "*" + ruleReference("C17") + "+" + m3 + "*" + ruleReference("D17");
    const midForecast = m1 + "*" + ruleReference("B18") + "+" +
        m2 + "*" + ruleReference("C18") + "+" + m3 + "*" + ruleReference("D18");
    const highForecast = m1 + "*" + ruleReference("B19") + "+" +
        m2 + "*" + ruleReference("C19") + "+" + m3 + "*" + ruleReference("D19");
    return {
        ad_rate_30d: "=IF(OR(" + ad30 + "=\"\"," + revenue30 + "=\"\"," +
            revenue30 + "=0),\"\"," + ad30 + "/" + revenue30 + ")",
        average_price_30d: "=IF(OR(" + revenue30 + "=\"\"," + sales30 +
            "=\"\"," + sales30 + "=0),\"\"," + revenue30 + "/" + sales30 + ")",
        trend_difference_rate: "=IF(OR(" + invalid + "," + sales30 + "<" +
            trendMinimum + "),\"\",(" + m1 + "-" + m3 + ")/MAX(" + m3 + ",1))",
        sales_status: "=IF(" + invalid + ",\"数据异常\",IF(" + sales30 +
            "=0,\"无销量\",IF(" + sales30 + "<" + trendMinimum +
            ",\"低销量观察\",IF(AND(" + m3 + "=0," + m1 +
            ">0),\"近期启动\",IF(" + trend + ">" + fastGrowth +
            ",\"快速增长\",IF(" + trend + ">" + growth +
            ",\"增长\",IF(" + trend + "<" + fastDecline +
            ",\"快速下降\",IF(" + trend + "<" + decline +
            ",\"下降\",\"稳定\"))))))))",
        system_monthly_sales: "=IF(" + invalid + ",\"\",ROUND(IF(" + sales30 +
            "<" + low + "," + lowForecast + ",IF(" + sales30 + "<" + high +
            "," + midForecast + "," + highForecast + ")),0))",
        ad_status: "=IF(OR(" + ad7 + "=\"\"," + ad30 + "=\"\"),\"数据不足\",IF(" + ad30 +
            "=0,\"无广告\",IF(" + adRate + "=\"\",\"数据不足\",IF(" +
            adRate + ">=" + highAd + ",\"高广告费\",IF((" + ad30 + "-" +
            ad7 + ")<=0,IF(" + ad7 + ">0,\"广告费上涨\",\"正常\"),IF((" +
            ad7 + "/7)/((" + ad30 + "-" + ad7 + ")/23)>1.2,\"广告费上涨\",IF((" +
            ad7 + "/7)/((" + ad30 + "-" + ad7 + ")/23)<0.8,\"广告费下降\",\"正常\")))))))",
        forecast_confidence: "=IF(OR(" + salesStatus + "=\"数据异常\"," +
            salesStatus + "=\"无销量\"," + salesStatus + "=\"近期启动\"," +
            salesStatus + "=\"快速增长\"," + salesStatus + "=\"快速下降\",AND(" +
            replenishmentStatus + "<>\"\"," + replenishmentStatus +
            "<>\"正常补货\"),AND(" + fba + "=0," + sales30 + ">0)),\"低\",IF(OR(" +
            sales30 + "<" + high + "," + salesStatus + "=\"增长\"," + salesStatus +
            "=\"下降\"," + adStatus + "=\"数据不足\"," + adStatus + "=\"高广告费\"," + adStatus +
            "=\"广告费上涨\"," + adStatus + "=\"广告费下降\"),\"中\",\"高\"))",
        exception_reason: "=IF(" + salesStatus + "=\"数据异常\",\"销量窗口异常；\",\"\")&" +
            "IF(" + salesStatus + "=\"无销量\",\"无销量；\",\"\")&IF(" +
            salesStatus + "=\"低销量观察\",\"低销量；\",\"\")&IF(OR(" +
            salesStatus + "=\"近期启动\"," + salesStatus + "=\"快速增长\"," +
            salesStatus + "=\"快速下降\"),\"趋势突变；\",\"\")&IF(AND(" +
            fba + "=0," + sales30 + ">0),\"当前无FBA可售；\",\"\")&IF(" +
            adStatus + "=\"高广告费\",\"广告依赖高；\",\"\")&IF(AND(" +
            replenishmentStatus +
            "<>\"\"," + replenishmentStatus + "<>\"正常补货\")," +
            replenishmentStatus + "&\"；\",\"\")",
        final_monthly_sales: "=IF(OR(" + replenishmentStatus +
            "=\"清库存\"," + replenishmentStatus + "=\"停售\"),0,IF(OR(" +
            replenishmentStatus + "=\"新品观察\"," + replenishmentStatus +
            "=\"暂缓补货\"),\"\"," + systemMonthly + "))",
        stock_coverage_days: "=IF(OR(" + finalMonthly + "=\"\"," + finalMonthly +
            "=0),\"\",SUM(" + fba + "," + reserved + ")/" + finalMonthly + "*30)",
        total_coverage_days: "=IF(OR(" + finalMonthly + "=\"\"," + finalMonthly +
            "=0),\"\",SUM(" + fba + "," + reserved + "," + inbound + ")/" +
            finalMonthly + "*30)",
        suggested_replenishment: "=IF(OR(" + replenishmentStatus +
            "=\"清库存\"," + replenishmentStatus + "=\"停售\"),0,IF(OR(" +
            replenishmentStatus + "=\"新品观察\"," + replenishmentStatus +
            "=\"暂缓补货\"," + finalMonthly + "=\"\"," + finalMonthly +
            "=0),\"\",ROUNDUP(MAX(0," + finalMonthly + "/30*" + target +
            "-SUM(" + fba + "," + reserved + "," + inbound + "))/" +
            replenishmentMultiple + ",0)*" + replenishmentMultiple + "))",
        inventory_status: "=IF(OR(" + finalMonthly + "=\"\"," + finalMonthly +
            "=0),\"数据不足\",IF(SUM(" + fba + "," + reserved +
            ")=0,\"缺货\",IF(" + stockDays + "<30,\"库存紧张\",IF(" +
            totalDays + "<" + target + ",\"需要补货\",IF(" + totalDays +
            "<=" + target + "+30,\"库存健康\",\"库存偏高\")))))"
    };
}

function setListingFormula(sheet, columns, row, field, formula, overwrite) {
    const range = sheet.Range(formulaCell(columns, field, row));
    const currentFormula = scalarFormula(range);
    if (!overwrite && currentFormula !== "") {
        return "formula";
    }
    // 人工最终月销只对当前这次判断有效。下一次数据同步会恢复标准公式，
    // 避免新销量数据继续沿用上一次人工判断。
    range.Formula = formula;
    if (field === "ad_rate_30d" || field === "trend_difference_rate") {
        range.NumberFormat = "0.0%";
    } else if (field === "average_price_30d") {
        range.NumberFormat = "0.00";
    } else if (field === "stock_coverage_days" || field === "total_coverage_days") {
        range.NumberFormat = "0.0";
    }
    return "formula";
}

function installListingFormulas(sheet, columns, rows, overwrite) {
    let formulaRows = 0;
    let manualOverrideRows = 0;
    for (let rowIndex = 0; rowIndex < rows.length; rowIndex++) {
        const row = rows[rowIndex];
        const formulas = listingFormulas(columns, row);
        let hasFormula = true;
        for (let fieldIndex = 0; fieldIndex < FORMULA_FIELDS.length; fieldIndex++) {
            const field = FORMULA_FIELDS[fieldIndex];
            const mode = setListingFormula(
                sheet, columns, row, field, formulas[field], overwrite
            );
            if (field === "final_monthly_sales" && mode === "manual") {
                manualOverrideRows++;
            }
            if (mode !== "formula" && field !== "final_monthly_sales") {
                hasFormula = false;
            }
        }
        if (hasFormula) {
            formulaRows++;
        }
    }
    return {
        formulaRows: formulaRows,
        manualOverrideRows: manualOverrideRows
    };
}

function normalizedConditionFormula(value) {
    return displayText(value).replace(/\$/g, "").replace(/\s+/g, "").toUpperCase();
}

function installLowConfidenceHighlight(sheet, columns, headerRow, lastRow) {
    if (lastRow <= headerRow) {
        return { applied: true, range: "" };
    }
    const firstRow = headerRow + 1;
    const finalColumn = columns.final_monthly_sales.columnLetter;
    const confidenceColumn = columns.forecast_confidence.columnLetter;
    const targetAddress = finalColumn + firstRow + ":" + finalColumn + lastRow;
    const formula = "=$" + confidenceColumn + firstRow + "=\"低\"";
    try {
        const targetRange = sheet.Range(targetAddress);
        const conditions = targetRange.FormatConditions;
        if (!conditions || typeof conditions.Add !== "function") {
            return { applied: false, range: targetAddress };
        }
        let condition = null;
        let legacyCondition = null;
        const expected = normalizedConditionFormula(formula);
        const legacyFormula = normalizedConditionFormula(
            "=$" + confidenceColumn + firstRow + "=\"中\""
        );
        const count = Number(conditions.Count) || 0;
        for (let index = 1; index <= count; index++) {
            const candidate = conditions.Item(index);
            const candidateFormula = normalizedConditionFormula(candidate.Formula1);
            if (candidateFormula === expected) {
                condition = candidate;
                break;
            }
            if (candidateFormula === legacyFormula) {
                legacyCondition = candidate;
            }
        }
        // 版本8安装过“中可信度”条件格式，版本9及以后必须将它改为“低”，
        // 否则新旧规则会同时着色。
        if (!condition && legacyCondition) {
            try {
                legacyCondition.Formula1 = formula;
            } catch (error) {
                // 某些WebOffice版本的Formula1是只读属性。
            }
            if (normalizedConditionFormula(legacyCondition.Formula1) === expected) {
                condition = legacyCondition;
            } else if (typeof legacyCondition.Modify === "function") {
                const modifyAttempts = [
                    function () { legacyCondition.Modify(2, -1, formula, ""); },
                    function () {
                        legacyCondition.Modify({
                            Type: 2, Operator: -1, Formula1: formula, Formula2: ""
                        });
                    }
                ];
                for (let index = 0; index < modifyAttempts.length; index++) {
                    try {
                        modifyAttempts[index]();
                        if (normalizedConditionFormula(legacyCondition.Formula1) === expected) {
                            condition = legacyCondition;
                            break;
                        }
                    } catch (error) {
                        // 继续尝试另一种条件格式修改签名。
                    }
                }
            }
            if (!condition && typeof legacyCondition.Delete === "function") {
                try {
                    legacyCondition.Delete();
                    legacyCondition = null;
                } catch (error) {
                    // 下方会返回明确失败，不叠加两套提示色。
                }
            }
            if (!condition && legacyCondition) {
                return {
                    applied: false,
                    range: targetAddress,
                    message: "无法移除旧的中可信度提示色"
                };
            }
        }
        if (condition && typeof condition.ModifyAppliesToRange === "function") {
            condition.ModifyAppliesToRange(targetRange);
        }
        if (!condition) {
            let expressionType = 2;
            try {
                expressionType = Application.Enum.XlFormatConditionType.xlExpression;
            } catch (error) {
                // Excel/WPS中xlExpression的稳定枚举值是2。
            }
            condition = conditions.Add(expressionType, -1, formula, "");
        }
        // 低可信度使用“蓝色，着色1，淡色60%”（#9DC3E6）。
        // Excel RGB整数为BGR顺序。
        condition.Interior.Color = 15123357;
        if (typeof condition.SetFirstPriority === "function") {
            condition.SetFirstPriority();
        }
        return { applied: true, range: targetAddress };
    } catch (error) {
        return {
            applied: false,
            range: targetAddress,
            message: displayText(error && error.message ? error.message : error)
        };
    }
}

function isSpreadsheetError(value) {
    return /^#(NAME\?|REF!|DIV\/0!|VALUE!|N\/A|NUM!|NULL!|SPILL!|CALC!)/i.test(
        displayText(value)
    );
}

function formulaStateCounts(sheet, columns, headerRow) {
    const lastRow = lastUsedRow(sheet, headerRow);
    let formulaRows = 0;
    let manualOverrideRows = 0;
    let formulaErrorRows = 0;
    for (let row = headerRow + 1; row <= lastRow; row++) {
        if (normalizeMsku(scalarValue(sheet.Range(formulaCell(columns, "msku", row)))) === "") {
            continue;
        }
        let complete = true;
        let hasFormulaError = false;
        for (let index = 0; index < FORMULA_FIELDS.length; index++) {
            const field = FORMULA_FIELDS[index];
            const range = sheet.Range(formulaCell(columns, field, row));
            const formula = scalarFormula(range);
            if (field === "final_monthly_sales" && formula === "" && displayText(scalarValue(range)) !== "") {
                manualOverrideRows++;
                continue;
            }
            if (formula === "") {
                complete = false;
            } else if (isSpreadsheetError(scalarValue(range))) {
                hasFormulaError = true;
            }
        }
        if (complete) {
            formulaRows++;
        }
        if (hasFormulaError) {
            formulaErrorRows++;
        }
    }
    return {
        formulaRows: formulaRows,
        manualOverrideRows: manualOverrideRows,
        formulaErrorRows: formulaErrorRows
    };
}

function listingStoredValue(sheet, columns, row, field) {
    const range = sheet.Range(columns[field].columnLetter + row);
    const formula = FORMULA_FIELDS.indexOf(field) >= 0 ? scalarFormula(range) : "";
    if (formula !== "") {
        return { kind: "formula", formula: formula };
    }
    return scalarValue(range);
}

function writeStoredValue(range, field, value) {
    if (
        FORMULA_FIELDS.indexOf(field) >= 0 &&
        value && typeof value === "object" &&
        value.kind === "formula" && displayText(value.formula) !== ""
    ) {
        range.Formula = displayText(value.formula);
    } else {
        range.Value2 = value === null || value === undefined ? "" : value;
    }
}

function comparableListingValue(field, value) {
    if (field === "current_data_date" || field === "previous_data_date") {
        return normalizedDate(value);
    }
    if (value && typeof value === "object") {
        return JSON.stringify(value);
    }
    return displayText(value);
}

function listingSnapshotEntry(sheet, columns, msku, row, field) {
    const column = columns[field];
    const address = column.columnLetter + row;
    const value = listingStoredValue(sheet, columns, row, field);
    return {
        targetType: "cell",
        sheetName: displayText(sheet.Name),
        matchHeader: columns.msku.text,
        matchValue: msku,
        itemKey: msku,
        field: field,
        header: column.text,
        cellAddress: address,
        value: value,
        comparableValue: comparableListingValue(field, value)
    };
}

function collectListingSnapshots(
    sheet,
    columns,
    headerRow,
    snapshotItems
) {
    const lastRow = lastUsedRow(sheet, headerRow);
    const rowsByMsku = buildRowsByMsku(sheet, columns, headerRow, lastRow);
    const seen = Object.create(null);
    const snapshots = [];
    for (let itemIndex = 0; itemIndex < snapshotItems.length; itemIndex++) {
        const source = snapshotItems[itemIndex];
        const msku = normalizeMsku(source && source.msku);
        if (msku === "" || seen[msku]) {
            continue;
        }
        seen[msku] = true;
        const rows = rowsByMsku[msku] || [];
        if (rows.length !== 1) {
            continue;
        }
        for (
            let fieldIndex = 0;
            fieldIndex < LISTING_WRITABLE_FIELDS.length;
            fieldIndex++
        ) {
            const field = LISTING_WRITABLE_FIELDS[fieldIndex];
            if (!columns[field]) {
                continue;
            }
            snapshots.push(
                listingSnapshotEntry(sheet, columns, msku, rows[0], field)
            );
        }
    }
    return snapshots;
}

function currentListingSnapshot(
    sheet,
    columns,
    headerRow,
    target,
    rowsByMsku
) {
    if (
        displayText(target && target.targetType).toLowerCase() !== "cell" ||
        normalizeHeader(target && target.sheetName) !== normalizeHeader(sheet.Name)
    ) {
        throw new Error("恢复目标不属于当前Listing子表");
    }
    const field = displayText(target.field);
    if (!columns[field] || LISTING_WRITABLE_FIELDS.indexOf(field) < 0) {
        throw new Error("恢复目标不是Listing系统可写字段：" + field);
    }
    const msku = normalizeMsku(target.matchValue);
    const rowMap = rowsByMsku || buildRowsByMsku(
        sheet,
        columns,
        headerRow,
        lastUsedRow(sheet, headerRow)
    );
    const rows = rowMap[msku] || [];
    if (rows.length !== 1) {
        throw new Error(
            "MSKU在Listing子表中不是唯一一行：" + displayText(target.matchValue)
        );
    }
    return listingSnapshotEntry(sheet, columns, msku, rows[0], field);
}

function sameComparable(left, right) {
    return JSON.stringify(left) === JSON.stringify(right);
}

function verifyListingPreconditions(
    sheet,
    columns,
    headerRow,
    preconditions
) {
    const rowsByMsku = buildRowsByMsku(
        sheet,
        columns,
        headerRow,
        lastUsedRow(sheet, headerRow)
    );
    for (let index = 0; index < preconditions.length; index++) {
        const expected = preconditions[index];
        const current = currentListingSnapshot(
            sheet, columns, headerRow, expected, rowsByMsku
        );
        const expectedComparable = Object.prototype.hasOwnProperty.call(
            expected, "comparableValue"
        ) ? expected.comparableValue : comparableListingValue(
            expected.field, expected.value
        );
        if (!sameComparable(current.comparableValue, expectedComparable)) {
            throw new Error(
                "共享表在写前快照后发生变化：" +
                displayText(expected.matchValue) + " / " +
                displayText(expected.header || expected.field) +
                "。本次已停止，未覆盖新值"
            );
        }
    }
}

function inspectListingChanges(
    sheet,
    columns,
    headerRow,
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
    const rowsByMsku = buildRowsByMsku(
        sheet,
        columns,
        headerRow,
        lastUsedRow(sheet, headerRow)
    );
    for (let index = 0; index < changes.length; index++) {
        const change = changes[index] && typeof changes[index] === "object"
            ? changes[index] : {};
        const globalIndex = indexOffset + index;
        try {
            const current = currentListingSnapshot(
                sheet, columns, headerRow, change, rowsByMsku
            );
            const expectedValue = direction === "rollback"
                ? change.newValue : change.oldValue;
            const desiredValue = direction === "rollback"
                ? change.oldValue : change.newValue;
            const expectedComparable = comparableListingValue(
                change.field, expectedValue
            );
            const desiredComparable = comparableListingValue(
                change.field, desiredValue
            );
            const item = {
                index: globalIndex,
                itemKey: displayText(change.itemKey || change.matchValue),
                matchValue: displayText(change.matchValue),
                field: displayText(change.field),
                header: displayText(change.header || current.header),
                cellAddress: current.cellAddress,
                currentValue: current.value,
                expectedValue: expectedValue,
                desiredValue: desiredValue
            };
            if (sameComparable(current.comparableValue, expectedComparable)) {
                result.ready.push(item);
            } else if (
                sameComparable(current.comparableValue, desiredComparable)
            ) {
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

function applyListingChanges(
    sheet,
    columns,
    headerRow,
    changes,
    direction,
    indexOffset
) {
    const inspected = inspectListingChanges(
        sheet, columns, headerRow, changes, direction, indexOffset
    );
    const result = {
        applied: [],
        alreadyApplied: inspected.alreadyApplied,
        conflicts: inspected.conflicts,
        failures: inspected.failures
    };
    for (let readyIndex = 0; readyIndex < inspected.ready.length; readyIndex++) {
        const ready = inspected.ready[readyIndex];
        try {
            const range = sheet.Range(ready.cellAddress);
            if (
                ready.field === "current_data_date" ||
                ready.field === "previous_data_date" ||
                ready.field === "rating_review" ||
                ready.field === "previous_rating_review" ||
                ready.field === "updated_at"
            ) {
                range.NumberFormat = "@";
            }
            writeStoredValue(range, ready.field, ready.desiredValue);
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
const action = normalizeHeader(argv.action || "validate").toLowerCase();
const sheetName = displayText(argv.sheet_name);
const dataDate = normalizedDate(argv.data_date);
const items = Array.isArray(argv.items) ? argv.items : [];

if (
    action !== "discover" &&
    action !== "validate" &&
    action !== "setup_rules" &&
    action !== "sync" &&
    action !== "snapshot" &&
    action !== "snapshot_targets" &&
    action !== "inspect_changes" &&
    action !== "apply_changes"
) {
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
if (action === "setup_rules") {
    const setupConfig = setupRuleConfig();
    const setupRules = validateRuleConfig();
    setupRules.protected = setupConfig.protected;
    setupRules.protectionVerified = setupConfig.protectionVerified;
    setupRules.editableRangesApplied = setupConfig.editableRangesApplied;
    const setupLastRow = lastUsedRow(targetSheet, headerRow);
    const setupRows = [];
    for (let row = headerRow + 1; row <= setupLastRow; row++) {
        if (
            normalizeMsku(
                scalarValue(targetSheet.Range(formulaCell(columns, "msku", row)))
            ) !== ""
        ) {
            setupRows.push(row);
        }
    }
    installListingFormulas(targetSheet, columns, setupRows, true);
    const setupHighlight = installLowConfidenceHighlight(
        targetSheet, columns, headerRow, setupLastRow
    );
    const setupCounts = formulaStateCounts(targetSheet, columns, headerRow);
    const setupResult = {
        success: true,
        schemaVersion: SCHEMA_VERSION,
        sheetName: targetSheet.Name,
        headerRow: headerRow,
        columns: columnLetters(columns),
        headers: columnHeaders(columns),
        rules: setupRules,
        formulaRows: setupCounts.formulaRows,
        manualOverrideRows: setupCounts.manualOverrideRows,
        formulaErrorRows: setupCounts.formulaErrorRows,
        archivedRuleSheetName: setupConfig.archivedRuleSheetName,
        lowConfidenceHighlightApplied: setupHighlight.applied,
        lowConfidenceHighlightRange: setupHighlight.range
    };
    console.log(JSON.stringify(setupResult));
    return setupResult;
}
let rules = { valid: false, version: "" };
try {
    rules = validateRuleConfig();
} catch (ruleError) {
    if (action === "validate" || action === "snapshot" || action === "sync") {
        throw ruleError;
    }
    // 恢复、恢复预览和目标回读不能因规则表后来被误改而失效。
}
const expectedRuleVersion = displayText(argv.expected_rule_version);
if (
    action === "sync" && expectedRuleVersion !== "" &&
    rules.version !== expectedRuleVersion
) {
    throw new Error(
        "规则版本在预览后发生变化，请重新预览再执行回填"
    );
}
const formulaCounts = formulaStateCounts(targetSheet, columns, headerRow);
const baseResult = {
    success: true,
    schemaVersion: SCHEMA_VERSION,
    sheetName: targetSheet.Name,
    headerRow: headerRow,
    columns: columnLetters(columns),
    headers: columnHeaders(columns),
    rules: rules,
    formulaRows: formulaCounts.formulaRows,
    manualOverrideRows: formulaCounts.manualOverrideRows,
    formulaErrorRows: formulaCounts.formulaErrorRows
};

if (action === "validate") {
    console.log(JSON.stringify(baseResult));
    return baseResult;
}

if (action === "snapshot") {
    const snapshotResult = Object.assign(baseResult, {
        snapshots: collectListingSnapshots(
            targetSheet, columns, headerRow, items
        )
    });
    console.log(JSON.stringify(snapshotResult));
    return snapshotResult;
}

if (action === "snapshot_targets") {
    const targets = Array.isArray(argv.targets) ? argv.targets : [];
    const rowsByMsku = buildRowsByMsku(
        targetSheet,
        columns,
        headerRow,
        lastUsedRow(targetSheet, headerRow)
    );
    const snapshots = targets.map(function (target) {
        return currentListingSnapshot(
            targetSheet, columns, headerRow, target, rowsByMsku
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
        ? inspectListingChanges(
            targetSheet, columns, headerRow, changes, direction, indexOffset
        )
        : applyListingChanges(
            targetSheet, columns, headerRow, changes, direction, indexOffset
        );
    const response = Object.assign(baseResult, changeResult);
    console.log(JSON.stringify(response));
    return response;
}

verifyListingPreconditions(
    targetSheet,
    columns,
    headerRow,
    Array.isArray(argv.preconditions) ? argv.preconditions : []
);

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
    "rating_review",
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
        row: row,
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
    for (let fieldIndex = 0; fieldIndex < DIRECT_FIELDS.length; fieldIndex++) {
        const field = DIRECT_FIELDS[fieldIndex];
        if (!hasOwn(source, field)) {
            continue;
        }
        pushWrite(
            writesByField,
            field,
            row,
            source[field] === null || source[field] === undefined
                ? "" : source[field],
            msku
        );
    }
    if (columns.discount_price && hasOwn(source, "discount_price")) {
        // 源文件有优惠价表头时，空值代表优惠结束，必须清除共享表旧值。
        pushWrite(
            writesByField,
            "discount_price",
            row,
            source.discount_price === null || source.discount_price === undefined
                ? "" : source.discount_price,
            msku
        );
    }
    pushWrite(writesByField, "updated_at", row, updateTime, msku);
}

writeFields(targetSheet, columns, writesByField, states);
const formulaRows = [];
const stateRowKeys = Object.keys(states);
for (let index = 0; index < stateRowKeys.length; index++) {
    const state = states[stateRowKeys[index]];
    if (!state.failed && state.written) {
        formulaRows.push(state.row);
    }
}
installListingFormulas(
    targetSheet, columns, formulaRows, false
);
const syncHighlight = installLowConfidenceHighlight(
    targetSheet, columns, headerRow, lastRow
);
const installedFormulaState = formulaStateCounts(targetSheet, columns, headerRow);
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
    failures: failures,
    rules: rules,
    formulaRows: installedFormulaState.formulaRows,
    manualOverrideRows: installedFormulaState.manualOverrideRows,
    formulaErrorRows: installedFormulaState.formulaErrorRows,
    lowConfidenceHighlightApplied: syncHighlight.applied,
    lowConfidenceHighlightRange: syncHighlight.range
});
console.log(JSON.stringify(result));
return result;
