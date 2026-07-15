import importlib.resources
import json
from functools import lru_cache
from urllib.parse import urlparse

from elasticsearch import AsyncElasticsearch

import nodenorm
from nodenorm.biolink import BIOLINK_MODEL_VERSION
from nodenorm.handlers.base import NodeNormalizationBaseHandler


@lru_cache(maxsize=None)
def get_openapi_version() -> str:
    """Read the API version from the openapi.json spec bundled with the webapp."""
    openapi_path = importlib.resources.files(nodenorm) / "webapp" / "openapi.json"
    openapi_spec = json.loads(openapi_path.read_text(encoding="utf-8"))
    return openapi_spec["info"]["version"]


class NodeNormHealthHandler(NodeNormalizationBaseHandler):
    """
    Important Endpoints
    * /_cat/nodes
    """

    name = "health"

    async def get(self):
        async_client: AsyncElasticsearch = self.biothings.elasticsearch.async_client
        search_indices = self.biothings.elasticsearch.indices

        mapping_response = await async_client.indices.get_mapping(index=search_indices)
        index_mapping = next(iter(mapping_response.values()))
        compendia_url = index_mapping["mappings"]["_meta"]["src"]["nodenorm"]["url"]
        parsed_compendia_url = urlparse(compendia_url)
        babel_version = parsed_compendia_url.path.rstrip("/").rsplit("/", maxsplit=1)[-1]
        babel_markdown = f"https://github.com/ncatstranslator/Babel/blob/master/releases/{babel_version}.md"
        version = get_openapi_version()
        try:
            attributes = [
                "name",
                "cpu",
                "disk.avail",
                "disk.total",
                "disk.used",
                "disk.used_percent",
                "heap.current",
                "heap.max",
                "load_1m",
                "load_5m",
                "load_15m",
                "uptime,version",
            ]
            h_string = ",".join(attributes)
            cat_nodes_response = await async_client.cat.nodes(format="json", h=h_string)
            nodes_status = {node["name"]: node for node in cat_nodes_response}
            nodes = {"elasticsearch": {"nodes": nodes_status}}
        except Exception:
            status_response = {
                "status": "error",
                "version": version,
                "babel_version": babel_version,
                "babel_version_url": babel_markdown,
                "backend": "elasticsearch",
            }
        else:
            status_response = {
                "status": "running",
                "version": version,
                "babel_version": babel_version,
                "babel_version_url": babel_markdown,
                "backend": "elasticsearch",
                "biolink_model_toolkit_version": BIOLINK_MODEL_VERSION,
                **nodes,
            }

        self.finish(status_response)
