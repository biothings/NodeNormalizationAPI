import importlib.resources
import importlib.util
import json
import logging
import pathlib
import re
import sys
import types

import tornado.options

from biothings.web import connections
from biothings.web.services.metadata import BiothingsESMetadata

import nodenorm


logger = logging.getLogger(__name__)


class NodeNormalizationAPINamespace:
    """Simplied namespace instance for our NodeNormalization API."""

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
            opentelemetry_module in sys.modules or importlib.util.find_spec(opentelemetry_module) is not None
        )

        if not opentelemetry_enabled:
            logger.info(
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

        logger.info(
            "OPENTELEMETRY is enabled. If you wish to disable it, set the OPENTELEMETRY_ENABLED value to <False>"
        )

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
        elasticsearch_namespace.indices = self._validate_elasticsearch_index(elasticsearch_namespace)
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
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # pylint: disable=import-outside-toplevel
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource  # pylint: disable=import-outside-toplevel
        from opentelemetry.sdk.trace import TracerProvider  # pylint: disable=import-outside-toplevel
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # pylint: disable=import-outside-toplevel
        from opentelemetry.sdk.trace.sampling import Sampler, SamplingResult, Decision  # pylint: disable=import-outside-toplevel
        from opentelemetry import trace  # pylint: disable=import-outside-toplevel

        excluded_patterns: list[re.Pattern] = [
            re.compile(pattern)
            for pattern in self.config.telemetry.get("OPENTELEMETRY_EXCLUDED_URLS", ["/status", "^/$"])
        ]
        # Reference captured here; handlers are populated after telemetry is configured
        namespace_handlers = self.handlers

        def _build_known_paths() -> set[str]:
            """Convert handler route patterns to plain path prefixes, computed once."""
            paths = set()
            for pattern in namespace_handlers:
                # Strip regex capture groups e.g. (.*) or ()
                clean = re.sub(r"\([^)]*\)", "", pattern)
                # Strip trailing optional-char marker '?'
                clean = clean.rstrip("?")
                paths.add(clean or "/")
            return paths

        class _EndpointFilterSampler(Sampler):
            """Only samples spans for known endpoints, then applies the exclusion list.

            Known endpoints are derived from the registered handler patterns on first
            use so no separate configuration is required.
            """

            def __init__(self):
                # Lazily populated on first request, after populate_handlers has run
                self._known_paths: set[str] | None = None

            def should_sample(self, parent_ctx, trace_id, name, kind=None, attributes=None, links=None, trace_state=None):
                if self._known_paths is None:
                    self._known_paths = _build_known_paths()

                target: str = (attributes or {}).get("http.target", "")
                path = target.split("?")[0] if target else ""

                if self._known_paths and not any(
                    path == ep or (ep.rstrip("/") and path.startswith(ep.rstrip("/") + "/"))
                    for ep in self._known_paths
                ):
                    return SamplingResult(Decision.DROP, attributes=attributes)

                if any(pattern.search(path) for pattern in excluded_patterns):
                    return SamplingResult(Decision.DROP, attributes=attributes)

                return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes=attributes)

            def get_description(self) -> str:
                return "EndpointFilterSampler"

        jaeger_host = self.config.telemetry["OPENTELEMETRY_JAEGER_HOST"]
        jaeger_port = self.config.telemetry["OPENTELEMETRY_JAEGER_PORT"]
        otlp_endpoint = f"{jaeger_host}:{jaeger_port}/v1/traces"

        trace_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)

        trace_provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: self.config.telemetry["OPENTELEMETRY_SERVICE_NAME"]}),
            sampler=_EndpointFilterSampler(),
        )
        trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))

        # Set the trace provider globally before instrumenting so TornadoInstrumentor
        # captures a tracer from this provider (and therefore uses the sampler)
        trace.set_tracer_provider(trace_provider)
        TornadoInstrumentor().instrument()

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

        optional_configuration_file = getattr(option_configuration, "conf", None)
        if optional_configuration_file is not None:
            optional_configuration = pathlib.Path(optional_configuration_file).absolute().resolve()
            if optional_configuration.exists():
                with open(optional_configuration, "r", encoding="utf-8") as handle:
                    configuration.update(json.load(handle))

        # Force the static path to the webapp directory
        package_directory = importlib.resources.files(nodenorm)
        webapp_directory = package_directory.joinpath("webapp")
        configuration["webserver"]["SETTINGS"]["static_path"] = str(webapp_directory)
        configuration["webserver"]["SETTINGS"]["static_url_prefix"] = "/"

        configuration_namespace = types.SimpleNamespace(**configuration)

        # override options
        option_host = getattr(option_configuration, "host", None)
        if option_host is not None:
            configuration_namespace.webserver["HOST"] = option_host

        option_port = getattr(option_configuration, "port", None)
        if option_port is not None:
            configuration_namespace.webserver["PORT"] = option_port

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
