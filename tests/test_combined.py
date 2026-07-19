from anda_tracker.combined import CombinedQueryService
from anda_tracker.models import QueryStatus, TrackingResult


class StubService:
    def __init__(self, results):
        self.results = results

    def query_many(self, _fbas):
        return self.results


def result(fba, status, carrier, error=""):
    return TrackingResult(
        fba=fba,
        status=status,
        carrier=carrier,
        latest_time="2026-07-19",
        latest_event="物流动态",
        error_category="network" if error else "",
        error_message=error,
    )


def test_identifies_each_carrier_and_reports_conflict():
    fbas = ["FBA111", "FBA222", "FBA333", "FBA444"]
    anda = [
        result("FBA111", QueryStatus.SUCCESS, "安达"),
        result("FBA222", QueryStatus.NOT_FOUND, "安达"),
        result("FBA333", QueryStatus.SUCCESS, "安达"),
        result("FBA444", QueryStatus.NOT_FOUND, "安达"),
    ]
    ch = [
        result("FBA111", QueryStatus.NOT_FOUND, "超鸿"),
        result("FBA222", QueryStatus.SUCCESS, "超鸿"),
        result("FBA333", QueryStatus.SUCCESS, "超鸿"),
        result("FBA444", QueryStatus.NOT_FOUND, "超鸿"),
    ]
    merged = CombinedQueryService(StubService(anda), StubService(ch)).query_many(fbas)
    assert [(item.carrier, item.status) for item in merged] == [
        ("安达", QueryStatus.SUCCESS),
        ("超鸿", QueryStatus.SUCCESS),
        ("安达 / 超鸿", QueryStatus.CONFLICT),
        ("", QueryStatus.NOT_FOUND),
    ]


def test_found_plus_other_carrier_failure_is_partial_not_silent_success():
    anda = [result("FBA111", QueryStatus.SUCCESS, "安达")]
    ch = [result("FBA111", QueryStatus.FAILED, "超鸿", "连接失败")]
    merged = CombinedQueryService(StubService(anda), StubService(ch)).query_many(["FBA111"])
    assert merged[0].status == QueryStatus.PARTIAL
    assert "无法排除货代冲突" in merged[0].error_message


def test_three_carriers_report_multi_carrier_conflict():
    fba = "FBA111"
    services = [
        StubService([result(fba, QueryStatus.SUCCESS, "安达")]),
        StubService([result(fba, QueryStatus.NOT_FOUND, "超鸿")]),
        StubService([result(fba, QueryStatus.SUCCESS, "易通")]),
    ]
    merged = CombinedQueryService(*services).query_many([fba])
    assert merged[0].status == QueryStatus.CONFLICT
    assert merged[0].carrier == "安达 / 易通"
