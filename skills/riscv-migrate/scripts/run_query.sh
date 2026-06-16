#!/usr/bin/env bash
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 幂等：缺啥装啥。用于让“查询知识库”默认可用。
python3 -m pip install -r "${SKILL_ROOT}/scripts/requirements.txt" >/dev/null

exec python3 "${SKILL_ROOT}/scripts/query.py" "$@"

