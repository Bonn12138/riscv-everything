#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_env_wrapper
prepare_download_dirs
log "RISC-V cross toolchain"
require_cmd curl

archive="${TOOLCHAIN_DIR}/riscv-toolchain.tar.xz"
RISCV_TOOLCHAIN_URL="https://artsz.zte.com.cn:443/artifactory/zf-eco-release-generic/rpm/riscv64_gcc14.1.0_glibc2.39.0_fp.tar.bz2"

if [[ -f "${archive}" ]]; then
  log "已存在，跳过下载: ${archive}"
else
  curl -H 'X-JFrog-Art-Api:AKCpBwvjChQQwNU5eU8YwNadzJr4Rx5XxyZjByQ8kByPa2d1LNUhwzk5gYajfjmKgmn2nb98Y' "${RISCV_TOOLCHAIN_URL}" -o "${archive}"
fi

if ! has_riscv64_toolchain_dir; then
  if [[ ! -f "${archive}" ]]; then
    log "ERROR: 缺少 ${archive} 且本地无工具链目录，无法解压" >&2
    exit 1
  fi
  log "解压: ${archive}"
  tar -xf "${archive}" -C "${TOOLCHAIN_DIR}"
fi

discover_toolchain_roots
if [[ -z "${RISCV_TOOLCHAIN_ROOT}" || ! -d "${RISCV_TOOLCHAIN_ROOT}/bin" ]]; then
  log "ERROR: 未发现 riscv64_* 工具链目录: ${TOOLCHAIN_DIR}" >&2
  exit 1
fi

export RISCV_TOOLCHAIN_ROOT="${RISCV_TOOLCHAIN_ROOT}"
export PATH="${RISCV_TOOLCHAIN_ROOT}/bin:${PATH}"

mkdir -p "${ENV_D_DIR}"
riscv_env="${ENV_D_DIR}/20-riscv.sh"
{
  echo "# RISC-V toolchain env"
  echo "export RISCV_TOOLCHAIN_ROOT=\"${RISCV_TOOLCHAIN_ROOT}\""
  echo "export PATH=\"${RISCV_TOOLCHAIN_ROOT}/bin:\${PATH}\""
} >"${riscv_env}"

log "RISC-V env written: ${riscv_env}"

