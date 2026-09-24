from nodenorm.babel import get_babel_release_url, get_babel_version


def index_mapping(source_metadata):
    return {
        "mappings": {
            "_meta": {
                "src": {
                    "nodenorm": source_metadata,
                }
            }
        }
    }


def test_babel_version_uses_explicit_source_version():
    mapping = index_mapping(
        {
            "version": "2026jul22",
            "url": "https://stars.renci.org/var/babel_outputs/latest/VERSION.txt",
        }
    )

    babel_version = get_babel_version(mapping)

    assert babel_version == "2026jul22"
    assert get_babel_release_url(babel_version) == (
        "https://github.com/NCATSTranslator/Babel/blob/main/releases/2026jul22/README.md"
    )


def test_babel_version_falls_back_to_legacy_source_url():
    mapping = index_mapping(
        {
            "url": "https://stars.renci.org/var/babel_outputs/2025sep1/",
        }
    )

    babel_version = get_babel_version(mapping)

    assert babel_version == "2025sep1"
    assert get_babel_release_url(babel_version) == (
        "https://github.com/NCATSTranslator/Babel/blob/main/releases/2025sep1/README.md"
    )
