---
name: riscv-migrate
description: 预分析 x86/ARM 代码库中的待迁移点，区分汇编与非汇编代码；非汇编代码直接迁移到支持 RISC-V 的 C/C++ 实现即可，汇编代码（含 intrinsic 热点）才进入「扫描→迁移→知识库→验证→llvm-mca 分析与迭代优化」一体化闭环。迁移过程中遇到指令/扩展/内建函数/约束必须主动查知识库拿证据；需要验证时由技能自动准备并加载 RISC-V 工具链与 QEMU；验证通过后对手写/RVV 汇编或 intrinsic 热点可用 llvm-mca 做静态性能分析并回归验证（细则见 referens/code_migrate.md）。
---

# RISC-V 迁移（riscv-migrate）

面向 **x86 或 ARM** 工程：先做 **预处理与分流**（判断待迁移点是否为汇编代码），非汇编代码直接迁移到支持 RISC-V 的 C/C++ 实现即可；汇编代码（含 intrinsic 热点）进入 **工程扫描**（产出 `scan_result.json`），再按条目做 **迁移点迁移**。不确定的指令或扩展用 **RISC-V-DOC-RAG MCP 知识库服务** 查文档（对应工具如 `search_core_isa_manuals` / `search_rvv_vector_extensions` 等），不要猜。

## 默认策略（关键）

- **一体化闭环（默认开启）**：预处理分流 → 扫描 → 迁移一个条目 →（自动）查知识库补齐证据 →（自动）触发验证指导；循环直到条目完成。
- **汇编/非汇编分流**：阶段 0 先判断待迁移点是否属于汇编代码（含 intrinsic 热点）；非汇编代码走轻量 C/C++ 直接迁移路径（阶段 0-B），汇编代码才进入完整 A→B→C→D→E 闭环。
- **知识库主动调用（禁止猜）**：只要出现任一信号：指令名/扩展名（如 `Zba`/`V`/`Zvbb`）、intrinsic（如 `__riscv_*`）、ABI/CSR/特权字段（如 `mstatus`），必须**主动查询**再下结论，并在输出里保留证据链字段（`file_path/header_path` 或等价信息）。
- **验证主动触发（不等用户提）**：每当迁移实现或测试有实质改动，就主动推进验证步骤（至少给出可执行命令与预期产物）；验证所需的 RISC-V 工具链与 QEMU 由技能侧自动准备并在当前会话加载。
- **不依赖虚拟环境**：默认按“系统 Python + 脚本自举依赖”的方式跑 `scripts/run_scan.sh` / `scripts/run_query.sh`；不要要求用户创建/激活 venv。

## 何时用本技能

- 用户要求做架构/内建函数/汇编向 RISC-V（含 RVV）迁移。
- 仓库或对话里出现 `scan_result.json`、`riscv_scan`、或「扫描待迁移点」。
- 用户主动查询某条 RISC-V 指令/扩展/约束（如 `vadd.vv`、`clmul`、`vclmul`、`Zbc/Zvbc` 等）。

## 使用方式（四件事可独立用，也可自动串联）

- **只预处理分流**：判断待迁移点是否为汇编代码，决定走轻量 C/C++ 迁移还是完整闭环。
- **只扫描**：生成/更新 `scan_result.json`，用于盘点迁移点（仅汇编代码需要）。
- **只查知识库**：回答"指令/扩展/约束/intrinsic 对应关系"等，并给证据链。
- **只迁移**：按条目改代码，但仍会在关键点主动查库并在每轮改动后引导验证。
- **只验证**：准备工具链/QEMU，运行测试/对比，定位不一致并回流修复。
- **只做性能分析（llvm-mca）**：对已通过验证的手写/RVV 汇编或 intrinsic 热点，用 `llvm-mca` 做静态吞吐/瓶颈分析，给出优化建议与回归验证指导（详见阶段 E）。

## 阶段 0：预处理与分流（入口，先于阶段 A）

在正式扫描之前，先判断待迁移点 **是否属于汇编代码**，据此决定后续走哪条路径。

### 判定依据

满足以下任一条件即视为 **汇编代码**（走完整闭环 A→E）：

1. **文件类型**：`.S` / `.s` / `.asm` 等汇编源文件。
2. **内联汇编**：C/C++ 源文件中含 `__asm__` / `__asm` / `asm volatile` 且迁移点恰好落在该代码段内。
3. **intrinsic 热点**：源码中含 x86 intrinsic（如 `_mm_*` / `_mm256_*` / `_mm512_*`）或 ARM intrinsic（如 `vld1q_*` / `vmlaq_*` / `vst1q_*` 等 NEON intrinsic），且属于性能热点。
4. **编译器内置函数（built-in）**：源码中含 `__builtin_*` 且与架构强绑定（如 `__builtin_ia32_*`、`__builtin_neon_*`）。

**不满足上述条件**（纯 C/C++ 逻辑、标准库调用、无架构绑定的普通代码）视为 **非汇编代码**，走轻量路径。

### 分流路径

| 判定结果 | 后续路径 | 说明 |
|----------|----------|------|
| **非汇编代码** | **阶段 0-B（轻量迁移）** | 直接将 C/C++ 代码迁移到在 RISC-V 上可编译运行的实现；确保 `-march` 含必要扩展后交叉编译即可，通常不需要 llvm-mca 性能分析。 |
| **汇编代码 / intrinsic 热点** | **阶段 A → B → C → D → E（完整闭环）** | 进入完整的扫描→迁移→查库→验证→性能分析闭环。 |

### 阶段 0-A：汇编代码确认与登记

对于判定为汇编代码的迁移点，登记以下信息以便后续阶段引用：

- `file_path`：源文件路径。
- `start_line` / `end_line`：汇编段行号范围。
- `asm_type`：`inline_asm` / `standalone_asm` / `intrinsic` / `builtin`。
- `arch_source`：`x86` / `x86_64` / `arm` / `aarch64`。
- `brief`：一句话描述该汇编段做什么（如 "NEON 8x16 向量加法"）。

### 阶段 0-B：非汇编代码的轻量迁移

对于非汇编代码，按以下轻量步骤完成迁移（无需走 A→E 完整闭环）：

1. **代码适配**：修改架构相关的宏、头文件、编译选项，使 C/C++ 源码在 RISC-V 上可编译。常见改动：
   - 替换 `#ifdef __x86_64__` / `#ifdef __ARM_ARCH` 等架构宏为 RISC-V 等价条件（如 `#ifdef __riscv`）。
   - 移除或替换 x86/ARM 专有头文件（如 `<immintrin.h>`、`<arm_neon.h>`）。
   - 确认字节序、对齐、数据类型宽度等假设在 RISC-V（默认小端、`long` 为 64-bit on RV64）下仍成立。
2. **交叉编译验证**：用 RISC-V 交叉编译器编译目标文件，确保无编译错误（准备环境见阶段 D）。
3. **功能验证**：在 QEMU（`qemu-riscv64 -cpu max`）中运行单元测试，确认行为与原始 x86/ARM 实现一致。
4. **完成**：测试通过即视为迁移完成；**不需要** llvm-mca 性能分析（除非后续发现该代码段实际是热点）。

**进度自检**：已对每个待迁移点完成分流判定；非汇编代码已完成阶段 0-B 并通过交叉编译 + QEMU 验证；汇编代码已登记信息并准备进入阶段 A。

## 阶段 A：工程扫描（仅针对阶段 0 判定为汇编代码的迁移点）

1. 在 **目标工程根目录**（用户指定或当前仓库根）定位固定输出：`<project_root>/scan_result.json`。
2. **若 `scan_result.json` 已存在**：视为扫描已完成，**不要**再跑扫描脚本，直接读该文件进入阶段 B（除非用户明确要求重新扫描——此时先备份或删除该文件再扫描）。
3. **若不存在**：直接使用包装脚本执行扫描（脚本会自举依赖；不需要 venv）。扫描范围为阶段 0 登记的汇编代码迁移点。
   - 推荐（自动装依赖）：`<skill_root>/scripts/run_scan.sh <project_root> -o <project_root>/scan_result.json`
   - 兜底（手动）：`python3 -m pip install -r <skill_root>/scripts/requirements.txt && python3 <skill_root>/scripts/riscv_scan <project_root> -o <project_root>/scan_result.json`
4. **若 riscv_scan 脚本执行失败**：确认当前解释器已安装 `scripts/requirements.txt` 中的依赖；必要时在技能目录重装依赖或换用满足版本要求的 Python。
5. 若工程暂无可用扫描实现：由代理按 [referens/project_scan.md](referens/project_scan.md) 中的 **JSON schema** 手工/static 分析填写 `scan_result.json`，再进入阶段 B。

**进度自检**：`scan_result.json` 存在且可读，`suggestion_class` / `missing_class` 覆盖当前要处理的迁移范围。

## 阶段 B：迁移点迁移（针对 `suggestion_class` / `missing_class` 中的条目）

对 **单个文件或单条条目**，严格按顺序执行（细节与编码/编译/测试/llvm-mca 见 [referens/code_migrate.md](referens/code_migrate.md)）：

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

## 脚本

- `scripts/riscv_scan`：扫描引擎入口（输出 `scan_result.json`，schema 见 [referens/project_scan.md](referens/project_scan.md)）。
- `scripts/query.py`：通过 RISC-V-DOC-RAG MCP 知识库服务查询 ISA/Intrinsic 手册（输出包含 `file_path/header_path`）。
- `scripts/run_scan.sh`：扫描入口（自动安装依赖）。
- `scripts/run_query.sh`：查询入口（自动安装依赖）。
- `scripts/prepare_verify_env.sh`：准备验证环境（从 `resources/` 部署/配置 RISC-V 工具链与 QEMU user-static，尽量不联网下载）。
- `resources/llvm_mca_env.sh`：本机无 `llvm-mca` 时拉取并解压 llvm-mca 工具包，写入 `PATH`/`env.d`；阶段 E 性能分析前按需执行。
