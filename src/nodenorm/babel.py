import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

BABEL_RELEASES_URL = "https://github.com/NCATSTranslator/Babel/blob/main/releases"
logger = logging.getLogger(__name__)


def validate_babel_version(version):
    if (
        not isinstance(version, str)
        or not version
        or not version.isascii()
        or not version.isprintable()
        or any(char.isspace() for char in version)
    ):
        raise ValueError("Babel release names must be nonempty ASCII strings without whitespace")


class BabelReleaseUnavailable(RuntimeError):
    """A supported release cannot be uniquely resolved from index metadata."""


@dataclass(frozen=True)
class BabelRelease:
    version: str
    index: str


class BabelReleases:
    """Babel releases and their Elasticsearch targets discovered from metadata."""

    def __init__(self, default_version: str, releases: dict, unavailable: dict = None):
        unavailable = unavailable or {}
        if not isinstance(releases, dict) or not (releases or unavailable):
            raise ValueError("At least one supported Babel release is required")
        for version, index in releases.items():
            validate_babel_version(version)
            if (
                not isinstance(index, str)
                or not index
                or index == "_all"
                or any(char.isspace() or char in "*,?" for char in index)
            ):
                raise ValueError("Each Babel release must target one Elasticsearch index or alias")
        for version in unavailable:
            validate_babel_version(version)
        if not isinstance(default_version, str) or default_version not in (releases.keys() | unavailable.keys()):
            raise ValueError("babel.default_version must name a supported release")

        self.default_version = default_version
        self._releases = {version: BabelRelease(version, index) for version, index in releases.items()}
        self._unavailable = dict(unavailable)

    @classmethod
    def from_mappings(cls, default_version, mappings, default_index=None):
        """Discover release names and targets from source metadata on concrete indices."""
        validate_babel_version(default_version)
        candidates = {default_version: []}
        for index, mapping in mappings.items():
            try:
                version = get_babel_version(mapping)
            except (KeyError, TypeError, ValueError):
                # A shared cluster can contain unrelated indices and incomplete
                # builds. Neither their names nor invalid metadata identify a release.
                logger.debug("Ignoring index %s without valid Babel source metadata", index)
                continue
            candidates.setdefault(version, []).append(index)

        releases = {}
        unavailable = {}
        for version, indices in candidates.items():
            if version == default_version and default_index is not None:
                # The existing deployment target already chooses the default,
                # including when the cluster also holds another copy of that release.
                releases[version] = default_index
            elif len(indices) == 1:
                releases[version] = indices[0]
            elif not indices:
                unavailable[version] = f"No index metadata identifies Babel release {version}"
            else:
                unavailable[version] = f"Multiple indices identify Babel release {version}: {', '.join(sorted(indices))}"
        return cls(default_version, releases, unavailable)

    @property
    def available_versions(self) -> list[str]:
        return sorted(self._releases)

    @property
    def errors(self) -> dict:
        return dict(self._unavailable)

    def resolve(self, version: str = None) -> BabelRelease:
        if version is None:
            version = self.default_version
        if not isinstance(version, str) or version not in (self._releases.keys() | self._unavailable.keys()):
            supported = ", ".join(self.available_versions)
            raise ValueError(f"Unsupported babel_version. Supported releases: {supported}")
        if version in self._unavailable:
            raise BabelReleaseUnavailable(self._unavailable[version])
        return self._releases[version]


def get_babel_version(index_mapping: dict) -> str:
    """Return the explicit Babel source version, with legacy URL fallback."""

    source_metadata = index_mapping["mappings"]["_meta"]["src"]["nodenorm"]
    if "version" in source_metadata:
        babel_version = source_metadata["version"]
        validate_babel_version(babel_version)
        return babel_version

    compendia_url = source_metadata["url"]
    if not isinstance(compendia_url, str):
        raise ValueError("Babel source URL must be a string")
    parsed_compendia_url = urlparse(compendia_url)
    version = parsed_compendia_url.path.rstrip("/").rsplit("/", maxsplit=1)[-1]
    if not re.fullmatch(r"[0-9]{4}[A-Za-z]{3}[0-9]{1,2}", version):
        raise ValueError("Legacy Babel source URL must identify a dated release")
    return version


def get_babel_release_url(version: str) -> str:
    """Return the canonical Babel release-notes URL for a build version."""

    return f"{BABEL_RELEASES_URL}/{version}/README.md"
