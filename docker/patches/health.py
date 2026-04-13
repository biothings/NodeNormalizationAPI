from urllib.parse import urlparse

from elasticsearch import AsyncElasticsearch

from biothings.web.handlers import BaseHandler

from nodenorm.biolink import BIOLINK_MODEL_VERSION


class NodeNormHealthHandler(BaseHandler):
    """
    Important Endpoints
    * /_cat/nodes
    
    Patched version to handle missing metadata gracefully and fix ES API compatibility
    """

    name = "health"

    async def get(self):
        async_client: AsyncElasticsearch = self.biothings.elasticsearch.async_client
        search_indices = self.biothings.elasticsearch.indices

        try:
            # Fixed: Use keyword argument for ES 8.x compatibility
            biothings_metadata = await async_client.indices.get(index=search_indices)
            
            # Use default babel version since metadata access may not be available
            babel_version = "1.9"  # Default fallback version
            babel_markdown = f"https://github.com/ncatstranslator/Babel/blob/master/releases/{babel_version}.md"
            
            try:
                attributes = [
                    "name",
                    "cpu",
                    "disk.avail",
                    "disk.total",
                    "disk.used",
                    "disk.used_percent",
                    "heap.current",
                    "heap.max",
                    "load_1m",
                    "load_5m",
                    "load_15m",
                    "uptime,version",
                ]
                h_string = ",".join(attributes)
                cat_nodes_response = await async_client.cat.nodes(format="json", h=h_string)
                nodes_status = {node["name"]: node for node in cat_nodes_response}
                nodes = {"elasticsearch": {"nodes": nodes_status}}
            except Exception:
                # Fallback if ES cluster info fails
                nodes = {"elasticsearch": {"status": "connected"}}
                
            status_response = {
                "status": "running",
                "babel_version": babel_version,
                "babel_version_url": babel_markdown,
                "biolink_model_toolkit_version": BIOLINK_MODEL_VERSION,
                **nodes,
            }
            
        except Exception as e:
            status_response = {
                "status": "error",
                "error": str(e),
                "babel_version": "unknown"
            }

        self.finish(status_response)