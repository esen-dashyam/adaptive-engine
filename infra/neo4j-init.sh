#!/bin/bash
set -e

# On first start the /data/databases/neo4j directory won't exist yet.
# Load the pre-baked dump so the knowledge graph is ready immediately.
if [ ! -d "/data/databases/neo4j" ]; then
    echo "==> First start: loading knowledge graph dump (~144k nodes)..."
    neo4j-admin database load neo4j \
        --from-path=/startup/kg.dump \
        --overwrite-destination=true
    echo "==> Knowledge graph loaded."
else
    echo "==> Database already exists, skipping dump restore."
fi

# Hand off to the official Neo4j entrypoint
exec /startup/docker-entrypoint.sh neo4j
