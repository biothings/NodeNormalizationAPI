"""
Main entrypoint for launching the NodeNormalization web service
"""

import logging
import sys
import pathlib
import importlib.resources

from tornado.options import define, options

from nodenorm.application import NodeNormalizationAPILauncher

logger = logging.getLogger(__name__)


# Command Line Options
# --------------------------

# Web Server Settings
# --------------------------
define("address", default="localhost", help="web server host ipv4 address. Defaults to localhost")
define("port", default=8000, help="web server host ipv4 port. Defaults to 8000")
define("autoreload", default=False, help="toggle web server auto reload when file changes are detected")

# Configuration Settings
# --------------------------
define("conf", default=None, help="override configuration file for settings configuration")

# Logger Settings
# --------------------------
define("debug", default=False, help="toggle web server logging preferences to increase logging output")


def main():
    """
    Entrypoint for the nodenormalization api application launcher

    Ported from the biothings.web.launcher

    We only have one "plugin" in this case to load, so we can short-cut some of
    the logic used from the pending.api application that assumes more than one
    """
    use_curl = False
    app_settings = {"static_path": "static"}

    options.parse_command_line()
    breakpoint()

    # with open(

    launcher = NodeNormalizationAPILauncher(options, app_settings, use_curl)
    launcher.start(host=launcher.host, port=options.port)


if __name__ == "__main__":
    main()
