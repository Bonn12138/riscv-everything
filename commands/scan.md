---
description: 阶段一：扫描 x86/ARM 代码库，盘点待迁移点。基础扫描器 + 可选并行扫描 Subagent → 主 Agent 合并去重后产出 scan_result.json（每条目带 status=TODO）
argument-hint: [source-dir]
---

# /everything-riscv:scan — 工程扫描（迁移阶段一）

对目标代码库执行结构化扫描，识别所有需要迁移的点（SIMD 内建函数、汇编块、架构特定代码等），产出 `scan_result.json`。对应 `riscv-migrate` 技能的**阶段一**。

## 在四大阶段中的位置

```
阶段一(本命令)     →     阶段二(按条目迁移，依赖分组后可并行)
     ↓                           ↓
scan_result.json            *_riscv 源码 + QEMU 验证通过
(每条目 status=TODO,          (status: TODO→START→DONE + marking,
 主 Agent 统一写入)              主 Agent 独占 JSON 写入)
                                      ↓
                            阶段三(并行诊断 + 串行修复) → 阶段四(多热点并行分析)
```

## 执行流程

1. 如果 `$ARGUMENTS` 提供了目录，以该目录为扫描根；否则以当前工作目录为准。
2. **第一步：运行基础扫描**。使用 `skills/riscv-migrate` 技能中的扫描脚本（首次运行会自动从内网 Artifactory 下载扫描引擎二进制——凭据已内置，无需手动配置；后续运行复用本地二进制）：

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/riscv-migrate/scripts/run_scan.sh" <source-dir>
```

3. **第二步：评估是否需要并行补充扫描**。主 Agent 根据工程规模判断：
   - 工程较小或扫描覆盖明确 → 无需 Subagent，直接进入合并。
   - 存在多架构或多独立目录 → 可派发 `riscv-scan-worker`（按维度：x86 intrinsic / ARM NEON / 架构宏 / 构建系统）并行扫描。
4. **第三步：主 Agent 合并去重**。合并基础扫描结果与 Subagent 候选条目，去重键：`class_type + file_path/missing_path + start_line + end_line + solver_type`。
5. 扫描结果写入 `<source-dir>/scan_result.json`（**只有主 Agent 可以写入**）。
6. **每个条目默认带 `status="TODO"` 与 `marking=""`**（权威源在 JSON，不在源码注释）。
7. 汇总报告：待迁移点总数、按类型分类（intrinsic / inline asm / .S 文件 / 架构宏）、涉及的文件列表、扫描 Subagent 调度统计（如有）。

## 产物

- `scan_result.json` — 迁移点清单，每个条目包含文件路径、行号、类型（`solver_type`）、原始代码片段、建议的 RISC-V 替代方案（如有知识库匹配），以及 `status` / `marking` 字段
- 阶段二将基于本文件分流（汇编/非汇编）并按条目迁移，通过改写 `status` / `marking` 字段同步进度（仅主 Agent 写入）

## 后续

- 调用 `/everything-riscv:migrate` 进入阶段二（按条目分流迁移，依赖分组后可并行）
- 详细 JSON Schema（含 `status` / `marking` 字段定义）见 `skills/riscv-migrate/referens/project_scan.md`
