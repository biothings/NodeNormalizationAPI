"""
Keep src/nodenorm/webapp/openapi.json in step with the handlers.

The OpenAPI document is written by hand and served as a static file (Tornado has nothing like
FastAPI's schema generation), so nothing stops it drifting from the code except these tests.
If one fails after a change to a handler, edit the JSON; if it fails after a change to the
JSON, the document now promises something the code doesn't do.
"""

import dataclasses
import importlib.resources
import inspect
import json
import re

import jsonschema
import tornado.web
from openapi_spec_validator import validate
from tornado.testing import AsyncHTTPTestCase

import nodenorm
from nodenorm.handlers import build_handlers
from nodenorm.handlers.base import NodeNormalizationBaseHandler
from nodenorm.handlers.conflations import ValidConflationsHandler
from nodenorm.handlers.set_identifiers import SetIDResponse

SPEC = json.loads((importlib.resources.files(nodenorm) / "webapp" / "openapi.json").read_text(encoding="utf-8"))

# Only the handlers that answer the API itself: not the static files, index or redirects.
API_HANDLERS = {
    pattern.rstrip("?"): handler
    for pattern, handler, *_ in build_handlers().values()
    if isinstance(handler, type) and issubclass(handler, NodeNormalizationBaseHandler)
}


def test_document_is_valid_openapi():
    validate(SPEC)


def test_every_route_is_documented_and_every_documented_path_is_routed():
    assert set(SPEC["paths"]) == set(API_HANDLERS)


def test_documented_methods_match_the_handlers():
    for path, handler in API_HANDLERS.items():
        documented = {method for method in SPEC["paths"][path] if method in ("get", "post")}
        implemented = {method for method in ("get", "post") if method in vars(handler)}
        assert documented == implemented, path


def test_documented_query_parameters_match_the_handlers():
    for path, handler in API_HANDLERS.items():
        documented = {p["name"] for p in SPEC["paths"][path]["get"].get("parameters", []) if p["in"] == "query"}
        implemented = set(re.findall(r'get_arguments?\("(\w+)"', inspect.getsource(handler.get)))
        assert documented == implemented, path


def test_setid_response_schema_matches_the_dataclass():
    documented = set(SPEC["components"]["schemas"]["SetIDResponse"]["properties"])
    assert documented == {field.name for field in dataclasses.fields(SetIDResponse)}


class TestAllowedConflations(AsyncHTTPTestCase):
    """Regression: this endpoint returned HTTP 500 because it finished a bare list."""

    def get_app(self) -> tornado.web.Application:
        return tornado.web.Application([(r"/get_allowed_conflations", ValidConflationsHandler)])

    def test_response_matches_the_documented_schema(self):
        response = self.fetch("/get_allowed_conflations")

        assert response.code == 200
        body = json.loads(response.body)
        jsonschema.validate(body, SPEC["components"]["schemas"]["ConflationList"])
        assert {"GeneProtein", "DrugChemical"} <= set(body["conflations"])
