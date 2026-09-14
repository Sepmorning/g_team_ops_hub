const SCHEMA_VERSION = 13;
const RULE_MODEL = "三窗口预测-v3";
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
    monthly_sales_method: ["月销计算方案"],
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
    operation_notes: ["运营备注"],
    product_attribute: ["产品属性"]
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
    "monthly_sales_method",
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

const VALUE_FIELDS = ["sales_status", "monthly_sales_method", "forecast_confidence", "exception_reason", "system_monthly_sales", "final_monthly_sales"];
const FORMULA_FIELDS = [
    "ad_rate_30d",
    "average_price_30d",
    "trend_difference_rate",
    "stock_coverage_days",
    "total_coverage_days",
    "suggested_replenishment",
    "inventory_status",
    "ad_status"
];

function displayText(value) {
    return String(value === null || value === undefined ? "" : value).trim();
}

function returnResult(result) {
    console.log(JSON.stringify(result));
    return result;
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

function locateHeaders(sheet, allowMethodUpgrade) {
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
            if (matches.length === 0 && !(allowMethodUpgrade && key === "monthly_sales_method")) {
                missing.push(FIELD_DEFINITIONS[key][0]);
            } else if (matches.length > 1) {
                duplicates.push(
                    FIELD_DEFINITIONS[key][0] + "（" +
                    matches.map(function (item) { return item.text; }).join("、") +
                    "）"
                );
            } else if (matches.length === 1) {
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
        ["A2", RULE_MODEL],
        ["A3", "参数"],
        ["B3", "当前值"],
        ["C3", "说明"],
        ["A4", "规则版本号"],
        ["B4", "R3.0"],
        ["C4", "修改参数后升级此版本；预览后的版本变化会停止回填"],
        ["A5", "低销量平滑参数"],
        ["B5", 20],
        ["C5", "响应系数=MIN(1,30日销量/B5)，不是已验证的最优参数"],
        ["A6", "高可信度最低销量"],
        ["B6", 30],
        ["C6", "30日销量达到此数且稳定、可比、未提示风险才可为高"],
        ["A7", "目标覆盖天数"],
        ["B7", 90],
        ["C7", "总库存覆盖目标；不是运输提前期"],
        ["A8", "低可信度销量门槛"],
        ["B8", 10],
        ["C8", "30日销量低于此数为低；不再阻断走势判断"],
        ["A9", "增长门槛"],
        ["B9", 0.2],
        ["C9", "M1>(1+B9)*M2，最近两周优先"],
        ["A10", "稳定最大差幅"],
        ["B10", 0.25],
        ["C10", "三个窗口最大值不超过最小值*(1+B10)"],
        ["A11", "下降门槛"],
        ["B11", -0.2],
        ["C11", "M1<(1+B11)*M2，最近两周优先"],
        ["A12", "成交均价变化提示门槛"],
        ["B12", 0.1],
        ["C12", "最近7天与前7天成交均价变动绝对值达到此比例；达到时降低可信度"],
        ["A13", "高广告费率门槛"],
        ["B13", 0.25],
        ["C13", "30日广告费/30日销售额；不单独降低可信度"],
        ["A14", "补货取整倍数"],
        ["B14", 5],
        ["C14", "非负缺口向上取整至此正整数倍数"],
        ["A15", "广告投入变化提示门槛"],
        ["B15", 0.3],
        ["C15", "最近7天相对前7天变化达到此比例；前期为0而近期>0也提示"],
        ["A16", "预测类型"],
        ["B16", "M1 最近7日"],
        ["C16", "M2 第8—14日"],
        ["D16", "M3 第15—30日"],
        ["E16", "权重合计"],
        ["A17", "平稳型"],
        ["B17", 0.4],
        ["C17", 0.3],
        ["D17", 0.3],
        ["A18", "近期增强型"],
        ["B18", 0.6],
        ["C18", 0.3],
        ["D18", 0.1],
        ["A19", "近期减弱型"],
        ["A20", "广告变化最低金额"],
        ["B20", 10],
        ["C20", "最近两周广告费差额；与B15同时满足才降低可信度，单位为源文件币种。同工作簿共用，跨币种需人工确认适用性"],
        ["B19", 0.7],
        ["C19", 0.2],
        ["D19", 0.1]
    ];
    for (let index = 0; index < defaults.length; index++) {
        writeRuleValue(sheet, defaults[index][0], defaults[index][1]);
    }
    const documentation = [
        ["工作列", "结果或项目", "精确触发条件 / 计算公式", "维护方式"],
        ["销量窗口", "M1/M2/M3", "M1=7日/7*30；M2=(14日-7日)/7*30；M3=(30日-14日)/16*30", "三个窗口不重叠；先不取整"],
        ["系统建议月销", "经营状态优先", "链接停售、已删除或已删除-原因：0；冻结、未知链接状态：留空", "不改人工状态列"],
        ["销量状态", "数据异常", "任一销量非数字、缺失、负数或累计倒挂；停止预测", "优先级1"],
        ["销量状态", "无销量", "窗口有效且30日=0", "优先级2；月销0"],
        ["销量状态", "近期无销量", "7日=0且30日>0", "优先级3；减弱型；提示排查供给限制"],
        ["销量状态", "近期启动／恢复出单", "7日>0且14日=7日，即前一周为0", "优先级4；增强型；低可信度"],
        ["销量状态", "增长 / 近期反弹", "M1>(1+B9)*M2；若M1>(1+B9)*M3为增长，否则为反弹", "优先级6；增强型"],
        ["销量状态", "放量后回落 / 下降", "M1<(1+B11)*M2；若M2>(1+B9)*M3为回落，否则下降", "优先级7；减弱型"],
        ["销量状态", "增长后高位维持", "未命中此前，(M1+M2)/2>(1+B9)*M3", "优先级8；增强型"],
        ["销量状态", "下降后低位维持", "未命中此前，(M1+M2)/2<(1+B11)*M3", "优先级9；减弱型"],
        ["销量状态", "稳定", "排除零销量后优先判断，三个窗口都>0且MAX<=(1+B10)*MIN", "优先级5；平稳型"],
        ["销量状态", "波动观察", "其他有效窗口", "优先级10；平稳型"],
        ["趋势差异率", "百分比 / 空", "(M1-M3)/MAX(M3,1)；异常或销量<B8时为空", "只展示，不再乘预测值"],
        ["预测可信度", "低", "有效在售链接：销量<B8、无销量/近期无销量/启动/反弹/回落/波动，或FBA可售=0且30日有销量；均价明显变化、实质广告变化或季节品非稳定也为低", "低优先于中、高；最终月销保留低可信度提示色"],
        ["预测可信度", "中", "未命中低，但未同时满足全部高条件", "广告费率高或停补不单独降低等级"],
        ["预测可信度", "高", "同时满足稳定、销量>=B6、库存完整非负且FBA可售>0、两周成交均价及广告窗口可比且未明显变化", "运营规则等级，不是统计准确率"],
        ["预测可信度", "不可评估 / 不适用", "冻结、未知状态或数据异常为不可评估；停售和已删除为不适用", "不强行归为低"],
        ["系统建议月销", "平稳型", "场景估算=M1*B17+M2*C17+M3*D17", "稳定与波动观察"],
        ["系统建议月销", "增强型", "场景估算=M1*B18+M2*C18+M3*D18", "增长、反弹、启动、高位维持"],
        ["系统建议月销", "减弱型", "场景估算=M1*B19+M2*C19+M3*D19", "下降、回落、近期无销量、低位维持"],
        ["系统建议月销", "小销量平滑", "q=MIN(1,30日销量/B5)；ROUND(30日销量*(1-q)+场景估算*q,0)", "无销量0；不能由测试数据证明预测准确性"],
        ["链接状态", "手工维护", "留白=在售；兼容在售；停售、即将下架、已删除、已删除-原因、冻结", "未识别的非空值暂停预测"],
        ["产品属性", "常规品 / 夏季品 / 冬季品", "季节品非稳定时提示外推持续性；不额外乘系数，不猜月份", "列可省略，不新增列；未知非空值提示核对"],
        ["补货状态", "留白 / 停补", "停补仍保留需求和覆盖估计，但补货量恒为0", "兼容旧正常补货；清库存、停售按停补处理"],
        ["补货状态", "旧枚举 / 未知", "旧新品观察、暂缓补货暂停自动补货；其他未知非空值要求核对", "不自动改写人工单元格"],
        ["最终补货月销", "系统值 / 人工值", "数字默认等于系统建议；冻结/未知状态/待确认为空；停售删除为0", "直接写数字；下次成功同步、初始化或重算恢复为系统建议"],
        ["在库覆盖天数", "天数 / 空", "库存有效且最终月销>0：(FBA可售+预留)/最终月销*30", "停补保留覆盖估计；零需求留空，不表示零天"],
        ["含在途覆盖天数", "天数 / 空", "库存有效且最终月销>0：(FBA可售+预留+在途)/最终月销*30", "仅数量估计，不保证中途不断货"],
        ["建议补货量", "留白（不补货）", "停售、删除、即将下架、停补；或有效需求为0或库存已足够", "停补约束不能被人工月销绕过"],
        ["建议补货量", "空", "冻结、未知状态、待确认、预测缺失/非数字/负数、库存不完整/负数", "无法计算不伪装成0"],
        ["建议补货量", "正整数", "ROUNDUP(MAX(0,最终月销/30*B7-总库存)/B14,0)*B14", "沿用公司库存口径"],
        ["库存状态", "经营状态优先", "退出销售、冻结待处理、停补优先，不再催促已停补商品", "之后才判断缺货/紧张/需要补货/健康/偏高"],
        ["异常原因", "均价变化", "最近7日销售额/销量，与前7日销售额/销量比较；绝对变化>=B12", "不可比不强行判高；异常仅显示最高优先级风险"],
        ["异常原因", "广告变化", "最近7日广告费对前7日广告费；绝对变化>=B15；零基期单独处理", "还须两周广告差额>=B20；数据缺失不默认为稳定"],
        ["广告状态", "高广告费", "30日广告费率>=B13", "成本提示，不单独降低销量可信度"],
        ["广告状态", "上涨 / 下降 / 正常", "最近7日与前7日广告费比较；变化超过B15才分类涨跌", "无广告=完整有效且30日费0；无效=数据不足"],
        ["30日实际成交均价", "金额 / 空", "有效30日销售额/30日销量；销量<=0或缺失时为空", "不直接用于销量倍数"],
        ["数据日期", "本次 / 上次", "同日修正不滚动；新日期按字段滚动；旧日期跳过", "上传预览仅检查源数据，写前快照在确认执行时采集"],
        ["恢复", "值与公式", "保留数字/文本及旧公式快照、前置条件、回读差异和条件恢复协议", "评分条件格式与人工列不纳入初始化覆盖"],
        ["公式升级", "逐个Listing子表初始化", "R2规则不能搭配旧公式；初始化重装当前子表公式", "同工作簿其他Listing需分别初始化后再同步"],
        ["规则表保护", "不设置保护", "所有参数可编辑；A2为计算模型标识，不可改；B4人工升级版本", "不设置工作表保护"]
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
        sheet.Range("B15").NumberFormat = "0.0%";
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
    if (displayText(scalarValue(sheet.Range("A2"))) !== RULE_MODEL) {
        throw new Error("规则计算模型过旧，请先初始化R2三窗口预测规则与公式");
    }
    const version = displayText(scalarValue(sheet.Range("B4")));
    const low = numericRule(sheet, "B5", "低销量平滑参数");
    const high = numericRule(sheet, "B6", "高可信度最低销量");
    const targetDays = numericRule(sheet, "B7", "目标覆盖天数");
    const trendMinimum = numericRule(sheet, "B8", "低可信度销量门槛");
    const growth = numericRule(sheet, "B9", "增长门槛");
    const fastGrowth = numericRule(sheet, "B10", "稳定最大差幅");
    const decline = numericRule(sheet, "B11", "下降门槛");
    const fastDecline = numericRule(sheet, "B12", "成交均价变化提示门槛");
    const highAdRate = numericRule(sheet, "B13", "高广告费率门槛");
    const replenishmentMultiple = numericRule(sheet, "B14", "补货取整倍数");
    const adChange = numericRule(sheet, "B15", "广告投入变化提示门槛");
    const adAmount = numericRule(sheet, "B20", "广告变化最低金额");
    if (adAmount < 0) throw new Error("广告变化最低金额不能为负数");
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
    if (low <= 0 || high < trendMinimum || targetDays <= 0 || trendMinimum <= 0) {
        throw new Error("销量分界或目标覆盖天数配置无效");
    }
    if (growth <= 0 || fastGrowth <= 0 || decline >= 0 || decline <= -1 || fastDecline <= 0 || adChange <= 0 || adChange >= 1) {
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
        model: RULE_MODEL,
        adChangeMinimumAmount: adAmount,
        smoothingSales: low,
        stableSpread: fastGrowth,
        priceChangeThreshold: fastDecline,
        adChangeThreshold: adChange,
        weights: weights
    };
}

function formulaCell(columns, field, row) {
    return columns[field].columnLetter + row;
}

function ruleReference(address) {
    return "'" + RULE_SHEET_NAME + "'!$" + address.charAt(0) + "$" + address.slice(1);
}

// Pure calculation: no worksheet writes, no spreadsheet formula evaluation.
function calculateListingForecast(v, rules) {
    const text = key => displayText(v[key]);
    const valid = value => typeof value === "number" && Number.isFinite(value);
    const sales = [v.sales_7d,v.sales_14d,v.sales_30d];
    const [s7,s14,s30] = sales;
    const link=text("link_status"), attr=text("product_attribute"), replenish=text("replenishment_status");
    const closed=["停售","已删除"].indexOf(link)>=0 || link.indexOf("已删除-")===0;
    const frozen=link==="冻结";
    const invalid=!sales.every(valid)||sales.some(x=>x<0)||s7>s14||s14>s30;
    const unknownLink=!closed&&["","在售","即将下架","冻结"].indexOf(link)<0;
    const badAttr=["","常规品","夏季品","冬季品"].indexOf(attr)<0;
    const pending=["","正常补货","停补","清库存","停售"].indexOf(replenish)<0;
    const m=invalid?[]:[s7/7*30,(s14-s7)/7*30,(s30-s14)/16*30];
    const [m1,m2,m3]=m;
    const stable=!invalid&&Math.min(...m)>0&&Math.max(...m)<=(1+rules.stableSpread)*Math.min(...m);
    let state="波动观察";
    if(invalid)state="数据异常";
    else if(s30===0)state="无销量";
    else if(m1===0)state="近期无销量";
    else if(m2===0)state="近期启动／恢复出单";
    else if(stable)state="稳定";
    else if(m1>(1+rules.growthThreshold)*m2)state=m1>(1+rules.growthThreshold)*m3?"增长":"近期反弹";
    else if(m1<(1+rules.declineThreshold)*m2)state=m2>(1+rules.growthThreshold)*m3?"放量后回落":"下降";
    else if((m1+m2)/2>(1+rules.growthThreshold)*m3)state="增长后高位维持";
    else if((m1+m2)/2<(1+rules.declineThreshold)*m3)state="下降后低位维持";
    const enhanced=["增长","近期反弹","近期启动／恢复出单","增长后高位维持"].indexOf(state)>=0;
    const reduced=["下降","放量后回落","近期无销量","下降后低位维持"].indexOf(state)>=0;
    const weights=rules.weights[enhanced?1:reduced?2:0];
    const q=invalid?0:Math.min(1,s30/rules.smoothingSales);
    const estimate=invalid?null:m.reduce((sum,x,i)=>sum+x*weights[i],0);
    const unavailable=invalid||frozen||unknownLink||badAttr;
    const monthly=closed?0:unavailable?"":Math.round(s30*(1-q)+estimate*q);
    const stock=[v.fba_available,v.reserved,v.inbound];
    const stockValid=stock.every(x=>valid(x)&&x>=0);
    const priceValid=[v.revenue_7d,v.revenue_14d].every(valid)&&v.revenue_7d>=0&&v.revenue_14d>v.revenue_7d&&s7>0&&s14>s7;
    const priceShift=priceValid&&Math.abs((v.revenue_7d/s7)/((v.revenue_14d-v.revenue_7d)/(s14-s7))-1)>=rules.priceChangeThreshold;
    const ads=[v.ad_spend_7d,v.ad_spend_14d,v.ad_spend_30d];
    const adValid=ads.every(x=>valid(x)&&x>=0)&&ads[0]<=ads[1]&&ads[1]<=ads[2];
    const prior=ads[1]-ads[0];
    const adShift=adValid&&(prior===0?ads[0]>0:Math.abs(ads[0]/prior-1)>=rules.adChangeThreshold)&&Math.abs(ads[0]-prior)>=rules.adChangeMinimumAmount;
    const risks=[];
    if(frozen)risks.push("链接冻结，暂停预测");
    else if(unknownLink)risks.push("链接状态未识别");
    else if(badAttr)risks.push("产品属性未识别");
    else if(invalid)risks.push("销量缺失、负数或累计倒挂");
    else {
        if(v.fba_available===0&&s30>0)risks.push("可售为0，可能受缺货影响");
        if(s30===0)risks.push("近30天无销量");
        else if(s7===0)risks.push("近7天无销量");
        if(s30<rules.trendMinimumSales)risks.push("样本少：30天仅"+s30+"单");
        if(state==="近期启动／恢复出单")risks.push("刚恢复出单，持续性待确认");
        if(priceShift)risks.push("近期成交均价明显变化");
        if(adShift)risks.push("近期广告投入明显变化");
        if(["夏季品","冬季品"].indexOf(attr)>=0&&state!=="稳定")risks.push("季节品走势变化");
        if(state==="放量后回落")risks.push("前期放量后回落");
        if(state==="近期反弹")risks.push("近期反弹，持续性待确认");
        if(state==="波动观察")risks.push("三窗口走势不一致");
    }
    const confidence=closed?"不适用":unavailable?"不可评估":risks.length?"低":
        stable&&s30>=rules.highSalesBoundary&&stockValid&&v.fba_available>0&&priceValid&&adValid?"高":"中";
    const method=closed?"退出销售归零":unavailable?"暂停预测":s30===0?"无销量归零":
        (enhanced?"近期增强 ":reduced?"近期减弱 ":"平稳加权 ")+weights.map(x=>Number((x*100).toFixed(6))).join("/")+(q<1?"＋低样本平滑":"");
    return {values:{sales_status:state,monthly_sales_method:method,forecast_confidence:confidence,
        exception_reason:closed?"":risks[0]||"",system_monthly_sales:monthly,
        final_monthly_sales:closed?0:unavailable||pending?"":monthly},
        detail:{windows:m,weights:weights,response:q,estimate:estimate,monthly:monthly,risks:closed?[]:risks}};
}

function forecastInputs(sheet, columns, row) {
    const fields=["sales_7d","sales_14d","sales_30d","revenue_7d","revenue_14d","ad_spend_7d","ad_spend_14d","ad_spend_30d","fba_available","reserved","inbound","link_status","product_attribute","replenishment_status"];
    const values={};
    fields.forEach(field=>{values[field]=columns[field]?scalarValue(sheet.Range(formulaCell(columns,field,row))):"";});
    return values;
}

function writeForecastValues(sheet, columns, row, rules) {
    const result=calculateListingForecast(forecastInputs(sheet,columns,row),rules);
    Object.keys(result.values).forEach(field=>{
        const range=sheet.Range(formulaCell(columns,field,row));
        range.Value2=result.values[field];
        range.NumberFormat=["system_monthly_sales","final_monthly_sales"].indexOf(field)>=0?"0":"@";
        if(field==="exception_reason"||field==="monthly_sales_method")range.WrapText=true;
    });
}

function listingFormulas(columns, row) {
    const c = field => formulaCell(columns, field, row);
    const q = text => '"' + text.replace(/"/g, '""') + '"';
    const iff = (test, yes, no) => "IF(" + test + "," + yes + "," + no + ")";
    const choose = (cases, fallback) => cases.reduceRight((result, entry) => iff(entry[0], entry[1], result), fallback);
    const or = (...tests) => "OR(" + tests.join(",") + ")";
    const and = (...tests) => "AND(" + tests.join(",") + ")";
    const eq = (cell, text) => cell + "=" + q(text);
    const oneOf = (cell, texts) => or(...texts.map(text => eq(cell, text)));
    const r = ruleReference;
    const s7 = c("sales_7d"), s14 = c("sales_14d"), s30 = c("sales_30d");
    const m1 = "(" + s7 + "/7*30)", m2 = "((" + s14 + "-" + s7 + ")/7*30)";
    const m3 = "((" + s30 + "-" + s14 + ")/16*30)";
    const invalid = or("COUNT(" + [s7,s14,s30].join(",") + ")<>3", s7+"<0", s14+"<0", s30+"<0", s7+">"+s14, s14+">"+s30);
    const status = c("sales_status"), confidence = c("forecast_confidence");
    const link = "TRIM(" + c("link_status") + ")", replenishment = "TRIM(" + c("replenishment_status") + ")";
    const attribute = columns.product_attribute ? "TRIM(" + c("product_attribute") + ")" : q("");
    const seasonal = oneOf(attribute, ["夏季品", "冬季品"]);
    const badAttribute = "NOT(" + oneOf(attribute, ["", "常规品", "夏季品", "冬季品"]) + ")";
    const closed = or(eq(link,"停售"),eq(link,"已删除"),"LEFT("+link+",4)="+q("已删除-"));
    const frozen = eq(link,"冻结");
    const unknownLink = "NOT(" + or(closed,oneOf(link,["", "在售", "即将下架", "冻结"])) + ")";
    // 旧补货枚举安全兼容；不会把已停补、清库存的旧行恢复成自动补货。
    const stop = or(closed,eq(link,"即将下架"),oneOf(replenishment,["停补","清库存","停售"]));
    const hold = oneOf(replenishment,["新品观察","暂缓补货"]);
    const unknownReplenishment = "NOT("+oneOf(replenishment,["","正常补货","停补","清库存","停售","新品观察","暂缓补货"])+")";
    const supply = ["fba_available","reserved","inbound"].map(c);
    const badStock = or("COUNT("+supply.join(",")+")<>3",...supply.map(x=>x+"<0"));
    const fba = c("fba_available"), final = c("final_monthly_sales"), system = c("system_monthly_sales");
    // 对比两个不重叠的7天窗口。缺失/退款导致负差额时不伪造为零或稳定。
    const rev7=c("revenue_7d"), rev14=c("revenue_14d"), rev30=c("revenue_30d");
    const ad7=c("ad_spend_7d"), ad14=c("ad_spend_14d"), ad30=c("ad_spend_30d");
    const adValid = and("COUNT("+[ad7,ad14,ad30].join(",")+")=3",ad7+">=0",ad14+">="+ad7,ad30+">="+ad14);
    const unavailable = or(invalid,frozen,unknownLink,badAttribute);
    const pending = or(unavailable,unknownReplenishment,hold);
    const validFinal = and("ISNUMBER("+final+")",final+">=0");
    const unavailableFinal = or(pending,"NOT("+validFinal+")",badStock);
    const inStock = "SUM("+fba+","+c("reserved")+")";
    const totalStock = "SUM("+supply.join(",")+")";
    const stockInvalid = or("COUNT("+fba+","+c("reserved")+")<>2",fba+"<0",c("reserved")+"<0");
    const coverage = (amount,bad) => iff(or(unavailable,"NOT("+validFinal+")",final+"=0",bad),q(""),amount+"/"+final+"*30");
    const adRate = c("ad_rate_30d");
    const adState = choose([
        ["NOT("+adValid+")",q("数据不足")], [ad30+"=0",q("无广告")],
        [eq(adRate,""),q("数据不足")], [adRate+">="+r("B13"),q("高广告费")],
        [ad14+"="+ad7,iff(ad7+">0",q("广告费上涨"),q("正常"))],
        [ad7+">(1+"+r("B15")+")*("+ad14+"-"+ad7+")",q("广告费上涨")],
        [ad7+"<(1-"+r("B15")+")*("+ad14+"-"+ad7+")",q("广告费下降")]
    ],q("正常"));
    const inventoryState = choose([
        [closed,q("退出销售")], [frozen,q("冻结待处理")], [stop,q("停补")],
        [unavailableFinal,q("数据待核对")], [final+"=0",q("无预测需求")],
        [inStock+"=0",q("缺货")], [c("stock_coverage_days")+"<30",q("库存紧张")],
        [c("total_coverage_days")+"<"+r("B7"),q("需要补货")],
        [c("total_coverage_days")+"<="+r("B7")+"+30",q("库存健康")]
    ],q("库存偏高"));
    return {
        ad_rate_30d: "="+iff(or("COUNT("+ad30+","+rev30+")<>2",ad30+"<0",rev30+"<=0"),q(""),ad30+"/"+rev30),
        average_price_30d: "="+iff(or("COUNT("+rev30+","+s30+")<>2",rev30+"<0",s30+"<=0"),q(""),rev30+"/"+s30),
        trend_difference_rate: "="+iff(invalid,q(""),iff(s30+"<"+r("B8"),q(""),"("+m1+"-"+m3+")/MAX("+m3+",1)")),
        stock_coverage_days: "="+coverage(inStock,stockInvalid),
        total_coverage_days: "="+coverage(totalStock,badStock),
        suggested_replenishment: "="+choose([[closed,q("")],[frozen,q("")],[stop,q("")],[unavailableFinal,q("")],[final+"=0",q("")],[final+"/30*"+r("B7")+"<="+totalStock,q("")]],"ROUNDUP(("+final+"/30*"+r("B7")+"-"+totalStock+")/"+r("B14")+",0)*"+r("B14")),
        inventory_status: "="+inventoryState,
        ad_status: "="+adState
    };
}

function setListingFormula(sheet, columns, row, field, formula, overwrite) {
    const range = sheet.Range(formulaCell(columns, field, row));
    const currentFormula = scalarFormula(range);
    if (!overwrite && currentFormula !== "") {
        return "formula";
    }
    // 仅安装仍需即时联动的下游公式；预测及最终月销由writeForecastValues写值。
    range.Formula = formula;
    if (field === "monthly_sales_method" || field === "exception_reason") {
        try { range.WrapText = true; } catch (error) { /* 显示设置不影响计算 */ }
    }
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
    const rules = validateRuleConfig();
    let formulaRows = 0;
    let manualOverrideRows = 0;
    for (let rowIndex = 0; rowIndex < rows.length; rowIndex++) {
        const row = rows[rowIndex];
        writeForecastValues(sheet, columns, row, rules);
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
    let formulaMismatchRows = 0;
    for (let row = headerRow + 1; row <= lastRow; row++) {
        if (normalizeMsku(scalarValue(sheet.Range(formulaCell(columns, "msku", row)))) === "") {
            continue;
        }
        let complete = true;
        let hasFormulaError = false;
        let hasMismatch = false;
        const expected = listingFormulas(columns, row);
        for (const field of VALUE_FIELDS) {
            const cell=sheet.Range(formulaCell(columns,field,row));
            if(scalarFormula(cell)!=="") hasMismatch=true;
            if(isSpreadsheetError(scalarValue(cell))) hasFormulaError=true;
        }
        const canonical = value => String(value).replace(/'ListingRules'/gi, "ListingRules").replace(/\s/g, "").toUpperCase();
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
            if (formula !== "" && canonical(formula) !== canonical(expected[field])) {
                hasMismatch = true;
            }
        }
        if (complete) {
            formulaRows++;
        }
        if (hasFormulaError) {
            formulaErrorRows++;
        }
        if (hasMismatch) formulaMismatchRows++;
    }
    return {
        formulaRows: formulaRows,
        manualOverrideRows: manualOverrideRows,
        formulaErrorRows: formulaErrorRows,
        formulaMismatchRows: formulaMismatchRows
    };
}

function listingStoredValue(sheet, columns, row, field) {
    const range = sheet.Range(columns[field].columnLetter + row);
    const formula = (FORMULA_FIELDS.indexOf(field) >= 0 || VALUE_FIELDS.indexOf(field) >= 0) ? scalarFormula(range) : "";
    if (formula !== "") {
        return { kind: "formula", formula: formula };
    }
    return scalarValue(range);
}

function writeStoredValue(range, field, value) {
    if (
        (FORMULA_FIELDS.indexOf(field) >= 0 || VALUE_FIELDS.indexOf(field) >= 0) &&
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
    && action !== "forecast_plan" && action !== "forecast_apply"
) {
    throw new Error("不支持的Listing操作：" + action);
}
if (action === "discover") {
    const discoveryResult = {
        success: true,
        schemaVersion: SCHEMA_VERSION,
        sheets: workbookSheets()
    };
    return returnResult(discoveryResult);
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
const located = locateHeaders(targetSheet, action === "setup_rules");
const headerRow = located.row;
const columns = located.columns;
if (action === "setup_rules") {
    // 升级只在已用区域右侧追加可读业务列，绝不插入或移动人工列。
    if (!columns.monthly_sales_method) {
        const used = targetSheet.UsedRange;
        const nextColumn = Number(used.Column) + Number(used.Columns.Count);
        if (!Number.isInteger(nextColumn) || nextColumn < 1 || nextColumn > 104) {
            throw new Error("无法安全追加月销计算方案，请在CZ列以内的空列添加该表头后重试");
        }
        const letter = columnNumberToName(nextColumn);
        targetSheet.Range(letter + headerRow).Value2 = "月销计算方案";
        columns.monthly_sales_method = {columnNumber: nextColumn, columnLetter: letter,
            text: "月销计算方案", normalized: "月销计算方案"};
    }
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
    return returnResult(setupResult);
}
let rules = { valid: false, version: "" };
try {
    rules = validateRuleConfig();
} catch (ruleError) {
    if (["validate","snapshot","sync","forecast_plan","forecast_apply"].indexOf(action)>=0) {
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
if ((action === "validate" || action === "snapshot" || action === "sync") && formulaCounts.formulaMismatchRows > 0) {
    throw new Error("当前Listing子表仍有旧版或自定义公式，请初始化当前子表的R2标准公式后重试");
}
if ((action === "snapshot" || action === "sync") && formulaCounts.formulaErrorRows > 0) {
    throw new Error("Listing公式存在计算错误，请先修复再回填");
}
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
    return returnResult(baseResult);
}

if (action === "forecast_plan") {
    const end=lastUsedRow(targetSheet,headerRow);
    const start=Math.max(headerRow+1,Number(argv.start_row)||headerRow+1);
    const rowMap=buildRowsByMsku(targetSheet,columns,headerRow,end);
    const entries=[];
    let cursor=start;
    for(;cursor<=end&&entries.length<MAX_ITEMS;cursor++) {
        const msku=normalizeMsku(scalarValue(targetSheet.Range(formulaCell(columns,"msku",cursor))));
        if(!msku)continue;
        if(rowMap[msku].length!==1)throw new Error("重复MSKU，无法重算："+msku);
        const inputs=forecastInputs(targetSheet,columns,cursor);
        const calculation=calculateListingForecast(inputs,rules);
        entries.push({msku:msku,inputs:inputs,detail:calculation.detail,values:calculation.values});
    }
    return returnResult(Object.assign(baseResult,{entries:entries,nextRow:cursor<=end?cursor:null,
        ruleSignature:JSON.stringify(rules),snapshots:collectListingSnapshots(targetSheet,columns,headerRow,entries)}));
}
if (action === "forecast_apply") {
    const entries=Array.isArray(argv.entries)?argv.entries:[];
    if(entries.length>MAX_ITEMS)throw new Error("单次重算行数超限");
    if(displayText(argv.rule_signature)!==JSON.stringify(rules))throw new Error("规则在重算前发生变化，请重新计算");
    verifyListingPreconditions(targetSheet,columns,headerRow,argv.preconditions||[]);
    const rowMap=buildRowsByMsku(targetSheet,columns,headerRow,lastUsedRow(targetSheet,headerRow));
    const rows=entries.map(entry=>{
        const matching=rowMap[normalizeMsku(entry.msku)]||[];
        if(matching.length!==1)throw new Error("重算目标不再唯一");
        const currentInputs=forecastInputs(targetSheet,columns,matching[0]);
        // HTTP/JSON往返可能改变对象键顺序；比较字段和值，不能比较序列化文本。
        if(!entry.inputs || Object.keys(currentInputs).length!==Object.keys(entry.inputs).length ||
            Object.keys(currentInputs).some(key=>!hasOwn(entry.inputs,key)||!sameComparable(currentInputs[key],entry.inputs[key]))) {
            throw new Error("重算输入已变化，请重试");
        }
        return matching[0];
    });
    // 仅输出列：不写日期、历史值或上传原始指标。
    installListingFormulas(targetSheet,columns,rows,true);
    return returnResult(Object.assign(baseResult,{updated:rows.length}));
}

if (action === "snapshot") {
    const snapshotResult = Object.assign(baseResult, {
        snapshots: collectListingSnapshots(
            targetSheet, columns, headerRow, items
        )
    });
    return returnResult(snapshotResult);
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
        ? inspectListingChanges(
            targetSheet, columns, headerRow, changes, direction, indexOffset
        )
        : applyListingChanges(
            targetSheet, columns, headerRow, changes, direction, indexOffset
        );
    const response = Object.assign(baseResult, changeResult);
    return returnResult(response);
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
return returnResult(result);
