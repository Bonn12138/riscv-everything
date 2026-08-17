---
name: riscv-migrate
description: x86/ARM → RISC-V（含 RVV）一体化迁移技能。采用「主 Agent 编排 + 阶段内动态 Subagent」架构：主 Agent 是唯一编排者与状态提交者，Subagent 承担阶段内工作单元。按四大阶段推进：一、扫描迁移点（可并行扫描 Subagent）；二、按迁移点分流迁移（规划 Subagent 先产出互不重叠任务分组 → 迁移 Subagent 先做必要性分析再迁移 → 依赖分组后独立条目可并行）；三、工程级编译修复（并行诊断、串行修复）；四、性能分析（多热点并行 llvm-mca）。每个迁移点的状态以 `scan_result.json` 条目里的 `status`（TODO/START/DONE）+ `marking`（异常/性能说明）字段同步，权威源在 JSON，不在源码注释。**只有主 Agent 可以写入 `scan_result.json`**。
---

# RISC-V 迁移（riscv-migrate）

> **架构：主 Agent 编排 + 阶段内动态 Subagent**

面向 **x86 或 ARM** 工程，按**四大阶段**推进：

| 阶段 | 名称 | 产出 | 执行模式 |
| ---- | ---- | ---- | -------- |
| **一** | 扫描迁移点 | `<project_root>/scan_result.json` | 基础扫描器 + 可选并行扫描 Subagent → 主 Agent 合并去重 |
| **二** | 按迁移点分流迁移 | `*_riscv` 源码 + 通过验证 | 规划 Subagent 先产出任务分组 → 迁移 Subagent 必要性分析 + 迁移（独立组可并行）；汇编/RVV 条目必过 Reviewer 门禁 |
| **三** | 工程级编译修复 | RV 可执行程序 | 并行诊断编译错误 + 主 Agent 或单个写入型 Subagent 串行修复 |
| **四**（独立 agent） | 性能分析与优化 | llvm-mca 报告 + 优化提交 | 多个独立热点可并行分析；主 Agent 统一实施、验收与回写 |

> **阶段四为独立 agent**：仅在阶段一/二/三全部通过后启动。

> **迁移点进度（必须，权威源在 `scan_result.json` 中）**：每个条目通过 `status` 与 `marking` 两个结构化字段同步进度：
> - `status`（三态互斥）：`TODO` / `START` / `DONE`
> - `marking`（备注/异常/性能说明）：`status=DONE` 时必填
> - 不再向源码写 `MIGRATE-*` 注释，JSON 是唯一权威源（详见 [referens/project_scan.md](referens/project_scan.md)）
> - **只有主 Agent 可以写入 `scan_result.json`**

---

## 架构概述：主 Agent 编排 + Subagent 执行

本技能采用「主 Agent 编排 + 阶段内动态 Subagent」模式：

```text
主 Agent（唯一编排者和状态提交者）
  │
  ├─ 阶段一：一个或多个只读扫描 Subagent
  │            └─ 主 Agent 合并、去重并生成 scan_result.json
  │
  ├─ 阶段二：迁移规划 Subagent 制定分配方案（riscv-migration-planner）
  │            └─ 一个或多个迁移 Subagent
  │                 ├─ 必要性分析（误报直接返回 NO_WORK_NEEDED）
  │                 ├─ 普通架构适配
  │                 ├─ RVV intrinsic 迁移
  │                 ├─ inline/standalone asm 迁移
  │                 └─ riscv-code-reviewer 独立审查
  │
  ├─ 阶段三：一个或多个只读诊断 Subagent
  │            └─ 主 Agent 或单个写入型 Subagent 串行实施修复
  │
  └─ 阶段四：一个或多个 riscv-asm-analyzer
                └─ riscv-code-reviewer 复核优化正确性
```

### 主 Agent 职责（唯一编排者与状态提交者）

主 Agent 不再默认亲自完成全部工作，而是负责：

1. 探测工程根目录和技能资源；
2. 读取并修复 `scan_result.json` schema；
3. 审核规划 Subagent 产出的阶段二任务分组方案；
4. 给每个任务分配唯一 `task_id`；
5. 收集并核验 Subagent 结果；
6. 合并代码修改；
7. 运行最终验证；
8. 更新 `status` / `marking`；
9. 输出阶段摘要；
10. 决定阶段推进或回流。

> **主 Agent 不得把"Subagent 返回成功"直接等同于"条目 DONE"**。Subagent 只完成被分配的工作单元，不拥有阶段推进权和最终完成认定权。

> **阶段二的任务拆分与分派由迁移规划 Subagent 专职完成**：主 Agent 只提供条目全集和工程上下文，规划 Subagent 输出互不重叠的任务分组方案，主 Agent 审核后按方案分派，从源头保证同一个文件不会被多个迁移 Subagent 同时操作。规划 Subagent 不可用或方案不满足硬性条件时，主 Agent 按同样的硬性条件自行接管规划职责。

### Subagent 通用约束

- **不修改 `scan_result.json`**：所有 Subagent（含 Reviewer、Analyzer、Worker）均不得直接写入状态文件。
- **不直接向用户提问**：Subagent 缺少信息时先将问题返回主 Agent，由主 Agent 按自主运行原则处理。
- **不超出分配范围**：Subagent 只能处理任务包内指定的文件与行号范围。
- **只返回"待审查/待验证"**：Subagent 内部使用 `READY_FOR_REVIEW`、`READY_FOR_VERIFY`、`BLOCKED` 等状态，这些状态不写入 `scan_result.json`。

---

## 动态调度决策

主 Agent 不是强制每个阶段都调用多个 Subagent，而是根据实际工作量选择：

```text
零个 Subagent：主 Agent 直接完成
一个 Subagent：委托一个明确工作单元
多个 Subagent：处理互不依赖且收益明显的工作流
```

**阶段二是例外**：任务拆分与分配默认必须先经过迁移规划 Subagent（或主 Agent 按同样硬性条件接管规划），不允许在没有分组方案的情况下直接并行分派迁移 Subagent。

### 分派前检查

主 Agent（阶段二由规划 Subagent）每次分派前评估：

1. 是否存在两个以上可独立完成的任务；
2. 任务是否修改相同文件；
3. 任务是否依赖同一个尚未确定的公共接口；
4. 是否需要共享完整上下文；
5. 并行节省是否大于上下文重建和结果合并成本；
6. 是否存在匹配的专业 Agent；
7. 当前执行环境是否支持隔离的写入工作区。

### 推荐调度规则

| 工作情况 | 调度策略 |
| -------- | -------- |
| 阶段二任务分组与分派 | 先由迁移规划 Subagent 产出分组方案，主 Agent 审核后执行 |
| 单文件、少量读取即可完成 | 规划方案中标记为主 Agent 直接处理 |
| 一个复杂但边界清晰的任务 | 一个 Subagent |
| 多个独立目录的只读扫描 | 多个 Subagent 并行 |
| 多个独立文件的迁移 | 可并行，但写入集合必须互斥（以规划方案的 `write_files` 为准） |
| 同一文件内多个迁移点 | 强制划入同一任务组，交给同一个 Subagent 串行处理 |
| 疑似扫描误报的条目 | 迁移 Subagent 先做必要性分析，确认误报直接返回 `NO_WORK_NEEDED`，不进入迁移 |
| 多个条目共享公共头文件/API | 先由一个 Agent 确定公共接口，再处理叶子条目 |
| 多个编译错误 | 可并行诊断，不并行写入修复 |
| 多个独立热点函数 | 多个 Analyzer 并行 |
| 单个小任务的复核 | 不额外创建多个 Reviewer |

### 并发上限（保守默认）

- 阶段一只读扫描：最多 4 个并行 Subagent；
- 阶段二迁移规划：同一时刻最多 1 个规划 Subagent，产出全量分组方案；
- 阶段二写入型迁移：最多 4 个并行 Subagent，且必须依据规划方案的分组，保证写入集合互斥或使用隔离工作区；
- 阶段二审查：可与其他互不相关条目的迁移并行；
- 阶段三只读诊断：最多 3 个并行 Subagent；
- 阶段三写入修复：同一时刻最多 1 个；
- 阶段四热点分析：最多 4 个并行 Analyzer；
- 用户明确要求不同并发度时，由主 Agent 在安全边界内覆盖默认值。

---

## 统一任务输入协议

主 Agent 应给每个 Subagent 提供明确的任务包，避免其重新探索整个工程或扩大范围。

### 规划任务包（阶段二）

```json
{
  "task_id": "phase2-plan-all",
  "phase": "PHASE_2",
  "role": "MIGRATION_PLANNER",
  "project_root": "/abs/path/project",
  "scan_result_path": "/abs/path/project/scan_result.json",
  "entries": "全部 TODO/START 条目（或直接指向 scan_result.json）",
  "requirements": [
    "按依赖分组规则产出任务组",
    "每个任务组显式列出 write_files",
    "任意两个可并行任务组的 write_files 交集为空",
    "同一文件的所有条目必须划入同一任务组",
    "为每个任务组标注 parallel/serial/main_agent 和工作量估计",
    "只读，不修改任何文件"
  ],
  "acceptance": [
    "每个条目恰好属于一个任务组",
    "返回 PLAN_READY + 结构化分组方案"
  ]
}
```

### 迁移任务包

```json
{
  "task_id": "phase2-src-crc-crc32-c-120-200",
  "phase": "PHASE_2",
  "role": "MIGRATION_WORKER",
  "project_root": "/abs/path/project",
  "scan_result_path": "/abs/path/project/scan_result.json",
  "scope": {
    "file_path": "/abs/path/project/src/crc/crc32.c",
    "start_line": 120,
    "end_line": 200,
    "solver_type": "InlineAsm"
  },
  "allowed_write_paths": [
    "/abs/path/project/src/crc/crc32_riscv.c",
    "/abs/path/project/tests/crc32_riscv_test.c"
  ],
  "read_only_paths": [
    "/abs/path/project/scan_result.json"
  ],
  "requirements": [
    "先做必要性分析：确认误报则不改代码，直接返回 NO_WORK_NEEDED 并附依据",
    "RVV 1.0",
    "补充原始实现与 RISC-V 实现对比测试",
    "涉及指令和扩展时查询知识库"
  ],
  "acceptance": [
    "局部交叉编译通过",
    "返回知识库 file_path/header_path",
    "不得修改 scan_result.json"
  ]
}
```

任务包中的路径列表是能力边界。主 Agent 合并结果时必须检查实际修改是否超出 `allowed_write_paths`。

## 统一结构化返回协议

所有 Subagent 应返回统一结果：

```json
{
  "task_id": "phase2-src-crc-crc32-c-120-200",
  "phase": "PHASE_2",
  "status": "READY_FOR_REVIEW",
  "summary": "已将 SSE CRC 内核迁移为 RVV 1.0 intrinsic 实现",
  "changed_files": [
    "/abs/path/project/src/crc/crc32_riscv.c",
    "/abs/path/project/tests/crc32_riscv_test.c"
  ],
  "commands_run": [
    "riscv64-unknown-linux-gnu-gcc ..."
  ],
  "tests": [
    {
      "name": "crc32-local-build",
      "result": "PASS",
      "evidence": "exit code 0"
    }
  ],
  "knowledge_evidence": [
    {
      "claim": "所用 intrinsic 属于 RVV 1.0",
      "file_path": "...",
      "header_path": "..."
    }
  ],
  "risks": [],
  "blocked_reason": ""
}
```

允许的 Subagent 内部 `status`（不得写入 `scan_result.json`）：

| 状态 | 含义 |
| ---- | ---- |
| `PLAN_READY` | 规划 Subagent 产出任务分组方案，等待主 Agent 审核 |
| `READY_FOR_REVIEW` | 修改完成，等待独立审查 |
| `READY_FOR_VERIFY` | 修改和局部检查完成，等待主 Agent 最终验证 |
| `PASS` | Reviewer/Analyzer 任务通过 |
| `NEEDS_FIX` | 存在明确可修复问题，需要回流原任务 |
| `BLOCKED` | 当前范围内无法继续，需要主 Agent 补充上下文或调整任务 |
| `FAILED` | 执行失败，且没有形成可合并结果 |
| `NO_WORK_NEEDED` | 必要性分析确认无需修改（误报），并提供依据 |

误报返回示例（迁移 Subagent 必要性分析确认误报）：

```json
{
  "task_id": "phase2-src-crc-crc32-c-120-200",
  "phase": "PHASE_2",
  "status": "NO_WORK_NEEDED",
  "summary": "条目指向的 CRC 分支已含 __riscv 条件编译实现，无需迁移",
  "changed_files": [],
  "commands_run": [],
  "tests": [],
  "knowledge_evidence": [],
  "false_positive_justification": {
    "file_path": "/abs/path/project/src/crc/crc32.c",
    "line_range": "120-200",
    "reason": "该函数已有 #ifdef __riscv 分支并使用 RVV intrinsic，x86 分支在 RV 构建下不参与编译",
    "evidence": "crc32.c:118 #ifdef __riscv；crc32.c:121-198 为 RVV 实现"
  },
  "risks": [],
  "blocked_reason": ""
}
```

主 Agent 复核 `false_positive_justification` 后，将条目以"误报"写入 `marking` 并置 `DONE`；对依据存疑时退回补充或二次分析，不得直接采信。

---

## 并发与写入安全

### 单一状态提交者

`scan_result.json` **只能由主 Agent 写入**。

- `riscv-code-reviewer`：返回建议的状态回流内容，不直接编辑 JSON；
- `riscv-asm-analyzer`：返回建议 marking，由主 Agent 验证后写入；
- 所有 Worker 类 Subagent：不接触 JSON。

### 文件独占

写入型任务在执行前必须声明 `allowed_write_paths`。任意两个并行任务的写入集合不得相交：

```text
write_set(task_a) ∩ write_set(task_b) = ∅
```

阶段二的写入集合互斥由迁移规划 Subagent 的分组方案保证，主 Agent 审核时校验三条硬性条件：

1. 每个条目恰好属于一个任务组；
2. 任意两个标记为 `parallel` 的任务组，其 `write_files` 交集为空；
3. 同一文件的所有条目位于同一任务组。

不满足时退回规划 Subagent 重规划；连续不可用时主 Agent 按同样硬性条件接管。

公共头文件、构建脚本、链接脚本和统一测试入口默认视为共享资源，必须串行修改。

### 隔离工作区与输出目录

若执行环境支持独立 worktree，可将不同写入型任务放在独立 worktree 中并行执行，由主 Agent 逐个审查合并。

局部测试和编译应使用任务独立的输出目录：

```text
<project_root>/.riscv-migrate/tasks/<task_id>/build/
<project_root>/.riscv-migrate/tasks/<task_id>/logs/
```

### 结果合并检查

主 Agent 合并前至少检查：

1. 实际修改路径是否越界；
2. 是否修改了 `scan_result.json`；
3. 是否覆盖用户原有未提交修改；
4. 是否与已合并任务冲突；
5. 是否保留现有代码风格和构建约定；
6. 测试结果是否可复现；
7. 知识库证据是否覆盖关键架构结论；
8. `NO_WORK_NEEDED` 的误报依据是否足以支撑结论。

---

## 失败、重试与回流

### Subagent 失败处理（不直接询问用户）

```text
Subagent 失败
  → 主 Agent 检查任务输入是否充分
  → 缩小或重新拆分任务
  → 恢复原 Subagent 上下文或更换角色
  → 主 Agent 必要时接管
  → 达到现有白名单阈值后才询问用户
```

### 回流路径

| 发现位置 | 回流位置 |
| -------- | -------- |
| 扫描候选冲突或遗漏 | 阶段一合并步骤 |
| 规划分组不完整或写入集合相交 | 主 Agent 退回规划 Subagent 重规划；连续不可用时接管 |
| 必要性分析确认误报 | 直接返回 `NO_WORK_NEEDED`，不进入迁移流程 |
| 主 Agent 对误报依据存疑 | 退回原 Subagent 补充依据，或另派 Subagent 二次分析 |
| Reviewer 发现 RVV/ABI 高危问题 | 阶段二 2.2 |
| 条目 QEMU 不一致 | 阶段二 2.2/2.4 修复循环 |
| 完整编译发现架构迁移遗漏 | 对应阶段二条目，状态保持或恢复为 `START` |
| 阶段四优化引入语义问题 | 阶段四修改撤回或回到阶段二 2.4 |
| Analyzer 结果互相冲突 | 主 Agent 统一基线参数后重新分析 |

### 恢复原 Subagent

同一个任务需要补充信息或继续修复时，应优先恢复原 Subagent 上下文，而不是创建新的 Agent 重新探索。只有以下情况才更换：

- 原任务范围划分错误；
- Agent 类型不匹配；
- 连续给出不可用结果；
- 需要独立审查而非继续执行。

---

## 阶段门禁（不变）

```text
阶段一完成
  → 才能进入阶段二
阶段二所有条目 DONE
  → 才能进入阶段三
阶段三产物构建并运行成功
  → 才能进入阶段四
```

阶段内可以并行，不代表阶段之间可以越级并行。

---

## 默认策略（关键）

- **四阶段串行 + 阶段内 Subagent 并行**：阶段门禁不变；阶段内按工作量动态调用 Subagent。
- **迁移点闭环（阶段二）**：每个条目严格按 `分流 → 迁移 → 知识库 → 验证` 四步走；验证通过才能从 `START` 推进到 `DONE`。
- **分流依据（`solver_type` + 源码特征）**：InlineAsm/Builtin 或含 intrinsic/asm → 汇编完整闭环 B→C→D→E（迁移→查库→验证→llvm-mca 迭代）；其余 → 轻量迁移（架构宏替换 + 交叉编译验证）。
- **知识库主动调用（禁止猜）**：一旦出现指令名/扩展名/`__riscv_*`/ABI/CSR 等信号，必须**主动查询**知识库（MCP 工具或 `scripts/query.py`），并在输出中保留证据链字段（`file_path/header_path`）。
- **验证主动触发（不等用户提）**：每轮迁移实质改动后，主动推进验证；RISC-V 工具链与 QEMU 由技能侧自动部署并加载。
- **状态字段强约束**：每个条目的 `status` 必须为 `TODO/START/DONE` 三态之一；`status=DONE` 必须伴随非空 `marking` 写明是否有异常/性能影响。**只有主 Agent 可以更新 `scan_result.json`**。
- **不依赖虚拟环境**：默认按"系统 Python + 脚本自举依赖"方式运行 `scripts/run_scan.sh` / `scripts/run_query.sh`；不要求用户建/激活 venv。
- **零交互自主运行（核心）**：所有决策按下文「自主运行原则」默认处理；只有白名单列出的 5 类场景才向用户提问。Subagent 不得直接向用户提问。**禁止**在 `-mcpu`/`-march`/`llvm-mca` 是否安装/汇编 vs intrinsic/测试怎么写 等典型决策上向用户请示。

---

## 自主运行原则（零交互硬约束）

> **目标**：技能被调用后，尽量不向用户提问，agent 直接自主推进到任务完成。下面的 18 条决策**默认由 agent 自行决定**，不要问用户；只有 B 节白名单里列出的 5 类场景才向用户问一次性输入。**Subagent 不得直接向用户提问**——缺少信息时先将问题返回主 Agent，由主 Agent 按本原则处理。

### A. 自主默认（agent 直接采用，无需确认）

| # | 决策点 | 默认值 / 默认行为 | 升级触发（覆盖默认） |
| - | ------ | ------------------ | --------------------- |
| 1 | 目标工程根目录 | 当前工作目录；若当前目录无源码则向上找最近的 `CMakeLists.txt` / `Makefile` / `meson.build` / 含 `.c`/`.cpp` 的目录 | 用户 prompt 显式指定则用指定值 |
| 2 | `scan_result.json` 输出路径 | `<目标工程根>/scan_result.json` | 无 |
| 3 | 是否重新扫描 | 存在 → 不重扫，直接读；不存在 → 跑扫描。不要问"要不要重扫" | 用户主动说"重新扫描"才覆盖旧文件 |
| 4 | 汇编形式（RVV 汇编 vs RVV intrinsic） | 默认 **RVV 1.0 intrinsic**（可读性、可移植性更好）；只有项目强制要求手写汇编或编译器版本不支持某些 intrinsic 时才用纯汇编 | 无 |
| 5 | 单元测试缺失 | **agent 自己补**：从原始 x86/ARM 源码静态推导算法，构造最小测试向量与断言（边界值 + 随机 + checksum）；不要问"测试怎么写" | 无 |
| 6 | `-mcpu`（llvm-mca / 编译） | `user prompt 显式指定 > zhufeng2（默认） > 工程 Makefile/CMake 中已声明的 -march > 探测到的目标部署芯片 > sifive-p450（乱序基线）`。**不要用 `generic`** | 无 |
| 7 | `-march` 起点 | `rv64gcv_zbb_zbc_zvbc_zvkb_zvksed`；编译报错时**自主追加**扩展（`Zfh`/`Zvfh`/`Zicclsm` 等），不询问 | 用户自定义 march 才覆盖 |
| 8 | `marking` 内容 | "无异常" = 测试一致 + 无编译告警；"性能 N%" = 跑 5 次取中位数，相对 x86/ARM 取 ±5%；"建议阶段四" = N% < 100（即劣于参考）；"语义差异" = QEMU 对比有差异但被测试规避 | 无 |
| 9 | 阶段四触发 | 条目 `marking` 含 "建议进入阶段四"，**或**存在手写汇编/RVV/intrinsic 热点循环，**或**用户主动要求 | 无 |
| 10 | 优化终止 | IPC ≥ Dispatch × 0.7 **且** 连续两轮 Block RThroughput 差距 < 5% **且** 输出 `No resource or data dependency bottlenecks` | 无 |
| 11 | 编译错误处理 | 自主诊断（看错误码）→ 自主修复（装系统包、调 march、追加 include、`-mabi` 切换等）→ 不在中途汇报"修了一个错误要不要继续" | 连续 3 次同类型失败才向用户汇报 |
| 12 | QEMU 对比失败修复 | 差异行 ≤ 3：自主改 RISC-V 侧；> 3：自主定位 + 修复；修复后回到"对比 → 修复"循环直到一致 | 连续 5 轮不一致才向用户汇报 |
| 13 | 知识库查询失败 | 改为更具体的查询条件重试 2 次（补指令名/扩展名/SEW/LMUL）；仍失败 → 降级为参考 [code_migrate.md](referens/code_migrate.md) 默认映射 | 重试都失败再问 |
| 14 | JSON schema 不一致 | 自主修补：缺 `status`/`marking` 字段的条目补默认值 `TODO`/`""`，写回时保留缩进 | 无 |
| 15 | 工具链/QEMU 不存在 | 自主调用 `prepare_verify_env.sh` 部署；不要问"要不要装" | 内网不可达才问 |
| 16 | `llvm-mca` 不存在 | 自主调用 `llvm_mca_env.sh` 安装；不要问"要不要装" | 制品源不可达才考虑降级为人工优化建议 |
| 17 | 报告时机 | 只在**每个阶段结束时**输出一次结构化摘要（条目数 / 变更文件 / 产物路径 / 阻塞项；并行阶段增加 Subagent 调度统计）；不要逐条目汇报 | 用户要求"详细日志"才细化 |
| 18 | 错误汇报时机 | 真正的不可恢复错误（内网断 / 所有编译器都不支持 / JSON schema 无法修补 / 用户输入来源空）才向用户汇报；可恢复的一律自助修复 | 无 |

### B. 真正可以问用户的时机（白名单）

只有以下 5 类场景**可以**问用户一次，且应给出明确推荐选项：

1. 用户 prompt 完全没指明目标工程，**且**当前目录无任何源码文件（找不到根，根目录探测失败）
2. 阶段二某条目 5 轮 QEMU 对比仍不一致（修复循环超出阈值）
3. 阶段三编译连续 3 个同类错误未能修复
4. 内网不可达 **且** 无离线制品包（`prepare_verify_env.sh` / `llvm_mca_env.sh` 都失败）
5. 用户主动问进度（每阶段结束后查询即可，agent 主动汇报）

### C. 不用问的"看起来该问"（反模式黑名单）

下列问题**永远不要问**，agent 自己决策：

- ❌ "用汇编还是 intrinsic 写 RVV？" → 默认 intrinsic（第 4 条）
- ❌ "测试怎么写？" → 自己补（第 5 条）
- ❌ "用什么 `-march`？" → 自己选起点，报错自主升级（第 7 条）
- ❌ "`llvm-mca` 没装，要不要装？" → 自己装（第 16 条）
- ❌ "性能掉到 X%，要不要进阶段四？" → 看 `marking` 约定，自主决定（第 8、9 条）
- ❌ "阶段一已完成，要进入阶段二吗？" → **默认自动进入**，禁止等待确认
- ❌ "阶段三跑通了，要进阶段四吗？" → 看 `marking` 字段约定，自主决定（第 9 条）
- ❌ "要不要重扫？" → 默认不重扫（第 3 条）
- ❌ "调度模型用哪个 `-mcpu`？" → 自己按优先级选（第 6 条）
- ❌ "RVV 用什么 LMUL？" → 自己按数据宽度算，参考知识库
- ❌ "找不到 `scan_result.json`，怎么办？" → 自己跑扫描（第 3 条）
- ❌ "工程里同时有 x86 和 ARM 实现，迁哪个？" → 默认两个都迁，按文件路径一一处理
- ❌ "用几个 Subagent 并行？" → 主 Agent 按工作量与依赖关系自主决策（见「动态调度决策」）

### D. 自我节奏控制

- **决策表优先于自由发挥**：遇到 A 节列出的 18 类决策，**必须**先在表里查默认值；查不到再走"自主推理"路径，但推理结果要在产出物（JSON `marking` 字段、commit message、阶段摘要）里说明依据。
- **可恢复错误一律自助**：见 A 节 #11、#12、#15、#16。
- **不可逆操作前自检**："删除/覆盖/重新扫描"等动作前先核对对象（避免误删用户未纳入迁移的源码）。

---

## 何时用本技能

- 用户要求把 x86/ARM 工程迁移到 RISC-V（含 RVV），最终要产出一个可在 RV 上跑起来的可执行程序。
- 仓库或对话里出现 `scan_result.json`、`riscv_scan`、`扫描待迁移点`、`迁移到 RISC-V`、`rv 工具链编译`。
- 用户主动查询某条 RISC-V 指令/扩展/约束（如 `vadd.vv`、`clmul`、`vclmul`、`Zbc/Zvbc` 等）。

---

## 阶段一：扫描迁移点（基础扫描 + 可选并行扫描 Subagent → 主 Agent 统一合并）

入口先**扫描**整个 x86/ARM 工程，盘点**全部**架构相关待迁移点（汇编、intrinsic、架构宏、架构分支源码等），产出 `<project_root>/scan_result.json`。

### 1.1 扫描执行

1. 在**目标工程根目录**（用户指定或当前仓库根）固定输出 `<project_root>/scan_result.json`。
2. **若 `scan_result.json` 已存在**：视为已完成，**不要**重跑脚本，直接读该文件进入阶段二分流（除非用户明确要求重新扫描——先备份或删除再扫）。
3. **若不存在**：

   **第一步：运行基础扫描**

   - 推荐（自动装依赖）：`<skill_root>/scripts/run_scan.sh <project_root> -o <project_root>/scan_result.json`
   - 兜底（手动）：`python3 -m pip install -r <skill_root>/scripts/requirements.txt && python3 <skill_root>/scripts/riscv_scan <project_root> -o <project_root>/scan_result.json`

   **第二步：评估是否需要补充并行扫描**

   主 Agent 在基础扫描完成后，根据工程规模和架构复杂度判断是否需要补充扫描 Subagent：

   - 工程文件较少或扫描器覆盖明确 → 无需 Subagent，直接进入合并步骤。
   - 存在多个架构（x86 + ARM 并存）或多个独立目录 → 可派发以下只读扫描 Subagent：
     - x86/intrinsic 扫描 Subagent；
     - ARM/NEON 扫描 Subagent；
     - 架构宏扫描 Subagent；
     - 构建与缺失目录扫描 Subagent。

   **第三步：合并与去重**

   主 Agent 合并基础扫描结果与各 Subagent 返回的候选条目，按以下键去重：

   ```text
   class_type + file_path/missing_path + start_line + end_line + solver_type
   ```

   对每个条目补充 `status="TODO"`、`marking=""`，然后**由主 Agent 统一写入最终 `scan_result.json`**。扫描 Subagent 不直接写 JSON。

4. 扫描覆盖**全部架构相关待迁移点**；类型由条目的 `solver_type` 标识（参见 [referens/project_scan.md](referens/project_scan.md)）。
5. 若 `riscv_scan` 执行失败：确认依赖已装、必要时换 Python 重试；仍失败则按 [referens/project_scan.md](referens/project_scan.md) 的 JSON schema 手工/静态分析填写 `scan_result.json`。

**阶段一进度自检**：`scan_result.json` 存在、可读；`suggestion_class` / `missing_class` 覆盖当前要处理的迁移范围；条目总数已盘点清楚。

---

## 阶段二：按迁移点分流迁移（规划 Subagent 先行分组；迁移 Subagent 必要性分析 + 迁移；汇编/RVV 条目必过 Reviewer 门禁）

主 Agent 读取 `scan_result.json` 中所有 `TODO`/`START` 条目，由迁移规划 Subagent 产出任务分组方案并经主 Agent 审核后，对可分发的任务组分派迁移 Subagent 执行。每个条目内部仍严格按 `必要性分析 → 分流 → 迁移 → 知识库 → 验证` 闭环执行。

### 2.0 任务规划与分派（阶段二入口，规划 Subagent 先行）

阶段二开始时：

```text
主 Agent 读取全部 TODO/START 条目
  → 迁移规划 Subagent（riscv-migration-planner）分析文件与依赖，产出任务分组方案
  → 主 Agent 审核方案（分组完整性、写入集合互斥）
  → 按方案将任务组分为可并行组与串行组
  → 主 Agent 将待处理条目写为 START
  → 分派迁移 Subagent
      └─ Subagent 先做必要性分析
          ├─ 误报 → 返回 NO_WORK_NEEDED + 依据
          └─ 确需迁移 → 迁移 → 查库 → 局部编译
  → 收集迁移结果
  → 主 Agent 复核误报依据，通过则以"误报"标记 DONE
  → 汇编/RVV 条目调用 Reviewer
  → 主 Agent 执行条目级 QEMU 验证
  → 通过后写 DONE + marking
```

**规划硬性条件**（主 Agent 审核时校验，不满足退回重规划）：

1. 每个条目恰好属于一个任务组；
2. 任意两个标记为 `parallel` 的任务组，其 `write_files` 交集为空；
3. 同一文件的所有条目位于同一任务组。

**依赖分组规则**（规划 Subagent 执行；主 Agent 接管时同样适用）：

下列情况必须进入同一串行任务组：
- 修改同一个源文件；
- 修改同一个头文件；
- 修改同一个构建脚本或链接脚本；
- 依赖同一个尚未确定的公共函数接口；
- 一个条目生成的文件是另一个条目的输入；
- 多个条目属于同一套架构实现并需要整体替换。

下列情况通常可以并行：
- 位于不同模块且没有公共修改文件；
- 独立的 missing_class 目录补齐；
- 不同源文件中的独立 intrinsic/asm 热点；
- 只读知识库查询和代码审查；
- 不同条目的局部测试构建不会写同一输出目录。

### 2.1 分流（条目级别——可由 Subagent 执行，主 Agent 裁决）

按 `solver_type` + 源码特征判断每个条目。满足以下**任一**即视为**汇编代码**：

1. **文件类型**：`.S` / `.s` / `.asm` 等汇编源文件。
2. **内联汇编**：源文件中含 `__asm__` / `__asm` / `asm volatile`，且迁移点落在该段内。
3. **intrinsic 热点**：含 x86 intrinsic（`_mm_*` / `_mm256_*` / `_mm512_*`）或 ARM NEON intrinsic（`vld1q_*` / `vmlaq_*` / `vst1q_*`），且是性能热点。
4. **架构强绑定 built-in**：含 `__builtin_ia32_*` / `__builtin_neon_*` 等。
5. **`solver_type` 为汇编类**：如 InlineAsm / Builtin。

- **汇编代码** → 登记后进入 **2.2 迁移 → 2.3 知识库 → 2.4 验证** 完整子闭环（含 Reviewer 门禁）。
- **非汇编代码**（源码/Shell/宏/Toml、纯 C/C++ 逻辑、标准库调用、无架构绑定）→ 走轻量子流程（仅 2.2 迁移中的代码适配 + 2.4 验证，无 Reviewer 门禁）。

登记字段：`file_path`、`start_line`/`end_line`、`asm_type`（`inline_asm`/`standalone_asm`/`intrinsic`/`builtin`）、`arch_source`（`x86`/`x86_64`/`arm`/`aarch64`）、`brief`。

### 2.2 迁移（按分流结果——可由迁移 Subagent 执行）

**前置必要性分析（必须先做）**：迁移 Subagent 动手修改任何代码前，先判断条目是否真的需要迁移。若确认为扫描误报，**不改任何代码**，直接返回 `NO_WORK_NEEDED` 并附可核验依据（文件、行号、判断理由）。判断要点：

- 条目指向的代码是否已通过条件编译天然支持 RISC-V（如已有 `__riscv` 分支）；
- 是否为死代码、注释掉的代码或不可达平台分支；
- 是否为跨平台兼容宏（如字节序、对齐处理）在 RV 上语义本就等价；
- 是否为扫描器对普通内置函数或非架构代码的误识别。

误报条目由主 Agent 复核依据后，以"误报"写入 `marking` 并置 `DONE`，不进入迁移和 QEMU 验证流程；主 Agent 对依据存疑时退回补充或另派 Subagent 二次分析，二次分析结论冲突时按"需要迁移"处理并回流。

**通用要求**：

- **测试先行**：为**原始 x86/ARM 实现**与**即将编写的 RISC-V 实现**补齐或编写可运行的单元测试（同一行为、可对比输出或 checksum）。无测试不得宣称迁移完成。
- **新增源文件命名带 `_riscv` 后缀**（与工程约定冲突时在说明里写清）。
- **算法一致、语义一致**；向量源必须用 **RVV**（汇编或 intrinsic，RVV 1.0），不得把汇编问题退化成纯 C 替代。
- **状态字段同步**：**主 Agent** 在分派任务前将该条目的 `status` 从 `TODO` 改为 `START`；Subagent 不得修改 `scan_result.json`。

**汇编代码（完整子闭环）**：

1. 读懂 x86/ARM 语义与边界；设计 RISC-V 寄存器分配、RVV `vl` / mask。
2. 编写 `*_riscv` 后缀的汇编或 intrinsic 实现。
3. **遇到指令/扩展/SEW&LMUL/intrinsic 对应/ABI 约束不确定时**：立即进入 2.3 查库，再继续编码。

**非汇编代码（轻量子流程）**：

1. **代码适配**：改架构宏/头文件/编译选项使 C/C++ 在 RISC-V 可编译——`#ifdef __x86_64__`/`__ARM_ARCH` 换 `__riscv`；移除 `<immintrin.h>`/`<arm_neon.h>` 等专有头文件；确认字节序/对齐/类型宽度在 RV64（小端、`long`=64-bit）下成立。
2. 不需要进入 2.3 知识库查询（除非出现指令/intrinsic 信号）。
3. 直接进入 2.4 验证。

### 2.2.5 Reviewer 门禁（汇编/RVV 条目必须）

汇编、inline asm、builtin、RVV intrinsic 等条目的迁移 Subagent 返回 `READY_FOR_REVIEW` 后，主 Agent **必须**召唤 `riscv-code-reviewer` 进行独立审查。Reviewer 只读审查，返回 `PASS`、`NEEDS_FIX` 或 `FAIL`：

- `PASS`：主 Agent 进入 2.4 验证。
- `NEEDS_FIX`：主 Agent 将 Reviewer 发现回流给迁移 Subagent 修复，修复后重新审查。
- `FAIL`：存在严重架构问题，主 Agent 判定是否缩小任务范围或接管修复。

非汇编轻量条目可跳过 Reviewer 门禁，由主 Agent 直接进入 2.4 验证。

### 2.3 知识库/手册查询（汇编条目必走；非汇编条目按需）

**触发条件**：迁移过程中出现任一信号：指令名/扩展名（如 `Zba`/`V`/`Zvbb`）、intrinsic（如 `__riscv_*`）、CSR 或 ABI 约束不确定时，**必须**主动查询知识库。

查询方式（二选一）：

1. **优先**：调用远端 MCP 知识库服务的对应工具（`search_core_isa_manuals` / `search_rvv_vector_extensions` / `search_special_instructions` / `search_docs_tools`）。
2. **备选（脚本方式）**：
   ```bash
   bash <skill_root>/scripts/run_query.sh -t <tool_name> -q "<查询内容>"
   ```
   其中 `<tool_name>` 为上述远端工具名；`<查询内容>` 为简洁英文自然语言（如 `"vsetvli vl LMUL SEW"`）。

**查库失败处理**：
- 降低查询具体度（补指令名 / 扩展名 / SEW / LMUL）重试，最多 2 次。
- 仍失败 → 降级为参考 [code_migrate.md](referens/code_migrate.md) 默认映射 + 在 `marking` 标注"未查到库证据、已用默认映射"。

**证据链产出**：每次查询必须输出 `file_path`（知识库源文件路径）和 `header_path`（头文件位置），作为迁移正确性的证据。

### 2.4 验证（条目级；主 Agent 必须执行最终 QEMU 验证）

每个条目迁移完成后，**主 Agent 必须执行条目级交叉编译 + QEMU 静态运行验证**。不能只凭 Subagent 自述或 `llvm-mca` 指标认定完成。

执行步骤：

1. 用 RISC-V 交叉编译器（`riscv64-unknown-linux-gnu-gcc` 等，由 `<skill_root>/resources/riscv_toolchain_env.sh` 提供）编译 `*_riscv.c` / `.S` 等源文件。
2. 在 QEMU user-static（`qemu-riscv64 -cpu max`）下执行，并与 x86/ARM 参考输出逐字节对比（`diff`）。
3. `-march` 遵循 A 节 #7："用户自定义 > `rv64gcv_zbb_zbc_zvbc_zvkb_zvksed` > 编译报错自主追加"。

**通过条件**：

- 编译零错误、输出 `diff` 一致。
- `status`：**主 Agent** 将该条目从 `START` 改为 `DONE`，并填写 `marking`：
  - 无异常：`无异常，性能持平`
  - 有异常：`语义差异：<具体点>，已通过测试规避` 或 `TODO(后续)：<具体项>`
  - 性能影响：`性能低于原实现 N%，原因：<…>，建议进入阶段四 llvm-mca 优化`

**状态写入唯一路径**：

```text
主 Agent：TODO → START         （分派任务前）
规划 Subagent：返回 PLAN_READY + 分组方案（主 Agent 审核后才分派）
迁移 Subagent：返回 NO_WORK_NEEDED / READY_FOR_REVIEW / READY_FOR_VERIFY
Reviewer：返回 PASS / NEEDS_FIX / FAIL（汇编/RVV 条目）
主 Agent：复核误报依据，或运行 QEMU 验证
主 Agent：START → DONE + marking（验证通过后；误报条目 marking 记为"误报 + 依据"）
```

**阶段二进度自检**：所有条目 `status=DONE`；每个 `DONE` 条目 `marking` 非空；每个条目动手前完成必要性分析（误报条目有可核验依据并经主 Agent 复核）；汇编条目经过 2.3 知识库证据 + 2.2.5 Reviewer 门禁；非汇编条目完成架构宏替换并通过主 Agent QEMU 验证。

---

## 阶段三：工程级编译修复（并行诊断编译错误 + 主 Agent 或单个写入型 Subagent 串行修复）

阶段二所有条目 DONE 后，**用 RISC-V 交叉工具链编译整个工程**，修复编译错误直至产出可运行的 RISC-V 可执行程序。

### 3.1 准备环境

- 由技能自动加载 RISC-V 交叉工具链：`<skill_root>/resources/riscv_toolchain_env.sh` + `source <skill_root>/resources/env.sh`。
- 同时准备 QEMU user-static：`<skill_root>/resources/qemu_static_env.sh`。

### 3.2 编译与修复（并行诊断 → 串行修复）

目标流程：

```text
主 Agent 完整编译并保存完整日志
  → 按模块和类型对错误切片
  → 一个或多个诊断 Subagent 并行分析
  → 主 Agent 汇总根因和依赖顺序
  → 主 Agent 或单个写入型 Subagent 实施一组修复
  → 主 Agent 重新完整编译
  → 重复直到成功
```

**关键约束**：

1. 并行只用于错误分析（诊断 Subagent 只读），不默认用于同时修改工程。
2. 在目标工程里用交叉工具链跑完整构建（如 `make` / `cmake --build`），前缀以 `*_TOOLCHAIN_ROOT/bin` 下实际文件为准（如 `riscv64-unknown-linux-gnu-gcc`）。
3. **修编译错误**：
   - 每轮只根据首个根因或一组互不冲突的根因实施修复。
   - 重新编译必须由主 Agent 统一执行，保证日志和环境一致。
   - 如果发现阶段二遗漏，必须回流对应条目，不在阶段三绕过状态闭环。
   - 缺什么装什么（系统包 + `<skill_root>/resources/*.sh`）。
   - `__x86_64__` / `__ARM_ARCH` 等宏残留未替换的，回阶段二补。
   - 头文件路径、链接库路径、ABI（`lp64d`）等，按工程实际调整。
4. **`-march` 覆盖所用扩展**；常见起点：`-march=rv64gcv_zbb_zbc_zvbc_zvkb_zvksed -mabi=lp64d`，不够再补。
5. 若工程要求静态链接，遵守之。
6. 若任务固定产物名（如 `riscv64_test`），遵守任务说明。
7. 现有"连续 3 次同类型错误才询问用户"的规则保持不变。

### 3.2.5 编译错误诊断 Subagent（按需）

主 Agent 在编译失败后，可召唤 `riscv-build-diagnoser` 对完整构建日志做只读分析。诊断 Subagent 将错误按根因聚类，识别首个根因与派生错误，判断是环境问题、构建参数问题、ABI/链接问题还是阶段二遗漏，并给出最小修复建议和回流标注（应在阶段三直接修复还是回流阶段二）。

### 3.3 运行验证

- 用 `qemu-riscv64 -cpu max <bin>` 跑主流程/集成测试，至少确认主程序可启动、无立即崩溃、关键路径输出与 x86/ARM 侧一致。
- 与阶段二条目级 QEMU 验证互补：条目级验证保证**单个迁移点**正确；工程级验证保证**整体**可运行。

**阶段三进度自检**：工程完整构建无错误；RV 可执行程序可在 QEMU 中跑通主流程。

---

## 阶段四（独立 agent）：性能分析与优化（多热点可并行分析；主 Agent 统一实施、验收与回写）

> **本阶段为独立 agent**，由 agent 在阶段三全部通过后**主动召唤**；不在阶段二内嵌运行。

### 4.1 触发条件

- 阶段一/二/三全部完成。
- 存在**手写汇编 / RVV 汇编 / intrinsic 热点**循环，且阶段二已 DONE。

### 4.2 工作流程（llvm-mca 静态分析 + 迭代优化；多热点可并行）

目标流程：

```text
主 Agent 枚举热点
  → 每个独立热点分配一个 riscv-asm-analyzer
  → 并行输出基线和优化建议
  → 主 Agent 选择并实施优化
  → riscv-code-reviewer 复核
  → 主 Agent 运行 QEMU 回归
  → 主 Agent 统一更新 marking
```

1. **提取热点汇编**（`hot.s`）：
   - 手写 `.S` 文件可直接作为输入。
   - C/intrinsic：`<RISCV_CLANG> -O3 -S ... -o - hot.c` 管道到 `llvm-mca`。
   - flags 必须与工程 `-march`/`-mabi`/`-mcmodel` 完全一致。
2. **执行 `llvm-mca`**（如本机无 `llvm-mca`，**agent 自主调用 `bash <skill_root>/resources/llvm_mca_env.sh` 安装**；详见 `agents/riscv-asm-analyzer.md` 的「llvm-mca 自动安装」一节）：
   - 快速分析：`llvm-mca -mtriple=riscv64 -mcpu=<cpu> -mattr=+v,+zvbc --all-stats --iterations=100 < hot.s`
   - 深度分析：`llvm-mca ... --bottleneck-analysis --all-views --timeline ...`
3. **`-mcpu` 选择优先级**：用户 prompt 中明确指定 > `zhufeng2`（默认） > 目标部署芯片匹配 > `sifive-p450`（乱序通用基线）/ `sifive-u74`（顺序通用基线）。**不要用 `generic`/`generic-rv64`**（无调度模型会导致报错）。
4. **多热点并行**：每个 Analyzer 绑定唯一热点函数或代码区间，互不依赖的热点可并行分析。但 Analyzer 不直接修改 `scan_result.json.marking`；若需要修改源码，必须获得独占文件范围。
5. **每轮优化后必须回到阶段二的 2.4 验证**（同一组测试、输出一致），禁止只追性能导致语义回归。
6. 优化终止条件：IPC 接近 Dispatch Width、`Block RThroughput` 不再显著下降、出现 `No resource or data dependency bottlenecks`。

### 4.3 闭环产出

- 每轮保留 `llvm-mca` 输出关键摘要（吞吐、周期、瓶颈）。
- 每轮修改后回到 2.4 验证（构建 + QEMU/对比测试）。
- Reviewer 被纳入优化后的标准门禁：优化代码完成后调用 `riscv-code-reviewer` 复核。
- Analyzer 只返回结构化性能基线、瓶颈、建议和建议 marking；**最终 `marking` 由主 Agent 在回归验证后统一写入**。
- 最终产出：优化后的 `*_riscv` 源文件 + 性能对比要点。

详细参数、注释格式警告（`.text` 段内**禁止**行尾 `/* */`）、结果解读与常见优化方向见 [referens/code_migrate.md](referens/code_migrate.md)。

---

## 主流程状态机

```text
resolve_project_root
  ↓
PHASE_1_SCAN
  ├─ direct_scan (run_scan.sh)
  └─ optional_parallel_scan_workers
  ↓
merge_and_write_scan_result (主 Agent)
  ↓
PHASE_2_MIGRATE
  ↓
migration planner: build task groups (write_files 互斥, riscv-migration-planner)
  ↓
主 Agent: review plan (三条硬性条件)
  ↓
for each runnable group:
  主 Agent: TODO → START
  dispatch migration worker(s)
  worker: 必要性分析
    ├─ false positive → NO_WORK_NEEDED → 主 Agent 复核 → DONE (marking=误报)
    └─ need migration → collect results
  review if required (riscv-code-reviewer)
  main-agent QEMU verify
  主 Agent: START → DONE + marking
  ↓
all entries DONE?
  ├─ no → continue phase 2
  └─ yes
       ↓
PHASE_3_BUILD
  ↓
full build (主 Agent)
  ├─ failed → parallel diagnose (build-diagnoser) → serial fix → rebuild
  └─ passed
       ↓
full QEMU/integration verify (主 Agent)
  ↓
PHASE_4_ANALYZE_IF_TRIGGERED
  ↓
parallel hotspot analysis (riscv-asm-analyzer x N)
  ↓
serial/isolated optimization merge
  ↓
review (riscv-code-reviewer) + QEMU regression (主 Agent)
  ↓
主 Agent updates marking
  ↓
final summary
```

---

## 迁移点状态字段规范（速查）

迁移进度由 `scan_result.json` 中每个条目的 `status` 与 `marking` 两个结构化字段维护，**权威源在 JSON，不在源码**。三态**互斥**（同一时刻只能有一个）；`status=DONE` 时 `marking` 必填。**只有主 Agent 可以写入 `scan_result.json`**。

```jsonc
{
  "suggestion_class": [
    {
      "file_path": "/abs/path/src/crc/crc32.c",
      "start_line": 120,
      "end_line": 200,
      "solver_type": "InlineAsm",
      "status": "TODO",        // ← 阶段一扫描后默认（主 Agent 写入）
      "marking": ""            // ← TODO 时为空
    }
  ]
}
```

**主 Agent** 分派阶段二任务前：

```jsonc
{ "status": "START", "marking": "" }
```

**主 Agent** QEMU 验证通过后：

```jsonc
{
  "status": "DONE",
  "marking": "无异常，性能与 SSE 实现持平；建议进入阶段四用 llvm-mca 确认吞吐"
}
```

| `status` | 含义 | 何时设置 | 谁设置 | `marking` 是否必填 |
| ------- | ---- | -------- | ------ | ----------------- |
| `TODO` | 未处理 | 阶段一扫描产出（默认值） | 主 Agent | 否 |
| `START` | 开始迁移 | 阶段二分派任务前 | 主 Agent | 否 |
| `DONE` | 迁移完成 | 主 Agent QEMU 验证通过后 | 主 Agent | **是** |

`marking` 常见内容：

- 无异常：`无异常，性能持平`
- 语义差异：`语义差异：<具体点>，已通过测试规避`
- 性能下降：`性能低于原实现 N%，原因：<…>，建议进入阶段四 llvm-mca 优化`
- 阶段四优化后：`经阶段四 llvm-mca 优化后 IPC 由 <x> 提升到 <y>（<cpu>），关键改动：<具体点>`（**由主 Agent 在回归验证后写入**）
- Reviewer 中危/建议发现：`TODO(后续)：<具体项>`（**由主 Agent 决定是否写入**）
- 需要后续修复：`TODO(后续)：<具体项>`

> **不要**在源码里写 `// [MIGRATE-*]` 注释——所有状态信息以 `scan_result.json` 为唯一权威源。

---

## 附加资源（按需阅读）

| 文件 | 内容 |
| ---- | ---- |
| [referens/project_scan.md](referens/project_scan.md) | `scan_result.json` 格式、`riscv_scan` 行为、扫描项类型 |
| [referens/code_migrate.md](referens/code_migrate.md) | 迁移三步、编译/测试/工具链约定、**阶段四（llvm-mca）** 详述 |
| [referens/subagent-orchestration-design.md](referens/subagent-orchestration-design.md) | 主 Agent 编排 + Subagent 调度方案完整设计 |

## 脚本

- `scripts/riscv_scan`：扫描引擎入口（输出 `scan_result.json`，schema 见 [referens/project_scan.md](referens/project_scan.md)）。
- `scripts/query.py`：通过 RISC-V-DOC-RAG MCP 知识库服务查询 ISA/Intrinsic 手册（输出包含 `file_path/header_path`）。
- `scripts/run_scan.sh`：扫描入口（自动安装依赖）。
- `scripts/run_query.sh`：查询入口（自动安装依赖）。
- `scripts/prepare_verify_env.sh`：准备验证环境（从 `resources/` 部署/配置 RISC-V 工具链与 QEMU user-static，尽量不联网下载）。
- `resources/llvm_mca_env.sh`：本机无 `llvm-mca` 时拉取并解压 llvm-mca 工具包，写入 `PATH`/`env.d`；阶段四性能分析前按需执行。

## Subagent 清单

| Subagent | 角色 | 读写属性 | 触发阶段 |
| -------- | ---- | -------- | -------- |
| `riscv-scan-worker` | 按目录/扫描维度返回候选迁移点 | 只读 | 阶段一（补充扫描） |
| `riscv-migration-planner` | 读取全部 TODO/START 条目，产出互不重叠的任务分组方案（含 `write_files`、执行方式、工作量估计） | 只读 | 阶段二（入口规划，分派迁移前必须） |
| `riscv-migration-worker` | 执行分配迁移条目（必要性分析 → 分流、迁移、查库） | 写入（指定文件范围内） | 阶段二 |
| `riscv-code-reviewer` | 审查汇编/RVV 迁移代码的向量化正确性、ABI、对齐、指令选择 | 只读 | 阶段二（汇编/RVV 条目门禁）、阶段四（优化复核） |
| `riscv-build-diagnoser` | 分析工程级完整构建日志，聚类根因 | 只读 | 阶段三（编译失败诊断） |
| `riscv-asm-analyzer` | 对热点汇编做 llvm-mca 静态分析，输出吞吐瓶颈与优化建议 | 可读写（热点级绑定，不写 JSON） | 阶段四 |
