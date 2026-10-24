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
NODENORM_DEPLOYMENT_ENV=local .venv/bin/python -m nodenorm --host=127.0.0.1 --port=8000
```

This explicitly reports `"deployment_environment": "local"` in `GET /status`.

The default configuration connects to Elasticsearch at `http://localhost:9200` using
the `annotator-nodenorm` index or alias. Add `--conf=/path/to/config.local.json` for
local overrides such as connection headers.

Local defaults enable `webserver.SETTINGS.debug` and `webserver.SETTINGS.autoreload`.
Changes to imported Python files automatically restart the server. JSON configuration
and OpenAPI version changes require a manual restart. The Docker configuration disables
debug mode and autoreload.

## Reporting the deployment environment

`GET /status` reports `deployment_environment` from explicit deployment configuration.
Set `NODENORM_DEPLOYMENT_ENV` to the environment name: `CI`, `Prod`, `Test`, or `local`.
Use `NODENORM_DEPLOYMENT_ENV=local` when running locally to override the `unknown`
fallback. You can also set `"deployment_environment": "local"` at the top level of
the JSON file passed through `--conf`. A nonempty environment variable takes precedence.
The value is loaded at startup and defaults to `unknown` when unset; request headers,
hostnames, and test-runner settings do not determine it.

Kubernetes deployments pass the pod's `gov.nih.ncats.appenv` label into
`NODENORM_DEPLOYMENT_ENV` through the Downward API. Set that label in the environment's
Helm values under `ncats.labels`. DevOps supplies `CI`, `Prod`, or `Test`, and the API
preserves that capitalization. The chart defaults to `unknown`. Restart the service
after changing the environment configuration.

## Selecting a Babel release in Node Normalization

Starting with API version 1.1.0, clients can select an available Babel release for each
request. Omitting `babel_version` uses the configured default release.

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
available release names. An unavailable backend returns an error. Requests never
fall back to another release.

`GET /status` groups release information under `babel`. Its `default_version` field names
the default release, `available_versions` maps each available release to its release URL,
and `indices` maps the same release names to arrays of selected concrete Elasticsearch
index names:

```json
{
  "status": "running",
  "deployment_environment": "CI",
  "version": "1.1.0",
  "biolink_model_toolkit_version": "v4.4.3",
  "babel": {
    "checked_at": "2026-10-23T12:00:00+00:00",
    "default_version": "2026jul22",
    "available_versions": {
      "2025sep1": "https://github.com/NCATSTranslator/Babel/blob/main/releases/2025sep1/README.md",
      "2026jul22": "https://github.com/NCATSTranslator/Babel/blob/main/releases/2026jul22/README.md"
    },
    "indices": {
      "2025sep1": ["nodenorm_2025sep1"],
      "2026jul22": ["nodenorm_2026jul22"]
    }
  }
}
```

Index names in this example are illustrative. Each `indices` array contains the
concrete targets selected by the latest successful discovery snapshot. The default release
uses every backing index of its configured alias; other releases select one index.
Available releases come from Elasticsearch metadata, rather than a configured version
list; these maps do not indicate backend health.

### Discovering available releases

At startup and every **10 minutes** in the background, the server uses its existing
`ES_HOST` client to discover release indices from `mappings._meta.src.nodenorm` metadata
on the associated cluster. No version list,
version-to-index mapping, or per-release alias is required. The client's Elasticsearch
permissions must allow reading mappings across that cluster. Discovery uses an explicit
`version` field, or a dated release tag in the legacy source URL; it does not infer
versions from index names. Unrelated indices and invalid release metadata are ignored.

For supported releases outside the default alias, group indices by their Babel
release metadata and choose one build using this rule:

1. Prefer the newest valid ISO build date in `mappings._meta.build_date`. Compare dates
   in UTC; timestamps without a timezone are treated as UTC. A valid date ranks above
   missing or malformed dates.
2. If build dates are equal, or all candidates lack valid dates, choose the
   lexicographically greatest concrete index name. This fallback provides a stable
   ordering; it does not establish the age or release of an index.

The default is different: `ES_INDEX` / `ES_ALIAS` is authoritative for both its
release and its exact concrete backing indices. All targets must identify the same
Babel release. The default keeps those targets even if newer builds exist elsewhere
on the cluster; a multi-index default queries all its backing indices. Explicitly
requesting the default Babel version uses those same alias targets. After the alias
moves to another release and discovery refreshes, the former default becomes a supported
nondefault release and uses the build-date rule above.

The default release has no separate Babel configuration override. Configuration is
loaded at startup. Adding a release or a newer build makes it available on the next
successful background refresh; adding a release does not automatically change the
default. Moving the configured default alias takes effect on the next successful
refresh, without restarting the API. Changing the configuration itself requires a restart.

Each process refreshes independently, even without incoming requests. A refresh has a
10-second total deadline. It publishes the new release registry and default query
indices together, so `/status` and new requests use the same snapshot. Each request
keeps its selected concrete indices throughout processing, including conflation lookups.
Keep previous indices available until every process has refreshed and requests using
the previous snapshot have completed. Verify `/status` and sample results before
removing indices; discovery relies on metadata accurately describing each index's contents.

`babel.checked_at` is the UTC time of the last successful discovery. A failed refresh
retains that snapshot and timestamp, adds `babel.refresh_error` with code
`discovery_failed`, and retries after 30 seconds. A successful retry clears the error.
Discovery failures alone do not determine service health.

`GET /status` checks that every selected default index still exposes the expected
Babel source version. These separate health results are cached for **5 seconds** on
success and **1 second** on failure, with concurrent requests sharing one check and a
3-second total deadline. Changing the default version or its concrete indices causes
the next status request to use a new health check. A failed check returns HTTP 503;
an expired healthy result is never served after a failed check. Health checks do not
trigger Babel discovery. All status responses send `Cache-Control: no-store`.
