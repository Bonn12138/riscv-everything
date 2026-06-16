#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_env_wrapper
prepare_download_dirs
log "ARM cross toolchain"
require_cmd curl

archive="${TOOLCHAIN_DIR}/arm-toolchain.tar.xz"
ARM_TOOLCHAIN_URL="https://artsz.zte.com.cn:443/artifactory/zf-eco-release-generic/rpm/aarch64_gcc14.1.0_glibc2.39.0_fp.tar.bz2"

if [[ -f "${archive}" ]]; then
  log "已存在，跳过下载: ${archive}"
else
  curl -H 'X-JFrog-Art-Api:AKCpBwvjChQQwNU5eU8YwNadzJr4Rx5XxyZjByQ8kByPa2d1LNUhwzk5gYajfjmKgmn2nb98Y' "${ARM_TOOLCHAIN_URL}" -o "${archive}"
fi

if ! has_aarch64_toolchain_dir; then
  if [[ ! -f "${archive}" ]]; then
    log "ERROR: 缺少 ${archive} 且本地无工具链目录，无法解压" >&2
    exit 1
  fi
  log "解压: ${archive}"
  tar -xf "${archive}" -C "${TOOLCHAIN_DIR}"
fi

discover_toolchain_roots
if [[ -z "${ARM_TOOLCHAIN_ROOT}" || ! -d "${ARM_TOOLCHAIN_ROOT}/bin" ]]; then
  log "ERROR: 未发现 aarch64_* 工具链目录: ${TOOLCHAIN_DIR}" >&2
  exit 1
fi

export ARM_TOOLCHAIN_ROOT="${ARM_TOOLCHAIN_ROOT}"
export PATH="${ARM_TOOLCHAIN_ROOT}/bin:${PATH}"

mkdir -p "${ENV_D_DIR}"
arm_env="${ENV_D_DIR}/10-arm.sh"
{
  echo "# ARM toolchain env"
  echo "export ARM_TOOLCHAIN_ROOT=\"${ARM_TOOLCHAIN_ROOT}\""
  echo "export PATH=\"${ARM_TOOLCHAIN_ROOT}/bin:\${PATH}\""
} >"${arm_env}"

log "ARM env written: ${arm_env}"

