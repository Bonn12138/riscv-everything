#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TOOLCHAIN_DIR="${SCRIPT_DIR}/toolchains"
QEMU_DIR="${SCRIPT_DIR}/qemu-static"
ENV_D_DIR="${SCRIPT_DIR}/env.d"

log() {
  echo "[init] $*"
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "ERROR: command not found: ${cmd}" >&2
    exit 1
  fi
}

prepare_download_dirs() {
  mkdir -p "${TOOLCHAIN_DIR}" "${QEMU_DIR}"
}

# 是否已有 aarch64 / riscv64 / x86_64 工具链解压目录
has_aarch64_toolchain_dir() {
  [[ -n "$(find "${TOOLCHAIN_DIR}" -maxdepth 1 -mindepth 1 -type d -name 'aarch64_*' 2>/dev/null | head -1)" ]]
}

has_riscv64_toolchain_dir() {
  [[ -n "$(find "${TOOLCHAIN_DIR}" -maxdepth 1 -mindepth 1 -type d -name 'riscv64_*' 2>/dev/null | head -1)" ]]
}

has_x86_64_toolchain_dir() {
  [[ -n "$(find "${TOOLCHAIN_DIR}" -maxdepth 1 -mindepth 1 -type d -name 'x86_64_*' 2>/dev/null | head -1)" ]]
}

# QEMU user-static 是否已解压（存在任一 qemu-* 文件，不含压缩包）
has_qemu_user_static_extracted() {
  [[ -n "$(find "${QEMU_DIR}" -maxdepth 1 -type f -name 'qemu-*' 2>/dev/null | head -1)" ]]
}

# 在 toolchains/ 下按目录名约定探测 ARM(aarch64) 与 RISC-V 工具链根目录
discover_toolchain_roots() {
  ARM_TOOLCHAIN_ROOT=""
  RISCV_TOOLCHAIN_ROOT=""
  [[ -d "${TOOLCHAIN_DIR}" ]] || return 0
  ARM_TOOLCHAIN_ROOT="$(find "${TOOLCHAIN_DIR}" -maxdepth 1 -mindepth 1 -type d -name 'aarch64_*' 2>/dev/null | head -1)"
  RISCV_TOOLCHAIN_ROOT="$(find "${TOOLCHAIN_DIR}" -maxdepth 1 -mindepth 1 -type d -name 'riscv64_*' 2>/dev/null | head -1)"
}

# 在 toolchains/ 下按目录名约定探测 x86_64 交叉工具链根目录
discover_x86_toolchain_root() {
  X86_TOOLCHAIN_ROOT=""
  [[ -d "${TOOLCHAIN_DIR}" ]] || return 0
  X86_TOOLCHAIN_ROOT="$(find "${TOOLCHAIN_DIR}" -maxdepth 1 -mindepth 1 -type d -name 'x86_64_*' 2>/dev/null | head -1)"
}

ensure_env_wrapper() {
  mkdir -p "${ENV_D_DIR}"
  local env_file="${SCRIPT_DIR}/env.sh"
  {
    echo "# 自动生成：source 本文件即可加载本技能环境变量"
    echo "# 用法: source \"${env_file}\""
    echo ""
    echo "ENV_D_DIR=\"${ENV_D_DIR}\""
    echo "if [[ -d \"\${ENV_D_DIR}\" ]]; then"
    echo "  for f in \"\${ENV_D_DIR}\"/*.sh; do"
    echo "    [[ -f \"\${f}\" ]] || continue"
    echo "    # shellcheck disable=SC1090"
    echo "    source \"\${f}\""
    echo "  done"
    echo "fi"
  } >"${env_file}"
}

