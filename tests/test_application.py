"""Application settings and routing coverage without Elasticsearch or BMT access."""

import json
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
from tornado.testing import AsyncHTTPTestCase
from tornado.web import RequestHandler

from nodenorm.application import NodeNormalizationAPI
from nodenorm.namespace import NodeNormalizationAPINamespace


class StubAPIHandler(RequestHandler):
    def get(self):
        self.finish({"status": "running"})


def build_application(options=None):
    """Keep real routes and configuration, replacing only external dependencies."""
    handler_modules = {}
    for module_name, handler_name in (
        ("conflations", "ValidConflationsHandler"),
        ("status", "NodeNormHealthHandler"),
        ("normalized_nodes", "NormalizedNodesHandler"),
        ("semantic_types", "SemanticTypeHandler"),
        ("set_identifiers", "SetIdentifierHandler"),
        ("version", "VersionHandler"),
    ):
        name = f"nodenorm.handlers.{module_name}"
        module = ModuleType(name)
        setattr(module, handler_name, StubAPIHandler)
        handler_modules[name] = module

    with (
        patch.dict("sys.modules", handler_modules),
        patch.object(NodeNormalizationAPINamespace, "configure_elasticsearch", return_value=SimpleNamespace()),
    ):
        namespace = NodeNormalizationAPINamespace(options or SimpleNamespace())
        return NodeNormalizationAPI.get_app(namespace)


def test_default_configuration_starts_autoreload():
    with patch("tornado.autoreload.start") as start:
        app = build_application()

    assert app.settings["debug"] is True
    assert app.settings["autoreload"] is True
    start.assert_called_once_with()
    assert "/status?" in app.biothings.handlers


@pytest.mark.parametrize("debug, autoreload", [(True, False), (False, True), (False, False)])
def test_custom_configuration_controls_autoreload(tmp_path, debug, autoreload):
    configuration_path = tmp_path / "config.json"
    configuration_path.write_text(
        json.dumps({"webserver": {"SETTINGS": {"debug": debug, "autoreload": autoreload}}}),
        encoding="utf-8",
    )

    with patch("tornado.autoreload.start") as start:
        app = build_application(SimpleNamespace(conf=configuration_path))

    assert app.settings["debug"] is debug
    assert app.settings["autoreload"] is autoreload
    if autoreload:
        start.assert_called_once_with()
    else:
        start.assert_not_called()


class TestApplicationRoutes(AsyncHTTPTestCase):
    def get_app(self):
        with patch("tornado.autoreload.start"):
            return build_application()

    def test_api_route_is_reachable_with_default_settings(self):
        response = self.fetch("/status")

        assert response.code == 200
        assert json.loads(response.body) == {"status": "running"}

    def test_root_serves_swagger_ui(self):
        response = self.fetch("/")

        assert response.code == 200
        assert response.headers["Content-Type"].startswith("text/html")
        assert b"SwaggerUIBundle" in response.body
        assert b"/webapp/openapi.json" in response.body

    def test_webapp_serves_openapi_spec(self):
        response = self.fetch("/webapp/openapi.json")

        assert response.code == 200
        specification = json.loads(response.body)
        assert "openapi" in specification
        assert "/status" in specification["paths"]

    def test_favicon_redirect_and_asset(self):
        response = self.fetch("/favicon.ico", follow_redirects=False)

        assert response.code == 301
        assert response.headers["Location"] == "/webapp/swaggerui/favicon-32x32.png"
        favicon = self.fetch(response.headers["Location"])
        assert favicon.code == 200
        assert favicon.headers["Content-Type"] == "image/png"
        assert favicon.body.startswith(b"\x89PNG\r\n\x1a\n")
