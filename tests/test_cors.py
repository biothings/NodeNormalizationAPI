import tornado.web
from tornado.testing import AsyncHTTPTestCase

from nodenorm.handlers.base import NodeNormalizationBaseHandler

ORIGIN = "https://translatorsri.github.io"


class PreflightHandler(NodeNormalizationBaseHandler):
    async def get(self):
        self.finish({"ok": True})


def assert_cors_headers(headers, allowed_headers="*"):
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert headers["Access-Control-Allow-Credentials"] == "false"
    assert headers["Access-Control-Allow-Methods"] == "GET, POST, HEAD, OPTIONS"
    assert headers["Access-Control-Allow-Headers"] == allowed_headers
    assert headers["Access-Control-Max-Age"] == "600"


class TestCorsHeaders(AsyncHTTPTestCase):
    def get_app(self) -> tornado.web.Application:
        return tornado.web.Application(
            [
                (r"/version", PreflightHandler),
                (r"/get_normalized_nodes", PreflightHandler),
            ]
        )

    def test_get_response_includes_cors_headers_for_browser_origin(self):
        response = self.fetch("/version", headers={"Origin": ORIGIN})

        assert response.code == 200
        assert_cors_headers(response.headers)

    def test_preflight_returns_cors_headers(self):
        response = self.fetch(
            "/get_normalized_nodes",
            method="OPTIONS",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

        assert response.code == 200
        assert response.body == b""
        assert_cors_headers(response.headers, "content-type")
