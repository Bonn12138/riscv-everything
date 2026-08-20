# RISC-V 迁移技能 Subagent 调度方案设计

## 1. 文档目的

本文档设计 `riscv-migrate` 技能的阶段内 Subagent 调度方案，重点说明它与当前方案的差别、调整后的职责边界、各阶段的调度方式、并发安全机制和落地范围。

本方案不改变现有迁移目标和阶段门禁：

1. 阶段一产出 `scan_result.json`；
2. 阶段二完成全部迁移点的条目级迁移与验证；
3. 阶段三完成工程级交叉编译与运行验证；
4. 阶段四只在前三阶段通过后开展性能分析与优化；
5. `scan_result.json` 中的 `status` / `marking` 仍是迁移状态的唯一权威源。

本方案主要改变的是**阶段内部由谁执行工作、如何拆分任务，以及多个 Subagent 的结果如何汇总**。

---

## 2. 当前方案概述

当前方案采用“主 Agent 直接执行为主，特定节点调用专业 Agent”的模式：

```text
主 Agent
  ├─ 阶段一：直接运行扫描并检查 scan_result.json
  ├─ 阶段二：逐条目串行完成分流、迁移、查库、验证
  │            └─ 可按需调用 riscv-code-reviewer
  ├─ 阶段三：直接进行完整编译、诊断和修复
  └─ 阶段四：调用 riscv-asm-analyzer
               └─ 优化后可调用 riscv-code-reviewer
```

当前方案的主要特点如下：

- 主 Agent 同时承担流程控制、代码修改、知识查询、验证和状态维护；
- 阶段之间严格串行；
- 阶段二各条目明确要求串行处理；
- 已定义 `riscv-code-reviewer` 和 `riscv-asm-analyzer` 两个专业 Agent；
- 专业 Agent 的触发点已有说明，但尚未形成统一的阶段内调度协议；
- 没有统一规定 Subagent 的任务输入、结构化返回、并发上限和写入冲突处理；
- 阶段二的任务拆分与分配完全由主 Agent 自行完成，没有专职规划角色；
- 迁移点没有统一的必要性分析步骤，扫描器误报会直接进入迁移流程；
- `riscv-asm-analyzer` 当前可以直接更新 `scan_result.json.marking`，`riscv-code-reviewer` 当前也允许直接回写 JSON，存在多个 Agent 写同一状态文件的可能。

当前方案适合条目较少、修改集中、由一个 Agent 连续完成的工程。面对大工程或多个互不依赖的迁移点时，主 Agent 容易成为串行瓶颈。

---

## 3. 目标方案概述

目标方案采用“主 Agent 编排 + 阶段内动态 Subagent”的模式：

```text
主 Agent（唯一编排者和状态提交者）
  │
  ├─ 阶段一：一个或多个只读扫描 Subagent
  │            └─ 主 Agent 合并、去重并生成 scan_result.json
  │
  ├─ 阶段二：迁移规划 Subagent 制定分配方案
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

目标方案中的主 Agent 不再默认亲自完成全部工作，而是负责：

- 判断阶段是否具备启动条件；
- 收集、审查和合并 Subagent 结果；
- 执行最终验证；
- 独占维护 `scan_result.json`；
- 决定是否进入下一阶段。

阶段二的任务拆分与分派由迁移规划 Subagent（见 6.3）专职完成：主 Agent 只提供条目全集和工程上下文，规划 Subagent 输出互不重叠的任务分组方案，由主 Agent 审核后按方案分派，从源头保证同一个文件不会被多个迁移 Subagent 同时操作。

Subagent 只完成被分配的工作单元，不拥有阶段推进权和最终完成认定权。

---

## 4. 当前方案与目标方案的核心差别

| 维度 | 当前方案 | 目标方案 |
| ---- | -------- | -------- |
| 总体架构 | 主 Agent 执行为主，专业 Agent 辅助 | 主 Agent 编排，Subagent 承担阶段内工作单元 |
| 阶段关系 | 阶段一、二、三串行，阶段四独立 | 阶段关系保持不变，只增加阶段内并行能力 |
| 阶段一 | 一个扫描流程直接生成最终 JSON | 多维扫描可并行，Subagent 只交候选结果，主 Agent 统一生成 JSON |
| 阶段二 | 所有条目整体串行 | 迁移规划 Subagent 统一规划分配，有依赖的条目串行，无依赖的条目可并行 |
| 迁移点必要性 | 扫描结果默认全部视为待迁移 | 迁移 Subagent 先做必要性分析，确认误报后直接返回 `NO_WORK_NEEDED` |
| 任务分配 | 主 Agent 逐条目即兴分派 | 规划 Subagent 预先产出互不重叠的任务分组，同文件条目强制同组串行 |
| 阶段三 | 主 Agent边编译边修复 | 可并行分析错误，但写入和重新编译仍串行 |
| 阶段四 | 单个性能分析 Agent 处理热点 | 多个独立热点可由多个分析 Agent 并行处理 |
| 状态文件写入 | 主 Agent、Reviewer、Analyzer 都可能写 JSON | 只有主 Agent可以写 `scan_result.json` |
| 完成判定 | 执行 Agent 可能完成修改并同步状态 | Subagent 只能报告“待审查/待验证”，主 Agent 验证后才能写 `DONE` |
| 调度粒度 | 阶段或单条迁移点 | 阶段、目录、模块、文件、条目、热点函数 |
| 并发策略 | 没有统一规则 | 先构建依赖分组，再按只读/写入属性决定并行方式 |
| 输入协议 | 由调用时自然语言约定 | 使用统一任务包，包含阶段、范围、文件、条目和验收条件 |
| 输出协议 | 各 Agent 各自定义输出 | 使用统一结构化返回协议 |
| 失败处理 | 主 Agent 直接诊断和重试 | 主 Agent 根据错误类型重试、缩小范围、更换 Agent 或接管 |
| 用户交互 | 五类白名单场景可询问 | 保持原白名单，不因 Subagent 失败增加新的询问场景 |

最关键的变化是：

> 当前方案把“执行结果”和“状态完成”绑定在同一个 Agent 上；目标方案将两者拆开，Subagent 负责执行，主 Agent 负责验收和状态提交。

---

## 5. 不变项与设计边界

引入 Subagent 后，下列约束保持不变：

### 5.1 阶段门禁不变

```text
阶段一完成
  → 才能进入阶段二
阶段二所有条目 DONE
  → 才能进入阶段三
阶段三产物构建并运行成功
  → 才能进入阶段四
```

阶段内可以并行，不代表阶段之间可以越级并行。例如，阶段二尚未完成时，不允许为了节省时间提前启动阶段四。

### 5.2 JSON 三态不变

`scan_result.json` 仍只允许：

- `TODO`
- `START`
- `DONE`

Subagent 内部的 `READY_FOR_REVIEW`、`READY_FOR_VERIFY`、`BLOCKED` 等状态只用于任务返回，不写入 `scan_result.json`。

### 5.3 验证规则不变

- 每个迁移点仍需条目级编译和 QEMU 对比；
- 阶段三仍需完整工程交叉编译和主流程运行验证；
- 阶段四每轮优化后仍需回到阶段二 2.4 的验证闭环；
- 禁止只凭 Subagent 自述或 `llvm-mca` 指标认定完成。

### 5.4 零交互原则不变

Subagent 缺少信息时，先将问题返回主 Agent。主 Agent 按现有自主决策表处理。只有当前方案规定的五类白名单场景才能询问用户。

Subagent 不得直接向用户提问。

---

## 6. 角色与职责设计

### 6.1 主 Agent

主 Agent 是唯一的阶段控制器、集成者和状态提交者。

职责：

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

主 Agent 不得把“Subagent 返回成功”直接等同于“条目 DONE”。

主 Agent 在阶段二不再亲自做任务拆分和分配。若规划 Subagent 不可用或方案不可用，主 Agent 可按第 7 节的调度规则自行接管规划职责。

### 6.2 扫描 Subagent

职责：

- 扫描指定目录或指定迁移点类型；
- 返回候选条目和扫描警告；
- 不修改源码；
- 不生成或修改最终 `scan_result.json`。

适合的扫描维度：

- x86/x86_64 汇编与 intrinsic；
- ARM/AArch64 汇编与 NEON intrinsic；
- 架构宏和条件编译；
- 构建脚本、编译参数和缺失架构目录。

### 6.3 迁移规划 Subagent

建议新增 `riscv-migration-planner`，专职负责阶段二的任务规划与分配，是“同一文件不被多个 Subagent 同时操作”的第一道保障。

职责：

- 读取全部 `TODO` / `START` 条目；
- 分析条目涉及的源文件、头文件、构建脚本和共享接口；
- 按第 8.2 节的依赖分组规则构建任务组；
- 保证任意两个可并行任务组的写入集合互不相交（同文件条目强制进同一串行组）；
- 为每个任务组估算工作量、难度和推荐执行方式（并行 / 串行 / 主 Agent 直接处理）；
- 输出结构化分组方案，交主 Agent 审核。

限制：

- 只读：不修改源码、构建文件和 `scan_result.json`；
- 只规划：不亲自执行任何迁移；
- 方案中每个任务组必须显式列出 `write_files`（写入文件集合），供主 Agent 做互斥校验。

规划产出不满足以下硬性条件时，主 Agent 必须退回重规划：

1. 每个条目恰好属于一个任务组；
2. 任意两个标记为可并行的任务组，其 `write_files` 交集为空；
3. 同一文件内的所有条目位于同一任务组。

### 6.4 迁移 Subagent

职责：

- 只处理分配的条目或模块；
- **前置必要性分析**：动手前先判断条目是否真的需要迁移，若确认为扫描误报，不改任何代码，直接返回 `NO_WORK_NEEDED` 并附依据；
- 阅读原始实现和关联测试；
- 编写 RISC-V/RVV 实现；
- 补齐测试；
- 查询知识库并保留证据链；
- 执行局部编译和测试；
- 返回修改和验证信息。

必要性分析的判断要点：

- 条目指向的代码是否已通过条件编译天然支持 RISC-V（如已有 `__riscv` 分支）；
- 是否为死代码、注释掉的代码或不可达平台分支；
- 是否为跨平台兼容宏（如字节序、对齐处理）在 RV 上语义本就等价；
- 是否为扫描器对普通内置函数或非架构代码的误识别。

误报处理：

- 返回 `NO_WORK_NEEDED` 时必须给出可核验依据（文件、行号、判断理由）；
- 主 Agent 复核依据后，可将条目以“误报”写入 `marking` 并置 `DONE`，不进入迁移和 QEMU 验证流程；
- 主 Agent 对依据存疑时，退回该 Subagent 或另派 Subagent 二次分析，不得直接采信。

限制：

- 不修改 `scan_result.json`；
- 不处理未分配条目；
- 不擅自扩大到其他模块；
- 不宣称整个阶段完成；
- 必要性分析未完成前不得开始任何代码修改。

### 6.5 `riscv-code-reviewer`

目标方案中将其从“可选辅助审查”提升为“汇编/RVV 条目的标准质量门禁”。

调用时机：

1. 汇编、inline asm、builtin、RVV intrinsic 条目完成迁移后；
2. QEMU 验证通过、主 Agent 写 `DONE` 前；
3. 阶段四优化代码完成后。

目标调整：

- 默认只读审查；
- 返回 `PASS`、`NEEDS_FIX` 或 `FAIL`；
- 不直接修改 `scan_result.json`；
- 高危发现回流迁移 Subagent；
- 中低风险发现由主 Agent决定是否写入 `marking`。

### 6.6 编译诊断 Subagent

建议新增 `riscv-build-diagnoser`，只分析完整构建日志。

职责：

- 将错误按根因聚类；
- 识别首个根因与派生错误；
- 判断是环境问题、构建参数问题、ABI/链接问题还是阶段二遗漏；
- 给出最小修复建议；
- 标注应在阶段三直接修复还是回流阶段二。

默认不修改源码和构建文件。

### 6.7 `riscv-asm-analyzer`

目标方案中支持一个或多个 Analyzer 分析互不依赖的热点。

职责保留：

- 提取热点汇编；
- 执行 `llvm-mca`；
- 分析吞吐、依赖和资源压力；
- 提出优化建议；
- 提供优化前后指标。

目标调整：

- 不再直接修改 `scan_result.json.marking`；
- 若需要修改源码，必须获得独占文件范围；
- 每个 Analyzer 绑定唯一热点函数或代码区间；
- 最终 `marking` 由主 Agent 在回归验证后统一写入。

---

## 7. 动态调度决策

目标不是强制每个阶段都调用多个 Subagent，而是根据实际工作量选择：

```text
零个 Subagent：主 Agent 直接完成
一个 Subagent：委托一个明确工作单元
多个 Subagent：处理互不依赖且收益明显的工作流
```

阶段二是例外：任务拆分与分配默认必须先经过迁移规划 Subagent（或主 Agent 按 6.3 同样的硬性条件接管规划），不允许在没有分组方案的情况下直接并行分派迁移 Subagent。

### 7.1 判断条件

主 Agent（阶段二由规划 Subagent）每次分派前检查：

1. 是否存在两个以上可独立完成的任务；
2. 任务是否修改相同文件；
3. 任务是否依赖同一个尚未确定的公共接口；
4. 是否需要共享完整上下文；
5. 并行节省是否大于上下文重建和结果合并成本；
6. 是否存在匹配的专业 Agent；
7. 当前执行环境是否支持隔离的写入工作区。

### 7.2 推荐调度规则

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

### 7.3 并发上限

建议使用保守默认值：

- 阶段一只读扫描：最多 4 个并行 Subagent；
- 阶段二迁移规划：同一时刻最多 1 个规划 Subagent，产出全量分组方案；
- 阶段二写入型迁移：最多 4 个并行 Subagent，且必须依据规划方案的分组，保证写入集合互斥或使用隔离工作区；
- 阶段二审查：可与其他互不相关条目的迁移并行；
- 阶段三只读诊断：最多 3 个并行 Subagent；
- 阶段三写入修复：同一时刻最多 1 个；
- 阶段四热点分析：最多 4 个并行 Analyzer；
- 用户明确要求不同并发度时，由主 Agent 在安全边界内覆盖默认值。

---

## 8. 各阶段流程差异

## 8.1 阶段一：从单扫描器输出改为可并行扫描与统一合并

### 当前流程

```text
主 Agent
  → 运行 run_scan.sh
  → 失败时静态补录
  → 检查最终 scan_result.json
```

### 目标流程

```text
主 Agent
  → 先运行现有扫描器获得基础结果
  → 判断是否需要补充并行扫描
      ├─ x86/intrinsic 扫描 Subagent
      ├─ ARM/NEON 扫描 Subagent
      ├─ 架构宏扫描 Subagent
      └─ 构建与缺失目录扫描 Subagent
  → 合并候选条目
  → 去重和冲突裁决
  → 补充 status="TODO"、marking=""
  → 主 Agent 写最终 scan_result.json
```

### 差别说明

1. 现有 `run_scan.sh` 不被替换，仍作为基础扫描入口；
2. Subagent 用于大工程的补充覆盖和交叉检查；
3. 扫描 Subagent 不直接写 JSON，避免并发覆盖；
4. 主 Agent 统一按 schema 生成最终结果；
5. 去重键建议采用：

```text
class_type + file_path/missing_path + start_line + end_line + solver_type
```

### 何时不使用 Subagent

- 工程文件较少；
- 扫描器执行成功且覆盖明确；
- 不存在多个架构或多个独立目录；
- 启动 Subagent 的成本明显高于补充扫描收益。

---

## 8.2 阶段二：从全部条目串行改为依赖分组后的受控并行

### 当前流程

```text
条目 1：分流 → 迁移 → 查库 → 验证 → DONE
条目 2：分流 → 迁移 → 查库 → 验证 → DONE
条目 3：分流 → 迁移 → 查库 → 验证 → DONE
```

当前设计明确要求对每个条目整体串行执行。

### 目标流程

```text
主 Agent 读取全部 TODO/START 条目
  → 迁移规划 Subagent 分析文件与依赖，产出任务分组方案
  → 主 Agent 审核方案（分组完整性、写入集合互斥）
  → 按方案将任务组分为可并行组与串行组
  → 主 Agent 将待处理条目写为 START
  → 分派迁移 Subagent
      └─ Subagent 先做必要性分析
          ├─ 误报 → 返回 NO_WORK_NEEDED + 依据
          └─ 确需迁移 → 迁移 → 查库 → 局部编译
  → 收集迁移结果
  → 主 Agent 复核误报依据，通过则以“误报”标记 DONE
  → 汇编/RVV 条目调用 Reviewer
  → 主 Agent 执行条目级 QEMU 验证
  → 通过后写 DONE + marking
```

### 依赖分组规则

依赖分组由迁移规划 Subagent 执行（规则本身与主 Agent 接管时一致）。下列情况必须进入同一串行任务组：

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

### 状态写入差别

当前方案允许执行流程在完成条目后直接更新 `DONE`。目标方案改为：

```text
主 Agent：TODO → START
规划 Subagent：返回分组方案（PLAN_READY，仅存在于返回协议，不落盘）
Subagent：返回 NO_WORK_NEEDED / READY_FOR_REVIEW / READY_FOR_VERIFY
Reviewer：返回 PASS / NEEDS_FIX / FAIL
主 Agent：复核误报依据，或运行 QEMU
主 Agent：START → DONE，并填写 marking（误报条目 marking 记为“误报 + 依据”）
```

这样可确保 `DONE` 始终代表已经由主流程验收，而不是某个 Subagent 的局部结论。

### 标准闭环

普通非汇编条目：

```text
迁移 Subagent
  → 必要性分析（误报则直接返回 NO_WORK_NEEDED）
  → 局部编译
  → 主 Agent QEMU 验证
  → 主 Agent 更新 JSON
```

汇编/RVV 条目：

```text
迁移 Subagent
  → 必要性分析（误报则直接返回 NO_WORK_NEEDED）
  → 知识库证据
  → 局部编译
  → riscv-code-reviewer
  → 必要时回流修复
  → 主 Agent QEMU 验证
  → 主 Agent 更新 JSON
```

---

## 8.3 阶段三：从单 Agent 编译修复改为并行诊断、串行实施

### 当前流程

```text
主 Agent 完整编译
  → 阅读错误
  → 修改代码/构建参数
  → 再次编译
  → 直到成功
```

### 目标流程

```text
主 Agent 完整编译并保存完整日志
  → 对错误按模块和类型切片
  → 一个或多个诊断 Subagent 并行分析
  → 主 Agent 汇总根因和依赖顺序
  → 主 Agent 或单个写入型 Subagent 实施一组修复
  → 主 Agent 重新完整编译
  → 重复直到成功
```

### 差别说明

- 并行只用于错误分析，不默认用于同时修改工程；
- 每轮只根据首个根因或一组互不冲突的根因实施修复；
- 重新编译必须由主 Agent 统一执行，保证日志和环境一致；
- 如果发现阶段二遗漏，必须回流对应条目，不在阶段三绕过状态闭环；
- 现有“连续 3 次同类型错误才询问用户”的规则保持不变。

### 错误分类建议

- 工具链或系统依赖缺失；
- `-march` / `-mabi` / ABI 不一致；
- 架构宏残留；
- 头文件和 include path；
- 链接库和符号缺失；
- 汇编器不识别指令或扩展；
- RVV intrinsic API 版本不匹配；
- 构建系统未纳入新增 `_riscv` 文件；
- 阶段二语义或接口迁移遗漏。

---

## 8.4 阶段四：从单热点分析改为多热点并行分析与统一验收

### 当前流程

```text
主 Agent 调用 riscv-asm-analyzer
  → Analyzer 分析并可能修改代码
  → Analyzer 回归验证
  → Analyzer 更新 marking
```

### 目标流程

```text
主 Agent 枚举热点
  → 每个独立热点分配一个 Analyzer
  → 并行输出基线和优化建议
  → 主 Agent 选择并实施优化
  → riscv-code-reviewer 复核
  → 主 Agent 运行 QEMU 回归
  → 主 Agent 统一更新 marking
```

### 差别说明

- 单个 Analyzer 从“阶段四整体执行者”变为“热点级分析者”；
- 可并行分析多个热点，但不能并行修改同一文件；
- Reviewer 被纳入优化后的标准门禁；
- Analyzer 不再直接提交最终状态；
- 优化终止条件仍采用现有 IPC、Dispatch Width、Block RThroughput 和瓶颈判断。

---

## 9. Subagent 任务输入协议

主 Agent 应给每个 Subagent 提供明确的任务包，避免其重新探索整个工程或扩大范围。

### 9.1 规划任务包（阶段二）

```json
{
  "task_id": "phase2-plan-all",
  "phase": "PHASE_2",
  "role": "MIGRATION_PLANNER",
  "project_root": "/abs/path/project",
  "scan_result_path": "/abs/path/project/scan_result.json",
  "entries": "全部 TODO/START 条目（或直接指向 scan_result.json）",
  "requirements": [
    "按 8.2 依赖分组规则产出任务组",
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

### 9.2 迁移任务包

建议字段：

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

任务包中的路径列表是能力边界，不只是提示。主 Agent 合并结果时必须检查实际修改是否超出 `allowed_write_paths`。

---

## 10. Subagent 结构化返回协议

所有 Subagent 应返回统一结果，便于主 Agent 自动验收：

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

允许的 `status`：

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

这些状态不得写入 `scan_result.json`。

### 10.1 规划返回示例

```json
{
  "task_id": "phase2-plan-all",
  "phase": "PHASE_2",
  "status": "PLAN_READY",
  "task_groups": [
    {
      "group_id": "g1-crc",
      "entry_ids": ["...scan_result 条目 ID 列表..."],
      "write_files": ["/abs/path/project/src/crc/crc32.c"],
      "exec_mode": "parallel",
      "workload": "medium",
      "reason": "同文件条目合并为一组；与其他组无共享文件"
    },
    {
      "group_id": "g2-sha-neon",
      "entry_ids": ["..."],
      "write_files": ["/abs/path/project/src/sha/sha256.c"],
      "exec_mode": "parallel",
      "workload": "high",
      "reason": "RVV intrinsic 迁移，独立目录"
    },
    {
      "group_id": "g3-common-header",
      "entry_ids": ["..."],
      "write_files": ["/abs/path/project/include/arch_utils.h"],
      "exec_mode": "serial",
      "workload": "low",
      "reason": "公共头文件，多组依赖，必须先定接口"
    }
  ],
  "unassigned_entries": [],
  "risks": []
}
```

### 10.2 误报返回示例

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

---

## 11. 并发与写入安全

### 11.1 单一状态提交者

`scan_result.json` 只能由主 Agent 写入。

需要相应调整当前专业 Agent：

- `riscv-code-reviewer` 从“可直接编辑 JSON”改为“返回建议的状态回流内容”；
- `riscv-asm-analyzer` 从“必须直接更新 marking”改为“返回建议 marking，由主 Agent 验证后写入”。

### 11.2 文件独占

写入型任务在执行前必须声明 `allowed_write_paths`。任意两个并行任务的写入集合不得相交：

```text
write_set(task_a) ∩ write_set(task_b) = ∅
```

阶段二的写入集合互斥由迁移规划 Subagent 的分组方案保证，主 Agent 审核时校验：

1. 每个条目恰好属于一个任务组；
2. 任意两个标记为 `parallel` 的任务组，其 `write_files` 交集为空；
3. 同一文件的所有条目位于同一任务组。

公共头文件、构建脚本、链接脚本和统一测试入口默认视为共享资源，必须串行修改。

### 11.3 隔离工作区

若执行环境支持独立 worktree，可以将不同写入型任务放在独立 worktree 中并行执行，然后由主 Agent 逐个审查和合并。

若没有隔离工作区：

- 只读任务可以并行；
- 写入型任务默认串行；
- 只有明确确认写入路径不重叠时才允许并行。

### 11.4 输出目录隔离

局部测试和编译应使用任务独立的输出目录，例如：

```text
<project_root>/.riscv-migrate/tasks/<task_id>/build/
<project_root>/.riscv-migrate/tasks/<task_id>/logs/
```

避免多个任务同时覆盖相同的 `a.out`、`ref.txt`、`riscv.txt` 或 `hot.s`。

### 11.5 结果合并检查

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

## 12. 失败、重试与回流

### 12.1 Subagent 失败不直接询问用户

处理顺序：

```text
Subagent 失败
  → 主 Agent 检查任务输入是否充分
  → 缩小或重新拆分任务
  → 恢复原 Subagent 上下文或更换角色
  → 主 Agent 必要时接管
  → 达到现有白名单阈值后才询问用户
```

### 12.2 回流路径

| 发现位置 | 回流位置 |
| -------- | -------- |
| 扫描候选冲突或遗漏 | 阶段一合并步骤 |
| 规划分组不完整或写入集合相交 | 主 Agent 退回规划 Subagent 重规划；连续不可用时按第 7 节规则接管 |
| 必要性分析确认误报 | 直接返回 `NO_WORK_NEEDED`，不进入迁移流程 |
| 主 Agent 对误报依据存疑 | 退回原 Subagent 补充依据，或另派 Subagent 二次分析 |
| Reviewer 发现 RVV/ABI 高危问题 | 阶段二 2.2 |
| 条目 QEMU 不一致 | 阶段二 2.2/2.4 修复循环 |
| 完整编译发现架构迁移遗漏 | 对应阶段二条目，状态保持或恢复为 `START` |
| 阶段四优化引入语义问题 | 阶段四修改撤回或回到阶段二 2.4 |
| Analyzer 结果互相冲突 | 主 Agent 统一基线参数后重新分析 |

### 12.3 恢复原 Subagent

同一个任务需要补充信息或继续修复时，应优先恢复原 Subagent 上下文，而不是创建新的 Agent 重新探索。只有以下情况才更换：

- 原任务范围划分错误；
- Agent 类型不匹配；
- 连续给出不可用结果；
- 需要独立审查而非继续执行。

---

## 13. 主流程状态机

```text
resolve_project_root
  ↓
PHASE_1_SCAN
  ├─ direct_scan
  └─ optional_parallel_scan_workers
  ↓
merge_and_write_scan_result
  ↓
PHASE_2_MIGRATE
  ↓
migration planner: build task groups (write_files 互斥)
  ↓
主 Agent: review plan
  ↓
for each runnable group:
  主 Agent: TODO → START
  dispatch migration worker(s)
  worker: 必要性分析
    ├─ false positive → NO_WORK_NEEDED → 主 Agent 复核 → DONE (marking=误报)
    └─ need migration → collect results
  review if required
  main-agent QEMU verify
  主 Agent: START → DONE + marking
  ↓
all entries DONE?
  ├─ no → continue phase 2
  └─ yes
       ↓
PHASE_3_BUILD
  ↓
full build
  ├─ failed → parallel diagnose → serial fix → rebuild
  └─ passed
       ↓
full QEMU/integration verify
  ↓
PHASE_4_ANALYZE_IF_TRIGGERED
  ↓
parallel hotspot analysis
  ↓
serial/isolated optimization merge
  ↓
review + QEMU regression
  ↓
主 Agent updates marking
  ↓
final summary
```

---

## 14. 对现有文件的建议调整

### 14.1 `skills/riscv-migrate/SKILL.md`

新增或修改：

1. 增加“主 Agent 与 Subagent 职责边界”；
2. 增加“动态调度决策”；
3. 增加“统一输入/返回协议”；
4. 阶段一增加可选并行补充扫描；
5. 阶段二入口增加“迁移规划 Subagent 先行”：任务分组方案经主 Agent 审核后才分派迁移 Subagent；
6. 阶段二每个条目增加“必要性分析”步骤：确认误报直接返回 `NO_WORK_NEEDED`，主 Agent 复核后以误报关闭条目；
7. 阶段二从“全部条目串行”改为“有依赖串行、无依赖可并行”；
8. 阶段三增加“并行诊断、串行修复”；
9. 阶段四允许多个独立热点并行分析；
10. 明确只有主 Agent 能更新 JSON；
11. 保留现有自主运行原则和五类询问白名单。

### 14.2 `commands/migrate.md`

增加阶段入口调度规则，使命令明确要求主 Agent 在每阶段开始时：

- 评估工作量；
- 阶段二必须先获取迁移规划 Subagent 的分组方案并审核；
- 选择零个、一个或多个 Subagent；
- 避免同文件并行写入；
- 汇总后再推进阶段。

### 14.3 `agents/riscv-code-reviewer.md`

调整：

- 默认只读；
- 增加标准输入字段；
- 增加 `PASS / NEEDS_FIX / FAIL` 输出；
- 删除直接写 JSON 的要求；
- 将建议的 `marking` 内容返回主 Agent。

### 14.4 `agents/riscv-asm-analyzer.md`

调整：

- 从阶段级 Agent 细化为热点级 Agent；
- 支持绑定唯一函数/区间；
- 删除直接写 JSON 的要求；
- 返回结构化性能基线、瓶颈、建议和建议 marking；
- 由主 Agent 负责回归验证与状态提交。

### 14.5 建议新增 Agent

- `riscv-scan-worker`：按目录或扫描维度返回候选迁移点，只读；
- `riscv-migration-planner`：专职阶段二任务规划，读取全部条目后产出互不重叠的任务分组方案（含 `write_files`、执行方式、工作量估计），只读；
- `riscv-migration-worker`：执行指定迁移条目，先做必要性分析（误报返回 `NO_WORK_NEEDED`），不写 JSON；
- `riscv-build-diagnoser`：只读分析工程级编译错误。

是否新增独立 `riscv-migration-worker` 可根据实际 Agent 注册机制决定；如果通用 Agent 已能按任务包完成修改，也可先只增加任务协议而不新增定义文件。`riscv-migration-planner` 建议落为独立定义文件，因为其“只规划、不执行”的约束与通用 Agent 的默认行为差异最大，独立定义最不容易被稀释。

---

## 15. 分阶段落地计划

### 第一步：建立编排边界

先修改技能规则，不新增并发写入：

- 主 Agent 独占 JSON；
- Reviewer/Analyzer 改为返回建议；
- 引入统一任务输入和输出；
- 引入迁移规划 Subagent：即使暂时全部串行执行，也先由规划产出任务分组方案，作为写入互斥的强制前置检查；
- 迁移 Subagent 增加前置必要性分析，误报条目在进入迁移前被过滤；
- 阶段二正式加入 Reviewer 门禁。

这一阶段风险最低，可先解决状态文件多写者问题和同文件并发写入问题。

### 第二步：引入只读并行

增加：

- 阶段一补充扫描并行；
- 阶段三错误诊断并行；
- 阶段四多热点只读分析并行。

只读并行不涉及代码合并，易于验证调度收益。

### 第三步：引入阶段二写入并行

增加：

- 按规划 Subagent 的分组方案分派并行任务；
- 文件写入集合检查（主 Agent 审核规划方案时强制执行）；
- 独立输出目录；
- 可选 worktree 隔离；
- 主 Agent 逐任务合并和 QEMU 验证。

这是收益最大、同时风险最高的一步，应在前两步稳定后启用。

### 第四步：完善度量与自适应调度

记录：

- 每阶段 Subagent 数；
- 每任务耗时；
- 重试次数；
- 合并冲突数；
- Reviewer 退回率；
- 误报率（`NO_WORK_NEEDED` 占条目比例）及误报判断被主 Agent 推翻的次数；
- 规划方案被退回重规划的次数；
- QEMU 首次通过率；
- 并行相对串行的总耗时收益。

主 Agent 后续可依据历史数据调整并发度和拆分粒度。

---

## 16. 风险与应对

| 风险 | 影响 | 应对 |
| ---- | ---- | ---- |
| 多个 Agent 同时写 JSON | 状态丢失、schema 损坏 | 主 Agent 独占 JSON 写入 |
| 多个 Agent 修改同一文件 | 合并冲突、覆盖代码 | 迁移规划 Subagent 分组保证写入集合互斥 + 主 Agent 审核校验 + 独立 worktree |
| 规划 Subagent 分组遗漏或重叠 | 并行任务写入冲突或条目漏处理 | 主 Agent 按三条硬性条件审核，不满足退回重规划 |
| 误报被当成 `NO_WORK_NEEDED` 放过 | 真实迁移点被跳过，阶段三暴露编译错误 | 误报依据必须可核验；主 Agent 复核存疑时二次分析；阶段三发现遗漏回流阶段二 |
| 必要性分析过度保守 | 该迁移的条目被反复退回分析 | 主 Agent 对同一条目的 `NO_WORK_NEEDED` 二次分析结论冲突时，按“需要迁移”处理并回流 |
| 任务拆分过细 | 上下文重建成本高、效率下降 | 小任务由主 Agent 直接处理，同文件条目合并为一个任务 |
| 任务拆分过粗 | 无法并行、Agent 上下文过大 | 按模块和文件依赖划分任务组 |
| Subagent 自述验证成功但不可复现 | 错误写入 DONE | 主 Agent 统一执行最终 QEMU 验证 |
| 不同 Agent 使用不同编译参数 | 结果不可比较 | 任务包统一传递 `-march/-mabi/-mcpu` 和环境入口 |
| Reviewer 与迁移 Agent 结论冲突 | 流程反复 | Reviewer 提供证据；主 Agent 裁决并定向回流 |
| 并行任务污染输出文件 | 测试结果串扰 | 每任务独立 build/log 目录 |
| Subagent 扩大修改范围 | 引入无关改动 | `allowed_write_paths` + 合并前越界检查 |
| Subagent 增加用户交互 | 破坏零交互目标 | Subagent 只向主 Agent 返回 BLOCKED，由主 Agent 按白名单处理 |

---

## 17. 验收标准

目标方案落地后，应满足：

1. 阶段之间仍严格按原门禁推进；
2. 每个阶段可根据工作量选择零个、一个或多个 Subagent；
3. `scan_result.json` 只有主 Agent 写入；
4. 阶段二在分派任何迁移 Subagent 前，已有经主 Agent 审核的任务分组方案，且任意两个并行任务组的写入集合不相交；
5. 同一个文件不会被两个未隔离的写入任务并行修改；
6. 每个迁移条目在动手修改前完成必要性分析，误报条目以 `NO_WORK_NEEDED` + 可核验依据关闭；
7. 阶段二独立条目可以并行处理；
8. 汇编/RVV 条目在 `DONE` 前经过独立 Reviewer；
9. 阶段三可以并行分析编译错误，但修复和完整重编译保持受控串行；
10. 阶段四多个独立热点可以并行分析；
11. 所有 Subagent 使用统一任务输入和结构化返回；
12. Subagent 成功不直接触发 `DONE`；
13. 主 Agent 的 QEMU/完整构建结果是最终完成依据；
14. 原有五类用户询问白名单没有扩张；
15. 阶段摘要能够列出 Subagent 数量、任务范围、通过/回流结果、误报条目数和最终产物。

---

## 18. 结论

本方案不是将当前串行流程简单替换成全面并行，而是在保留阶段门禁、正确性验证和 JSON 状态机的基础上，将阶段内部工作拆成可调度单元。

与当前方案相比，目标方案形成四层职责：

```text
规划 Subagent：把迁移点全集切成互不重叠、可安全调度的任务组
迁移 Subagent：先做必要性分析，再执行或放弃一个有边界的工作单元
Reviewer：从独立上下文检查关键迁移或优化结果
主 Agent：审核规划、合并、最终验证、状态提交和阶段推进
```

其中最重要的设计约束是：

> Subagent 可以完成修改并报告“待审查”或“待验证”，但只有主 Agent 完成最终验证后，才能把 `scan_result.json` 条目从 `START` 更新为 `DONE`。

在此基础上新增两条前置防线：

> 阶段二的任何并行分派必须建立在经审核的分组方案之上，写入集合互斥在规划阶段保证，而不是靠执行时碰运气；
> 任何迁移动手之前先做必要性分析，扫描误报在消耗迁移和验证成本之前被过滤。

这些约束解决了引入多个 Subagent 后最容易出现的状态竞争、同文件并发写入、错误完成和误报空转问题，同时保留了当前技能以 `scan_result.json` 为唯一权威源的核心设计。
