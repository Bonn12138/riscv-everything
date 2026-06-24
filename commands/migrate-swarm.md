---
description: 批量迁移（多智能体）：按文件并行把 x86/ARM 代码迁移到 RISC-V（含 RVV），每文件组走 迁移→查KB→2票对抗review→修复，支持断点续传
argument-hint: [target-dir | classified.json]
allowed-tools: Workflow, Bash, Read, Edit, Glob, Grep
---

# /everything-riscv:migrate-swarm — 批量迁移（workflow）

对 `classified.json` 中的待迁移条目做**批量并行迁移**（对应技能 `riscv-migrate` 阶段 B+C，多智能体版本）。按 `file_path` 聚合（同文件一个 agent）+ 按 `tier` 分批（底层先做），每文件组走 `迁移 → 查知识库补证据 → 2 票对抗 review → 修复`，更新 `progress.json`，支持断点续传。

> 与 `/everything-riscv:migrate`（单 agent 逐条）的区别：并行处理多文件 + 对抗式双票审查 + tier 依赖排序，适合大规模迁移。质量护栏见 `skills/riscv-migrate/referens/workflow_patterns.md` §8 HARD RULES。

## 前置条件

- 已有 `<target>/.riscv_migrate/classified.json`（由 `/everything-riscv:classify-swarm` 产出）。**若不存在，先提示用户跑 `/everything-riscv:classify-swarm`**，不要跳过分类直接迁移。
- 本次运行会发起多智能体 workflow，**首次请求授权（opt-in）**。

## 执行流程

### 1. 解析参数与路径

- `target`：`$ARGUMENTS` 给目录用之；否则从 `classified.json` 的 `.target` 取；都没有用当前工作目录。
- `classified_path`：默认 `<target>/.riscv_migrate/classified.json`。
- 检查 classified.json 存在；不存在则停止并提示先跑 classify-swarm。
- Bash 取 skill 根与时间戳：

```bash
echo "SKILL=${CLAUDE_PLUGIN_ROOT}/skills/riscv-migrate"
date -Iseconds
```

### 2. 发起 workflow（用 Workflow 工具）

调用 **Workflow** 工具：

- `scriptPath`：`${CLAUDE_PLUGIN_ROOT}/skills/riscv-migrate/workflows/migrate-batch.workflow.js`
- `args`：
  ```json
  {
    "target": "<目标工程绝对路径>",
    "skill_root": "<解析出的 skill 绝对路径>",
    "classified_path": "<classified.json 绝对路径>",
    "now": "<date -Iseconds 输出>"
  }
  ```
  - 可选 `files_filter`：`["<file_path>", ...]`，只迁移指定文件（小批量联调/补迁移用）。
  - 可选 `max_parallel`：并发上限（默认 16）。
  - **把 `${CLAUDE_PLUGIN_ROOT}` 替换成实际绝对路径**再传入。

### 3. 汇总结果

workflow 返回 `{files, migrated, blocked, accepted, tiers, workdir, next}`。向用户报告：

- 迁移了多少条目、多少文件、按 tier 的 accepted 分布
- blocked 条目清单（需上游就绪后重跑）
- 产物在 `<target>/.riscv_migrate/progress.json`
- 下一步：`/everything-riscv:verify-swarm` 做 QEMU 对比收敛

## 约束

- 每个 agent 只改自己分配的文件、只动 `_riscv` 后缀、必查 KB 留证据链、禁把汇编退化纯 C、禁 reward-hack 注释（见 workflow_patterns.md §8）。
- 不改 `scan_result.json`；进度只写 `progress.json`。
- 断点续传：`progress.json` 中处于终态（`done`/`verified`/`perf_done`/`skipped`）的条目自动跳过，只处理 `pending`/`blocked`，可直接重跑补齐 blocked/失败项。
