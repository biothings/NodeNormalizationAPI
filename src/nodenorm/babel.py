from urllib.parse import urlparse

BABEL_RELEASES_URL = "https://github.com/NCATSTranslator/Babel/releases/tag"


def get_babel_version(index_mapping: dict) -> str:
    """Return the explicit Babel source version, with legacy URL fallback."""

    source_metadata = index_mapping["mappings"]["_meta"]["src"]["nodenorm"]
    if babel_version := source_metadata.get("version"):
        return babel_version

    compendia_url = source_metadata["url"]
    parsed_compendia_url = urlparse(compendia_url)
    return parsed_compendia_url.path.rstrip("/").rsplit("/", maxsplit=1)[-1]
