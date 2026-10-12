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
from types import SimpleNamespace

import jsonschema
import tornado.web
from openapi_spec_validator import validate
from tornado.testing import AsyncHTTPTestCase

import nodenorm
from nodenorm.handlers import build_handlers
from nodenorm.handlers.base import NodeNormalizationBaseHandler
from nodenorm.handlers.conflations import ValidConflationsHandler
from nodenorm.handlers.semantic_types import SemanticTypeHandler
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
    # The whole class, not just get(), so a parameter read in a helper method still counts.
    for path, handler in API_HANDLERS.items():
        documented = {p["name"] for p in SPEC["paths"][path]["get"].get("parameters", []) if p["in"] == "query"}
        implemented = set(re.findall(r'get_arguments?\("(\w+)"', inspect.getsource(handler)))
        assert documented == implemented, path


def resolve(schema: dict) -> dict:
    """Follow a local $ref, and an array to its items, down to the object schema of one body item."""
    if "$ref" in schema:
        return resolve(SPEC["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]])
    if schema.get("type") == "array":
        return resolve(schema["items"])
    return schema


def test_documented_request_bodies_match_the_handlers():
    # post() reads each body field with .get("field", default), whether the body is one object
    # (get_normalized_nodes) or a list of them (get_setid).
    posting = {path: handler for path, handler in API_HANDLERS.items() if "post" in vars(handler)}
    assert posting, "no handler defines post()"
    for path, handler in posting.items():
        body_schema = SPEC["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
        documented = set(resolve(body_schema)["properties"])
        implemented = set(re.findall(r'\.get\("(\w+)"', inspect.getsource(handler.post)))
        assert documented == implemented, path


def test_setid_response_schema_matches_the_dataclass():
    documented = set(SPEC["components"]["schemas"]["SetIDResponse"]["properties"])
    assert documented == {field.name for field in dataclasses.fields(SetIDResponse)}


class TestOpenAPIAddresses(AsyncHTTPTestCase):
    """/openapi.json is where NodeNorm Redis serves its document; /webapp/openapi.json is the older address."""

    def get_app(self) -> tornado.web.Application:
        return tornado.web.Application(list(build_handlers().values()))

    def test_the_document_is_served_at_both_addresses(self):
        for path in ("/openapi.json", "/webapp/openapi.json"):
            response = self.fetch(path, follow_redirects=False)

            assert response.code == 200, path
            assert response.headers["Content-Type"].startswith("application/json"), path
            assert json.loads(response.body) == SPEC, path

    def test_only_the_exact_path_matches(self):
        assert self.fetch("/openapiXjson", follow_redirects=False).code == 404

    def test_swagger_ui_loads_the_root_address(self):
        index = (importlib.resources.files(nodenorm) / "webapp" / "index.html").read_text(encoding="utf-8")
        assert 'url: "/openapi.json"' in index


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


class FakeElasticsearch:
    """Just enough of AsyncElasticsearch for the semantic-types aggregation."""

    async def search(self, **kwargs):
        return SimpleNamespace(body={"aggregations": {"unique_types": {"buckets": [{"key": "biolink:Disease"}]}}})


class TestSemanticTypes(AsyncHTTPTestCase):
    """Regression: the handler lower-cased every class ("biolink:namedthing")."""

    def get_app(self) -> tornado.web.Application:
        app = tornado.web.Application([(r"/get_semantic_types", SemanticTypeHandler)])
        app.biothings = SimpleNamespace(
            elasticsearch=SimpleNamespace(indices="nodenorm", async_client=FakeElasticsearch())
        )
        return app

    def test_types_are_biolink_class_curies(self):
        response = self.fetch("/get_semantic_types")

        assert response.code == 200
        body = json.loads(response.body)
        jsonschema.validate(body, SPEC["components"]["schemas"]["SemanticTypes"])
        types = body["semantic_types"]["types"]
        assert "biolink:Disease" in types and "biolink:NamedThing" in types
        assert all(re.fullmatch(r"biolink:[A-Z][A-Za-z]*", t) for t in types), types
