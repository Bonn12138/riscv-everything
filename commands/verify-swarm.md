---
description: 验证收敛（多智能体）：循环 survey 所有 RISC-V 产物(QEMU 对比 baseline)→并行修真发散→2票review→apply，直到全部一致
argument-hint: [target-dir]
allowed-tools: Workflow, Bash, Read, Edit, Glob, Grep
---

# /everything-riscv:verify-swarm — 验证收敛（workflow）

对 `progress.json` 中所有已迁移（`status:done`）的 RISC-V 产物做**循环收敛验证**（工作集谓词：`status=="done"`；对应技能 `riscv-migrate` 阶段 D，多智能体版本）。每轮并行编译原始 vs RISC-V（QEMU）并对比 baseline，只把**真发散**交给 fix agent 修，2 票审查后应用，循环到 `failing=0 && uncovered=0`。**收敛后把 passing/triaged 条目从 `done` 推进到 `verified`（+ `.verify` sidecar 写回 `progress.json`），供 `/everything-riscv:mca-swarm` 消费**。

> 与 `/everything-riscv:verify`（单 agent）的区别：并行探测 + 循环收敛 + baseline 缓存 + passing/triaged 累积不重跑。仿 Bun mega-swarm 模式。

## 前置条件

- 已有 `<target>/.riscv_migrate/progress.json`，且含 `status:done` 的产物（由 `/everything-riscv:migrate-swarm` 产出）。**若无 done 产物，先跑 migrate-swarm**。
- 首轮会准备 RISC-V 工具链 + QEMU（`prepare_verify_env.sh`，首次约 5-15 分钟下载，幂等）。
- 发起多智能体 workflow，**首次请求授权（opt-in）**。

## 执行流程

### 1. 解析参数与路径

- `target`：`$ARGUMENTS` 给目录用之；否则从 `progress.json` 的 `.target` 取；都没有用当前工作目录。
- 检查 `<target>/.riscv_migrate/progress.json` 存在且有 done 条目。
- Bash 取 skill 根与时间戳：

```bash
echo "SKILL=${CLAUDE_PLUGIN_ROOT}/skills/riscv-migrate"
date -Iseconds
```

### 2. 发起 workflow（用 Workflow 工具）

调用 **Workflow** 工具：

- `scriptPath`：`${CLAUDE_PLUGIN_ROOT}/skills/riscv-migrate/workflows/verify-swarm.workflow.js`
- `args`：
  ```json
  {
    "target": "<目标工程绝对路径>",
    "skill_root": "<解析出的 skill 绝对路径>",
    "now": "<date -Iseconds 输出>"
  }
  ```
  - 可选 `max_rounds`（默认 10）、`max_fix`（默认 20，每轮并行 fix 上限）。
  - **把 `${CLAUDE_PLUGIN_ROOT}` 替换成实际绝对路径**再传入。

### 3. 汇总结果

workflow 返回 `{rounds, done, passing, total, verified, history, workdir}` 或跑到上限未收敛。向用户报告：

- 收敛与否（`done:true` = 全部一致）、跑了多少轮、passing/total
- 仍未通过的产物（可重跑续传，`passing.txt` 里已通过的不重跑）
- `triaged-slow.txt` 里被排除的（baseline 本就异常/环境慢，非迁移 bug）
- 下一步：对 `asm_hotspot` 产物跑 `/everything-riscv:mca-swarm` 做性能分析

## 约束

- 每个 fix agent 只改 RISC-V 侧（`_riscv` 产物），**禁止改原始 x86/ARM 实现**（基线真相）。
- 正确性优先，不追性能；禁 reward-hack 注释与 unsafe 绕过（见 workflow_patterns.md §8）。
- baseline 首次缓存后不重跑；`passing.txt` 累积永不重探测。
