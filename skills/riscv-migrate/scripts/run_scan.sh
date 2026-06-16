#!/usr/bin/env bash
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCAN_BIN="${SKILL_ROOT}/scripts/riscv_scan"
ART_API_HEADER='X-JFrog-Art-Api:AKCpBwvjChQQwNU5eU8YwNadzJr4Rx5XxyZjByQ8kByPa2d1LNUhwzk5gYajfjmKgmn2nb98Y'

SCAN_X86_URL_DEFAULT="https://artsz.zte.com.cn:443/artifactory/zf-eco-release-generic/rpm/x86_riscv_scan"
SCAN_ARM_URL_DEFAULT="https://artsz.zte.com.cn:443/artifactory/zf-eco-release-generic/rpm/arm_riscv_scan"

SCAN_X86_URL="${SCAN_X86_URL:-${SCAN_X86_URL_DEFAULT}}"
SCAN_ARM_URL="${SCAN_ARM_URL:-${SCAN_ARM_URL_DEFAULT}}"

# ---------- 判断本机架构并选择下载 URL ----------
scan_suffix_from_uname() {
  case "$(uname -m)" in
    x86_64 | amd64)
      printf '%s' 'x86'
      ;;
    aarch64 | arm64)
      printf '%s' 'arm'
      ;;
    *)
      echo "[scan] WARN: 未识别 CPU 型号 $(uname -m)，回退使用 x86 包" >&2
      printf '%s' 'x86'
      ;;
  esac
}

# ---------- 下载（制品是单文件可执行程序，非 tar） ----------
download_scan_bin() {
  local suffix="$1"
  local url

  case "${suffix}" in
    x86) url="${SCAN_X86_URL}" ;;
    arm) url="${SCAN_ARM_URL}" ;;
  esac

  echo "[scan] 本机 CPU: $(uname -m)，架构: ${suffix}，下载 riscv_scan ..."

  if command -v curl >/dev/null 2>&1; then
    curl -fsS -H "${ART_API_HEADER}" "${url}" -o "${SCAN_BIN}"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --header="${ART_API_HEADER}" "${url}" -O "${SCAN_BIN}"
  else
    echo "[scan] ERROR: 需要 curl 或 wget 来下载 riscv_scan" >&2
    exit 1
  fi

  chmod +x "${SCAN_BIN}"
  echo "[scan] 下载完成: ${SCAN_BIN}"
}

# ---------- 主流程 ----------

# 已有二进制则跳过下载
if [[ ! -x "${SCAN_BIN}" ]]; then
  suffix="$(scan_suffix_from_uname)"
  download_scan_bin "${suffix}"
fi

exec "${SCAN_BIN}" "$@"
