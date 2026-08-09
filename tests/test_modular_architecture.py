from g_team_ops.web.app import create_app as compatibility_create_app
from g_team_ops.web.factory import create_app as factory_create_app
from fastapi.testclient import TestClient


EXPECTED_HTTP_ROUTES = {
    ("GET", "/health"),
    ("GET", "/"),
    ("GET", "/setup"),
    ("POST", "/setup"),
    ("GET", "/login"),
    ("POST", "/login"),
    ("POST", "/logout"),
    ("GET", "/dashboard"),
    ("GET", "/change-password"),
    ("POST", "/change-password"),
    ("GET", "/admin"),
    ("POST", "/api/admin/users"),
    ("POST", "/api/admin/users/{user_id}/toggle"),
    ("POST", "/api/admin/users/{user_id}/reset-password"),
    ("GET", "/carriers"),
    ("GET", "/api/carriers/status"),
    ("POST", "/api/carriers/validation"),
    ("GET", "/api/carriers/credentials"),
    ("POST", "/api/carriers/anda"),
    ("DELETE", "/api/carriers/{kind}"),
    ("POST", "/api/carriers/yitong/captcha-challenges"),
    ("POST", "/api/carriers/yitong/session"),
    ("GET", "/shops"),
    ("POST", "/api/shops"),
    ("DELETE", "/api/shops/{shop_id}"),
    ("POST", "/api/shops/{shop_id}/validation"),
    ("GET", "/api/shops/{shop_id}/config"),
    ("POST", "/api/shops/{shop_id}/logistics-connection"),
    ("POST", "/api/shops/{shop_id}/listing-connection"),
    ("POST", "/api/shops/{shop_id}/sites"),
    ("POST", "/api/shops/{shop_id}/countries"),
    ("POST", "/api/shops/{shop_id}/sites/{site_id}/validation"),
    ("POST", "/api/shops/{shop_id}/discover-sites"),
    ("GET", "/tracking"),
    ("POST", "/api/tracking/query"),
    ("POST", "/api/shops/{shop_id}/tracking-sync"),
    ("GET", "/inventory"),
    ("GET", "/api/inventory/config"),
    ("DELETE", "/api/inventory/countries/{country_id}"),
    ("POST", "/api/inventory/countries/{country_id}/validation"),
    ("POST", "/api/inventory/imports/preview"),
    ("POST", "/api/inventory/imports/apply"),
}


def test_legacy_create_app_path_remains_compatible():
    assert compatibility_create_app is factory_create_app


def test_g_team_product_identity_is_exposed_by_app_and_health(tmp_path):
    app = factory_create_app(tmp_path / "data")

    assert app.title == "G组运营工作台"
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "G组运营工作台"}


def test_modular_router_refactor_preserves_http_contract(tmp_path):
    app = factory_create_app(tmp_path / "data")

    def route_pairs(routes):
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                yield from route_pairs(included.routes)
                continue
            for method in getattr(route, "methods", None) or set():
                if method != "HEAD" and not route.path.startswith("/static"):
                    yield method, route.path

    actual = {
        pair for pair in route_pairs(app.routes)
    }
    assert actual == EXPECTED_HTTP_ROUTES
