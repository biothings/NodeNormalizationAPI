"""
NodeNormalization specific application builder for overriding
the default builder provided by the biothings.api package

Responsible for generating the tornado.web.Application instance
"""

import logging
from pprint import pformat
from types import SimpleNamespace

from biothings import __version__
from biothings.web import connections
from biothings.web.applications import TornadoBiothingsAPI
from biothings.web.services.metadata import BiothingsESMetadata
import tornado.httpserver
import tornado.ioloop
import tornado.log
import tornado.options
import tornado.web

from nodenorm.handlers import build_handlers

logger = logging.getLogger(__name__)


class NodeNormalizationAPILauncher:
    def __init__(
        self, options: tornado.options.OptionParser, app_handlers: list[tuple], app_settings: dict, use_curl: bool
    ):
        logging.info("Biothings API %s", __version__)
        self.handlers = app_handlers
        self.host = options.address
        self.settings = self.configure_settings(options, app_settings)
        self.config = load_configuration(options.conf)
        self.configure_logging()

        self.application = NodeNormalizationAPI.get_app(self.config, self.settings, self.handlers)

        if use_curl:
            self.enable_curl_httpclient()

    def configure_settings(self, options: tornado.options.OptionParser, app_settings: dict) -> dict:
        """
        Configure the `settings` attribute for the launcher
        """
        app_settings.update(debug=options.debug)
        app_settings.update(autoreload=options.autoreload)
        return app_settings

    def configure_logging(self):
        root_logger = logging.getLogger()

        logging.getLogger("urllib3").setLevel(logging.ERROR)
        logging.getLogger("elasticsearch").setLevel(logging.WARNING)

        if self.settings["debug"]:
            root_logger.setLevel(logging.DEBUG)
        else:
            root_logger.setLevel(logging.INFO)

    @staticmethod
    def enable_curl_httpclient():
        """
        Use curl implementation for tornado http clients.
        More on https://www.tornadoweb.org/en/stable/httpclient.html
        """
        curl_httpclient_option = "tornado.curl_httpclient.CurlAsyncHTTPClient"
        tornado.httpclient.AsyncHTTPClient.configure(curl_httpclient_option)

    def start(self, host: str = None, port: int = None):
        """
        Starts the HTTP server and IO loop used for running
        the pending.api backend
        """

        if host is None:
            host = "0.0.0.0"

        if port is None:
            port = 8000

        port = str(port)

        http_server = tornado.httpserver.HTTPServer(self.application, xheaders=True)
        http_server.listen(port, host)

        logger.info(
            "nodenormalization-api web server is running on %s:%s ...\n nodenormalization handlers:\n%s",
            host,
            port,
            pformat(self.application.biothings.handlers, width=200),
        )
        loop = tornado.ioloop.IOLoop.instance()
        loop.start()


class NodeNormalizationNamespace:
    """Simplied namespace instance for our NodeNormalization API.

    The namespace loads our configuration for the web API
    """

    def __init__(self, configuration: dict):
        self.config = configuration
        self.handlers = {}
        self.elasticsearch: SimpleNamespace = SimpleNamespace()
        self.configure_elasticsearch()

    def configure_elasticsearch(self):
        """Main configuration method for generating our elasticsearch client instance(s).

        Simplified significantly compared to the base namespace as we don't need any infrastructure
        for querying as we handle that in the handlers
        """
        self.elasticsearch = SimpleNamespace()

        self.elasticsearch.client = connections.es.get_client(self.config.ES_HOST, **self.config.ES_ARGS)
        self.elasticsearch.async_client = connections.es.get_async_client(self.config.ES_HOST, **self.config.ES_ARGS)

        self.elasticsearch.metadata = BiothingsESMetadata(
            self.config.ES_INDICES,
            self.elasticsearch.async_client,
        )


class NodeNormalizationAPI(TornadoBiothingsAPI):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def get_app(cls, config, settings=None, handlers=None):
        """
        Return the tornado.web.Application defined by this config.
        **Additional** settings and handlers are accepted as parameters.
        """
        biothings = NodeNormalizationNamespace(config)
        handlers = build_handlers()
        app = cls(handlers)
        app.biothings = biothings
        app.populate_handlers(handlers)
        return app

    def populate_handlers(self, handlers):
        """Populates the handler routes for the NodeNormalization API.

        These routes take the following form: `(regex, handler_class, options)` tuples
        <http://www.tornadoweb.org/en/stable/web.html#application-configuration>`_.

        Overrides the _get_handlers method provided by TornadoBiothingsAPI as we don't need
        the custom implementation for handling how we parse the handler path
        """
        for handler in handlers:
            self.biothings.handlers[handler[0]] = handler[1]
