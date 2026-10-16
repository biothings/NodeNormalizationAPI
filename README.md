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

## Selecting a Babel release in Node Normalization

Clients can select a supported Babel release for each request. Omitting `babel_version`
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
clients through CORS. Unknown, retired, or malformed selectors return HTTP 400 with the
supported release names. A supported release whose backend is unavailable returns an
error; requests never fall back to another release.

`GET /status` reports `default_babel_version` and `available_babel_versions`. The existing
`babel_version` and `babel_version_url` metadata describe the default release. The
available list reflects configured support, rather than backend health.

### Configuring supported releases

Add an optional top-level `babel` object to the server JSON configuration supplied with
`--conf`. For example (replace the Elasticsearch targets with those in your deployment):

```json
{
  "babel": {
    "default_version": "2025sep1",
    "releases": {
      "2025sep1": "annotator-nodenorm",
      "2026jul22": "nodenorm-2026jul22"
    }
  }
}
```

Each release must map to one concrete Elasticsearch index or an alias for that release.
`default_version` must name a configured release. Explicit Babel configuration overrides
`ES_INDEX` / `ES_ALIAS` for the request default and utilities. With the default `"babel": {}`,
the server retains the legacy Elasticsearch target and discovers its single Babel
release from index metadata at startup.

Configuration is loaded at startup. Restart the service after changing release
configuration or alias targets, and keep version-specific aliases tied to the same
Babel release. Removing a release from the configuration retires it for new requests.
