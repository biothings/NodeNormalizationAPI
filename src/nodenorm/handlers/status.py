import importlib.resources
import json
from functools import lru_cache

from tornado.web import HTTPError

import nodenorm
from nodenorm.babel import get_babel_release_url
from nodenorm.biolink import BIOLINK_MODEL_VERSION
from nodenorm.handlers.base import NodeNormalizationBaseHandler
from nodenorm.status_health import StatusHealthCache


@lru_cache(maxsize=None)
def get_openapi_version() -> str:
    """Read the API version from the openapi.json spec bundled with the webapp."""
    openapi_path = importlib.resources.files(nodenorm) / "webapp" / "openapi.json"
    openapi_spec = json.loads(openapi_path.read_text(encoding="utf-8"))
    return openapi_spec["info"]["version"]


class NodeNormHealthHandler(NodeNormalizationBaseHandler):
    """Report service status and release metadata."""

    name = "health"

    def set_default_headers(self):
        super().set_default_headers()
        self.set_header("Cache-Control", "no-store")

    async def get(self):
        elasticsearch = self.biothings.elasticsearch
        releases = elasticsearch.babel
        babel_status = {
            "default_version": releases.default_version,
            "available_versions": {version: get_babel_release_url(version) for version in releases.available_versions},
            "indices": {version: releases.resolve(version).search_indices for version in releases.available_versions},
            "checked_at": elasticsearch.babel_checked_at,
        }
        if releases.errors:
            babel_status["errors"] = releases.errors
        refresh_error = elasticsearch.babel_refresh_error
        if refresh_error is not None:
            babel_status["refresh_error"] = refresh_error

        release = releases.resolve()
        health = getattr(elasticsearch, "status_health_cache", None)
        if health is None or health.configuration != (release.version, tuple(release.search_indices)):
            health = StatusHealthCache(elasticsearch.async_client, release)
            elasticsearch.status_health_cache = health
        if not await health.check():
            raise HTTPError(503, reason="Elasticsearch health check failed")

        status_response = {
            "status": "running",
            "deployment_environment": self.biothings.config.deployment_environment,
            "version": get_openapi_version(),
            "biolink_model_toolkit_version": BIOLINK_MODEL_VERSION,
            "babel": babel_status,
        }
        self.finish(status_response)
