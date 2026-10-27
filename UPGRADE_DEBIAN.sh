#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ $# -ne 1 ]]; then echo "Pemakaian: bash UPGRADE_DEBIAN.sh /path/to/BAG-v0.6.0"; exit 2; fi
python3 tools/upgrade.py --target "$1"
read -r -p "Gateway lama harus berhenti. Ketik LANJUT untuk menerapkan upgrade: " confirm
if [[ "$confirm" != 'LANJUT' ]]; then echo 'Dibatalkan'; exit 0; fi
python3 tools/upgrade.py --target "$1" --apply
