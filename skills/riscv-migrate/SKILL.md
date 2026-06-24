---
name: riscv-migrate
description: 先扫描 x86/ARM 代码库盘点全部待迁移点（产出 scan_result.json），再基于扫描结果按规模（单 agent / 多智能体 workflow）与类型（汇编 / 非汇编）分流；非汇编代码直接迁移到支持 RISC-V 的 C/C++ 实现，汇编代码（含 intrinsic 热点）进入「迁移→知识库→验证→llvm-mca」一体化闭环。迁移中遇指令/扩展/内建函数/约束必须主动查知识库拿证据；验证由技能自动准备并加载 RISC-V 工具链与 QEMU；汇编热点验证通过后用 llvm-mca 做静态性能分析并回归（细则见 referens/code_migrate.md）。大规模（50+ 迁移点）场景走 /everything-riscv:*-swarm 多智能体 workflow 并行编排。
---

# RISC-V 迁移（riscv-migrate）

面向 **x86 或 ARM** 工程：入口先**工程扫描**产出 `scan_result.json`（盘点全部架构相关待迁移点及其类型），再**基于扫描结果分流**——按条目数决定单 agent 串行还是多智能体 workflow，按 `solver_type`/源码特征区分汇编与非汇编。非汇编代码走轻量 C/C++ 迁移；汇编代码（含 intrinsic 热点）进入「迁移→查库→验证→llvm-mca」完整闭环。不确定的指令或扩展用 **RISC-V-DOC-RAG 知识库**查文档（`search_core_isa_manuals` / `search_rvv_vector_extensions` 等），不要猜。

## 默认策略（关键）

- **扫描先行**：先扫描产出 `scan_result.json`（盘点全部架构相关待迁移点），再据此分流——**规模看条目数、类型看 `solver_type`**。无扫描结果则无法判断规模与类型。
- **一体化闭环（默认开启）**：扫描 → 分流 → 迁移一个条目 →（自动）查知识库补齐证据 →（自动）触发验证指导；循环直到条目完成。
- **规模分流（基于扫描条目数）**：`suggestion_class_count + missing_class_count` ≤50 走单 agent 串行；50+ 走 `/everything-riscv:*-swarm` 多智能体 workflow 并行编排。
- **汇编/非汇编分流（基于 solver_type）**：InlineAsm/Builtin 类或源码含 intrinsic/asm → 汇编完整闭环 B→C→D→E；其余（源码/Shell/宏/Toml）→ 轻量 C/C++ 迁移。
- **知识库主动调用（禁止猜）**：只要出现任一信号：指令名/扩展名（如 `Zba`/`V`/`Zvbb`）、intrinsic（如 `__riscv_*`）、ABI/CSR/特权字段（如 `mstatus`），必须**主动查询**再下结论，并在输出里保留证据链字段（`file_path/header_path` 或等价信息）。
- **验证主动触发（不等用户提）**：每当迁移实现或测试有实质改动，就主动推进验证步骤（至少给出可执行命令与预期产物）；验证所需的 RISC-V 工具链与 QEMU 由技能侧自动准备并在当前会话加载。
- **不依赖虚拟环境**：默认按“系统 Python + 脚本自举依赖”的方式跑 `scripts/run_scan.sh` / `scripts/run_query.sh`；不要要求用户创建/激活 venv。

## 何时用本技能

- 用户要求做架构/内建函数/汇编向 RISC-V（含 RVV）迁移。
- 仓库或对话里出现 `scan_result.json`、`riscv_scan`、或「扫描待迁移点」。
- 用户主动查询某条 RISC-V 指令/扩展/约束（如 `vadd.vv`、`clmul`、`vclmul`、`Zbc/Zvbc` 等）。

## 使用方式（可独立用，也可自动串联）

- **只扫描**：生成/更新 `scan_result.json`，盘点全部待迁移点（规模与类型判断的依据）。
- **只分流**：读已有 `scan_result.json`，判断规模与汇编性，决定走 workflow、轻量迁移还是完整闭环。
- **只查知识库**：回答"指令/扩展/约束/intrinsic 对应关系"等，并给证据链。
- **只迁移**：按条目改代码，但仍会在关键点主动查库并在每轮改动后引导验证。
- **只验证**：准备工具链/QEMU，运行测试/对比，定位不一致并回流修复。
- **只做性能分析（llvm-mca）**：对已通过验证的手写/RVV 汇编或 intrinsic 热点，用 `llvm-mca` 做静态吞吐/瓶颈分析，给出优化建议与回归验证指导（详见阶段 E）。

## 阶段 A：工程扫描与分流（入口）

入口先扫描盘点全部待迁移点，再基于扫描结果做规模与类型分流。**无 `scan_result.json` 则无法判断规模与类型，必须先扫描。**

### A.1 扫描

1. 在 **目标工程根目录**（用户指定或当前仓库根）定位固定输出：`<project_root>/scan_result.json`。
2. **若 `scan_result.json` 已存在**：视为扫描已完成，**不要**再跑扫描脚本，直接读该文件进入 A.2 分流（除非用户明确要求重新扫描——此时先备份或删除该文件再扫描）。
3. **若不存在**：用包装脚本执行扫描（脚本自举依赖；不需要 venv）。扫描覆盖**全部架构相关待迁移点**（汇编、intrinsic、架构宏、架构分支源码等），不限于汇编；类型由条目的 `solver_type` 标识。
   - 推荐（自动装依赖）：`<skill_root>/scripts/run_scan.sh <project_root> -o <project_root>/scan_result.json`
   - 兜底（手动）：`python3 -m pip install -r <skill_root>/scripts/requirements.txt && python3 <skill_root>/scripts/riscv_scan <project_root> -o <project_root>/scan_result.json`
4. **若 riscv_scan 脚本执行失败**：确认当前解释器已安装 `scripts/requirements.txt` 中的依赖；必要时在技能目录重装依赖或换用满足版本要求的 Python。
5. 若工程暂无可用扫描实现：由代理按 [referens/project_scan.md](referens/project_scan.md) 中的 **JSON schema** 手工/static 分析填写 `scan_result.json`，再进入 A.2。

**进度自检**：`scan_result.json` 存在且可读，`suggestion_class` / `missing_class` 覆盖当前要处理的迁移范围。

### A.2 基于扫描结果分流

#### 规模分流：单 agent 还是 workflow

按 `suggestion_class_count + missing_class_count` 决定执行模式：

| 条目数　　　　　　　　　　　　　　　　　　　　 | 执行模式　　　　　　　| 后续　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　 |
| ------------------------------------------------| -----------------------| ------------------------------------------------------------------------------|
| **≤50**、或只处理单文件/单条目、需精细人工判断 | **单 agent 串行**　　 | 继续下面的「汇编性分流」→ 轻量迁移或阶段 B–E　　　　　　　　　　　　　　　　 |
| **50+**、或用户说"批量/大项目/全量迁移"　　　　| **多智能体 workflow** | 引导发起 `/everything-riscv:*-swarm`（下表）；单 agent 阶段 B–E 不再逐条驱动 |

workflow 路径：4 个 swarm 命令各对应一个阶段，按 `scan → classify-swarm → migrate-swarm → verify-swarm → mca-swarm` 串联，每次发起需用户授权（opt-in）；汇编性分流由 `classify-swarm` 自动完成。

| 命令　　　　　　　　　　　　　　　 | 对应阶段 | 作用　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　 |
| ------------------------------------| ----------| ----------------------------------------------------------------------------------------|
| `/everything-riscv:classify-swarm` | 阶段 A.2 | 按文件并行预分流（含汇编/非汇编判定），产出 `classified.json` + 初始化 `progress.json` |
| `/everything-riscv:migrate-swarm`　| 阶段 B+C | 按 tier 分批并行迁移，每文件组 迁移→查 KB→2 票 review→修复　　　　　　　　　　　　　　 |
| `/everything-riscv:verify-swarm`　 | 阶段 D　 | QEMU 对比 baseline 循环收敛，修真发散　　　　　　　　　　　　　　　　　　　　　　　　　|
| `/everything-riscv:mca-swarm`　　　| 阶段 E　 | 对 `asm_hotspot` 并行 llvm-mca 分析 + 迭代优化　　　　　　　　　　　　　　　　　　　　 |

> workflow 与单 agent 共享同一批脚本（`scripts/`）、agent（`agents/`）、迁移细则（`code_migrate.md`），是后者的**并行 + 对抗验证 + 循环收敛**版本，不替换它。产物集中到 `<target>/.riscv_migrate/`（`progress.json` 支持断点续传，可整体删除重建；target 是 git 仓库时建议加 `.gitignore`）；设计约定见 [referens/workflow_patterns.md](referens/workflow_patterns.md)。

#### 汇编性分流：汇编还是非汇编

按 `solver_type` + 源码特征判断每个条目（workflow 路径由 `classify-swarm` 自动判定，单 agent 路径手动判定）。满足以下**任一**即视为**汇编代码**：

1. **文件类型**：`.S` / `.s` / `.asm` 等汇编源文件。
2. **内联汇编**：C/C++ 源文件中含 `__asm__` / `__asm` / `asm volatile` 且迁移点落在该段内。
3. **intrinsic 热点**：含 x86 intrinsic（`_mm_*` / `_mm256_*` / `_mm512_*`）或 ARM NEON intrinsic（`vld1q_*` / `vmlaq_*` / `vst1q_*`），且是性能热点。
4. **架构强绑定 built-in**：含 `__builtin_ia32_*` / `__builtin_neon_*` 等。
5. **`solver_type` 为汇编类**：如 InlineAsm / Builtin。

- **汇编代码** → 登记后进入完整闭环 **B→C→D→E**。登记字段：`file_path`、`start_line`/`end_line`、`asm_type`（`inline_asm`/`standalone_asm`/`intrinsic`/`builtin`）、`arch_source`（`x86`/`x86_64`/`arm`/`aarch64`）、`brief`（一句话描述）。
- **非汇编代码**（源码/Shell/宏/Toml、纯 C/C++ 逻辑、标准库调用、无架构绑定）→ 走下面的轻量迁移。

## 非汇编代码的轻量迁移

无需走 B→E 完整闭环：

1. **代码适配**：改架构宏/头文件/编译选项使 C/C++ 在 RISC-V 可编译——`#ifdef __x86_64__`/`__ARM_ARCH` 换 `__riscv`；移除 `<immintrin.h>`/`<arm_neon.h>` 等专有头文件；确认字节序/对齐/类型宽度在 RV64（小端、`long`=64-bit）下成立。
2. **交叉编译验证**：RISC-V 交叉编译器编译通过（环境见阶段 D）。
3. **功能验证**：QEMU（`qemu-riscv64 -cpu max`）跑单测，行为与原始 x86/ARM 一致。
4. **完成**：测试通过即迁移完成；一般**不需要** llvm-mca（除非后续发现是热点）。

**进度自检**：扫描已完成；每个待迁移点已分流；非汇编代码已完成轻量迁移并通过交叉编译 + QEMU 验证；汇编代码已登记、准备进入阶段 B。

## 阶段 B：迁移点迁移（汇编代码完整闭环）

针对 A.2 判定为**汇编代码**的条目，对单个文件或单条条目严格按顺序执行（细节与编码/编译/测试/llvm-mca 见 [referens/code_migrate.md](referens/code_migrate.md)）：

1. **测试先行**：为 **原始 x86/ARM 实现** 与 **即将编写的 RISC-V 实现** 补齐或编写可运行的单元测试（同一行为、可对比输出或 checksum）。无测试则不得宣称迁移完成。
2. **RISC-V 迁移**：编写 `*_riscv` 后缀的汇编或 intrinsic 实现，保持算法与语义一致；向量源必须用 **RVV**（汇编或 intrinsic，RVV 1.0），不得把汇编问题退化成纯 C 替代。
   - 迁移中一旦涉及“指令选择 / 扩展依赖 / SEW&LMUL 限制 / intrinsic 对应 / ABI 约束”，必须立刻进入阶段 C 查证据，再继续写代码。
3. **对比与修复（主动触发）**：每次完成一轮迁移改动后，主动推进验证（阶段 D）去跑同一组测试并对比（如 `qemu-riscv64 -cpu max`）；不一致则迭代修复 RISC-V 侧逻辑直至一致。
4. **性能分析与改进（汇编迁移必做）**：阶段 D 验证通过后，进入阶段 E 使用 `llvm-mca` 做静态吞吐/瓶颈分析；基于结论做一次或多次小步优化，每轮优化后都必须回到阶段 D 复测并保持输出一致。

**进度自检**：测试通过、对比一致、构建与命名约定满足 [referens/code_migrate.md](referens/code_migrate.md)；完成阶段 E 的 `llvm-mca` 分析并通过回归验证。

## 阶段 C：知识库/手册查询（可单独使用）

当用户问“某指令对应什么 / 是否保留 / 属于哪个扩展 / SEW 限制 / intrinsic 对应的汇编 / profile 约束”等，按如下方式查询并引用输出（不要猜）：

### 选工具的规则（先选对知识库）

- **`search_core_isa_manuals`**：核心 ISA/汇编/Profile（合并知识库，Milvus spec=`core-isa-manuals`）
  - 覆盖：`riscv/riscv-isa-manual`、`riscv-non-isa/riscv-asm-manual`、`riscv/riscv-profiles`
- **`search_rvv_vector_extensions`**：RVV/向量相关扩展与 vector crypto（合并知识库，Milvus spec=`rvv-vector-extensions`）
  - 覆盖：`riscv-non-isa/riscv-rvv-intrinsic-doc`、`riscv/integer-vector-absolute-difference`、`riscv/riscv-crypto`
- **`search_special_instructions`**：真正的指令扩展（合并知识库，Milvus spec=`special-instructions`）
  - 覆盖：`riscv-zabha`、`riscv-zalasr`、`riscv-zaamo-zalrsc`、`riscv-bitmanip`、`riscv-bfloat16`
- **`search_docs_tools`**：工具/指南/性能与优化（合并知识库，Milvus spec=`docs-tools`）
  - 覆盖：`riscv-performance-events`、`riscv-optimization-guide`

### 推荐调用方式（自动装依赖）

- 列工具确认服务端暴露的工具名：
  - `<skill_root>/scripts/run_query.sh --list-tools`
- 常见查询示例：
  - `<skill_root>/scripts/run_query.sh -t search_core_isa_manuals -q "mstatus MPP"`
  - `<skill_root>/scripts/run_query.sh -t search_rvv_vector_extensions -q "__riscv_vsetvl"`
  - `<skill_root>/scripts/run_query.sh -t search_special_instructions -q "Zba 有哪些指令"`
  - `<skill_root>/scripts/run_query.sh -t search_docs_tools -q "performance events"`

### 输出要求（证据链）

- **必须**在结论里保留 MCP 返回中的 `file_path` 与 `header_path`（或等价的标题路径信息），作为证据链。
- 如果返回未包含上述字段：优先让问题更具体（指令名/扩展名/操作数形态/SEW/LMUL），再重查；不要猜。

### 环境提示

- 这里强调的是**结果的证据链**（`file_path/header_path` 等），不是运行过程的虚拟环境。若查询失败，优先让问题更具体并重试查询；不要让用户先去“建 venv/装一堆东西”。

## 阶段 D：验证（自动准备工具链/QEMU + 自动加载环境）

当用户提出“验证/对比/用 QEMU 跑”时，或迁移阶段完成一轮实质改动后（默认策略要求你主动触发）：

1. 先准备环境（会下载；已存在则跳过）：
   - `<skill_root>/scripts/prepare_verify_env.sh`
2. 由技能在当前会话**自动加载**验证所需环境：所需的 RISC-V 工具链与 `qemu-*` **从 skill 的 source 资源（`<skill_root>/resources/`）中部署/解包并加载**（已存在则复用），不进行联网下载，也不要求用户手动 `source`。
3. 在目标工程里跑构建与测试，并用 `qemu-riscv64 -cpu max ...` 做输出对比。

## 阶段 E：性能分析与改进（llvm-mca）

手写/RVV 汇编或 intrinsic 热点，且阶段 D 已通过时执行。详细步骤见 [referens/code_migrate.md](referens/code_migrate.md) 的 **「第四步（阶段 E）」** 一节。

### 快速开始

```bash
# 1. 准备 llvm-mca（若尚未安装）
bash <skill_root>/resources/llvm_mca_env.sh && source <skill_root>/resources/env.sh

# 2. 快速分析热点汇编（注意：-mcpu 不能用 generic，须选具体型号）
llvm-mca -mtriple=riscv64 -mcpu=zhufeng2 -mattr=+v,+zvbc --all-stats < hot.s

# 3. 深度分析（含瓶颈分析）
llvm-mca -mtriple=riscv64 -mcpu=zhufeng2 -mattr=+v,+zvbc \
  --bottleneck-analysis --all-views --timeline --iterations=100 < hot.s
```

### 关键注意事项

- **`-mcpu` 必须指定有调度模型的具体 CPU**；`generic` / `generic-rv64` 在 RISC-V 上会导致报错。**选择优先级**：用户 prompt 中明确指定 > `zhufeng2`（默认） > 目标部署芯片匹配 > `sifive-p450`（乱序通用基线）/ `sifive-u74`（顺序通用基线）。详见 [referens/code_migrate.md](referens/code_migrate.md)。
- **`-mattr` 须与工程 `-march` 一致**：用到 Zvbc 就要加 `+zvbc`，用到 V 扩展就加 `+v`。
- **手写 .S 文件的注释须用 `//` 或 `#`**：LLVM MC 不支持 `.text` 段内的 `/* */` 行尾注释。
- **每轮优化后必须回到阶段 D 验证正确性**，禁止只追性能导致语义回归。
- 闭环步骤、参数详解、结果解读、优化方向与回归验证见 [referens/code_migrate.md](referens/code_migrate.md)。

## 附加资源（按需阅读）

| 文件　　　　　　　　　　　　　　　　　　　　　　　　 | 内容　　　　　　　　　　　　　　　　　　　　　　　　　　　　|
| ------------------------------------------------------| -------------------------------------------------------------|
| [referens/project_scan.md](referens/project_scan.md) | `scan_result.json` 格式、`riscv_scan` 行为、扫描项类型　　　|
| [referens/code_migrate.md](referens/code_migrate.md) | 迁移三步、编译/测试/工具链约定、**阶段 E（llvm-mca）** 详述 |
| [referens/workflow_patterns.md](referens/workflow_patterns.md) | 多智能体 workflow 设计约定（HARD RULES / 并发聚合 / 产物 schema / anti-reward-hack） |

## 脚本

- `scripts/riscv_scan`：扫描引擎入口（输出 `scan_result.json`，schema 见 [referens/project_scan.md](referens/project_scan.md)）。
- `scripts/query.py`：通过 RISC-V-DOC-RAG MCP 知识库服务查询 ISA/Intrinsic 手册（输出包含 `file_path/header_path`）。
- `scripts/run_scan.sh`：扫描入口（自动安装依赖）。
- `scripts/run_query.sh`：查询入口（自动安装依赖）。
- `scripts/prepare_verify_env.sh`：准备验证环境（从 `resources/` 部署/配置 RISC-V 工具链与 QEMU user-static，尽量不联网下载）。
- `resources/llvm_mca_env.sh`：本机无 `llvm-mca` 时拉取并解压 llvm-mca 工具包，写入 `PATH`/`env.d`；阶段 E 性能分析前按需执行。
- `workflows/*.workflow.js`：多智能体编排脚本（大规模场景，由 `/everything-riscv:*-swarm` 命令经 Workflow 工具 + `scriptPath` 发起；约定见 [referens/workflow_patterns.md](referens/workflow_patterns.md)）。
