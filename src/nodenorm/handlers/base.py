"""Shared handler behavior for NodeNormalization API endpoints."""

import json

import tornado.escape
from biothings.web.handlers import BaseHandler
from tornado.web import HTTPError


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

    def options(self, *args, **kwargs):
        self.finish()

    def load_json_body(self):
        """Parse the request body as JSON, answering 400 rather than 500 if it can't be.

        json.loads() raises UnicodeDecodeError, not JSONDecodeError, for bytes that aren't valid UTF-8.
        """
        try:
            return json.loads(self.request.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise HTTPError(detail=f"The body is not valid JSON: {error}", status_code=400)

    def write(self, chunk):
        # Tornado refuses a top-level list ("Lists not accepted for security reasons"), a guard
        # against JSON hijacking through <script> tags in very old browsers. This is a public
        # JSON API that allows any origin anyway, and NodeNorm Redis returns lists, so encode a
        # list the way Tornado encodes a dict.
        if isinstance(chunk, list):
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            chunk = tornado.escape.json_encode(chunk)
        super().write(chunk)
