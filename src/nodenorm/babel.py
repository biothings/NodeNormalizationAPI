from dataclasses import dataclass
from urllib.parse import urlparse

BABEL_RELEASES_URL = "https://github.com/NCATSTranslator/Babel/blob/main/releases"


@dataclass(frozen=True)
class BabelRelease:
    version: str
    index: str


class BabelReleases:
    """Deployment allowlist of Babel releases and their Elasticsearch targets."""

    def __init__(self, default_version: str, releases: dict):
        if not isinstance(releases, dict) or not releases:
            raise ValueError("babel.releases must be a nonempty mapping of versions to indices or aliases")
        for version, index in releases.items():
            if not isinstance(version, str) or not version.strip():
                raise ValueError("Babel release names must be nonempty strings")
            if not isinstance(index, str) or not index.strip() or any(char in index for char in "*,?"):
                raise ValueError("Each Babel release must target one Elasticsearch index or alias")
        if not isinstance(default_version, str) or default_version not in releases:
            raise ValueError("babel.default_version must name a release in babel.releases")

        self.default_version = default_version
        self._releases = {version: BabelRelease(version, index) for version, index in releases.items()}

    @property
    def available_versions(self) -> list[str]:
        return sorted(self._releases)

    def resolve(self, version: str = None) -> BabelRelease:
        if version is None:
            version = self.default_version
        if not isinstance(version, str) or version not in self._releases:
            supported = ", ".join(self.available_versions)
            raise ValueError(f"Unsupported babel_version. Supported releases: {supported}")
        return self._releases[version]


def get_babel_version(index_mapping: dict) -> str:
    """Return the explicit Babel source version, with legacy URL fallback."""

    source_metadata = index_mapping["mappings"]["_meta"]["src"]["nodenorm"]
    if babel_version := source_metadata.get("version"):
        return babel_version

    compendia_url = source_metadata["url"]
    parsed_compendia_url = urlparse(compendia_url)
    return parsed_compendia_url.path.rstrip("/").rsplit("/", maxsplit=1)[-1]


def get_babel_release_url(version: str) -> str:
    """Return the canonical Babel release-notes URL for a build version."""

    return f"{BABEL_RELEASES_URL}/{version}/README.md"
