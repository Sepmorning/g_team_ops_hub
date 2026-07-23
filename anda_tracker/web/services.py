from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass

from ..airscript import AirScriptClient, AirScriptConfig, AirScriptSyncSummary
from ..chaohong import ChaoHongClient, ChaoHongQueryService
from ..client import AndaClient
from ..combined import CombinedQueryService, query_in_batches
from ..errors import AuthenticationError, CarrierError, ConfigurationError
from ..models import QueryStatus, TrackingResult
from ..service import AndaQueryService
from ..settings import AppSettings
from ..storage import ProjectDatabase
from ..yitong import CaptchaChallenge, YiTongClient, YiTongQueryService


logger = logging.getLogger("fba_tracker.web.services")
CAPTCHA_TTL_SECONDS = 5 * 60


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
        self._registry_lock = threading.Lock()

    def _user_lock(self, user_id: str) -> threading.Lock:
        with self._registry_lock:
            return self._locks.setdefault(user_id, threading.Lock())

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
            services = [
                self._anda_service(database),
                ChaoHongQueryService(ChaoHongClient(retries=self.settings.retries)),
                self._yitong_service(database),
            ]
            combined = CombinedQueryService(*services)
            results = query_in_batches(
                combined,
                fbas,
                batch_size=database.query_batch_size(),
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

    def configured_status(self, user_id: str) -> list[CarrierConnectionStatus]:
        database = ProjectDatabase(self.database_path, profile_id=user_id)
        anda_username = database.credential_username("anda")
        yitong_token = database.load_session_token("yitong")
        return [
            CarrierConnectionStatus(
                "安达",
                bool(anda_username),
                "已配置账号" if anda_username else "未配置账号",
            ),
            CarrierConnectionStatus("超鸿", True, "无需账号登录"),
            CarrierConnectionStatus(
                "易通",
                bool(yitong_token),
                "已保存登录状态"
                if yitong_token
                else "需要完成验证码登录",
            ),
        ]

    def validate_all(self, user_id: str) -> list[CarrierConnectionStatus]:
        """一键任务开始前真实验证全部货代；任何一家失败都由调用方停止。"""
        with self._user_lock(user_id):
            database = ProjectDatabase(self.database_path, profile_id=user_id)
            statuses: list[CarrierConnectionStatus] = []

            anda_credentials = database.load_credentials("anda")
            if anda_credentials is None:
                statuses.append(CarrierConnectionStatus("安达", False, "未配置账号"))
            else:
                try:
                    AndaClient(retries=self.settings.retries).login(
                        anda_credentials.username, anda_credentials.password
                    )
                    statuses.append(CarrierConnectionStatus("安达", True, "登录成功"))
                except CarrierError as exc:
                    statuses.append(
                        CarrierConnectionStatus(
                            "安达", False, exc.user_message
                        )
                    )
                except Exception:
                    logger.exception("carrier_validation_unexpected carrier=anda user=%s", user_id)
                    statuses.append(CarrierConnectionStatus("安达", False, "登录验证发生未预期错误"))

            try:
                ChaoHongClient(retries=self.settings.retries).query_batch(
                    ["FBA_CONNECTION_CHECK"]
                )
                statuses.append(CarrierConnectionStatus("超鸿", True, "接口可用"))
            except CarrierError as exc:
                statuses.append(
                    CarrierConnectionStatus(
                        "超鸿", False, exc.user_message
                    )
                )
            except Exception:
                logger.exception(
                    "carrier_validation_unexpected carrier=chaohong user=%s", user_id
                )
                statuses.append(
                    CarrierConnectionStatus("超鸿", False, "接口验证发生未预期错误")
                )

            yitong_token = database.load_session_token("yitong")
            if not yitong_token:
                statuses.append(
                    CarrierConnectionStatus("易通", False, "未登录或登录状态已过期")
                )
            else:
                try:
                    client = YiTongClient(retries=self.settings.retries)
                    client.token = yitong_token
                    client.validate_token()
                    statuses.append(CarrierConnectionStatus("易通", True, "登录有效"))
                except AuthenticationError as exc:
                    database.delete_session_token("yitong")
                    statuses.append(
                        CarrierConnectionStatus(
                            "易通", False, exc.user_message
                        )
                    )
                except CarrierError as exc:
                    # 网络或服务端临时故障不能删除仍可能有效的登录令牌。
                    statuses.append(CarrierConnectionStatus("易通", False, exc.user_message))
                except Exception:
                    logger.exception(
                        "carrier_validation_unexpected carrier=yitong user=%s", user_id
                    )
                    statuses.append(
                        CarrierConnectionStatus("易通", False, "登录验证发生未预期错误")
                    )
            logger.info(
                "carrier_validation_complete user=%s connected=%d total=%d",
                user_id,
                sum(item.connected for item in statuses),
                len(statuses),
            )
            return statuses

    def _anda_service(self, database: ProjectDatabase):
        credentials = database.load_credentials("anda")
        if credentials is None:
            return UnavailableQueryService("安达", "当前账号尚未配置安达账号")
        client = AndaClient(retries=self.settings.retries)

        def login() -> None:
            client.login(credentials.username, credentials.password)

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
