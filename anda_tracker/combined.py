from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .models import QueryStatus, TrackingResult


class CombinedQueryService:
    """并行查询全部货代，并进行安全的归属判定。"""

    def __init__(self, *services):
        if not services:
            raise ValueError("至少需要一个货代查询服务")
        self.services = services

    def query_many(self, fbas: list[str]) -> list[TrackingResult]:
        with ThreadPoolExecutor(max_workers=len(self.services), thread_name_prefix="carrier-query") as executor:
            futures = [executor.submit(service.query_many, fbas) for service in self.services]
            result_maps = [{item.fba: item for item in future.result()} for future in futures]
        return [self._merge(fba, *[items[fba] for items in result_maps]) for fba in fbas]

    @staticmethod
    def _merge(fba: str, *carrier_results: TrackingResult) -> TrackingResult:
        found = [item for item in carrier_results if item.status == QueryStatus.SUCCESS]
        failures = [item for item in carrier_results if item.status == QueryStatus.FAILED]
        if len(found) > 1:
            carriers = " / ".join(item.carrier for item in found)
            return TrackingResult(
                fba=fba,
                status=QueryStatus.CONFLICT,
                carrier=carriers,
                error_category="carrier_conflict",
                error_message=f"{carriers} 均查到该 FBA，请人工确认货代归属",
            )
        if len(found) == 1 and not failures:
            item = found[0]
            return TrackingResult(
                fba=fba,
                status=QueryStatus.SUCCESS,
                carrier=item.carrier,
                latest_time=item.latest_time,
                latest_event=item.latest_event,
            )
        if len(found) == 1:
            item = found[0]
            details = "；".join(f"{failure.carrier or '货代'}：{failure.error_message}" for failure in failures)
            return TrackingResult(
                fba=fba,
                status=QueryStatus.PARTIAL,
                carrier=item.carrier,
                latest_time=item.latest_time,
                latest_event=item.latest_event,
                error_category="carrier_query_failed",
                error_message=f"其他货代查询失败，暂时无法排除货代冲突：{details}",
            )
        if failures:
            return TrackingResult(
                fba=fba,
                status=QueryStatus.FAILED,
                error_category="carrier_query_failed",
                error_message="；".join(f"{item.carrier or '货代'}：{item.error_message}" for item in failures),
            )
        return TrackingResult(fba=fba, status=QueryStatus.NOT_FOUND)
