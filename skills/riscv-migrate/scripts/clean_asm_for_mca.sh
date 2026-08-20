#!/usr/bin/env bash
# clean_asm_for_mca.sh — 把 GCC 交叉编译器 -S 输出的 RISC-V 汇编清洗成
# LLVM MC / llvm-mca 可解析的指令流（等价于 clang 管线的替代方案）。
#
# 背景：llvm-mca 底层用 LLVM MC 解析器，对 GNU as 风格汇编中的部分伪指令
#       和注释格式不兼容（最典型：.text 内行尾 /* */、.attribute/.size/.type
#       等描述性伪指令、间接符号修饰），直接喂入会 unexpected token 报错。
#
# 用法：
#   bash clean_asm_for_mca.sh <input.s|- > [output.s|-]
#   riscv64-unknown-linux-gnu-gcc -O3 -S -march=... foo.c -o - \
#     | bash clean_asm_for_mca.sh - - \
#     | llvm-mca -mtriple=riscv64 -mcpu=zhufeng2 -mattr=+v --all-stats
#
# 处理规则（保守，逐条可解释）：
#   1. 删除 GNU 描述性伪指令行：.attribute/.file/.loc/.size/.type/.ident/
#      .section/.p2align/.align/.option/.abicalls/.nan/.module/.text/.data/.bss
#      等段描述与调试信息（llvm-mca 只需要指令流与标签）
#   2. 删除 .cfi_* 行（CFI 调试框架指令）
#   3. .text 段内行尾 /* */ 注释 → //（LLVM MC 不支持 C 风格行尾注释）
#   4. 裸标签保留（llvm-mca 需要它们识别循环边界；用户可用 LLVM-MCA-BEGIN/END
#      进一步圈定热点范围）
#   5. 删除空行（可选，默认保留无妨）
#
# 注意：清洗只影响送入 llvm-mca 的视图，不改动工程源码。

set -euo pipefail

in="${1:--}"
out="${2:--}"

if [[ "$in" == "-" && "$out" == "-" ]]; then
  # 纯管道模式
  sed -E \
    -e '/^[[:space:]]*\.(attribute|file|loc|size|type|ident|section|p2align|align|option|abicalls|nan|module|text|data|bss|globl|local|weak|hidden|protected|ent|end|frame|mask|fmask|set|gnu_attribute)([[:space:]]|$)/d' \
    -e '/^[[:space:]]*\.cfi_[a-z_]+/d' \
    -e 's#/\*([^*]|\*[^/])*\*/[[:space:]]*$#// cleansed#' \
    | awk 'NF'
elif [[ "$in" == "-" ]]; then
  sed -E \
    -e '/^[[:space:]]*\.(attribute|file|loc|size|type|ident|section|p2align|align|option|abicalls|nan|module|text|data|bss|globl|local|weak|hidden|protected|ent|end|frame|mask|fmask|set|gnu_attribute)([[:space:]]|$)/d' \
    -e '/^[[:space:]]*\.cfi_[a-z_]+/d' \
    -e 's#/\*([^*]|\*[^/])*\*/[[:space:]]*$#// cleansed#' \
    | awk 'NF' > "$out"
else
  # 文件模式：写回原文件或指定输出
  target="${out:-$in}"
  [[ "$out" == "-" ]] && target="$in"
  tmp="$(mktemp)"
  sed -E \
    -e '/^[[:space:]]*\.(attribute|file|loc|size|type|ident|section|p2align|align|option|abicalls|nan|module|text|data|bss|globl|local|weak|hidden|protected|ent|end|frame|mask|fmask|set|gnu_attribute)([[:space:]]|$)/d' \
    -e '/^[[:space:]]*\.cfi_[a-z_]+/d' \
    -e 's#/\*([^*]|\*[^/])*\*/[[:space:]]*$#// cleansed#' \
    "$in" | awk 'NF' > "$tmp"
  if [[ "$target" == "-" ]]; then cat "$tmp"; else mv "$tmp" "$target"; fi
  [[ "$target" != "-" ]] && rm -f "$tmp" 2>/dev/null || true
fi
