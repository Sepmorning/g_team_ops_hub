import html
import io
import zipfile

import pytest

from anda_tracker.errors import ConfigurationError
from anda_tracker.listing import (
    SOURCE_HEADERS,
    calculate_system_monthly_sales,
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
    assert row.system_monthly_sales == 30


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
    assert row.system_monthly_sales == 0
    payload = row.to_payload()
    assert "fba_available" not in payload
    assert payload["sales_7d"] == 0


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


def test_system_monthly_sales_and_filename_date_rules():
    assert calculate_system_monthly_sales(0, 0) == 0
    assert calculate_system_monthly_sales(5, 0) == 20
    assert calculate_system_monthly_sales(0, 31) == 31
    assert calculate_system_monthly_sales(7, 31) == 30
    assert calculate_system_monthly_sales(None, 31) is None
    assert infer_listing_data_date("Listing20260726-939972.xlsx") == "2026-07-26"
