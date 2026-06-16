#!/usr/bin/env bash
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 下载/准备 RISC-V 交叉工具链（若已存在会跳过）
bash "${SKILL_ROOT}/resources/riscv_toolchain_env.sh"

# 下载/准备 qemu-user-static（若已存在会跳过）
bash "${SKILL_ROOT}/resources/qemu_static_env.sh"

# 下载/准备 llvm-mca（若已存在会跳过）；阶段 E 性能分析所需
bash "${SKILL_ROOT}/resources/llvm_mca_env.sh"

cat <<EOF

已准备好验证与分析环境（含 RISC-V 工具链、QEMU、llvm-mca）。

在当前 shell 执行以下命令加载环境变量：
  source "${SKILL_ROOT}/resources/env.sh"

EOF

