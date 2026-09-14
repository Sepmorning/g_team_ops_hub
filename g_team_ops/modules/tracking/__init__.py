"""物流查询与共享表同步模块。"""

def build_router(ctx):
    # Keep the domain service importable without initializing the web application.
    from .router import build_router as create_router
    return create_router(ctx)

__all__ = ["build_router"]
