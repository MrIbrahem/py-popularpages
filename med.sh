#!/bin/bash

# use bash strict mode
set -euo pipefail

# toolforge-jobs run med --image python3.11 --command "~/REPOS_2026/py-popularpages/med.sh"
cd ~/REPOS_2026/py-popularpages

source venv.sh

pip install -r requirements.txt

python3 src/py_port/generate_report.py --wiki en.wikipedia --project Medicine --dry-run
