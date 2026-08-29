#!/bin/bash
# use bash strict mode
set -euo pipefail

# Full path to the uploaded repository passed from GitHub Actions
FULL_PATH=$1

TOOL_NAME="mdw"

if [ -z "$FULL_PATH" ]; then
    echo "Error: Missing full repository path argument." >&2
    exit 1
fi

echo ">>> Stage 1: Updating '$TOOL_NAME' tool core repository..."
echo ">>> FULL_PATH: $FULL_PATH"

# Run deployment steps inside toolforge context
become "$TOOL_NAME" sh -c "cp -rf \"$FULL_PATH/toolforge/deploy_scripts\" /data/project/$TOOL_NAME -v;chmod +x /data/project/$TOOL_NAME/deploy_scripts/*.sh -v;/data/project/$TOOL_NAME/deploy_scripts/update_pop_local.sh \"$FULL_PATH\""

echo ">>> '$TOOL_NAME' repository update completed successfully."
