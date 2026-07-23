from __future__ import annotations


class CarrierError(Exception):
    """可安全展示的货代模块基础异常。"""

    category = "unknown"
    retryable = False

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class NetworkError(CarrierError):
    category = "network"
    retryable = True


class AuthenticationError(CarrierError):
    category = "authentication"


class RateLimitError(CarrierError):
    category = "rate_limit"
    retryable = True


class ServerError(CarrierError):
    category = "server"
    retryable = True


class ResponseError(CarrierError):
    category = "response"


class ConfigurationError(CarrierError):
    category = "configuration"
