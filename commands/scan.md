---
description: 扫描 x86/ARM 代码库，盘点待迁移点，产出 scan_result.json
argument-hint: [source-dir]
---

# /everything-riscv:scan — 工程扫描

对目标代码库执行结构化扫描，识别所有需要迁移的点（SIMD 内建函数、汇编块、架构特定代码等），产出 `scan_result.json`。

## 执行流程

1. 如果 `$ARGUMENTS` 提供了目录，以该目录为扫描根；否则以当前工作目录为准
2. 使用 `skills/riscv-migrate` 技能中的扫描脚本（首次运行会自动从内网 Artifactory 下载扫描引擎二进制——凭据已内置，无需手动配置；后续运行复用本地二进制）：

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/riscv-migrate/scripts/run_scan.sh" <source-dir>
```

3. 扫描结果写入 `<source-dir>/scan_result.json`
4. 汇总报告：待迁移点总数、按类型分类（intrinsic / inline asm / .S 文件 / 架构宏）、涉及的文件列表

## 产物

- `scan_result.json` — 迁移点清单，每个条目包含文件路径、行号、类型、原始代码片段、建议的 RISC-V 替代方案（如有知识库匹配）
