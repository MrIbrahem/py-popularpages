#!/bin/bash
# toolforge-jobs run med --image python3.13 --command "~/REPOS_2026/py-popularpages/med.sh"
# use bash strict mode
set -euo pipefail

source www/python/venv/bin/activate

# toolforge-jobs run test --image python3.11 --command "~/REPOS_2026/py-popularpages/test.sh"
cd ~/REPOS_2026/py-popularpages

#pip install -r requirements.txt

python3 py_port/generate_report.py --wiki en.wikipedia --project Medicine --dry-run
python3 py_port/check_reports.py --dry-run


