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

## Configuration

Settings are read from `src/nodenorm/config/config.default.json`, which is set up for local
development (Elasticsearch on localhost, Tornado debug mode on, telemetry off). A file passed with
`--conf` is laid over it section by section, so it only needs the keys it changes. The Docker image
passes `docker/configuration/config.json`, which holds the deployment's own settings: the in-cluster
Elasticsearch host, telemetry on, and debug mode off (in debug mode Tornado puts tracebacks in its
error pages). It also repeats `ES_INDEX` and `ES_ALIAS`, though they match the default today: they
name the index in the deployment's cluster, so pinning them means a change to the default for local
development can't silently repoint production. The cost is that a rename has to be made in both
files. Other settings the deployment leaves out follow the default, so a change there reaches
production on the next build. `tests/test_configuration.py` checks that the deployment file names no
setting the default lacks. The app reads none of these settings from environment variables, so the
Helm chart doesn't set any.
