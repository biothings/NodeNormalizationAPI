import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


class FakeBiolinkToolkit:
    def get_ancestors(self, biolink_type):
        return [biolink_type]

    def get_element(self, ancestor):
        return {"class_uri": ancestor}


def load_normalized_nodes_module():
    module_name = "_normalized_nodes_under_test"
    module_path = Path(__file__).parents[1] / "src" / "nodenorm" / "handlers" / "normalized_nodes.py"
    fake_biolink = ModuleType("nodenorm.biolink")
    fake_biolink.BIOLINK_MODEL_VERSION = "test"
    fake_biolink.toolkit = FakeBiolinkToolkit()

    original_biolink = sys.modules.get("nodenorm.biolink")
    sys.modules["nodenorm.biolink"] = fake_biolink
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    finally:
        if original_biolink is None:
            sys.modules.pop("nodenorm.biolink", None)
        else:
            sys.modules["nodenorm.biolink"] = original_biolink

    return module


normalized_nodes = load_normalized_nodes_module()
_lookup_curie_metadata = normalized_nodes._lookup_curie_metadata
_lookup_equivalent_identifiers = normalized_nodes._lookup_equivalent_identifiers
create_normalized_node = normalized_nodes.create_normalized_node
NormalizedNode = normalized_nodes.NormalizedNode


class FakeAsyncElasticsearch:
    def __init__(self, response_batches):
        self.response_batches = list(response_batches)
        self.calls = []

    async def msearch(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(body={"responses": self.response_batches.pop(0)})


def fake_namespace(response_batches, indices=None):
    return SimpleNamespace(
        elasticsearch=SimpleNamespace(
            async_client=FakeAsyncElasticsearch(response_batches),
            indices=indices or ["nodenorm"],
        )
    )


def hit_response(curie, source=None, total=1):
    if source is None:
        source = {
            "identifiers": [{"i": curie, "l": curie}],
            "type": "biolink:ChemicalEntity",
            "ic": 1.0,
            "preferred_name": curie,
            "taxa": [],
        }
    return {"hits": {"total": {"value": total}, "hits": [{"_id": curie, "_source": source}]}}


def no_hit_response():
    return {"hits": {"total": {"value": 0}, "hits": []}}


@pytest.mark.asyncio
async def test_create_normalized_node_aggregates_descriptions_when_requested():
    node = NormalizedNode(
        curie="NCIT:C34373",
        canonical_identifier="MONDO:0004976",
        preferred_label="amyotrophic lateral sclerosis",
        information_content=74.9,
        identifiers=[
            {"i": "MONDO:0004976", "l": "amyotrophic lateral sclerosis", "d": ["first description"]},
            {"i": "NCIT:C34373", "l": "Amyotrophic Lateral Sclerosis", "d": ["second description"]},
            {"i": "UMLS:C0002736", "l": "Amyotrophic Lateral Sclerosis", "d": ["first description", ""]},
            {"i": "MESH:D000690", "l": "Amyotrophic Lateral Sclerosis"},
        ],
        types=["biolink:Disease"],
        taxa=[],
    )

    response = await create_normalized_node(node, include_descriptions=True)

    assert response["id"]["description"] == "first description"
    assert response["descriptions"] == ["first description", "second description"]
    assert response["equivalent_identifiers"][0]["description"] == "first description"
    assert response["equivalent_identifiers"][1]["description"] == "second description"


@pytest.mark.asyncio
async def test_create_normalized_node_hides_descriptions_by_default():
    node = NormalizedNode(
        curie="NCIT:C34373",
        canonical_identifier="MONDO:0004976",
        preferred_label="amyotrophic lateral sclerosis",
        information_content=74.9,
        identifiers=[{"i": "MONDO:0004976", "l": "amyotrophic lateral sclerosis", "d": ["first description"]}],
        types=["biolink:Disease"],
        taxa=[],
    )

    response = await create_normalized_node(node)

    assert "description" not in response["id"]
    assert "descriptions" not in response
    assert "description" not in response["equivalent_identifiers"][0]


@pytest.mark.asyncio
async def test_lookup_equivalent_identifiers_uses_shared_msearch_index():
    namespace = fake_namespace([[hit_response("CHEBI:17310"), no_hit_response()]])

    lookup, malformed = await _lookup_equivalent_identifiers(namespace, ["CHEBI:17310", "MISSING:1"])

    assert set(lookup) == {"CHEBI:17310"}
    assert malformed == {"MISSING:1"}

    msearch_call = namespace.elasticsearch.async_client.calls[0]
    assert msearch_call["index"] == ["nodenorm"]
    assert msearch_call["searches"][0] == {}
    assert msearch_call["searches"][1]["query"]["bool"]["filter"][0]["terms"] == {"identifiers.i": ["CHEBI:17310"]}
    assert msearch_call["searches"][2] == {}
    assert msearch_call["searches"][3]["query"]["bool"]["filter"][0]["terms"] == {"identifiers.i": ["MISSING:1"]}


@pytest.mark.asyncio
async def test_lookup_equivalent_identifiers_rejects_msearch_response_count_mismatch():
    namespace = fake_namespace([[hit_response("CHEBI:17310")]])

    with pytest.raises(RuntimeError, match="returned 1 responses for 2 CURIEs"):
        await _lookup_equivalent_identifiers(namespace, ["CHEBI:17310", "CHEBI:12"])


@pytest.mark.asyncio
async def test_lookup_equivalent_identifiers_raises_on_per_search_error():
    namespace = fake_namespace([[{"error": {"type": "query_shard_exception", "reason": "boom"}}]])

    with pytest.raises(RuntimeError, match="Elasticsearch msearch failed for CURIE CHEBI:17310"):
        await _lookup_equivalent_identifiers(namespace, ["CHEBI:17310"])


@pytest.mark.asyncio
async def test_lookup_curie_metadata_falls_back_when_all_conflation_curies_are_missing(caplog):
    base_source = {
        "identifiers": [{"i": "BASE:1", "l": "base", "c": {"dc": ["MISSING:1"]}}],
        "type": "biolink:ChemicalEntity",
        "ic": 1.0,
        "preferred_name": "base",
        "taxa": [],
    }
    namespace = fake_namespace([[hit_response("BASE:1", base_source)], [no_hit_response()]])

    with caplog.at_level(logging.WARNING):
        nodes = await _lookup_curie_metadata(namespace, ["BASE:1"], {"DrugChemical": True})

    assert len(nodes) == 1
    assert nodes[0].curie == "BASE:1"
    assert [identifier["i"] for identifier in nodes[0].identifiers] == ["BASE:1"]
    assert "falling back to base normalized node" in caplog.text
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_lookup_curie_metadata_logs_skipped_conflation_curies_once(caplog):
    base_source = {
        "identifiers": [{"i": "BASE:1", "l": "base", "c": {"dc": ["MISSING:1", "CONF:1"]}}],
        "type": "biolink:ChemicalEntity",
        "ic": 1.0,
        "preferred_name": "base",
        "taxa": [],
    }
    conflation_source = {
        "identifiers": [{"i": "CONF:1", "l": "conflated"}],
        "type": "biolink:Drug",
        "ic": 2.0,
        "preferred_name": "conflated",
        "taxa": [],
    }
    namespace = fake_namespace(
        [
            [hit_response("BASE:1", base_source)],
            [no_hit_response(), hit_response("CONF:1", conflation_source)],
        ]
    )

    with caplog.at_level(logging.WARNING):
        nodes = await _lookup_curie_metadata(namespace, ["BASE:1"], {"DrugChemical": True})

    assert len(nodes) == 1
    assert [identifier["i"] for identifier in nodes[0].identifiers] == ["CONF:1"]
    skip_logs = [record for record in caplog.records if "Skipped 1 conflation CURIEs" in record.message]
    assert len(skip_logs) == 1
