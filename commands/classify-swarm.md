---
description: 批量预分流（多智能体）：按文件并行判定 scan_result.json 每个待迁移点是 汇编/非汇编/热点 + 迁移策略 + tier，产出 classified.json 并初始化 progress.json
argument-hint: [scan_result.json | target-dir]
allowed-tools: Workflow, Bash, Read, Glob, Grep
---

# /everything-riscv:classify-swarm — 批量预分流（workflow）

对 `scan_result.json` 的所有待迁移条目做**批量并行预分流**（对应技能 `riscv-migrate` 的阶段 A.2，多智能体版本）。每个文件分配一个子 agent，读代码段后判定 `asm_flag`（non_asm / light_asm / asm_hotspot）、`strategy`（c_direct / intrinsic / rvv_asm）、`tier`（迁移顺序），产出 `<target>/.riscv_migrate/classified.json` 并初始化 `progress.json`。

> 与 `/everything-riscv:migrate`（单 agent 逐条）的区别：本命令**只分类、不写迁移代码**，用并行快速盘点全局，为后续 `/everything-riscv:migrate-swarm` 提供按 tier 分批的输入。适合 50+ 条目的大工程。

## 前置条件

- 已有 `scan_result.json`（由 `/everything-riscv:scan` 产出），通常在目标工程根目录。
- 本次运行会发起多智能体 workflow，**首次会请求你授权（opt-in）**——属预期行为。

## 执行流程

### 1. 解析参数与路径

- `target`：`$ARGUMENTS` 给目录则用之；否则从 `scan_result.json` 的 `.target` 字段取；都没有则用当前工作目录。
- `scan_result_path`：`$ARGUMENTS` 给了 json 路径就用；否则 `<target>/scan_result.json`。
- 用 Bash 取 skill 根与时间戳：

```bash
echo "SKILL=${CLAUDE_PLUGIN_ROOT}/skills/riscv-migrate"
date -Iseconds   # 作为 args.now
```

### 2. 发起 workflow（用 Workflow 工具）

调用 **Workflow** 工具，参数：

- `scriptPath`：`${CLAUDE_PLUGIN_ROOT}/skills/riscv-migrate/workflows/classify.workflow.js`
- `args`（JSON 对象）：
  ```json
  {
    "target": "<目标工程绝对路径>",
    "skill_root": "${CLAUDE_PLUGIN_ROOT}/skills/riscv-migrate",
    "scan_result_path": "<scan_result.json 绝对路径>",
    "now": "<date -Iseconds 输出>"
  }
  ```
  - 可选 `files_filter`：`["<file_path>", ...]`，只分类指定文件（小批量联调用）。
  - **把 `${CLAUDE_PLUGIN_ROOT}` 替换成上一步解析出的实际绝对路径**再传入（workflow 脚本无 fs，不能自己解析）。

### 3. 汇总结果

workflow 返回 `{total, files, by_asm_flag, by_strategy, by_tier, workdir}`。向用户报告：

- 共分类多少条目、涉及多少文件
- `by_asm_flag` / `by_strategy` / `by_tier` 分布表
- 产物位置 `<target>/.riscv_migrate/classified.json` + `progress.json`
- 下一步建议：`/everything-riscv:migrate-swarm` 进入批量迁移

## 约束

- 本命令不改任何源代码，只产出 `.riscv_migrate/` 下的元数据。
- 设计约定见 `skills/riscv-migrate/referens/workflow_patterns.md`。
