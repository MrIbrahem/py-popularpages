#!/bin/bash
# use bash strict mode
set -euo pipefail

# Full path to the uploaded repository passed from GitHub Actions
FULL_PATH=$1

TOOL_NAME="himo"

if [ -z "$FULL_PATH" ]; then
    echo "Error: Missing full repository path argument." >&2
    exit 1
fi

echo ">>> Stage 1: Updating 'himo' tool core repository..."
echo ">>> FULL_PATH: $FULL_PATH"

# Run deployment steps inside himo's toolforge context
become himo sh -c "cp -rf \"$FULL_PATH/toolforge/deploy_scripts\" /data/project/himo -v;chmod +x /data/project/himo/deploy_scripts/*.sh -v;/data/project/himo/deploy_scripts/update_pop_local.sh \"$FULL_PATH\""

echo ">>> 'himo' repository update completed successfully."
