#!/bin/bash

# use bash strict mode
set -euo pipefail

# toolforge-jobs run test --image python3.11 --command "~/REPOS_2026/py-popularpages/test.sh"
cd ~/REPOS_2026/py-popularpages

source venv.sh

pip install -r requirements.txt

python3 src/cli/generate_report.py --wiki en.wikipedia --project Dinosaurs --dry-run
