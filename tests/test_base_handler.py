import json

import tornado.web
from tornado.testing import AsyncHTTPTestCase

from nodenorm.handlers.base import NodeNormalizationBaseHandler


class ListHandler(NodeNormalizationBaseHandler):
    async def get(self):
        self.finish([{"curie": "MESH:D014867"}, {"curie": "NCIT:C34373"}])


class TestWriteList(AsyncHTTPTestCase):
    """Tornado's own write() raises on a list, which made /get_allowed_conflations return HTTP 500."""

    def get_app(self) -> tornado.web.Application:
        return tornado.web.Application([(r"/list", ListHandler)])

    def test_a_list_is_written_as_json(self):
        response = self.fetch("/list")

        assert response.code == 200
        assert response.headers["Content-Type"] == "application/json; charset=UTF-8"
        assert json.loads(response.body) == [{"curie": "MESH:D014867"}, {"curie": "NCIT:C34373"}]
