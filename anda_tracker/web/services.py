from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace

from ..airscript import (
    AirScriptClient,
    AirScriptConfig,
    AirScriptSyncSummary,
    PendingTrackingItem,
)
from ..chaohong import ChaoHongClient, ChaoHongQueryService
from ..client import AndaClient
from ..combined import CombinedQueryService, query_in_batches
from ..errors import AuthenticationError, CarrierError, ConfigurationError
from ..models import QueryStatus, TrackingDetails, TrackingResult
from ..service import AndaQueryService
from ..settings import AppSettings
from ..storage import ProjectDatabase
from ..yitong import CaptchaChallenge, YiTongClient, YiTongQueryService
from ..tracking_details import (
    TRACKING_SCHEMA_VERSION,
    current_time_text,
    minimal_tracking_details,
)


logger = logging.getLogger("fba_tracker.web.services")
CAPTCHA_TTL_SECONDS = 5 * 60
VALIDATED_CLIENT_TTL_SECONDS = 30
CARRIER_STATUS_SNAPSHOT_TTL_SECONDS = 10 * 60
CARRIER_DEFINITIONS = {
    "anda": ("安达", "安达"),
    "chaohong": ("超鸿", "超鸿"),
    "yitong": ("易通", "易通"),
}


def carrier_key_from_sheet(value: str) -> str:
    """按共享表“货代”单元格中的名称识别唯一货代。"""
    normalized = "".join(str(value or "").split())
    matches = [
        key
        for key, (_label, keyword) in CARRIER_DEFINITIONS.items()
        if keyword in normalized
    ]
    return matches[0] if len(matches) == 1 else ""


@dataclass
class WebQueryResponse:
    results: list[TrackingResult]
    wps_summary: AirScriptSyncSummary | None = None
    wps_error: str = ""


@dataclass(frozen=True)
class CarrierConnectionStatus:
    carrier: str
    connected: bool
    message: str
    checked: bool = True
    cached: bool = False


class UnavailableQueryService:
    def __init__(self, carrier: str, message: str):
        self.carrier = carrier
        self.message = message

    def query_many(self, fbas: list[str]) -> list[TrackingResult]:
        return [
            TrackingResult(
                fba=fba,
                status=QueryStatus.FAILED,
                carrier=self.carrier,
                error_category="configuration",
                error_message=self.message,
            )
            for fba in fbas
        ]


class QueryCoordinator:
    """网页版查询协调器；统一编排独立的货代服务。"""

    def __init__(self, database_path, settings_path):
        self.database_path = database_path
        self.settings = AppSettings.load(settings_path)
        self._locks: dict[str, threading.Lock] = {}
        self._validated_anda_clients: dict[str, tuple[float, AndaClient]] = {}
        self._validated_carrier_sets: dict[str, tuple[float, set[str]]] = {}
        self._carrier_status_snapshots: dict[
            str, dict[str, tuple[float, CarrierConnectionStatus]]
        ] = {}
        self._registry_lock = threading.Lock()

    def _user_lock(self, user_id: str) -> threading.Lock:
        with self._registry_lock:
            return self._locks.setdefault(user_id, threading.Lock())

    def _store_validated_anda_client(
        self, user_id: str, client: AndaClient
    ) -> None:
        with self._registry_lock:
            self._validated_anda_clients[user_id] = (time.monotonic(), client)

    def _take_validated_anda_client(self, user_id: str) -> AndaClient | None:
        with self._registry_lock:
            cached = self._validated_anda_clients.pop(user_id, None)
        if cached and time.monotonic() - cached[0] <= VALIDATED_CLIENT_TTL_SECONDS:
            return cached[1]
        return None

    def _store_validated_carriers(
        self, user_id: str, statuses: list[CarrierConnectionStatus]
    ) -> None:
        connected = {
            key
            for item in statuses
            if item.connected and (key := carrier_key_from_sheet(item.carrier))
        }
        with self._registry_lock:
            self._validated_carrier_sets[user_id] = (
                time.monotonic(),
                connected,
            )

    def _take_validated_carriers(self, user_id: str) -> set[str] | None:
        with self._registry_lock:
            cached = self._validated_carrier_sets.pop(user_id, None)
        if cached and time.monotonic() - cached[0] <= VALIDATED_CLIENT_TTL_SECONDS:
            return cached[1]
        return None

    def _store_status_snapshot(
        self, user_id: str, statuses: list[CarrierConnectionStatus]
    ) -> None:
        now = time.monotonic()
        with self._registry_lock:
            values = self._carrier_status_snapshots.setdefault(user_id, {})
            expired = [
                key
                for key, (checked_at, _item) in values.items()
                if now - checked_at > CARRIER_STATUS_SNAPSHOT_TTL_SECONDS
            ]
            for key in expired:
                values.pop(key, None)
            for item in statuses:
                key = carrier_key_from_sheet(item.carrier)
                if key:
                    values[key] = (now, item)

    def _status_snapshot(
        self, user_id: str
    ) -> dict[str, CarrierConnectionStatus]:
        now = time.monotonic()
        with self._registry_lock:
            values = self._carrier_status_snapshots.get(user_id)
            if values is None:
                return {}
            expired = [
                key
                for key, (checked_at, _item) in values.items()
                if now - checked_at > CARRIER_STATUS_SNAPSHOT_TTL_SECONDS
            ]
            for key in expired:
                values.pop(key, None)
            if not values:
                self._carrier_status_snapshots.pop(user_id, None)
                return {}
            return {
                key: replace(item, cached=True)
                for key, (_checked_at, item) in values.items()
            }

    def remember_status(
        self, user_id: str, status: CarrierConnectionStatus
    ) -> None:
        """记录某家货代刚完成的真实登录或检查结果。"""
        self._store_status_snapshot(user_id, [status])

    def invalidate_status(
        self, user_id: str, carrier_key: str | None = None
    ) -> None:
        """凭据变更后仅清除受影响货代的页面状态。"""
        if carrier_key is not None and carrier_key not in CARRIER_DEFINITIONS:
            raise ValueError("未知货代")
        with self._registry_lock:
            if carrier_key is None:
                self._carrier_status_snapshots.pop(user_id, None)
            else:
                values = self._carrier_status_snapshots.get(user_id)
                if values is not None:
                    values.pop(carrier_key, None)
                    if not values:
                        self._carrier_status_snapshots.pop(user_id, None)
            self._validated_carrier_sets.pop(user_id, None)
            if carrier_key in (None, "anda"):
                self._validated_anda_clients.pop(user_id, None)

    def query(
        self,
        user_id: str,
        fbas: list[str],
        airscript_config: AirScriptConfig | None = None,
    ) -> WebQueryResponse:
        # 防止同一用户重复点击后并发挤掉自己的货代会话。
        with self._user_lock(user_id):
            logger.info(
                "tracking_query_start user=%s count=%d sync=%s",
                user_id,
                len(fbas),
                airscript_config is not None,
            )
            database = ProjectDatabase(self.database_path, profile_id=user_id)
            recently_connected = self._take_validated_carriers(user_id)
            selected = (
                set(CARRIER_DEFINITIONS)
                if recently_connected is None
                else recently_connected
            )
            service_factories = {
                "anda": lambda: self._anda_service(database),
                "chaohong": lambda: ChaoHongQueryService(
                    ChaoHongClient(retries=self.settings.retries)
                ),
                "yitong": lambda: self._yitong_service(database),
            }
            service_instances = {
                key: service_factories[key]()
                for key in CARRIER_DEFINITIONS
                if key in selected
            }
            services = list(service_instances.values())
            if not services:
                results = [
                    TrackingResult(
                        fba=fba,
                        status=QueryStatus.FAILED,
                        error_category="carrier_unavailable",
                        error_message="本次实时检查没有可用的货代连接",
                    )
                    for fba in fbas
                ]
                logger.info(
                    "tracking_query_complete user=%s count=%d statuses=%s wps_error=%s",
                    user_id,
                    len(results),
                    {QueryStatus.FAILED.value: len(results)},
                    False,
                )
                return WebQueryResponse(results=results)
            combined = CombinedQueryService(*services)
            results = query_in_batches(
                combined,
                fbas,
                batch_size=database.query_batch_size(),
            )
            if airscript_config is not None:
                results = self._enrich_tracking_results(
                    database,
                    results,
                    service_instances,
                    {},
                )
            response = WebQueryResponse(results=results)
            if airscript_config is not None:
                self._sync_wps(airscript_config, response)
            counts: dict[str, int] = {}
            for result in results:
                counts[result.status.value] = counts.get(result.status.value, 0) + 1
            logger.info(
                "tracking_query_complete user=%s count=%d statuses=%s wps_error=%s",
                user_id,
                len(results),
                counts,
                bool(response.wps_error),
            )
            return response

    def query_routed(
        self,
        user_id: str,
        items: list[PendingTrackingItem],
        airscript_config: AirScriptConfig | None = None,
    ) -> WebQueryResponse:
        """优先查询共享表指定货代，仅对“未找到”的FBA查询其他货代。"""
        with self._user_lock(user_id):
            logger.info(
                "tracking_routed_query_start user=%s count=%d sync=%s",
                user_id,
                len(items),
                airscript_config is not None,
            )
            database = ProjectDatabase(self.database_path, profile_id=user_id)
            grouped: dict[str, list[str]] = {
                key: [] for key in CARRIER_DEFINITIONS
            }
            result_map: dict[str, TrackingResult] = {}
            primary_carriers: dict[str, str] = {}
            for item in items:
                key = carrier_key_from_sheet(item.carrier)
                if not key:
                    visible_carrier = item.carrier.strip()
                    result_map[item.fba] = TrackingResult(
                        fba=item.fba,
                        status=QueryStatus.FAILED,
                        carrier=visible_carrier,
                        error_category="carrier_configuration",
                        error_message=(
                            "共享表货代列无法识别唯一货代；"
                            "单元格需且只能包含安达、超鸿或易通中的一个名称"
                        ),
                    )
                    continue
                grouped[key].append(item.fba)
                primary_carriers[item.fba] = key

            service_factories = {
                "anda": lambda: self._anda_service(database),
                "chaohong": lambda: ChaoHongQueryService(
                    ChaoHongClient(retries=self.settings.retries)
                ),
                "yitong": lambda: self._yitong_service(database),
            }
            service_instances: dict[str, object] = {}

            def service_for(key: str):
                if key not in service_instances:
                    service_instances[key] = service_factories[key]()
                return service_instances[key]

            active = {
                key: (service_for(key), fbas)
                for key, fbas in grouped.items()
                if fbas
            }

            def run_group(service, fbas: list[str]) -> list[TrackingResult]:
                return query_in_batches(
                    service,
                    fbas,
                    batch_size=database.query_batch_size(),
                )

            def normalize_group_results(
                carrier_key: str,
                requested: list[str],
                returned: list[TrackingResult],
                *,
                phase: str,
            ) -> list[TrackingResult]:
                """拒绝货代漏返、错返或重复返回的FBA，避免后续映射崩溃。"""
                requested_set = set(requested)
                result_map = {
                    item.fba: item
                    for item in returned
                    if item.fba in requested_set
                }
                missing_count = sum(
                    fba not in result_map for fba in requested
                )
                unexpected_count = sum(
                    item.fba not in requested_set for item in returned
                )
                if missing_count or unexpected_count:
                    logger.error(
                        "routed_carrier_invalid_results carrier=%s phase=%s missing=%d unexpected=%d",
                        carrier_key,
                        phase,
                        missing_count,
                        unexpected_count,
                    )
                label = CARRIER_DEFINITIONS[carrier_key][0]
                return [
                    result_map.get(fba)
                    or TrackingResult(
                        fba=fba,
                        status=QueryStatus.FAILED,
                        carrier=label,
                        error_category="invalid_response",
                        error_message=f"{label}未返回该FBA的有效查询结果",
                    )
                    for fba in requested
                ]

            if active:
                with ThreadPoolExecutor(
                    max_workers=len(active),
                    thread_name_prefix="routed-carrier-query",
                ) as executor:
                    futures = {
                        key: executor.submit(run_group, service, fbas)
                        for key, (service, fbas) in active.items()
                    }
                    for key, future in futures.items():
                        try:
                            returned = normalize_group_results(
                                key,
                                grouped[key],
                                future.result(),
                                phase="primary",
                            )
                            for result in returned:
                                result_map[result.fba] = result
                        except Exception:
                            label = CARRIER_DEFINITIONS[key][0]
                            logger.exception(
                                "routed_carrier_query_unexpected carrier=%s user=%s",
                                key,
                                user_id,
                            )
                            for fba in grouped[key]:
                                result_map[fba] = TrackingResult(
                                    fba=fba,
                                    status=QueryStatus.FAILED,
                                    carrier=label,
                                    error_category="unexpected",
                                    error_message=f"{label}查询发生未预期错误",
                                )

            # “未找到”表示指定货代已正常响应但没有该FBA，可能是共享表货代
            # 填写错误。只为这些号码查询另外两家；登录、网络等失败不兜底，
            # 避免用其他货代结果掩盖真实故障。
            missing_primary = {
                fba: primary_key
                for fba, primary_key in primary_carriers.items()
                if result_map[fba].status == QueryStatus.NOT_FOUND
            }
            fallback_groups: dict[str, list[str]] = {
                key: [] for key in CARRIER_DEFINITIONS
            }
            for fba, primary_key in missing_primary.items():
                for key in CARRIER_DEFINITIONS:
                    if key != primary_key:
                        fallback_groups[key].append(fba)

            fallback_results: dict[str, list[TrackingResult]] = {
                fba: [] for fba in missing_primary
            }
            fallback_active = {
                key: (service_for(key), fbas)
                for key, fbas in fallback_groups.items()
                if fbas
            }
            if fallback_active:
                with ThreadPoolExecutor(
                    max_workers=len(fallback_active),
                    thread_name_prefix="routed-carrier-fallback",
                ) as executor:
                    futures = {
                        key: executor.submit(run_group, service, fbas)
                        for key, (service, fbas) in fallback_active.items()
                    }
                    for key, future in futures.items():
                        try:
                            returned = normalize_group_results(
                                key,
                                fallback_groups[key],
                                future.result(),
                                phase="fallback",
                            )
                        except Exception:
                            label = CARRIER_DEFINITIONS[key][0]
                            logger.exception(
                                "routed_carrier_fallback_unexpected carrier=%s user=%s",
                                key,
                                user_id,
                            )
                            returned = [
                                TrackingResult(
                                    fba=fba,
                                    status=QueryStatus.FAILED,
                                    carrier=label,
                                    error_category="unexpected",
                                    error_message=f"{label}兜底查询发生未预期错误",
                                )
                                for fba in fallback_groups[key]
                            ]
                        for result in returned:
                            fallback_results[result.fba].append(result)

            fallback_recovered = 0
            fallback_conflicts = 0
            fallback_inconclusive = 0
            for fba, candidates in fallback_results.items():
                found = [
                    item
                    for item in candidates
                    if item.status == QueryStatus.SUCCESS
                ]
                failures = [
                    item
                    for item in candidates
                    if item.status in {QueryStatus.FAILED, QueryStatus.PARTIAL}
                ]
                if len(found) == 1 and not failures:
                    result_map[fba] = found[0]
                    fallback_recovered += 1
                elif len(found) == 1:
                    found_item = found[0]
                    details = "；".join(
                        f"{item.carrier or '货代'}："
                        f"{item.error_message or item.status.value}"
                        for item in failures
                    )
                    result_map[fba] = TrackingResult(
                        fba=fba,
                        status=QueryStatus.PARTIAL,
                        carrier=found_item.carrier,
                        latest_time=found_item.latest_time,
                        latest_event=found_item.latest_event,
                        error_category="carrier_query_failed",
                        error_message=(
                            "兜底货代中有一家查到该 FBA，但另有货代查询失败，"
                            f"暂时无法排除货代冲突：{details}"
                        ),
                    )
                    fallback_inconclusive += 1
                elif len(found) > 1:
                    carriers = " / ".join(item.carrier for item in found)
                    result_map[fba] = TrackingResult(
                        fba=fba,
                        status=QueryStatus.CONFLICT,
                        carrier=carriers,
                        error_category="carrier_conflict",
                        error_message=(
                            f"指定货代未找到，但 {carriers} 均查到该 FBA，"
                            "请人工确认共享表货代"
                        ),
                    )
                    fallback_conflicts += 1
                elif failures:
                    result_map[fba] = TrackingResult(
                        fba=fba,
                        status=QueryStatus.FAILED,
                        error_category="carrier_query_failed",
                        error_message="；".join(
                            f"{item.carrier or '货代'}："
                            f"{item.error_message or item.status.value}"
                            for item in failures
                        ),
                    )
                    fallback_inconclusive += 1

            results = [result_map[item.fba] for item in items]
            results = self._enrich_tracking_results(
                database,
                results,
                service_instances,
                primary_carriers,
            )
            response = WebQueryResponse(results=results)
            if airscript_config is not None:
                self._sync_wps(airscript_config, response)
            counts: dict[str, int] = {}
            for result in results:
                counts[result.status.value] = counts.get(result.status.value, 0) + 1
            logger.info(
                "tracking_routed_query_complete user=%s count=%d carriers=%s statuses=%s fallback=%d recovered=%d conflicts=%d inconclusive=%d wps_error=%s",
                user_id,
                len(results),
                {key: len(value) for key, value in grouped.items()},
                counts,
                len(missing_primary),
                fallback_recovered,
                fallback_conflicts,
                fallback_inconclusive,
                bool(response.wps_error),
            )
            return response

    def _enrich_tracking_results(
        self,
        database: ProjectDatabase,
        results: list[TrackingResult],
        service_instances: dict[str, object],
        primary_carriers: dict[str, str],
    ) -> list[TrackingResult]:
        """按最新轨迹变化读取完整详情，并复用本机缓存减少官网请求。"""
        enriched: list[TrackingResult] = []
        cache_hits = 0
        detail_fetches = 0
        detail_failures = 0
        now = current_time_text()

        for result in results:
            if result.status != QueryStatus.SUCCESS:
                enriched.append(result)
                continue
            carrier_key = carrier_key_from_sheet(result.carrier)
            if not carrier_key:
                enriched.append(result)
                continue

            details: TrackingDetails | None = None
            cached = database.load_tracking_cache(carrier_key, result.fba)
            if (
                cached is not None
                and cached[0] == TRACKING_SCHEMA_VERSION
                and cached[1] == result.latest_time
                and cached[2] == result.latest_event
            ):
                try:
                    details = TrackingDetails.from_dict(cached[3])
                    cache_hits += 1
                except (TypeError, ValueError):
                    details = None

            if details is None:
                service = service_instances.get(carrier_key)
                fetch = getattr(service, "fetch_tracking_details", None)
                if callable(fetch):
                    try:
                        details = fetch(result.fba)
                        detail_fetches += 1
                        database.save_tracking_cache(
                            carrier_key,
                            result.fba,
                            TRACKING_SCHEMA_VERSION,
                            result.latest_time,
                            result.latest_event,
                            details.to_dict(),
                        )
                    except (CarrierError, ValueError) as exc:
                        detail_failures += 1
                        logger.warning(
                            "tracking_detail_fetch_failed carrier=%s category=%s message=%s",
                            carrier_key,
                            getattr(exc, "category", "invalid_detail"),
                            getattr(exc, "user_message", str(exc)),
                        )
                    except Exception:
                        detail_failures += 1
                        logger.exception(
                            "tracking_detail_fetch_unexpected carrier=%s",
                            carrier_key,
                        )

            if details is None:
                details = minimal_tracking_details(
                    result.fba,
                    result.carrier,
                    result.latest_time,
                    result.latest_event,
                )

            snapshot = replace(details.snapshot, updated_time=now)
            primary_key = primary_carriers.get(result.fba)
            if primary_key and primary_key != carrier_key:
                snapshot = replace(
                    snapshot,
                    data_status=(
                        f"货代不一致：表中{CARRIER_DEFINITIONS[primary_key][0]}，"
                        f"实际由{result.carrier}查到"
                    ),
                )
            enriched.append(
                replace(
                    result,
                    snapshot=snapshot,
                    events=details.events,
                )
            )

        logger.info(
            "tracking_detail_enrichment cache_hits=%d fetched=%d failures=%d",
            cache_hits,
            detail_fetches,
            detail_failures,
        )
        return enriched

    def configured_status(self, user_id: str) -> list[CarrierConnectionStatus]:
        database = ProjectDatabase(self.database_path, profile_id=user_id)
        anda_username = database.credential_username("anda")
        yitong_token = database.load_session_token("yitong")
        configured = [
            CarrierConnectionStatus(
                "安达",
                bool(anda_username),
                "已配置账号" if anda_username else "未配置账号",
                checked=False,
            ),
            CarrierConnectionStatus(
                "超鸿", True, "无需账号登录", checked=False
            ),
            CarrierConnectionStatus(
                "易通",
                bool(yitong_token),
                "已保存登录状态"
                if yitong_token
                else "需要完成验证码登录",
                checked=False,
            ),
        ]
        snapshot = self._status_snapshot(user_id)
        return [
            snapshot.get(carrier_key_from_sheet(item.carrier), item)
            for item in configured
        ]

    def validate_all(
        self, user_id: str, *, force: bool = False
    ) -> list[CarrierConnectionStatus]:
        return self.validate_required(
            user_id,
            set(CARRIER_DEFINITIONS),
            force=force,
        )

    def validate_required(
        self,
        user_id: str,
        required: set[str],
        *,
        force: bool = False,
    ) -> list[CarrierConnectionStatus]:
        """优先复用十分钟状态；仅联网验证缺失、过期或强制检查的货代。"""
        required = set(required) & set(CARRIER_DEFINITIONS)
        if not required:
            return []
        with self._user_lock(user_id):
            cached_statuses = {} if force else {
                key: item
                for key, item in self._status_snapshot(user_id).items()
                if key in required
            }
            to_validate = required - set(cached_statuses)
            if not to_validate:
                statuses = [
                    cached_statuses[key]
                    for key in CARRIER_DEFINITIONS
                    if key in cached_statuses
                ]
                if required == set(CARRIER_DEFINITIONS):
                    self._store_validated_carriers(user_id, statuses)
                logger.info(
                    "carrier_validation_cache_hit user=%s total=%d",
                    user_id,
                    len(statuses),
                )
                return statuses

            database = ProjectDatabase(self.database_path, profile_id=user_id)

            def validate_anda() -> CarrierConnectionStatus:
                try:
                    credentials = database.load_credentials("anda")
                    if credentials is None:
                        return CarrierConnectionStatus("安达", False, "未配置账号")
                    client = AndaClient(retries=self.settings.retries)
                    client.login(
                        credentials.username, credentials.password
                    )
                    self._store_validated_anda_client(user_id, client)
                    return CarrierConnectionStatus("安达", True, "登录成功")
                except CarrierError as exc:
                    return CarrierConnectionStatus("安达", False, exc.user_message)
                except Exception:
                    logger.exception(
                        "carrier_validation_unexpected carrier=anda user=%s",
                        user_id,
                    )
                    return CarrierConnectionStatus(
                        "安达", False, "登录验证发生未预期错误"
                    )

            def validate_chaohong() -> CarrierConnectionStatus:
                try:
                    ChaoHongClient(retries=self.settings.retries).query_batch(
                        ["FBA_CONNECTION_CHECK"]
                    )
                    return CarrierConnectionStatus("超鸿", True, "接口可用")
                except CarrierError as exc:
                    return CarrierConnectionStatus("超鸿", False, exc.user_message)
                except Exception:
                    logger.exception(
                        "carrier_validation_unexpected carrier=chaohong user=%s",
                        user_id,
                    )
                    return CarrierConnectionStatus(
                        "超鸿", False, "接口验证发生未预期错误"
                    )

            def validate_yitong() -> CarrierConnectionStatus:
                try:
                    token = database.load_session_token("yitong")
                    if not token:
                        return CarrierConnectionStatus(
                            "易通", False, "未登录或登录状态已过期"
                        )
                    client = YiTongClient(retries=self.settings.retries)
                    client.token = token
                    client.validate_token()
                    return CarrierConnectionStatus("易通", True, "登录有效")
                except AuthenticationError as exc:
                    database.delete_session_token("yitong")
                    return CarrierConnectionStatus("易通", False, exc.user_message)
                except CarrierError as exc:
                    # 网络或服务端临时故障不能删除仍可能有效的登录令牌。
                    return CarrierConnectionStatus("易通", False, exc.user_message)
                except Exception:
                    logger.exception(
                        "carrier_validation_unexpected carrier=yitong user=%s",
                        user_id,
                    )
                    return CarrierConnectionStatus(
                        "易通", False, "登录验证发生未预期错误"
                    )

            validators = {
                "anda": validate_anda,
                "chaohong": validate_chaohong,
                "yitong": validate_yitong,
            }
            with ThreadPoolExecutor(
                max_workers=len(to_validate),
                thread_name_prefix="carrier-validation",
            ) as executor:
                futures = {
                    key: executor.submit(validators[key])
                    for key in to_validate
                }
                checked_statuses = {
                    key: future.result()
                    for key, future in futures.items()
                }
            self._store_status_snapshot(
                user_id,
                list(checked_statuses.values()),
            )
            statuses_by_key = {**cached_statuses, **checked_statuses}
            statuses = [
                statuses_by_key[key]
                for key in CARRIER_DEFINITIONS
                if key in statuses_by_key
            ]
            if required == set(CARRIER_DEFINITIONS):
                self._store_validated_carriers(user_id, statuses)
            logger.info(
                "carrier_validation_complete user=%s connected=%d total=%d live=%d cached=%d force=%s",
                user_id,
                sum(item.connected for item in statuses),
                len(statuses),
                len(checked_statuses),
                len(cached_statuses),
                force,
            )
            return statuses

    def _anda_service(self, database: ProjectDatabase):
        credentials = database.load_credentials("anda")
        if credentials is None:
            return UnavailableQueryService("安达", "当前账号尚未配置安达账号")
        client = self._take_validated_anda_client(database.profile_id)
        if client is None:
            client = AndaClient(retries=self.settings.retries)

        def login() -> None:
            client.login(credentials.username, credentials.password)

        if not client.token:
            try:
                login()
            except CarrierError as exc:
                # 查询服务会将其稳定呈现为单货代失败；这里不泄露密码或请求细节。
                return UnavailableQueryService("安达", exc.user_message)
            except Exception:
                logger.exception("carrier_login_unexpected carrier=anda")
                return UnavailableQueryService("安达", "安达登录发生未预期错误")
        return AndaQueryService(
            client,
            batch_size=self.settings.batch_size,
            request_interval=self.settings.request_interval,
            reauthenticate=login,
        )

    def _yitong_service(self, database: ProjectDatabase):
        token = database.load_session_token("yitong")
        if not token:
            return UnavailableQueryService("易通", "易通尚未登录或登录状态已过期")
        client = YiTongClient(retries=self.settings.retries)
        client.token = token
        return YiTongQueryService(
            client,
            batch_size=self.settings.batch_size,
            request_interval=self.settings.request_interval,
        )

    def _sync_wps(
        self, config: AirScriptConfig, response: WebQueryResponse
    ) -> None:
        try:
            response.wps_summary = AirScriptClient(
                config, retries=self.settings.retries
            ).sync_tracking_results(response.results)
            logger.info(
                "airscript_sync_complete updated=%d unchanged=%d failures=%d",
                len(response.wps_summary.updated),
                len(response.wps_summary.unchanged),
                len(response.wps_summary.failures),
            )
        except (CarrierError, ConfigurationError) as exc:
            response.wps_error = exc.user_message
            logger.warning(
                "airscript_sync_failed category=%s message=%s",
                getattr(exc, "category", "configuration"),
                exc.user_message,
            )
        except Exception:
            logger.exception("airscript_sync_unexpected")
            response.wps_error = "物流查询完成，但共享表更新发生未预期错误"


@dataclass
class PendingCaptcha:
    user_id: str
    client: YiTongClient
    challenge: CaptchaChallenge
    created_at: float


class CaptchaRegistry:
    def __init__(self):
        self._items: dict[str, PendingCaptcha] = {}
        self._lock = threading.Lock()

    def create(self, user_id: str, token: str, settings: AppSettings) -> PendingCaptcha:
        client = YiTongClient(retries=settings.retries)
        now = time.monotonic()
        pending = PendingCaptcha(user_id, client, client.fetch_captcha(), now)
        with self._lock:
            # 每个用户只保留最后一次验证码，旧验证码立即失效。
            self._items = {
                key: value
                for key, value in self._items.items()
                if value.user_id != user_id
                and now - value.created_at <= CAPTCHA_TTL_SECONDS
            }
            self._items[token] = pending
        return pending

    def pop(self, user_id: str, token: str) -> PendingCaptcha:
        with self._lock:
            pending = self._items.pop(token, None)
        if (
            pending is None
            or pending.user_id != user_id
            or time.monotonic() - pending.created_at > CAPTCHA_TTL_SECONDS
        ):
            raise ConfigurationError("验证码已失效，请刷新后重新输入")
        return pending


def result_dict(result: TrackingResult) -> dict[str, str]:
    return {
        "fba": result.fba,
        "carrier": result.carrier,
        "status": result.status.value,
        "latest_time": result.latest_time,
        "latest_event": result.latest_event,
        "error_category": result.error_category,
        "error_message": result.error_message,
    }


def summary_dict(summary: AirScriptSyncSummary | None) -> dict | None:
    return None if summary is None else asdict(summary) | {"message": summary.message}
