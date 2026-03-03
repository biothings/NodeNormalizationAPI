"""
Main entrypoint for launching the NodeNormalization web service
"""

import logging

from tornado.options import define, options

from nodenorm.application import NodeNormalizationAPI
from nodenorm.namespace import NodeNormalizationAPINamespace
from nodenorm.server import NodeNormalizationWebServer

logger = logging.getLogger(__name__)


# Command Line Options
# --------------------------

# Web Server Settings
# --------------------------
define("host", default=None, help="web server host ipv4 address")
define("port", default=None, help="web server host ipv4 port")

# Configuration Settings
# --------------------------
define("conf", default=None, help="override configuration file for settings configuration")


def main():
    """
    Entrypoint for the nodenormalization api application launcher

    Ported from the biothings.web.launcher

    We only have one "plugin" in this case to load, so we can short-cut some of
    the logic used from the pending.api application that assumes more than one
    """
    options.parse_command_line()
    configuration_namespace = NodeNormalizationAPINamespace(options)
    application_instance = NodeNormalizationAPI.get_app(configuration_namespace)
    webserver = NodeNormalizationWebServer(application_instance, configuration_namespace)
    webserver.start()


if __name__ == "__main__":
    main()
