"""Shared handler behavior for NodeNormalization API endpoints."""

from biothings.web.handlers import BaseHandler
from tornado.web import HTTPError

from nodenorm.babel import BabelRelease, BabelReleaseUnavailable


class NodeNormalizationBaseHandler(BaseHandler):
    """Base handler that keeps the lightweight BioThings handler plus CORS."""

    cors_origin = "*"
    cors_methods = "GET, POST, HEAD, OPTIONS"
    cors_max_age = "600"

    def set_default_headers(self):
        super().set_default_headers()

        origin = self.request.headers.get("Origin")
        if origin is None:
            return

        requested_headers = self.request.headers.get("Access-Control-Request-Headers")

        self.set_header("Access-Control-Allow-Origin", self.cors_origin)
        self.set_header("Access-Control-Allow-Methods", self.cors_methods)
        self.set_header("Access-Control-Allow-Headers", requested_headers or "*")
        self.set_header("Access-Control-Max-Age", self.cors_max_age)
        self.set_header("Access-Control-Expose-Headers", "X-Babel-Version")

    def select_babel_release(self, version: str = None) -> BabelRelease:
        """Resolve once per request without changing the shared Elasticsearch target."""
        try:
            release = self.biothings.elasticsearch.babel.resolve(version)
        except ValueError as exc:
            raise HTTPError(400, reason=str(exc)) from exc
        except BabelReleaseUnavailable as exc:
            raise HTTPError(503, reason=str(exc)) from exc
        self.set_header("X-Babel-Version", release.version)
        return release

    def options(self, *args, **kwargs):
        self.finish()
