#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_env_wrapper
prepare_download_dirs
log "llvm-mca tool bundle"
require_cmd curl
require_cmd tar

MCA_DIR="${SCRIPT_DIR}/llvm-mca"
mkdir -p "${MCA_DIR}"

mca_suffix_from_uname() {
  case "$(uname -m)" in
    x86_64 | amd64)
      printf '%s' 'x86'
      ;;
    aarch64 | arm64)
      printf '%s' 'arm'
      ;;
    *)
      log "WARN: 未识别 CPU 型号 $(uname -m)，回退使用 x86 包"
      printf '%s' 'x86'
      ;;
  esac
}

has_llvm_mca_extracted() {
  [[ -n "$(find "${MCA_DIR}" -type f -name 'llvm-mca' 2>/dev/null | head -1)" ]]
}

suffix="$(mca_suffix_from_uname)"

LLVM_MCA_X86_URL_DEFAULT="https://artsz.zte.com.cn:443/artifactory/zf-eco-release-generic/rpm/llvm-mca-bin-x86.tar"
# 约定：ARM(arm64/aarch64) 对应包名为 llvm-mca-arm.tar；如你的制品命名不同，用 LLVM_MCA_ARM_URL 覆盖。
LLVM_MCA_ARM_URL_DEFAULT="https://artsz.zte.com.cn:443/artifactory/zf-eco-release-generic/rpm/llvm-mca-bin-arm.tar"

LLVM_MCA_X86_URL="${LLVM_MCA_X86_URL:-${LLVM_MCA_X86_URL_DEFAULT}}"
LLVM_MCA_ARM_URL="${LLVM_MCA_ARM_URL:-${LLVM_MCA_ARM_URL_DEFAULT}}"

case "${suffix}" in
  x86)
    LLVM_MCA_URL="${LLVM_MCA_URL:-${LLVM_MCA_X86_URL}}"
    ;;
  arm)
    LLVM_MCA_URL="${LLVM_MCA_URL:-${LLVM_MCA_ARM_URL}}"
    ;;
esac

tarball="${MCA_DIR}/llvm-mca-${suffix}.tar"

log "本机 CPU: $(uname -m)，选择 llvm-mca 包: ${tarball}"

if [[ -f "${tarball}" ]]; then
  log "已存在，跳过下载: ${tarball}"
else
  curl -fsS -H 'X-JFrog-Art-Api:AKCpBwvjChQQwNU5eU8YwNadzJr4Rx5XxyZjByQ8kByPa2d1LNUhwzk5gYajfjmKgmn2nb98Y' "${LLVM_MCA_URL}" -o "${tarball}"
fi

if ! has_llvm_mca_extracted; then
  if [[ ! -f "${tarball}" ]]; then
    log "ERROR: 缺少 ${tarball} 且本地无 llvm-mca 文件，无法解压" >&2
    exit 1
  fi
  log "解压: ${tarball}"
  tar -xf "${tarball}" -C "${MCA_DIR}"
fi

# 尝试定位 llvm-mca 所在目录并写入 env.d
LLVM_MCA_BIN_DIR="$(dirname "$(find "${MCA_DIR}" -type f -name 'llvm-mca' 2>/dev/null | head -1)")"
if [[ -z "${LLVM_MCA_BIN_DIR}" || ! -x "${LLVM_MCA_BIN_DIR}/llvm-mca" ]]; then
  log "ERROR: 解压后未发现可执行 llvm-mca: ${MCA_DIR}" >&2
  exit 1
fi

export LLVM_MCA_BIN_DIR="${LLVM_MCA_BIN_DIR}"
export PATH="${LLVM_MCA_BIN_DIR}:${PATH}"

mkdir -p "${ENV_D_DIR}"
mca_env="${ENV_D_DIR}/25-llvm-mca.sh"
{
  echo "# llvm-mca env"
  echo "export LLVM_MCA_BIN_DIR=\"${LLVM_MCA_BIN_DIR}\""
  echo "export PATH=\"${LLVM_MCA_BIN_DIR}:\${PATH}\""
} >"${mca_env}"

log "llvm-mca env written: ${mca_env}"

