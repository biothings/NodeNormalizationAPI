import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Union
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


def _index_build_order(index, index_mapping):
    """Rank dated builds first, then break ties consistently by index name."""
    build_date = index_mapping["mappings"]["_meta"].get("build_date")
    if isinstance(build_date, str):
        try:
            # Python <3.11 does not accept the ISO UTC suffix directly.
            if build_date.endswith("Z"):
                build_date = build_date[:-1] + "+00:00"
            parsed = datetime.fromisoformat(build_date)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return True, parsed.astimezone(timezone.utc), index
        except (ValueError, OverflowError):
            pass
    return False, datetime.min.replace(tzinfo=timezone.utc), index


class BabelReleaseUnavailable(RuntimeError):
    """A supported release has no discoverable Elasticsearch target."""


@dataclass(frozen=True)
class BabelRelease:
    version: str
    index: Union[str, tuple[str, ...]]

    @property
    def search_indices(self) -> list[str]:
        return [self.index] if isinstance(self.index, str) else list(self.index)


class BabelReleases:
    """Babel releases and their Elasticsearch targets discovered from metadata."""

    def __init__(self, default_version: str, releases: dict, unavailable: dict = None):
        unavailable = unavailable or {}
        if not isinstance(releases, dict) or not (releases or unavailable):
            raise ValueError("At least one supported Babel release is required")
        for version, index in releases.items():
            validate_babel_version(version)
            targets = (index,) if isinstance(index, str) else index
            if not isinstance(targets, tuple) or not targets:
                raise ValueError("Each Babel release must target one or more Elasticsearch indices")
            for target in targets:
                if (
                    not isinstance(target, str)
                    or not target
                    or target == "_all"
                    or any(char.isspace() or char in "*,?" for char in target)
                ):
                    raise ValueError("Each Babel release must target literal Elasticsearch indices")
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
                # The default alias is authoritative, including when it selects
                # an older build or several indices of the same release.
                releases[version] = default_index
            elif not indices:
                unavailable[version] = f"No index metadata identifies Babel release {version}"
            else:
                # Releases outside the default alias select their newest build.
                selected = max(indices, key=lambda index: _index_build_order(index, mappings[index]))
                releases[version] = selected
                if len(indices) > 1:
                    logger.info("Selected index %s for Babel release %s from %s", selected, version, sorted(indices))
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
    if "version" in source_metadata and source_metadata["version"] is not None:
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
