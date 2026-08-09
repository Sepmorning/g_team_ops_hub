"""兼容入口；应用装配统一位于 :mod:`g_team_ops.web.factory`。"""

from .factory import create_app

__all__ = ["create_app"]
