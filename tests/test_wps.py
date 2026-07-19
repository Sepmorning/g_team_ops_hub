from urllib.parse import parse_qs, urlparse

import pytest

from anda_tracker.errors import ConfigurationError, ResponseError
from anda_tracker.models import QueryStatus, TrackingResult
from anda_tracker.wps import (
    REQUIRED_US_HEADERS,
    ROUTE_HEADER,
    WPS_SCOPES,
    WPS_WRITE_BATCH_SIZE,
    WpsClient,
    WpsCredentials,
    WpsSheetBinding,
    WpsTokens,
    excel_column_to_index,
    index_to_excel_column,
    locate_headers,
    parse_share_file_id,
)


def test_share_link_validation_and_file_id_extraction():
    assert parse_share_file_id("https://www.kdocs.cn/l/cq2hhdLDWmWF") == "cq2hhdLDWmWF"
    with pytest.raises(ConfigurationError):
        parse_share_file_id("https://example.com/not-a-wps-sheet")


def test_excel_column_conversion_and_validation():
    assert excel_column_to_index("E") == 4
    assert excel_column_to_index(" y ") == 24
    assert index_to_excel_column(4) == "E"
    assert index_to_excel_column(24) == "Y"
    with pytest.raises(ConfigurationError):
        excel_column_to_index("E5")


def make_header_cells():
    names = ["店铺", "FBA号", *REQUIRED_US_HEADERS, "人工备注"]
    return [
        {"row_from": 0, "col_from": index * 2, "cell_text": name}
        for index, name in enumerate(names)
    ]


def test_header_lookup_does_not_depend_on_fixed_column_positions():
    header_map, fba_col = locate_headers(make_header_cells())
    assert fba_col == 2
    assert header_map[ROUTE_HEADER] == (1 + len(REQUIRED_US_HEADERS)) * 2


def test_missing_required_header_stops_sync():
    cells = [item for item in make_header_cells() if item["cell_text"] != "预计接收日期"]
    with pytest.raises(ResponseError, match="预计接收日期"):
        locate_headers(cells)


def test_authorization_url_requests_only_approved_user_scopes():
    client = WpsClient(
        WpsCredentials(
            "appid", "secret", "https://www.kdocs.cn/l/file123", fba_col=4, route_col=10
        ),
        tokens=WpsTokens("access", "refresh", 9999999999),
    )
    query = parse_qs(urlparse(client.authorization_url("state-x")).query)
    assert query["scope"][0].split(",") == list(WPS_SCOPES)
    assert query["state"] == ["state-x"]


def test_validation_uses_sheet_permission_without_file_meta_entitlement():
    client = WpsClient(
        WpsCredentials(
            "appid", "secret", "https://www.kdocs.cn/l/file123", fba_col=4, route_col=10
        ),
        tokens=WpsTokens("access", "refresh", 9999999999),
    )
    calls = []

    def fake_request(method, path, **_kwargs):
        calls.append((method, path))
        return {
            "code": 0,
            "data": {
                "sheets": [
                    {
                        "sheet_id": 9,
                        "name": "US-FBA",
                        "max_row": 100,
                        "max_col": 20,
                        "active_area": {
                            "row_from": 0,
                            "row_to": 12,
                            "col_from": 0,
                            "col_to": 80,
                        },
                    }
                ]
            },
        }

    client._api_request = fake_request
    binding = client.validate_and_bind()
    assert binding.worksheet_id == 9
    assert binding.max_row == 80
    assert binding.max_col == 12
    assert binding.fba_col == 4
    assert binding.route_col == 10
    assert calls == [("GET", "/sheets/file123/worksheets")]


def test_find_cells_uses_valid_non_empty_filter_and_one_based_page():
    client = WpsClient(
        WpsCredentials("appid", "secret", "https://www.kdocs.cn/l/file123"),
        tokens=WpsTokens("access", "refresh", 9999999999),
    )
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update({"method": method, "path": path, "payload": kwargs["payload"]})
        return {"code": 0, "data": {"range_data": [], "merge_range_data": []}}

    client._api_request = fake_request
    client._find_cells(
        WpsSheetBinding("file123", 9, "US-FBA", 80, 12),
        row_from=0,
        row_to=0,
        col_from=0,
        col_to=12,
        search=[{"col": 5, "value": ["是否完成"]}],
        option_cols=list(range(13)),
    )
    payload = captured["payload"]
    assert payload["filter"] == {"search": [{"col": 5, "value": ["是否完成"]}]}
    assert payload["page"] == {"page": 1, "page_size": 50}


class FakeSyncClient:
    def __init__(self, cells):
        self.cells = cells
        self.writes = []
        self.find_calls = []

    def read_header_map(self, _binding):
        return ({ROUTE_HEADER: 7}, 3)

    def _find_cells(self, *_args, **kwargs):
        self.find_calls.append(kwargs)
        return self.cells

    def _api_request(self, method, path, *, payload=None, params=None, retry_auth=True):
        self.writes.append((method, path, payload))
        return {"code": 0}


def tracking(fba, event, status=QueryStatus.SUCCESS):
    return TrackingResult(
        fba=fba,
        status=status,
        carrier="安达",
        latest_time="2026-07-19 10:00",
        latest_event=event,
    )


def test_sync_updates_only_changed_successful_routes_and_preserves_other_columns():
    cells = [
        {"row_from": 4, "col_from": 3, "cell_text": "FBA111"},
        {"row_from": 4, "col_from": 7, "cell_text": "旧路由"},
        {"row_from": 8, "col_from": 3, "cell_text": "FBA222"},
        {"row_from": 8, "col_from": 7, "cell_text": "2026-07-19 10:00 无变化"},
    ]
    client = FakeSyncClient(cells)
    binding = WpsSheetBinding("file", 1, "US-FBA", 100, 20, 3, 7)
    results = [
        tracking("FBA111", "新路由"),
        tracking("FBA222", "无变化"),
        tracking("FBA333", "冲突", QueryStatus.CONFLICT),
    ]
    summary = WpsClient.sync_tracking_results(client, binding, results)
    assert summary.updated == ["FBA111"]
    assert summary.unchanged == ["FBA222"]
    assert summary.skipped == ["FBA333"]
    assert len(client.find_calls) == 2
    assert client.find_calls[0]["row_from"] == 0
    assert client.find_calls[0]["col_from"] == 3
    assert client.find_calls[0]["col_to"] == 3
    assert client.find_calls[0]["option_cols"] == []
    assert client.find_calls[1]["col_from"] == 7
    assert client.find_calls[1]["col_to"] == 7
    assert client.find_calls[1]["row_from"] == 0
    assert client.find_calls[1]["option_cols"] == []
    assert len(client.writes) == 1
    update = client.writes[0][2]["range_data"][0]
    assert update == {
        "op_type": "cell_operation_type_formula",
        "row_from": 4,
        "row_to": 4,
        "col_from": 7,
        "col_to": 7,
        "formula": "2026-07-19 10:00 新路由",
    }
    assert "xf" not in update


def test_duplicate_fba_rows_are_never_updated():
    cells = [
        {"row_from": 2, "col_from": 3, "cell_text": "FBA111"},
        {"row_from": 2, "col_from": 7, "cell_text": "旧1"},
        {"row_from": 9, "col_from": 3, "cell_text": "FBA111"},
        {"row_from": 9, "col_from": 7, "cell_text": "旧2"},
    ]
    client = FakeSyncClient(cells)
    summary = WpsClient.sync_tracking_results(
        client,
        WpsSheetBinding("file", 1, "US-FBA", 100, 20, 3, 7),
        [tracking("FBA111", "新路由")],
    )
    assert summary.duplicate_rows == ["FBA111"]
    assert client.writes == []


def test_find_cells_reads_all_result_pages():
    client = WpsClient(
        WpsCredentials("appid", "secret", "https://www.kdocs.cn/l/file123"),
        tokens=WpsTokens("access", "refresh", 9999999999),
    )
    requested_pages = []

    def fake_request(_method, _path, **kwargs):
        page = kwargs["payload"]["page"]["page"]
        requested_pages.append(page)
        return {
            "code": 0,
            "data": {
                "total": 51,
                "range_data": [
                    {"row_from": page, "col_from": 3, "cell_text": f"FBA{page}"}
                ],
                "merge_range_data": [],
            },
        }

    client._api_request = fake_request
    cells = client._find_cells(
        WpsSheetBinding("file123", 9, "US-FBA", 80, 12),
        row_from=1,
        row_to=80,
        col_from=3,
        col_to=3,
        search=[{"col": 3, "value": ["FBA1", "FBA2"]}],
        option_cols=[],
    )
    assert requested_pages == [1, 2]
    assert [cell["cell_text"] for cell in cells] == ["FBA1", "FBA2"]


def test_failed_batch_is_not_reported_as_updated():
    cells = [{"row_from": 4, "col_from": 3, "cell_text": "FBA111"}]
    client = FakeSyncClient(cells)

    def fail_write(*_args, **_kwargs):
        raise ResponseError("write failed")

    client._api_request = fail_write
    with pytest.raises(ResponseError, match="write failed"):
        WpsClient.sync_tracking_results(
            client,
            WpsSheetBinding("file", 1, "US-FBA", 100, 20, 3, 7),
            [tracking("FBA111", "新路由")],
        )


def test_wps_writes_more_than_twenty_updates_in_separate_batches():
    results = [tracking(f"FBA{index:03d}", f"新路由{index}") for index in range(21)]
    cells = [
        {"row_from": index + 1, "col_from": 3, "cell_text": result.fba}
        for index, result in enumerate(results)
    ]
    client = FakeSyncClient(cells)
    summary = WpsClient.sync_tracking_results(
        client,
        WpsSheetBinding("file", 1, "US-FBA", 100, 20, 3, 7),
        results,
    )
    assert WPS_WRITE_BATCH_SIZE == 20
    assert summary.updated == [result.fba for result in results]
    assert len(client.writes) == 2
    assert len(client.writes[0][2]["range_data"]) == 20
    assert len(client.writes[1][2]["range_data"]) == 1
