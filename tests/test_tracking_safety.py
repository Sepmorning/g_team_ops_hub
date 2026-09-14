from dataclasses import replace

import pytest

from g_team_ops.errors import ConfigurationError, NetworkError, ResponseError
from g_team_ops.models import QueryStatus, TrackingResult
from g_team_ops.modules.operations.repository import OperationRepository
from g_team_ops.modules.tracking.service import header_signature, initialize_headers
from g_team_ops.service import AndaQueryService
from g_team_ops.storage import ProjectDatabase
from g_team_ops.tracking_details import TRACKING_SCHEMA_VERSION, normalize_tracking_details
from g_team_ops.web.services import QueryCoordinator


def test_header_initialization_is_audited_idempotent_and_not_reversible(tmp_path):
    repository = OperationRepository(tmp_path / "app.db")

    class Client:
        writes = 0
        headers = ["人工备注"]

        def preview_headers(self):
            return {"plans": [{"sheetName": "FBA", "additions": ["FBA号"]}],
                "snapshots": [{"targetType": "tracking_headers", "sheetName": "FBA",
                    "matchValue": "__headers__", "itemKey": "__headers__", "field": "__headers__",
                    "value": list(self.headers)}]}

        def apply_headers(self, before):
            self.writes += 1
            self.headers.append("FBA号")
            return {"success": True}

        def snapshot_targets(self, targets):
            return self.preview_headers()["snapshots"]

    client = Client()
    signature = header_signature(client.preview_headers())
    result = initialize_headers(repository, "owner", "shop", "US", client, signature, "header-test")
    assert repository.get_batch("owner", result.batch.id).reversible is False
    assert repository.get_batch("other", result.batch.id) is None
    repeated = initialize_headers(repository, "owner", "shop", "US", client, signature, "header-test")
    assert repeated.reused and client.writes == 1
    with pytest.raises(ConfigurationError, match="重新预览"):
        initialize_headers(repository, "owner", "shop", "US", client, signature, "changed-preview")
    assert client.writes == 1


def test_old_cache_refreshes_even_when_latest_route_is_unchanged_and_failure_keeps_cache(tmp_path):
    db = ProjectDatabase(tmp_path / "app.db", "owner")
    coordinator = QueryCoordinator(tmp_path / "app.db", tmp_path / "settings.json")
    old = normalize_tracking_details(fba="FBA12345", carrier="安达", raw_events=[
        {"event_time": "2026-07-01", "content": "已开船"}])
    old = replace(old, snapshot=replace(old.snapshot, updated_time="2020-01-01 00:00:00"))
    db.save_tracking_cache("anda", "FBA12345", TRACKING_SCHEMA_VERSION, "2026-07-01", "已开船", old.to_dict())
    result = TrackingResult("FBA12345", QueryStatus.SUCCESS, carrier="安达", latest_time="2026-07-01", latest_event="已开船")

    class Service:
        calls = 0
        def fetch_tracking_details(self, fba):
            self.calls += 1
            raise NetworkError("模拟详情失败")

    service = Service()
    response = coordinator._enrich_tracking_results(db, [result], {"anda": service}, {})
    assert service.calls == 1
    assert response[0].status == QueryStatus.PARTIAL
    assert response[0].snapshot is None
    assert db.load_tracking_cache("anda", "FBA12345")[3] == old.to_dict()
    assert ProjectDatabase(tmp_path / "app.db", "other").load_tracking_cache("anda", "FBA12345") is None


def test_anda_rejects_unknown_order_shape_and_duplicate_fba():
    class Client:
        def query_batch(self, fbas):
            return [{"newUnknownField": "FBA12345"}]
    service = AndaQueryService(Client(), request_interval=0)
    assert service.query_many(["FBA12345"])[0].status == QueryStatus.FAILED
    service.client.query_batch = lambda fbas: [{"fbaCode": "FBA12345"}, {"fbaCode": "FBA12345"}]
    assert service.query_many(["FBA12345"])[0].error_category == "ambiguous_order"


def test_anda_empty_full_trace_does_not_fall_back_to_status_map():
    class Client:
        def get_trace_list(self, trace):
            return []
    service = AndaQueryService(Client())
    service.last_records["FBA12345"] = {"fbaCode": "FBA12345", "orderStatusMap": [{"stateName": "已到港"}]}
    with pytest.raises(ResponseError, match="完整轨迹"):
        service.fetch_tracking_details("FBA12345")
