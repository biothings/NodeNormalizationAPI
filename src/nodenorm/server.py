"""
NodeNormalization specific application builder for overriding
the default builder provided by the biothings.api package

Responsible for generating the tornado.web.Application instance
"""

import logging
from pprint import pformat

from biothings import __version__
import tornado.httpserver
import tornado.ioloop
import tornado.log
import tornado.options
import tornado.web

from nodenorm.namespace import NodeNormalizationAPINamespace
from nodenorm.application import NodeNormalizationAPI


logger = logging.getLogger(__name__)


class NodeNormalizationWebServer:
    def __init__(self, application: NodeNormalizationAPI, namespace: NodeNormalizationAPINamespace):
        logger.info("Biothings API %s", __version__)
        self.application = application
        self.namespace = namespace

        if self.namespace.config.webserver["ENABLE_CURL_CLIENT"]:
            self.enable_curl_httpclient()

    @staticmethod
    def enable_curl_httpclient():
        """
        Use curl implementation for tornado http clients.
        More on https://www.tornadoweb.org/en/stable/httpclient.html
        """
        curl_httpclient_option = "tornado.curl_httpclient.CurlAsyncHTTPClient"
        tornado.httpclient.AsyncHTTPClient.configure(curl_httpclient_option)

    def start(self):
        """
        Starts the HTTP server and IO loop used for running
        the pending.api backend
        """
        host = self.namespace.config.webserver["HOST"]
        port = int(self.namespace.config.webserver["PORT"])
        try:
            http_server = tornado.httpserver.HTTPServer(self.application, xheaders=True)
            http_server.listen(port, host)
        except Exception as gen_exc:
            logger.exception(gen_exc)
            logger.error("Unable to create server instance on [%s:%s]", host, port)

        logger.info(
            "nodenormalization-api web server is running on %s:%s ...\n nodenormalization handlers:\n%s",
            host,
            port,
            pformat(self.namespace.handlers, width=200),
        )

        try:
            loop = tornado.ioloop.IOLoop.instance()
            loop.start()
        except Exception as gen_exc:
            logger.exception(gen_exc)
            raise gen_exc
        finally:
            loop.close()
