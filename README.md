This repository maintains a set of biomedical knowledgebase APIs built with the [BioThings SDK](https://biothings.io).
These APIs are either the Knowledgebase APIs built for [the Translator Project](https://ncats.nih.gov/research/research-activities/translator)
or the "pending" APIs to be integrated into the [official BioThings APIs](https://biothings.io) (e.g. [MyGene.info](https://mygene.info), 
[MyVariant.info](https://myvariant.info), [MyChem.info](https://mychem.info), [MyDisease.info](https://mydisease.info) etc.)

The list of Translator-associated knowledgebase APIs are hosted at:
https://biothings.ncats.io.

There are additional APIs are hosted at https://pending.biothings.io.

## Knowledgebase APIs for the Translator Project

Each knowledgebase API is created as a "data plugin" (see examples under [plugins](/plugins) folder). The [BioThings SDK](https://biothings.io) package
will then process the data plugin and turn it into a hosted "BioThings API". You can follow the tutorial of the [data plugin](https://github.com/biothings/pending.api/tree/master/plugins) for more details.

### How to add a new API

Our internal developement team will handle the process of adding a new data plugin and deploying it as a new API. 
For our internal developers, please follow [this documentation](
https://github.com/biothings/biothings-internal/wiki/How-To-Add-A-New-KP-API-(to-pending.api))

### How to update data for an existing API
For external collaborators who have submitted their "data plugins" as new APIs, you can follow this workflow to request
a update of your data:

https://github.com/biothings/biothings_explorer/blob/main/docs/README-maintaining-a-data-source.md

The documentation is maintained at the [biothings_explorer](https://github.com/biothings/biothings_explorer) repository,
as each knowledgebase API will be integrated into the [BioThings Explorer application](https://explorer.biothings.io) become
a Translator's standard KP (Knowledge Provider) API.

## Running Node Normalization locally

With the project installed in `.venv`, start the server from the repository root:

```bash
.venv/bin/python -m nodenorm --host=127.0.0.1 --port=8000
```

The default configuration connects to Elasticsearch at `http://localhost:9200` using
the `annotator-nodenorm` index or alias. Add `--conf=/path/to/config.local.json` for
local overrides such as connection headers.

Local defaults enable `webserver.SETTINGS.debug` and `webserver.SETTINGS.autoreload`.
Changes to imported Python files automatically restart the server. JSON configuration
and OpenAPI version changes require a manual restart. The Docker configuration disables
debug mode and autoreload.

## Selecting a Babel release in Node Normalization

Clients can select an available Babel release for each request. Omitting `babel_version`
uses the configured default release.

- `GET /get_normalized_nodes`, `GET /get_semantic_types`, and both `GET` and `POST /get_setid`
  accept the optional `babel_version` query parameter.
- `POST /get_normalized_nodes` accepts an optional `babel_version` string in its JSON body.
- `POST /get_setid` keeps its existing array body; its query parameter selects one release
  for the entire batch.

For example, select `2025sep1` with
`GET /get_normalized_nodes?curie=MESH:D014867&babel_version=2025sep1`, or send:

```json
{
  "curies": ["MESH:D014867"],
  "babel_version": "2025sep1"
}
```

Successful data responses include `X-Babel-Version`, which is also exposed to browser
clients through CORS. Unknown, removed, or malformed selectors return HTTP 400 with the
available release names. Selecting a release with ambiguous metadata returns HTTP 503;
an unavailable backend also returns an error. Requests never fall back to another release.

`GET /status` groups release information under `babel`. Its `default_version` field names
the default release, and `available_versions` maps each available release to its release URL:

```json
{
  "status": "running",
  "version": "1.0.0",
  "biolink_model_toolkit_version": "v4.4.3",
  "babel": {
    "default_version": "2026jul22",
    "available_versions": {
      "2025sep1": "https://github.com/NCATSTranslator/Babel/blob/main/releases/2025sep1/README.md",
      "2026jul22": "https://github.com/NCATSTranslator/Babel/blob/main/releases/2026jul22/README.md"
    }
  }
}
```

Available releases come from Elasticsearch metadata, rather than a configured version
list; they do not indicate backend health. When discovery finds ambiguous releases,
`babel.errors` maps their names to error messages. Those releases are excluded from
`babel.available_versions`.

### Discovering available releases

At startup, the server uses its existing `ES_HOST` client to discover release indices
from `mappings._meta.src.nodenorm` metadata on the associated cluster. No version list,
version-to-index mapping, or per-release alias is required. The client's Elasticsearch
permissions must allow reading mappings across that cluster. Discovery uses an explicit
`version` field, or a dated release tag in the legacy source URL; it does not infer
versions from index names. Unrelated indices and invalid metadata are ignored.

By default, `ES_INDEX` / `ES_ALIAS` still selects the default target. Its metadata must
identify a release; this configured target also disambiguates the default if several
indices declare that version. The default is pinned to the concrete backing indices
resolved at startup, so moving an alias takes effect after restart. A default alias
may span multiple indices if all declare the same release. Other available versions
must each identify one concrete index. An optional top-level `babel` object in the
server JSON configuration supplied with `--conf` can override the default:

```json
{
  "babel": {
    "default_version": "2026jul22"
  }
}
```

The override must name a discovered release with a unique match. Configuration and
discovery are loaded at startup. Adding a release index makes it available after
restart; the default changes only when the configured target, alias, or default
override changes and the service restarts. Restart also refreshes removed releases
and changed metadata. Keep indices serving existing processes until the restart
rollout completes; removing an index earlier makes requests using it fail.
Verify the reported versions and sample results before rollout; discovery relies on
metadata accurately describing each index's contents.
