---
description: 性能优化（多智能体）：对 asm_hotspot 产物并行做 llvm-mca 分析+迭代优化，每热点 analyze→optimize→QEMU回归，正确性不可回归
argument-hint: [target-dir | hotspot-file]
allowed-tools: Workflow, Bash, Read, Edit, Glob, Grep
---

# /everything-riscv:mca-swarm — 性能分析与优化（workflow）

对 `progress.json` 中所有 `asm_hotspot` 产物做**并行 llvm-mca 性能优化**（对应技能 `riscv-migrate` 阶段 E，多智能体版本）。每个热点分配一个 agent，内部循环 `提 hot.s → llvm-mca 分析瓶颈 → 小步优化 → QEMU 回归验证`，直到 IPC 达 Dispatch Width 70% 或收敛，**正确性不可回归**。

> 与单 agent 阶段 E 的区别：多热点并行 + 每热点独立迭代 + 自动写回 `progress.json` 的 `mca` 字段。适合 verify-swarm 通过后压性能。

## 前置条件

- **强烈建议先跑 `/everything-riscv:verify-swarm`** 确认正确性已收敛——对未通过验证的代码优化性能无意义。
- `progress.json` 含 `asm_hotspot` 且 `status:verified` 的产物（工作集谓词：`status=="verified" ∧ asm_flag=="asm_hotspot"`）；或用 `args.hotspots` 显式指定。
- 首轮准备环境（含 llvm-mca，首次约 5-15 分钟下载，幂等）。
- 发起多智能体 workflow，**首次请求授权（opt-in）**。

## 执行流程

### 1. 解析参数与路径

- `target`：`$ARGUMENTS` 给目录用之；否则从 `progress.json` 的 `.target` 取；都没有用当前工作目录。
- `-mcpu` 选择优先级（见 `skills/riscv-migrate/referens/code_migrate.md`）：用户指定 > **`zhufeng2`**（默认，自研六发射乱序）> 目标部署芯片 > `sifive-p450`（乱序基线）/ `sifive-u74`（顺序基线）。**不要用 `generic`/`generic-rv64`**（无调度模型，llvm-mca 会报错）。
- Bash 取 skill 根与时间戳：

```bash
echo "SKILL=${CLAUDE_PLUGIN_ROOT}/skills/riscv-migrate"
date -Iseconds
```

### 2. 发起 workflow（用 Workflow 工具）

调用 **Workflow** 工具：

- `scriptPath`：`${CLAUDE_PLUGIN_ROOT}/skills/riscv-migrate/workflows/mca-analyze.workflow.js`
- `args`：
  ```json
  {
    "target": "<目标工程绝对路径>",
    "skill_root": "<解析出的 skill 绝对路径>",
    "now": "<date -Iseconds 输出>",
    "mcpu": "zhufeng2",
    "march": "rv64gcv"
  }
  ```
  - 可选 `hotspots`：`[{artifact, fn}]` 显式指定（默认从 progress.json 取 asm_hotspot）。
  - 可选 `max_rounds`（默认 5，每热点迭代上限）。
  - **把 `${CLAUDE_PLUGIN_ROOT}` 替换成实际绝对路径**再传入。

### 3. 汇总结果

workflow 返回 `{hotspots, regression_ok, ipc_improved, details:[{artifact, ipc, rt, bottleneck, regression_ok}], workdir}`。向用户报告：

- 优化了多少热点、IPC 提升数、正确性回归情况（`regression_ok` 必须全 true，否则需人工复查）
- 每热点的 IPC/Block RThroughput 前后对比 + 主要瓶颈
- 结果写回 `progress.json` 各 entry 的 `mca` 字段，并把 `status` 从 `verified` 推进到 `perf_done`

## 约束

- **正确性不可回归**：每轮优化后 QEMU 输出须与优化前一致；回归则回退。
- 指令/扩展拿不准先查 KB（`run_query.sh`），禁凭感觉编指令；禁 reward-hack 注释。
- 见 `skills/riscv-migrate/referens/workflow_patterns.md` §8。
