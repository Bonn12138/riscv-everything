#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_env_wrapper
prepare_download_dirs
log "x86_64 cross toolchain"
require_cmd curl

archive="${TOOLCHAIN_DIR}/x86-toolchain.tar.xz"
X86_TOOLCHAIN_URL="https://artsz.zte.com.cn:443/artifactory/zf-eco-release-generic/rpm/x86_64_gcc6.2.0_glibc2.24.0.tar.bz2"

if [[ -f "${archive}" ]]; then
  log "已存在，跳过下载: ${archive}"
else
  curl -H 'X-JFrog-Art-Api:AKCpBwvjChQQwNU5eU8YwNadzJr4Rx5XxyZjByQ8kByPa2d1LNUhwzk5gYajfjmKgmn2nb98Y' "${X86_TOOLCHAIN_URL}" -o "${archive}"
fi

if ! has_x86_64_toolchain_dir; then
  if [[ ! -f "${archive}" ]]; then
    log "ERROR: 缺少 ${archive} 且本地无工具链目录，无法解压" >&2
    exit 1
  fi
  log "解压: ${archive}"
  tar -xf "${archive}" -C "${TOOLCHAIN_DIR}"
fi

discover_x86_toolchain_root
if [[ -z "${X86_TOOLCHAIN_ROOT}" || ! -d "${X86_TOOLCHAIN_ROOT}/bin" ]]; then
  log "ERROR: 未发现 x86_64_* 工具链目录: ${TOOLCHAIN_DIR}" >&2
  exit 1
fi

export X86_TOOLCHAIN_ROOT="${X86_TOOLCHAIN_ROOT}"
export PATH="${X86_TOOLCHAIN_ROOT}/bin:${PATH}"

mkdir -p "${ENV_D_DIR}"
x86_env="${ENV_D_DIR}/15-x86.sh"
{
  echo "# x86_64 cross toolchain env"
  echo "export X86_TOOLCHAIN_ROOT=\"${X86_TOOLCHAIN_ROOT}\""
  echo "export PATH=\"${X86_TOOLCHAIN_ROOT}/bin:\${PATH}\""
} >"${x86_env}"

log "x86 toolchain env written: ${x86_env}"
