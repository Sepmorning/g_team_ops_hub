import html
import io
import zipfile

import pytest

from g_team_ops.errors import ConfigurationError, ResponseError
from g_team_ops.listing import (
    ListingAirScriptClient,
    SOURCE_HEADERS,
    TARGET_HEADERS,
    infer_listing_data_date,
    parse_listing_export,
)


def build_listing_xlsx(headers, rows):
    def column_name(number):
        result = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            result = chr(65 + remainder) + result
        return result

    xml_rows = []
    for row_number, values in enumerate([headers, *rows], 1):
        cells = []
        for column_number, value in enumerate(values, 1):
            reference = f"{column_name(column_number)}{row_number}"
            if value is None:
                continue
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{reference}"><v>{value}</v></c>')
            else:
                cells.append(
                    f'<c r="{reference}" t="inlineStr"><is><t>'
                    f"{html.escape(str(value))}</t></is></c>"
                )
        xml_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')

    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Listing导出" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return output.getvalue()


def source_row(**overrides):
    values = {
        "MSKU": "SKU-1",
        "ASIN": "B012345678",
        "币种": "USD",
        "价格": 19.99,
        "FBA可售": 20,
        "FBA待调仓": 2,
        "FBA预留": 3,
        "FBA计划入库": 4,
        "FBA标发在途": 5,
        "FBA入库中": 99,
        "FBA不可售": 88,
        "7日销量": 7,
        "14日销量": 15,
        "30日销量": 31,
        "昨日广告费": 2.5,
        "7日广告费": 12.5,
        "14日广告费": 24.5,
        "30日广告费": 48.5,
        "7日销售额": 140,
        "14日销售额": 300,
        "30日销售额": 620,
        "Rating总数": 123,
        "评分": 4.4,
        "未来新增列": "应忽略",
    }
    values.update(overrides)
    return values


def test_listing_export_matches_headers_by_name_and_ignores_new_columns():
    headers = [
        "未来新增列",
        "评分",
        "MSKU",
        "30日销量",
        "FBA标发在途",
        "ASIN",
        "Rating总数",
        "币种",
        "价格",
        "FBA可售",
        "FBA待调仓",
        "FBA预留",
        "FBA计划入库",
        "FBA入库中",
        "FBA不可售",
        "7日销量",
        "14日销量",
        "昨日广告费",
        "7日广告费",
        "14日广告费",
        "30日广告费",
        "7日销售额",
        "14日销售额",
        "30日销售额",
    ]
    values = source_row()
    parsed = parse_listing_export(
        build_listing_xlsx(headers, [[values[item] for item in headers]])
    )

    assert parsed.sheet_name == "Listing导出"
    assert parsed.ignored_headers == ("未来新增列",)
    assert len(parsed.rows) == 1
    row = parsed.rows[0]
    assert row.review_count == 123
    assert row.rating == 4.4
    assert row.reserved == 5
    assert row.inbound == 9
    assert row.fba_available == 20
    assert row.price == 19.99
    assert row.ad_spend_30d == 48.5
    assert row.revenue_30d == 620
    assert row.to_payload()["rating_review"] == "4.4/123"
    assert "system_monthly_sales" not in row.to_payload()


def test_listing_export_preserves_blank_inventory_but_keeps_zero_sales():
    headers = list(SOURCE_HEADERS)
    values = source_row(
        **{
            "MSKU": "FBM-ONLY",
            "FBA可售": None,
            "FBA待调仓": None,
            "FBA预留": None,
            "FBA计划入库": None,
            "FBA标发在途": None,
            "Rating总数": None,
            "昨日广告费": None,
            "7日广告费": None,
            "14日广告费": None,
            "30日广告费": None,
            "7日销售额": None,
            "14日销售额": None,
            "30日销售额": None,
            "7日销量": 0,
            "14日销量": 0,
            "30日销量": 0,
        }
    )
    parsed = parse_listing_export(
        build_listing_xlsx(headers, [[values[item] for item in headers]])
    )
    row = parsed.rows[0]
    assert row.fba_available is None
    assert row.reserved is None
    assert row.inbound is None
    assert row.review_count is None
    assert row.sales_7d == 0
    payload = row.to_payload()
    assert "fba_available" not in payload
    assert payload["sales_7d"] == 0
    assert payload["yesterday_ad_spend"] == ""
    assert payload["ad_spend_30d"] == ""
    assert payload["revenue_30d"] == ""
    assert "discount_price" not in payload


def test_optional_discount_price_is_parsed_and_blank_clears_old_value():
    # 领星列顺序可能变化；倒序标准列并把优惠价插入中间验证按表头定位。
    headers = list(reversed(SOURCE_HEADERS))
    headers.insert(4, "优惠价")
    priced = source_row(**{"优惠价": 15.99})
    blank = source_row(**{"MSKU": "SKU-2", "优惠价": None})
    parsed = parse_listing_export(
        build_listing_xlsx(
            headers,
            [
                [priced[item] for item in headers],
                [blank[item] for item in headers],
            ],
        )
    )

    assert parsed.has_discount_price is True
    assert "优惠价" not in parsed.ignored_headers
    assert parsed.rows[0].discount_price == 15.99
    assert parsed.rows[0].to_payload()["discount_price"] == 15.99
    assert parsed.rows[1].discount_price is None
    assert parsed.rows[1].to_payload()["discount_price"] == ""


def test_duplicate_optional_discount_header_is_rejected():
    headers = [*SOURCE_HEADERS, "优惠价", " 优惠价 "]
    values = source_row(**{"优惠价": 15.99, " 优惠价 ": 14.99})
    with pytest.raises(ConfigurationError, match="优惠价"):
        parse_listing_export(
            build_listing_xlsx(headers, [[values[item] for item in headers]])
        )


def test_listing_export_rejects_missing_stable_header():
    headers = [item for item in SOURCE_HEADERS if item != "Rating总数"]
    values = source_row()
    with pytest.raises(ConfigurationError, match="Rating总数"):
        parse_listing_export(
            build_listing_xlsx(headers, [[values[item] for item in headers]])
        )


def test_duplicate_msku_rows_are_reported_and_not_sent():
    headers = list(SOURCE_HEADERS)
    first = source_row()
    second = source_row(**{"ASIN": "B099999999"})
    parsed = parse_listing_export(
        build_listing_xlsx(
            headers,
            [
                [first[item] for item in headers],
                [second[item] for item in headers],
                [
                    source_row(**{"MSKU": "SKU-2"})[item]
                    for item in headers
                ],
            ],
        )
    )
    assert parsed.duplicate_mskus == ("SKU-1",)
    assert [row.msku for row in parsed.rows] == ["SKU-2"]


def test_invalid_cumulative_sales_are_imported_with_warning_for_sheet_review():
    headers = list(SOURCE_HEADERS)
    values = source_row(**{"7日销量": 12, "14日销量": 10, "30日销量": 31})
    parsed = parse_listing_export(
        build_listing_xlsx(headers, [[values[item] for item in headers]])
    )

    assert parsed.rows[0].sales_7d == 12
    assert parsed.rows[0].source_warning == "7日销量大于14日销量"
    assert parsed.data_warnings == (
        "第2行 SKU-1：7日销量大于14日销量，共享表将标记数据异常",
    )


def test_blank_sales_window_clears_stale_metric_and_marks_data_incomplete():
    headers = list(SOURCE_HEADERS)
    values = source_row(**{"7日销量": None})
    parsed = parse_listing_export(
        build_listing_xlsx(headers, [[values[item] for item in headers]])
    )

    assert parsed.rows[0].source_warning == "销量窗口数据不完整"
    assert parsed.rows[0].to_payload()["sales_7d"] == ""


def test_filename_date_rule():
    assert infer_listing_data_date("Listing20260726-939972.xlsx") == "2026-07-26"


def test_listing_binding_rejects_formula_errors_before_import():
    with pytest.raises(ResponseError, match="公式计算错误"):
        ListingAirScriptClient._binding_from_result(
            {
                "sheetName": "纯粹-美国",
                "headerRow": 1,
                "columns": {header: "A" for header in TARGET_HEADERS},
                "rules": {"valid": True, "version": "R1.0"},
                "formulaRows": 32,
                "manualOverrideRows": 0,
                "formulaErrorRows": 32,
            }
        )
