"""Shared handler behavior for NodeNormalization API endpoints."""

from biothings.web.handlers import BaseHandler


class NodeNormalizationBaseHandler(BaseHandler):
    """Base handler that keeps the lightweight BioThings handler plus CORS."""

    cors_origin = "*"
    cors_methods = "GET, POST, HEAD, OPTIONS"
    cors_max_age = "600"

    def set_default_headers(self):
        origin = self.request.headers.get("Origin")
        if origin is None:
            return

        requested_headers = self.request.headers.get("Access-Control-Request-Headers")

        self.set_header("Access-Control-Allow-Origin", self.cors_origin)
        self.set_header("Access-Control-Allow-Methods", self.cors_methods)
        self.set_header("Access-Control-Allow-Headers", requested_headers or "*")
        self.set_header("Access-Control-Max-Age", self.cors_max_age)

    def options(self, *args, **kwargs):
        self.finish()
