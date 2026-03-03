import importlib.resources
import importlib.util
import json
import logging
import pathlib
import sys
import types

import tornado.options

from biothings.web import connections
from biothings.web.services.metadata import BiothingsESMetadata

import nodenorm


logger = logging.getLogger(__name__)


class NodeNormalizationAPINamespace:
    """Simplied namespace instance for our NodeNormalization API.

    The namespace loads our configuration for the web API
    """

    def __init__(self, option_configuration: tornado.options.OptionParser):
        self.handlers = {}
        self.default_configuration = "config.default.json"
        self.config: types.SimpleNamespace = self.load_configuration(option_configuration)
        self.elasticsearch: types.SimpleNamespace = self.configure_elasticsearch()
        if self._is_open_telemetry_configurable():
            self.configure_telemetry()

    def _is_open_telemetry_configurable(self) -> bool:
        """Check for verifying if we can configure opentelemetry."""
        opentelemetry_enabled = self.config.telemetry["OPENTELEMETRY_ENABLED"]

        opentelemetry_module = "opentelemetry"
        opentelemetry_installed = (
            opentelemetry_module not in sys.modules and importlib.util.find_spec(opentelemetry_module) is None
        )

        if not opentelemetry_enabled:
            logger.debug(
                "OPENTELEMETRY is disabled. If you wish to enable it, set the OPENTELEMETRY_ENABLED value to <True>"
            )
            return False

        if not opentelemetry_installed:
            logging.warning(
                (
                    "`opentelemetry` package not found, unable to enable opentelemetry."
                    "Use `pip install nodenorm[telemetry]` to install required packages."
                )
            )
            return False

        return opentelemetry_enabled and opentelemetry_installed

    def configure_elasticsearch(self) -> types.SimpleNamespace:
        """Main configuration method for generating our elasticsearch client instance(s).

        Simplified significantly compared to the base namespace as we don't need any infrastructure
        for querying as we perform query building in the handlers themselves directly
        """
        elasticsearch_namespace = types.SimpleNamespace()
        elasticsearch_configuration = self.config.elasticsearch

        elasticsearch_namespace.client = connections.es.get_client(
            elasticsearch_configuration["ES_HOST"], **elasticsearch_configuration["ES_ARGS"]
        )
        elasticsearch_namespace.async_client = connections.es.get_async_client(
            elasticsearch_configuration["ES_HOST"], **elasticsearch_configuration["ES_ARGS"]
        )
        elasticsearch_namespace.index = self._validate_elasticsearch_index(elasticsearch_namespace)
        return elasticsearch_namespace

    def _validate_elasticsearch_index(self, elasticsearch_namespace: types.SimpleNamespace) -> dict:
        """Validates the elasticsearch index / alias.

        Ensures we have a valid index pointing to our cluster for
        nodenorm. Raises an error if we cannot find a valid index
        or alias from the configuration
        """
        config_index = self.config.elasticsearch["ES_INDEX"]
        config_alias = self.config.elasticsearch["ES_ALIAS"]

        elasticsearch_index = set()
        if config_index != "" and elasticsearch_namespace.client.indices.exists(index=config_index):
            elasticsearch_index.add(config_index)
        elif elasticsearch_namespace.client.indices.exists_alias(name=config_alias):
            elasticsearch_index.add(config_alias)

        elasticsearch_index = list(elasticsearch_index)

        if len(elasticsearch_index) == 0:
            raise RuntimeError("Unable to validate nodenorm elasticsearch index / alias")

        return elasticsearch_index

    def configure_telemetry(self):
        """Configure our opentelemetry for our web API."""
        from opentelemetry.instrumentation.tornado import TornadoInstrumentor  # pylint: disable=import-outside-toplevel
        from opentelemetry.exporter.jaeger.thrift import JaegerExporter  # pylint: disable=import-outside-toplevel
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource  # pylint: disable=import-outside-toplevel
        from opentelemetry.sdk.trace import TracerProvider  # pylint: disable=import-outside-toplevel
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # pylint: disable=import-outside-toplevel
        from opentelemetry import trace  # pylint: disable=import-outside-toplevel

        TornadoInstrumentor().instrument()

        trace_exporter = JaegerExporter(
            agent_host_name=self.config.telemetry["OPENTELEMETRY_JAEGER_HOST"],
            agent_port=self.config.telemetry["OPENTELEMETRY_JAEGER_PORT"],
            udp_split_oversized_batches=True,
        )

        trace_provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: self.config.telemetry["OPENTELEMETRY_SERVICE_NAME"]})
        )
        trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))

        # Set the trace provider globally
        trace.set_tracer_provider(trace_provider)

    def load_configuration(self, option_configuration: tornado.options.OptionParser) -> types.SimpleNamespace:
        """Load the json configuration file for our webserver + nodenorm api.

        Loads the default configuration file found in ~/src/nodenorm/config/config.default.json
        to create a base structure. It then checks if an optional configuration file was provided
        via the command line options. We override the default configuration file with this option
        based file. After that, we setup our static path structure for loading the webapp containing
        our swaggerui frontend. We check any other command line options and then return our
        configuration data structure
        """
        configuration = {}

        # note we have to use this format as relative paths were only supported for
        # importlib.resources.read_text in python3.13
        default_configuration_path = importlib.resources.files(nodenorm) / "config" / self.default_configuration
        with default_configuration_path.open("r", encoding="utf-8") as handle:
            default_configuration = json.load(handle)

        configuration.update(default_configuration)

        if option_configuration.conf is not None:
            optional_configuration = pathlib.Path(option_configuration.conf).absolute().resolve()
            if optional_configuration.exists():
                with open(optional_configuration, "r", encoding="utf-8") as handle:
                    configuration.update(json.load(handle))

        # Force the static path to the webapp directory
        package_directory = importlib.resources.files(nodenorm)
        webapp_directory = package_directory.joinpath("webapp")
        configuration["webserver"]["SETTINGS"]["static_path"] = str(webapp_directory)
        configuration["webserver"]["SETTINGS"]["static_url_prefix"] = "/"

        configuration_namespace = types.SimpleNamespace(configuration)

        # override options
        if option_configuration.host is not None:
            configuration_namespace.websever.HOST = option_configuration.host

        if option_configuration.port is not None:
            configuration_namespace.websever.PORT = option_configuration.port

        return configuration_namespace

    def populate_handlers(self, handlers):
        """Populates the handler routes for the NodeNormalization API.

        These routes take the following form: `(regex, handler_class, options)` tuples
        <http://www.tornadoweb.org/en/stable/web.html#application-configuration>`_.

        Overrides the _get_handlers method provided by TornadoBiothingsAPI as we don't need
        the custom implementation for handling how we parse the handler path
        """
        for handler in handlers.values():
            self.handlers[handler[0]] = handler[1:]
