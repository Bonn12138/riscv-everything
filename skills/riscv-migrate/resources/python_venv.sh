#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_env_wrapper
log "setup Python virtual environment: ${VENV_DIR}"
require_cmd python3

# 默认 python3 -m venv 会跑 ensurepip；在部分发行版（如 RHEL 精简 Python）上会直接失败。
# 使用 --without-pip 创建，再用 get-pip.py 安装 pip，可稳定绕过 ensurepip。
if [[ ! -d "${VENV_DIR}" ]] || [[ ! -x "${VENV_DIR}/bin/python3" ]]; then
  rm -rf "${VENV_DIR}"
  python3 -m venv --without-pip "${VENV_DIR}"
fi

venv_python="${VENV_DIR}/bin/python3"
if ! "${venv_python}" -m pip --version >/dev/null 2>&1; then
  bootstrap_pip_in_venv "${venv_python}"
fi

"${venv_python}" -m pip install --upgrade pip setuptools wheel
"${venv_python}" -m pip install -r "${PROJECT_ROOT}/scripts/requirements.txt"

log "Python venv ready: ${VENV_DIR}"

