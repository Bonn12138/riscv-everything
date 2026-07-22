---
description: 按三大阶段推进 RISC-V（含 RVV）迁移：阶段一扫描、阶段二按条目分流迁移、阶段三工程级交叉编译；性能分析（llvm-mca）作为独立 agent 仅在迁移完成后启动。迁移点状态以 scan_result.json 的 status/marking 字段维护。
argument-hint: [scan_result.json]
---

# /everything-riscv:migrate — 启动迁移流程

> **零交互自主运行**：本命令执行期间**默认不向用户提问**。所有决策（目标工程根目录、是否重扫、汇编 vs intrinsic、`-mcpu`/`-march`、测试补全、是否进入阶段四、错误自修复等）由 agent 按 [`skills/riscv-migrate/SKILL.md`](../skills/riscv-migrate/SKILL.md)「自主运行原则」默认处理；只有白名单 5 类场景（找不到工程根 / 条目级 5 轮不一致 / 编译连错 3 次 / 内网不通 / 用户主动问）才请示。**禁止**在 `-mcpu`、`-march`、`llvm-mca` 是否安装、汇编 vs intrinsic 等典型决策上向用户请示。

按**三大阶段**推进 RISC-V 迁移：

| 阶段 | 名称 | 产出 |
| ---- | ---- | ---- |
| **一** | 扫描迁移点 | `scan_result.json`（每条目 `status="TODO"`） |
| **二** | 按迁移点分流迁移 | `*_riscv` 源码 + 通过 QEMU 验证；条目 `status="DONE"` + `marking` |
| **三** | 工程级交叉编译 | RV 可执行程序（QEMU 可运行） |
| **四**（独立 agent） | 性能分析与优化 | llvm-mca 报告 + 优化提交；更新条目 `marking` |

每个迁移点的进度以 `scan_result.json` 中的结构化字段同步（**权威源在 JSON，不在源码注释**）：

- `status`（三态互斥）：`TODO` / `START` / `DONE`
- `marking`（备注/异常/性能说明）：`status=DONE` 时必填

阶段二全部条目 `status=DONE` 后才能进入阶段三；阶段三产物可运行后才召唤阶段四性能分析 agent。

## 前置条件

- 已运行 `/riscv:scan` 产出 `scan_result.json`
- 或手动构造合法的 `scan_result.json`（每条目带 `status` 与 `marking` 字段）

## 执行流程

### 阶段一：扫描迁移点（若尚未执行）

如未检测到 `scan_result.json`，先调用 `/riscv:scan` 完成扫描。

### 阶段二：按迁移点分流迁移（核心闭环）

加载 `scan_result.json`，对**每个条目**严格按 `分流 → 迁移 → 知识库 → 验证` 串行执行：

1. **加载清单**：读取 `scan_result.json`（如果 `$ARGUMENTS` 指定了路径则使用该路径，否则在当前目录查找）；筛选 `status != "DONE"` 的条目作为本次处理范围。
2. **逐条目分流**：
   - 按 `solver_type` + 源码特征判断：汇编（`.S`/`.s`/`.asm`、内联汇编、intrinsic 热点、`__builtin_ia32_*`/`__builtin_neon_*`、`InlineAsm`/`Builtin`）→ 完整子闭环；其余（源码/Shell/宏/Toml、纯 C/C++ 逻辑）→ 轻量子流程。
3. **逐条目迁移**：
   - 测试先行：为原始 x86/ARM 与即将编写的 RISC-V 实现补齐可运行单元测试。
   - 新增源文件命名带 `_riscv` 后缀。
   - 算法一致、语义一致；向量源必须用 RVV（汇编或 intrinsic，RVV 1.0），不允许把汇编问题退化成纯 C 替代。
   - **写 JSON**：进入条目时把 `scan_result.json` 中该条目的 `status` 从 `TODO` 改为 `START`（`marking` 留空）。
4. **主动查知识库**（汇编条目必走；非汇编条目按需）：遇到指令名 / 扩展名 / `__riscv_*` / ABI / CSR 字段时查询 MCP 知识库（`search_core_isa_manuals` / `search_rvv_vector_extensions` / `search_special_instructions` / `search_docs_tools`），输出证据链（`file_path` / `header_path`）。
5. **生成 RISC-V 替代代码**并写入目标文件。
6. **自动触发验证指导**：给出编译命令与预期产物。验证所需的 RISC-V 工具链与 QEMU 由技能侧自动部署并加载。
7. **验证通过后更新 JSON**（必须）：
   - 把 `scan_result.json` 中该条目的 `status` 从 `START` 改为 `DONE`
   - 同时填写 `marking`：
     - 正常：`无异常，性能持平`
     - 异常：`语义差异：<具体点>，已通过测试规避` 或 `TODO(后续)：<具体项>`
     - 性能影响：`性能低于原实现 N%，原因：<…>，建议进入阶段四 llvm-mca 优化`
   - **不要**在源码里写 `// [MIGRATE-*]` 注释

### 阶段三：工程级交叉编译

阶段二所有条目 `status="DONE"` 后，用 RISC-V 交叉工具链编译整个工程：

1. 自动加载 RISC-V 工具链：`<skill_root>/resources/riscv_toolchain_env.sh` + `source <skill_root>/resources/env.sh`
2. 在工程里跑完整构建（`make` / `cmake --build`），修编译错误至通过：
   - 缺什么装什么（系统包 + `<skill_root>/resources/*.sh`）
   - 残留未替换的架构宏（`__x86_64__` / `__ARM_ARCH`）回阶段二补
   - 头文件路径、链接库、ABI（`lp64d`）按工程实际调整
   - 常见起点：`-march=rv64gcv_zbb_zbc_zvbc_zvkb_zvksed -mabi=lp64d`
3. 用 `qemu-riscv64 -cpu max <bin>` 跑主流程/集成测试，至少确认主程序可启动、无立即崩溃、关键路径输出与 x86/ARM 侧一致。

### 阶段四（独立 agent）：性能分析与优化

阶段三产物可运行后，**主动召唤** `riscv-asm-analyzer` agent：

- 用 `llvm-mca` 对热点做静态吞吐/瓶颈分析；如本机无 `llvm-mca`，**agent 应自主调用 `bash <skill_root>/resources/llvm_mca_env.sh` 安装**（详见 `agents/riscv-asm-analyzer.md` 的「llvm-mca 自动安装」一节）。
- `-mcpu` 选择优先级：用户指定 > `zhufeng2`（默认）> 目标部署芯片 > `sifive-p450`/`sifive-u74`。**不要用 `generic`**。
- 每轮优化后必须回到阶段二 2.4 验证（构建 + QEMU/对比测试），禁止只追性能导致语义回归。
- 优化终止条件：IPC 接近 Dispatch Width、`Block RThroughput` 不再显著下降、出现 `No resource or data dependency bottlenecks`。
- 优化通过后**必须**回到 `scan_result.json` 对应条目的 `marking` 字段标注：`经阶段四 llvm-mca 优化后性能 <提升 N%>；关键改动：<具体点>`

## 汇总报告

完成条目数（`status=DONE`）、跳过条目数、需人工确认条目数、阶段三产物路径、阶段四是否触发及触发结果。

## 约束

- 禁止猜测指令 / 扩展对应关系，必须查知识库
- 每次改动后主动推进验证步骤
- 每个迁移点的状态以 `scan_result.json` 中 `status` / `marking` 字段为唯一权威源；源码不写 `MIGRATE-*` 注释
- 不依赖虚拟环境，使用系统 Python + 脚本自举依赖
- 阶段二全部 `status=DONE` 后才进入阶段三；阶段三通过后才进入阶段四
- 性能分析不在阶段二内嵌，作为独立 agent 在阶段三后召唤