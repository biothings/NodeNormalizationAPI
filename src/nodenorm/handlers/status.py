import importlib.resources
import json
from functools import lru_cache

from elasticsearch import AsyncElasticsearch

import nodenorm
from nodenorm.babel import get_babel_release_url, get_babel_version
from nodenorm.biolink import BIOLINK_MODEL_VERSION
from nodenorm.handlers.base import NodeNormalizationBaseHandler


@lru_cache(maxsize=None)
def get_openapi_version() -> str:
    """Read the API version from the openapi.json spec bundled with the webapp."""
    openapi_path = importlib.resources.files(nodenorm) / "webapp" / "openapi.json"
    openapi_spec = json.loads(openapi_path.read_text(encoding="utf-8"))
    return openapi_spec["info"]["version"]


class NodeNormHealthHandler(NodeNormalizationBaseHandler):
    """Report service status and release metadata."""

    name = "health"

    async def get(self):
        async_client: AsyncElasticsearch = self.biothings.elasticsearch.async_client
        search_indices = self.biothings.elasticsearch.indices

        mapping_response = await async_client.indices.get_mapping(index=search_indices)
        index_mapping = next(iter(mapping_response.values()))
        # Validate live default metadata while reporting the releases resolved at startup.
        get_babel_version(index_mapping)

        releases = self.biothings.elasticsearch.babel
        babel_status = {
            "default_version": releases.default_version,
            "available_versions": {version: get_babel_release_url(version) for version in releases.available_versions},
        }
        if releases.errors:
            babel_status["errors"] = releases.errors

        status_response = {
            "status": "running",
            "deployment_environment": self.biothings.config.deployment_environment,
            "version": get_openapi_version(),
            "biolink_model_toolkit_version": BIOLINK_MODEL_VERSION,
            "babel": babel_status,
        }
        self.finish(status_response)
