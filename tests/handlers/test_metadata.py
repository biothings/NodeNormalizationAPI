"""
Tests for mocking the metadata handling
"""

import json

import tornado
from tornado.testing import AsyncHTTPTestCase
from tornado.httpclient import AsyncHTTPClient

from web.handlers import EXTRA_HANDLERS
from web.application import PendingAPI
from web.settings.configuration import load_configuration


class TestMetadataHandler(AsyncHTTPTestCase):

    def get_app(self) -> tornado.web.Application:
        configuration = load_configuration("config_web")
        app_handlers = EXTRA_HANDLERS
        app_settings = {"static_path": "static"}
        application = PendingAPI.get_app(configuration, app_settings, app_handlers)
        return application

    def test_plugin_metadata(self):
        """
        Tests the plugin metadata responses

        Accumulates all of the API's found through the /api/list endpoint
        and then test each one to ensure that the metadata structure is consistent
        across all endpoints

        Also ensures that we correctly handle SMARTAPI identifier integration
        for plugins that have and don't have it when reporting the metadata
        """
        http_client = AsyncHTTPClient()
        http_client.fetch(self.get_url("/api/list"), self.stop, method="GET")
        api_list_response = self.wait()

        api_list = json.loads(api_list_response.body.decode("utf-8"))
        self.assertEqual(api_list_response.code, 200)

        for plugin_api in api_list:
            plugin_metadata_endpoint = f"/{plugin_api}/metadata"
            http_client = AsyncHTTPClient()
            http_client.fetch(self.get_url(plugin_metadata_endpoint), self.stop, method="GET")
            metadata_response = self.wait()
            breakpoint()
            pass
