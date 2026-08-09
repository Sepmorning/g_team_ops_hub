"""兼容入口；应用装配统一位于 :mod:`anda_tracker.web.factory`。"""

from .factory import create_app

__all__ = ["create_app"]
