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
from unittest import mock

import jsonschema
import pytest
import tornado.web
from openapi_spec_validator import validate
from tornado.testing import AsyncHTTPTestCase

import nodenorm
from nodenorm.handlers import build_handlers
from nodenorm.handlers.base import NodeNormalizationBaseHandler
from nodenorm.handlers.conflations import ValidConflationsHandler
from nodenorm.handlers.health import NodeNormHealthHandler
from nodenorm.handlers.normalized_nodes import NormalizedNodesHandler, parse_boolean
from nodenorm.handlers.semantic_types import SemanticTypeHandler
from nodenorm.handlers.set_identifiers import SetIdentifierHandler, SetIDResponse

SPEC = json.loads((importlib.resources.files(nodenorm) / "webapp" / "openapi.json").read_text(encoding="utf-8"))

# Only the handlers that answer the API itself: not the static files, index or redirects.
API_HANDLERS = {
    pattern: handler
    for pattern, handler, *_ in build_handlers().values()
    if isinstance(handler, type) and issubclass(handler, NodeNormalizationBaseHandler)
}


def test_document_is_valid_openapi():
    validate(SPEC)


def test_every_route_is_documented_and_every_documented_path_is_routed():
    assert set(SPEC["paths"]) == set(API_HANDLERS)


# Every method Tornado routes except OPTIONS, which the base handler answers for CORS preflight.
HTTP_METHODS = {method.lower() for method in tornado.web.RequestHandler.SUPPORTED_METHODS} - {"options"}


def test_documented_methods_match_the_handlers():
    for path, handler in API_HANDLERS.items():
        documented = set(SPEC["paths"][path]) & HTTP_METHODS
        implemented = {method for method in HTTP_METHODS if method in vars(handler)}
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
    # Body fields are read with .get("field", default), whether the body is one object
    # (get_normalized_nodes) or a list of them (get_setid). Like the query-parameter check, this
    # scans the whole class, so a field read in a helper method still counts.
    posting = {path: handler for path, handler in API_HANDLERS.items() if "post" in vars(handler)}
    assert posting, "no handler defines post()"
    for path, handler in posting.items():
        body_schema = SPEC["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
        documented = set(resolve(body_schema)["properties"])
        implemented = set(re.findall(r'\.get\("(\w+)"', inspect.getsource(handler)))
        assert documented == implemented, path


def test_setid_response_schema_matches_the_dataclass():
    documented = set(SPEC["components"]["schemas"]["SetIDResponse"]["properties"])
    assert documented == {field.name for field in dataclasses.fields(SetIDResponse)}


class TestOpenAPIRedirect(AsyncHTTPTestCase):
    """/openapi.json is where NodeNorm Redis, SmartAPI and Babel Validation look for the document."""

    def get_app(self) -> tornado.web.Application:
        return tornado.web.Application(list(build_handlers().values()))

    def test_openapi_json_redirects_to_the_document(self):
        response = self.fetch("/openapi.json", follow_redirects=False)

        assert response.code == 301
        assert response.headers["Location"] == "/webapp/openapi.json"

    def test_only_the_exact_path_redirects(self):
        assert self.fetch("/openapiXjson", follow_redirects=False).code == 404

    def test_api_paths_are_matched_exactly(self):
        # Regression: patterns like r"/get_setid?" made the last letter optional, so /get_seti was routed.
        for path in API_HANDLERS:
            assert self.fetch(path[:-1]).code == 404, path


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

    def test_head_is_answered(self):
        assert self.fetch("/get_allowed_conflations", method="HEAD").code == 200


class TestSetIDErrors(AsyncHTTPTestCase):
    """Answers that /get_setid gives without reaching Elasticsearch."""

    def get_app(self) -> tornado.web.Application:
        app = tornado.web.Application([(r"/get_setid", SetIdentifierHandler)])
        app.biothings = SimpleNamespace()
        return app

    def test_get_reports_an_unknown_conflation(self):
        # Regression: generate_setid() returned the dataclass itself here, which Tornado can't write (HTTP 500).
        response = self.fetch("/get_setid?curie=MESH:D014867&conflation=Bogus")

        assert response.code == 200
        body = json.loads(response.body)
        jsonschema.validate(body, SPEC["components"]["schemas"]["SetIDResponse"])
        assert "Bogus" in body["error"]
        assert body["setid"] is None

    def test_post_reports_an_unknown_conflation(self):
        body = json.dumps([{"curies": ["MESH:D014867"], "conflations": ["Bogus"]}])
        response = self.fetch("/get_setid", method="POST", body=body)

        assert response.code == 200
        result = json.loads(response.body)
        jsonschema.validate(result[0], SPEC["components"]["schemas"]["SetIDResponse"])
        assert "Bogus" in result[0]["error"]

    def test_post_answers_a_set_with_no_curies(self):
        # The other early return in generate_setid(); it too used to return the dataclass (HTTP 500).
        empty_lookup = mock.AsyncMock(return_value={})
        with mock.patch("nodenorm.handlers.set_identifiers.get_normalized_nodes", empty_lookup):
            response = self.fetch("/get_setid", method="POST", body=json.dumps([{"curies": []}]))

        assert response.code == 200
        result = json.loads(response.body)
        jsonschema.validate(result[0], SPEC["components"]["schemas"]["SetIDResponse"])
        assert result[0]["normalized_curies"] == [] and result[0]["setid"] is None

    def test_post_rejects_a_body_that_is_not_an_array_of_objects(self):
        # A single object was the documented body before; it used to fail with a 500.
        for body in (
            '{"curies": ["MESH:D014867"]}',
            '["MESH:D014867"]',
            "[]",
            "not json",
            "",
            b'[{"curies": ["\xff"]}]',
        ):
            assert self.fetch("/get_setid", method="POST", body=body).code == 400, body


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


class UnreachableElasticsearch:
    """An AsyncElasticsearch whose cluster is down."""

    class indices:
        @staticmethod
        async def get_mapping(**kwargs):
            raise ConnectionError("cluster unreachable")


class TestStatusWithoutElasticsearch(AsyncHTTPTestCase):
    """Regression: the index mapping was read outside the try, so a down cluster gave a 500, not status=error."""

    def get_app(self) -> tornado.web.Application:
        app = tornado.web.Application([(r"/status", NodeNormHealthHandler)])
        app.biothings = SimpleNamespace(
            elasticsearch=SimpleNamespace(indices="nodenorm", async_client=UnreachableElasticsearch())
        )
        return app

    def test_status_is_error(self):
        response = self.fetch("/status")

        assert response.code == 200
        body = json.loads(response.body)
        jsonschema.validate(
            body, SPEC["paths"]["/status"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        )
        assert body["status"] == "error"


@pytest.mark.parametrize("value", [True, "true", "TRUE", "1", "yes", "on", "t", "Y"])
def test_true_flags(value):
    assert parse_boolean("conflate", value) is True


@pytest.mark.parametrize("value", [False, "false", "False", "0", "no", "off", "f", "N"])
def test_false_flags(value):
    assert parse_boolean("conflate", value) is False


@pytest.mark.parametrize("value", ["", "maybe", "2", None, 0])
def test_other_flags_are_rejected(value):
    # Regression: anything but "false" used to mean true, so conflate=0 turned conflation on.
    with pytest.raises(tornado.web.HTTPError) as raised:
        parse_boolean("conflate", value)
    assert raised.value.status_code == 400


class TestNormalizedNodesFlags(AsyncHTTPTestCase):
    """A flag that isn't a boolean is rejected before Elasticsearch is queried."""

    def get_app(self) -> tornado.web.Application:
        app = tornado.web.Application([(r"/get_normalized_nodes", NormalizedNodesHandler)])
        app.biothings = SimpleNamespace()
        return app

    def test_get_rejects_a_flag_that_is_not_a_boolean(self):
        assert self.fetch("/get_normalized_nodes?curie=MESH:D014867&conflate=maybe").code == 400

    def test_post_rejects_a_flag_that_is_not_a_boolean(self):
        body = json.dumps({"curies": ["MESH:D014867"], "conflate": "maybe"})
        assert self.fetch("/get_normalized_nodes", method="POST", body=body).code == 400

    def test_post_rejects_a_body_that_is_not_a_json_object(self):
        # Each of these used to fail with a 500.
        for body in ('["MESH:D014867"]', "not json", b'{"curies": ["\xff"]}'):
            assert self.fetch("/get_normalized_nodes", method="POST", body=body).code == 400, body


async def fake_generate_setid(biothings, curies, conflations):
    return dataclasses.asdict(SetIDResponse(curies=curies, conflations=conflations, normalized_curies=sorted(curies)))


class TestSetIdentifierPost(AsyncHTTPTestCase):
    """Regression: POST returned an index-keyed object because Tornado refused to write a list."""

    def setUp(self):
        # Patched per test and undone afterwards: assigning the module attribute instead would leak
        # the fake into tests/test_set_identifiers.py whenever pytest-randomly runs it later.
        patcher = mock.patch("nodenorm.handlers.set_identifiers.generate_setid", fake_generate_setid)
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    def get_app(self) -> tornado.web.Application:
        app = tornado.web.Application([(r"/get_setid", SetIdentifierHandler)])
        app.biothings = None
        return app

    def test_returns_one_response_per_set_as_a_list(self):
        sets = [{"curies": ["MESH:D014867"]}, {"curies": ["NCIT:C34373"], "conflations": ["GeneProtein"]}]
        response = self.fetch("/get_setid", method="POST", body=json.dumps(sets))

        assert response.code == 200
        assert response.headers["Content-Type"].startswith("application/json")
        body = json.loads(response.body)
        assert isinstance(body, list) and [r["curies"] for r in body] == [s["curies"] for s in sets]
        for result in body:
            jsonschema.validate(result, SPEC["components"]["schemas"]["SetIDResponse"])
