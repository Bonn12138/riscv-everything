---
name: riscv-migrate
description: x86/ARM → RISC-V（含 RVV）一体化迁移技能。按三大阶段推进：一、扫描迁移点；二、按迁移点分流（汇编/非汇编）迁移到 RV 平台（迁移→知识库→验证）；三、用交叉编译工具链编译整个工程并修复，直至产出 RV 可执行程序。性能分析（llvm-mca）作为独立 agent，仅在项目迁移完成后启动。每个迁移点的状态以 `scan_result.json` 条目里的 `status`（TODO/START/DONE）+ `marking`（异常/性能说明）字段同步，权威源在 JSON，不在源码注释。
---

# RISC-V 迁移（riscv-migrate）

面向 **x86 或 ARM** 工程，按**三大阶段**推进：

| 阶段 | 名称 | 产出 | 主要动作 |
| ---- | ---- | ---- | -------- |
| **一** | 扫描迁移点 | `<project_root>/scan_result.json` | 盘点全部架构相关待迁移点，类型由 `solver_type` 标识 |
| **二** | 按迁移点分流迁移 | `*_riscv` 源码 + 通过验证 | 对每个条目：分流 → 迁移 → 查知识库 → 验证 |
| **三** | 工程级编译修复 | RV 可执行程序 | 用交叉工具链编译整个工程，修编译错误，产出可运行的 RISC-V 产物 |
| **四**（独立 agent） | 性能分析与优化 | llvm-mca 报告 + 优化提交 | 仅在前三阶段全部完成后启动 |

> **性能分析是独立 agent，不嵌在阶段二内**：前三阶段全部通过后，再召唤性能分析 agent 对热点做 `llvm-mca` 静态分析并迭代优化。

> **迁移点进度（必须，权威源在 `scan_result.json` 中）**：每个条目通过 `status` 与 `marking` 两个结构化字段同步进度：
> - `status`（三态互斥）：`TODO` / `START` / `DONE`
> - `marking`（备注/异常/性能说明）：`status=DONE` 时必填
> - 不再向源码写 `MIGRATE-*` 注释，JSON 是唯一权威源（详见 [referens/project_scan.md](referens/project_scan.md)）

---

## 默认策略（关键）

- **三阶段串行 + 性能分析独立**：阶段一必须先于阶段二；阶段二全部条目 DONE 后才进入阶段三；阶段三产物可运行后才召唤性能分析 agent。
- **迁移点闭环（阶段二）**：每个条目严格按 `分流 → 迁移 → 知识库 → 验证` 四步走；验证通过才能从 `START` 推进到 `DONE`。
- **分流依据（`solver_type` + 源码特征）**：InlineAsm/Builtin 或含 intrinsic/asm → 汇编完整闭环 B→C→D→E（迁移→查库→验证→llvm-mca 迭代）；其余 → 轻量迁移（架构宏替换 + 交叉编译验证）。
- **知识库主动调用（禁止猜）**：一旦出现指令名/扩展名/`__riscv_*`/ABI/CSR 等信号，必须**主动查询**知识库（MCP 工具或 `scripts/query.py`），并在输出中保留证据链字段（`file_path/header_path`）。
- **验证主动触发（不等用户提）**：每轮迁移实质改动后，主动推进验证；RISC-V 工具链与 QEMU 由技能侧自动部署并加载。
- **状态字段强约束**：每个条目的 `status` 必须为 `TODO/START/DONE` 三态之一；`status=DONE` 必须伴随非空 `marking` 写明是否有异常/性能影响。
- **不依赖虚拟环境**：默认按"系统 Python + 脚本自举依赖"方式运行 `scripts/run_scan.sh` / `scripts/run_query.sh`；不要求用户建/激活 venv。
- **零交互自主运行（核心）**：所有决策按下文「自主运行原则」默认处理；只有白名单列出的 5 类场景才向用户提问。**禁止**在 `-mcpu`/`-march`/`llvm-mca` 是否安装/汇编 vs intrinsic/测试怎么写 等典型决策上向用户请示。

---

## 自主运行原则（零交互硬约束）

> **目标**：技能被调用后，尽量不向用户提问，agent 直接自主推进到任务完成。下面的 18 条决策**默认由 agent 自行决定**，不要问用户；只有 B 节白名单里列出的 5 类场景才向用户问一次性输入。

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
| 17 | 报告时机 | 只在**每个阶段结束时**输出一次结构化摘要（条目数 / 变更文件 / 产物路径 / 阻塞项）；不要逐条目汇报 | 用户要求"详细日志"才细化 |
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

### D. 自我节奏控制

- **决策表优先于自由发挥**：遇到 A 节列出的 18 类决策，**必须**先在表里查默认值；查不到再走"自主推理"路径，但推理结果要在产出物（JSON `marking` 字段、commit message、阶段摘要）里说明依据。
- **可恢复错误一律自助**：见 A 节 #11、#12、#15、#16。
- **不可逆操作前自检**："删除/覆盖/重新扫描"等动作前先核对对象（避免误删用户未纳入迁移的源码）。

---

---

## 何时用本技能

- 用户要求把 x86/ARM 工程迁移到 RISC-V（含 RVV），最终要产出一个可在 RV 上跑起来的可执行程序。
- 仓库或对话里出现 `scan_result.json`、`riscv_scan`、`扫描待迁移点`、`迁移到 RISC-V`、`rv 工具链编译`。
- 用户主动查询某条 RISC-V 指令/扩展/约束（如 `vadd.vv`、`clmul`、`vclmul`、`Zbc/Zvbc` 等）。

---

## 阶段一：扫描迁移点

入口先**扫描**整个 x86/ARM 工程，盘点**全部**架构相关待迁移点（汇编、intrinsic、架构宏、架构分支源码等），产出 `<project_root>/scan_result.json`。

### 1.1 扫描执行

1. 在**目标工程根目录**（用户指定或当前仓库根）固定输出 `<project_root>/scan_result.json`。
2. **若 `scan_result.json` 已存在**：视为已完成，**不要**重跑脚本，直接读该文件进入阶段二分流（除非用户明确要求重新扫描——先备份或删除再扫）。
3. **若不存在**：执行
   - 推荐（自动装依赖）：`<skill_root>/scripts/run_scan.sh <project_root> -o <project_root>/scan_result.json`
   - 兜底（手动）：`python3 -m pip install -r <skill_root>/scripts/requirements.txt && python3 <skill_root>/scripts/riscv_scan <project_root> -o <project_root>/scan_result.json`
4. 扫描覆盖**全部架构相关待迁移点**；类型由条目的 `solver_type` 标识（参见 [referens/project_scan.md](referens/project_scan.md)）。
5. 若 `riscv_scan` 执行失败：确认依赖已装、必要时换 Python 重试；仍失败则按 [referens/project_scan.md](referens/project_scan.md) 的 JSON schema 手工/静态分析填写 `scan_result.json`。

**阶段一进度自检**：`scan_result.json` 存在、可读；`suggestion_class` / `missing_class` 覆盖当前要处理的迁移范围；条目总数已盘点清楚。

---

## 阶段二：按迁移点分流迁移（每个条目：分流 → 迁移 → 知识库 → 验证）

对 `scan_result.json` 中的**每个条目**（文件 / 迁移点）严格按下列子步骤串行执行。所有条目 DONE 后才能进入阶段三。

### 2.1 分流（条目级别）

按 `solver_type` + 源码特征判断每个条目。满足以下**任一**即视为**汇编代码**：

1. **文件类型**：`.S` / `.s` / `.asm` 等汇编源文件。
2. **内联汇编**：源文件中含 `__asm__` / `__asm` / `asm volatile`，且迁移点落在该段内。
3. **intrinsic 热点**：含 x86 intrinsic（`_mm_*` / `_mm256_*` / `_mm512_*`）或 ARM NEON intrinsic（`vld1q_*` / `vmlaq_*` / `vst1q_*`），且是性能热点。
4. **架构强绑定 built-in**：含 `__builtin_ia32_*` / `__builtin_neon_*` 等。
5. **`solver_type` 为汇编类**：如 InlineAsm / Builtin。

- **汇编代码** → 登记后进入 **2.2 迁移 → 2.3 知识库 → 2.4 验证** 完整子闭环。
- **非汇编代码**（源码/Shell/宏/Toml、纯 C/C++ 逻辑、标准库调用、无架构绑定）→ 走轻量子流程（仅 2.2 迁移中的代码适配 + 2.4 验证）。

登记字段：`file_path`、`start_line`/`end_line`、`asm_type`（`inline_asm`/`standalone_asm`/`intrinsic`/`builtin`）、`arch_source`（`x86`/`x86_64`/`arm`/`aarch64`）、`brief`。

### 2.2 迁移（按分流结果）

**通用要求**：

- **测试先行**：为**原始 x86/ARM 实现**与**即将编写的 RISC-V 实现**补齐或编写可运行的单元测试（同一行为、可对比输出或 checksum）。无测试不得宣称迁移完成。
- **新增源文件命名带 `_riscv` 后缀**（与工程约定冲突时在说明里写清）。
- **算法一致、语义一致**；向量源必须用 **RVV**（汇编或 intrinsic，RVV 1.0），不得把汇编问题退化成纯 C 替代。
- **状态字段同步**：进入该条目时，把 `scan_result.json` 中该条目的 `status` 从 `TODO` 改为 `START`；完成后再改为 `DONE` 并填 `marking`。

**汇编代码（完整子闭环）**：

1. 读懂 x86/ARM 语义与边界；设计 RISC-V 寄存器分配、RVV `vl` / mask。
2. 编写 `*_riscv` 后缀的汇编或 intrinsic 实现。
3. **遇到指令/扩展/SEW&LMUL/intrinsic 对应/ABI 约束不确定时**：立即进入 2.3 查库，再继续编码。

**非汇编代码（轻量子流程）**：

1. **代码适配**：改架构宏/头文件/编译选项使 C/C++ 在 RISC-V 可编译——`#ifdef __x86_64__`/`__ARM_ARCH` 换 `__riscv`；移除 `<immintrin.h>`/`<arm_neon.h>` 等专有头文件；确认字节序/对齐/类型宽度在 RV64（小端、`long`=64-bit）下成立。
2. 不需要进入 2.3 知识库查询（除非出现指令/intrinsic 信号）。
3. 直接进入 2.4 验证。

### 2.3 知识库/手册查询（汇编条目必走；非汇编条目按需）

**触发条件**：迁移过程中出现任一信号：指令名/扩展名（如 `Zba`/`V`/`Zvbb`）、intrinsic（如 `__riscv_*`）、ABI/CSR/特权字段（如 `mstatus`）。

**工具选择**：

- **`search_core_isa_manuals`**：核心 ISA/汇编/Profile（Milvus spec=`core-isa-manuals`），覆盖 `riscv/riscv-isa-manual`、`riscv-non-isa/riscv-asm-manual`、`riscv/riscv-profiles`。
- **`search_rvv_vector_extensions`**：RVV/向量扩展与 vector crypto（Milvus spec=`rvv-vector-extensions`），覆盖 `riscv-non-isa/riscv-rvv-intrinsic-doc`、`riscv/integer-vector-absolute-difference`、`riscv/riscv-crypto`。
- **`search_special_instructions`**：真正的指令扩展（Milvus spec=`special-instructions`），覆盖 `riscv-zabha`、`riscv-zalasr`、`riscv-zaamo-zalrsc`、`riscv-bitmanip`、`riscv-bfloat16`。
- **`search_docs_tools`**：工具/指南/性能与优化（Milvus spec=`docs-tools`），覆盖 `riscv-performance-events`、`riscv-optimization-guide`。

**调用方式**：

- 列工具：`<skill_root>/scripts/run_query.sh --list-tools`
- 示例：
  - `<skill_root>/scripts/run_query.sh -t search_core_isa_manuals -q "mstatus MPP"`
  - `<skill_root>/scripts/run_query.sh -t search_rvv_vector_extensions -q "__riscv_vsetvl"`
  - `<skill_root>/scripts/run_query.sh -t search_special_instructions -q "Zba 有哪些指令"`

**证据链**：结论里必须保留 MCP 返回的 `file_path` 与 `header_path`（或等价标题路径），作为证据链。

### 2.4 验证（每个条目迁移完成后立即触发）

1. **自动准备环境**（已存在则跳过）：
   - `<skill_root>/scripts/prepare_verify_env.sh`
   - 由技能在当前会话**自动加载** RISC-V 工具链与 `qemu-*`（从 `<skill_root>/resources/` 部署/解包），不联网，不要求用户手动 `source`。
2. 构建并跑测试；用 `qemu-riscv64 -cpu max <bin>` 做输出对比。
3. 对比不一致 → 迭代修复 RISC-V 侧逻辑 / 测试 / 构建脚本，直到一致。
4. **验证通过后必须更新 JSON 字段**：
   - 把 `scan_result.json` 中该条目的 `status` 从 `START` 改为 `DONE`
   - 同时填写 `marking`：
     - 正常完成：`无异常，性能与 x86/ARM 实现持平（或给出相对比例）`
     - 有异常：`语义差异：<具体点>，已通过测试规避` 或 `TODO(后续)：<具体项>`
     - 性能影响：`性能低于原实现 N%，原因：<…>，建议进入阶段四 llvm-mca 优化`

**阶段二进度自检**：所有条目 `status=DONE`；每个 `DONE` 条目 `marking` 非空；汇编条目经过 2.3 知识库证据；非汇编条目完成架构宏替换并通过 QEMU 验证。

---

## 阶段三：工程级编译修复（交叉工具链编译整个工程）

阶段二所有条目 DONE 后，**用 RISC-V 交叉工具链编译整个工程**，修复编译错误直至产出可运行的 RISC-V 可执行程序。

### 3.1 准备环境

- 由技能自动加载 RISC-V 交叉工具链：`<skill_root>/resources/riscv_toolchain_env.sh` + `source <skill_root>/resources/env.sh`。
- 同时准备 QEMU user-static：`<skill_root>/resources/qemu_static_env.sh`。

### 3.2 编译整个工程

1. 在目标工程里用交叉工具链跑完整构建（如 `make` / `cmake --build`），前缀以 `*_TOOLCHAIN_ROOT/bin` 下实际文件为准（如 `riscv64-unknown-linux-gnu-gcc`）。
2. **修编译错误**：
   - 缺什么装什么（系统包 + `<skill_root>/resources/*.sh`）。
   - `__x86_64__` / `__ARM_ARCH` 等宏残留未替换的，回阶段二补。
   - 头文件路径、链接库路径、ABI（`lp64d`）等，按工程实际调整。
3. **`-march` 覆盖所用扩展**；常见起点：`-march=rv64gcv_zbb_zbc_zvbc_zvkb_zvksed -mabi=lp64d`，不够再补。
4. 若工程要求静态链接，遵守之。
5. 若任务固定产物名（如 `riscv64_test`），遵守任务说明。

### 3.3 运行验证

- 用 `qemu-riscv64 -cpu max <bin>` 跑主流程/集成测试，至少确认主程序可启动、无立即崩溃、关键路径输出与 x86/ARM 侧一致。
- 与阶段二条目级 QEMU 验证互补：条目级验证保证**单个迁移点**正确；工程级验证保证**整体**可运行。

**阶段三进度自检**：工程完整构建无错误；RV 可执行程序可在 QEMU 中跑通主流程。

---

## 阶段四（独立 agent）：性能分析与优化

> **本阶段为独立 agent**，由 agent 在阶段三全部通过后**主动召唤**；不在阶段二内嵌运行。

### 4.1 触发条件

- 阶段一/二/三全部完成。
- 存在**手写汇编 / RVV 汇编 / intrinsic 热点**循环，且阶段二已 DONE。

### 4.2 工作流程（llvm-mca 静态分析 + 迭代优化）

1. **提取热点汇编**（`hot.s`）：
   - 手写 `.S` 文件可直接作为输入。
   - C/intrinsic：`<RISCV_CLANG> -O3 -S ... -o - hot.c` 管道到 `llvm-mca`。
   - flags 必须与工程 `-march`/`-mabi`/`-mcmodel` 完全一致。
2. **执行 `llvm-mca`**（如本机无 `llvm-mca`，**agent 自主调用 `bash <skill_root>/resources/llvm_mca_env.sh` 安装**；详见 `agents/riscv-asm-analyzer.md` 的「llvm-mca 自动安装」一节）：
   - 快速分析：`llvm-mca -mtriple=riscv64 -mcpu=<cpu> -mattr=+v,+zvbc --all-stats --iterations=100 < hot.s`
   - 深度分析：`llvm-mca ... --bottleneck-analysis --all-views --timeline ...`
3. **`-mcpu` 选择优先级**：用户 prompt 中明确指定 > `zhufeng2`（默认） > 目标部署芯片匹配 > `sifive-p450`（乱序通用基线）/ `sifive-u74`（顺序通用基线）。**不要用 `generic`/`generic-rv64`**（无调度模型会导致报错）。
4. **每轮优化后必须回到阶段二的 2.4 验证**（同一组测试、输出一致），禁止只追性能导致语义回归。
5. 优化终止条件：IPC 接近 Dispatch Width、`Block RThroughput` 不再显著下降、出现 `No resource or data dependency bottlenecks`。

### 4.3 闭环产出

- 每轮保留 `llvm-mca` 输出关键摘要（吞吐、周期、瓶颈）。
- 每轮修改后回到 2.4 验证（构建 + QEMU/对比测试）。
- 最终产出：优化后的 `*_riscv` 源文件 + 性能对比要点。

详细参数、注释格式警告（`.text` 段内**禁止**行尾 `/* */`）、结果解读与常见优化方向见 [referens/code_migrate.md](referens/code_migrate.md)。

---

## 迁移点状态字段规范（速查）

迁移进度由 `scan_result.json` 中每个条目的 `status` 与 `marking` 两个结构化字段维护，**权威源在 JSON，不在源码**。三态**互斥**（同一时刻只能有一个）；`status=DONE` 时 `marking` 必填。

```jsonc
{
  "suggestion_class": [
    {
      "file_path": "/abs/path/src/crc/crc32.c",
      "start_line": 120,
      "end_line": 200,
      "solver_type": "InlineAsm",
      "status": "TODO",        // ← 阶段一扫描后默认
      "marking": ""            // ← TODO 时为空
    }
  ]
}
```

进入阶段二 2.2 时：

```jsonc
{ "status": "START", "marking": "" }
```

阶段二 2.4 验证通过后：

```jsonc
{
  "status": "DONE",
  "marking": "无异常，性能与 SSE 实现持平；建议进入阶段四用 llvm-mca 确认吞吐"
}
```

| `status` | 含义 | 何时设置 | `marking` 是否必填 |
| ------- | ---- | -------- | ----------------- |
| `TODO` | 未处理 | 阶段一扫描产出（默认值） | 否 |
| `START` | 开始迁移 | 阶段二 2.2 进入该条目 | 否 |
| `DONE` | 迁移完成 | 阶段二 2.4 验证通过 | **是** |

`marking` 常见内容：

- 无异常：`无异常，性能持平`
- 语义差异：`语义差异：<具体点>，已通过测试规避`
- 性能下降：`性能低于原实现 N%，原因：<…>，建议进入阶段四 llvm-mca 优化`
- 阶段四优化后：`经阶段四 llvm-mca 优化后 IPC 由 <x> 提升到 <y>（<cpu>），关键改动：<具体点>`
- 需要后续修复：`TODO(后续)：<具体项>`

> **不要**在源码里写 `// [MIGRATE-*]` 注释——所有状态信息以 `scan_result.json` 为唯一权威源。

---

## 附加资源（按需阅读）

| 文件　　　　　　　　　　　　　　　　　　　　　　　　 | 内容　　　　　　　　　　　　　　　　　　　　　　　　　　　　|
| ------------------------------------------------------| -------------------------------------------------------------|
| [referens/project_scan.md](referens/project_scan.md) | `scan_result.json` 格式、`riscv_scan` 行为、扫描项类型　　　|
| [referens/code_migrate.md](referens/code_migrate.md) | 迁移三步、编译/测试/工具链约定、**阶段四（llvm-mca）** 详述 |

---

## 脚本

- `scripts/riscv_scan`：扫描引擎入口（输出 `scan_result.json`，schema 见 [referens/project_scan.md](referens/project_scan.md)）。
- `scripts/query.py`：通过 RISC-V-DOC-RAG MCP 知识库服务查询 ISA/Intrinsic 手册（输出包含 `file_path/header_path`）。
- `scripts/run_scan.sh`：扫描入口（自动安装依赖）。
- `scripts/run_query.sh`：查询入口（自动安装依赖）。
- `scripts/prepare_verify_env.sh`：准备验证环境（从 `resources/` 部署/配置 RISC-V 工具链与 QEMU user-static，尽量不联网下载）。
- `resources/llvm_mca_env.sh`：本机无 `llvm-mca` 时拉取并解压 llvm-mca 工具包，写入 `PATH`/`env.d`；阶段四性能分析前按需执行。