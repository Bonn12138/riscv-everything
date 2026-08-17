---
description: 按四大阶段推进 RISC-V（含 RVV）迁移。采用「主 Agent 编排 + 阶段内动态 Subagent」架构：主 Agent 是唯一编排者与状态提交者，Subagent 承担阶段内工作单元。阶段一扫描、阶段二由规划 Subagent 先产出互不重叠任务分组、迁移 Subagent 先做必要性分析再按分流迁移（独立组可并行）、阶段三工程级交叉编译（并行诊断、串行修复）；性能分析（llvm-mca）作为独立 Subagent 仅在迁移完成后启动。迁移点状态以 scan_result.json 的 status/marking 字段维护，只有主 Agent 可以写入。
argument-hint: [scan_result.json]
---

# /everything-riscv:migrate — 启动迁移流程

> **零交互自主运行**：本命令执行期间**默认不向用户提问**。所有决策（目标工程根目录、是否重扫、汇编 vs intrinsic、`-mcpu`/`-march`、测试补全、是否进入阶段四、Subagent 数量、错误自修复等）由 agent 按 [`skills/riscv-migrate/SKILL.md`](../skills/riscv-migrate/SKILL.md)「自主运行原则」默认处理；只有白名单 5 类场景（找不到工程根 / 条目级 5 轮不一致 / 编译连错 3 次 / 内网不通 / 用户主动问）才请示。Subagent 不得直接向用户提问。**禁止**在 `-mcpu`、`-march`、`llvm-mca` 是否安装、汇编 vs intrinsic、用几个 Subagent 等典型决策上向用户请示。

按**四大阶段**推进 RISC-V 迁移，采用「主 Agent 编排 + 阶段内动态 Subagent」架构：

| 阶段 | 名称 | 产出 | 执行模式 |
| ---- | ---- | ---- | -------- |
| **一** | 扫描迁移点 | `scan_result.json`（每条目 `status="TODO"`） | 基础扫描器 + 可选并行扫描 Subagent → 主 Agent 合并去重 |
| **二** | 按迁移点分流迁移 | `*_riscv` 源码 + 通过 QEMU 验证；条目 `status="DONE"` + `marking` | 规划 Subagent 先产出任务分组 → 迁移 Subagent 必要性分析 + 迁移（独立组可并行）；汇编/RVV 条目必过 Reviewer 门禁 |
| **三** | 工程级交叉编译 | RV 可执行程序（QEMU 可运行） | 并行诊断编译错误 + 主 Agent 或单个写入型 Subagent 串行修复 |
| **四**（独立 Subagent） | 性能分析与优化 | llvm-mca 报告 + 优化提交；主 Agent 更新条目 `marking` | 多个独立热点可并行分析；主 Agent 统一实施、验收与回写 |

每个迁移点的进度以 `scan_result.json` 中的结构化字段同步（**权威源在 JSON，不在源码注释；只有主 Agent 可以写入**）：

- `status`（三态互斥）：`TODO` / `START` / `DONE`
- `marking`（备注/异常/性能说明）：`status=DONE` 时必填

阶段二全部条目 `status=DONE` 后才能进入阶段三；阶段三产物可运行后才召唤阶段四性能分析 Subagent。

## 调度规则（主 Agent 每阶段入口必评估）

主 Agent 在每个阶段开始时**必须**：

1. **评估工作量**：条目数量、文件范围、依赖关系。
2. **阶段二必须先规划**：召唤 `riscv-migration-planner` 产出互不重叠的任务分组方案（同文件条目强制同组、可并行组 `write_files` 互斥），主 Agent 按硬性条件审核后才分派迁移 Subagent；不允许在没有分组方案的情况下直接并行分派。
3. **选择调度策略**：根据工作量选择零个、一个或多个 Subagent（见 SKILL.md「动态调度决策」）。
4. **避免同文件并行写入**：并行任务的 `allowed_write_paths` 不得有交集（以规划方案的 `write_files` 为准）。
5. **汇总后再推进阶段**：所有 Subagent 结果收集、合并、验证完成后，才推进到下一阶段或下一轮。

## 前置条件

- 已运行 `/everything-riscv:scan` 产出 `scan_result.json`
- 或手动构造合法的 `scan_result.json`（每条目带 `status` 与 `marking` 字段）

## 执行流程

### 阶段一：扫描迁移点（若尚未执行）

如未检测到 `scan_result.json`，先执行扫描（基础扫描 + 可选并行扫描 Subagent，主 Agent 合并去重）。

### 阶段二：按迁移点分流迁移（核心闭环，规划先行 + 必要性分析 + 独立组可并行）

主 Agent 加载 `scan_result.json`，执行：

1. **任务规划**：召唤 `riscv-migration-planner` 读取全部 `TODO`/`START` 条目，产出互不重叠的任务分组方案（每个任务组显式列出 `write_files`）。主 Agent 审核三条硬性条件：每个条目恰好一组、可并行组写入集合互斥、同文件条目同组。不满足退回重规划。
2. **分派任务**：按审核通过的方案，主 Agent 将待处理条目写为 `START`，为每个任务组分配一个 `riscv-migration-worker` Subagent（标记 `parallel` 的组可并行分派）。
3. **逐条目闭环**（每个 Subagent 内部）：
   - **必要性分析（最先做）**：判断条目是否真需迁移；确认误报直接返回 `NO_WORK_NEEDED` + 可核验依据，不改任何代码。主 Agent 复核依据后以"误报"关闭条目。
   - **分流**：按 `solver_type` + 源码特征判断汇编 vs 非汇编。
   - **迁移**：编写 `*_riscv` 实现 + 对比测试；局部编译验证。
   - **查知识库**（汇编条目必走）。
   - 汇编/RVV 条目迁移完成后返回 `READY_FOR_REVIEW`。
4. **Reviewer 门禁**（汇编/RVV 条目）：主 Agent 召唤 `riscv-code-reviewer` 独立审查，返回 `PASS` / `NEEDS_FIX` / `FAIL`。
5. **主 Agent 最终 QEMU 验证**：审查通过后，主 Agent 运行条目级 QEMU 对比验证（误报条目跳过）。
6. **主 Agent 更新 JSON**：验证通过后，主 Agent 将条目从 `START` 改为 `DONE` 并填写 `marking`（误报条目 marking 记为"误报 + 依据"）。

### 阶段三：工程级交叉编译（并行诊断 → 串行修复）

阶段二所有条目 `status="DONE"` 后：

1. 主 Agent 完整编译并保存日志。
2. 编译失败时，主 Agent 召唤 `riscv-build-diagnoser`（可多个并行）分析错误日志。
3. 主 Agent 汇总根因和修复顺序，由主 Agent 或单个写入型 Subagent 实施修复。
4. 主 Agent 重新完整编译，重复直到成功。
5. 用 `qemu-riscv64 -cpu max <bin>` 跑主流程/集成测试。

### 阶段四（独立 Subagent）：性能分析与优化

阶段三产物可运行后，主 Agent **主动召唤** `riscv-asm-analyzer`：

- 多个独立热点可分配给多个 Analyzer 并行分析。
- 每个 Analyzer 绑定唯一热点函数/区间，返回结构化分析结果和建议 marking。
- 主 Agent 选择并实施优化，召唤 `riscv-code-reviewer` 复核。
- 主 Agent 运行 QEMU 回归验证后**统一更新 marking**。

## 状态写入规则（关键）

```text
只有主 Agent 可以写入 scan_result.json：
  主 Agent: TODO → START         （分派任务前）
  规划 Subagent: 返回 PLAN_READY + 分组方案（主 Agent 审核后才分派）
  迁移 Subagent: 返回 NO_WORK_NEEDED / READY_FOR_REVIEW / READY_FOR_VERIFY
  Reviewer: 返回 PASS / NEEDS_FIX / FAIL
  主 Agent: 复核误报依据，或运行 QEMU 验证
  主 Agent: START → DONE + marking（验证通过后；误报条目 marking 记为"误报 + 依据"）
```

## 汇总报告

完成条目数（`status=DONE`）、误报关闭条目数、跳过条目数、需人工确认条目数、阶段三产物路径、阶段四是否触发及触发结果、各阶段 Subagent 调度统计（Subagent 数量、任务范围、通过/回流结果、规划方案退回重规划次数）。

## 约束

- 禁止猜测指令 / 扩展对应关系，必须查知识库
- 每次改动后主动推进验证步骤
- 每个迁移点的状态以 `scan_result.json` 中 `status` / `marking` 字段为唯一权威源；源码不写 `MIGRATE-*` 注释
- **只有主 Agent 可以写入 `scan_result.json`**
- 不依赖虚拟环境，使用系统 Python + 脚本自举依赖
- 阶段二全部 `status=DONE` 后才进入阶段三；阶段三通过后才进入阶段四
- 性能分析不在阶段二内嵌，作为独立 Subagent 在阶段三后召唤
- Subagent 不得直接向用户提问
