from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .models import QueryStatus, TrackingResult


SYSTEM_QUERY_BATCH_SIZE = 50
logger = logging.getLogger("fba_tracker.carriers")


def query_in_batches(
    service,
    fbas: list[str],
    *,
    batch_size: int = SYSTEM_QUERY_BATCH_SIZE,
    on_progress: Callable[[int, int, int, int], None] | None = None,
) -> list[TrackingResult]:
    """总量不设上限；内部按安全批次查询并保持输入顺序。"""
    if batch_size < 1:
        raise ValueError("查询批次大小必须大于0")
    total = len(fbas)
    if total == 0:
        return []
    batch_count = (total + batch_size - 1) // batch_size
    results: list[TrackingResult] = []
    for batch_index, offset in enumerate(range(0, total, batch_size), start=1):
        batch = fbas[offset : offset + batch_size]
        batch_results = service.query_many(batch)
        if len(batch_results) != len(batch):
            raise RuntimeError("货代查询服务返回的结果数量与请求数量不一致")
        results.extend(batch_results)
        if on_progress:
            on_progress(len(results), total, batch_index, batch_count)
    return results


class CombinedQueryService:
    """并行查询全部货代，并进行安全的归属判定。"""

    def __init__(self, *services):
        if not services:
            raise ValueError("至少需要一个货代查询服务")
        self.services = services

    def query_many(self, fbas: list[str]) -> list[TrackingResult]:
        with ThreadPoolExecutor(max_workers=len(self.services), thread_name_prefix="carrier-query") as executor:
            futures = [
                (service, executor.submit(service.query_many, fbas))
                for service in self.services
            ]
            result_maps = [
                self._safe_result_map(service, future, fbas)
                for service, future in futures
            ]
        return [self._merge(fba, *[items[fba] for items in result_maps]) for fba in fbas]

    @staticmethod
    def _safe_result_map(service, future, fbas: list[str]) -> dict[str, TrackingResult]:
        carrier = str(getattr(service, "carrier", service.__class__.__name__))
        try:
            returned = future.result()
        except Exception:
            logger.exception("carrier_query_unexpected carrier=%s count=%d", carrier, len(fbas))
            return {
                fba: TrackingResult(
                    fba=fba,
                    status=QueryStatus.FAILED,
                    carrier=carrier,
                    error_category="unexpected",
                    error_message=f"{carrier}查询发生未预期错误",
                )
                for fba in fbas
            }

        result_map = {item.fba: item for item in returned if item.fba in fbas}
        for fba in fbas:
            if fba not in result_map:
                logger.error("carrier_query_missing_result carrier=%s fba=%s", carrier, fba)
                result_map[fba] = TrackingResult(
                    fba=fba,
                    status=QueryStatus.FAILED,
                    carrier=carrier,
                    error_category="invalid_response",
                    error_message=f"{carrier}未返回该FBA的查询结果",
                )
        return result_map

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
