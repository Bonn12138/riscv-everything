# 迁移点迁移（code_migrate）

针对 `scan_result.json` 中的某条条目（通常来自 `suggestion_class[]` / `missing_class[]` 或单文件内若干点），先按 **前三步** 完成迁移与功能性对比；涉及 **手写汇编 / RVV 汇编 / intrinsic 热点** 且第三步已通过时，须再执行 **第四步（llvm-mca，阶段 E）**（见本文第四节）。可与用户约定一次处理一条或整文件。

## 工具与知识

- 指令/扩展不熟：**优先 riscv-doc-mcp**；无 MCP 时在技能根目录用 **`scripts/query.py`**（需先 `python3 -m pip install -r scripts/requirements-mcp.txt`）。**禁止凭感觉编指令**。
- 列工具：MCP 客户端工具列表，或 `python3 scripts/query.py --list-tools`。常用四组知识库：`search_core_isa_manuals` / `search_rvv_vector_extensions` / `search_special_instructions` / `search_docs_tools`。
- 任务开始前可简述：清单、算法要点、拟用指令或内建函数。

```bash
python3 scripts/query.py --list-tools
python3 scripts/query.py -t search_core_isa_manuals -q "vadd.vv"
python3 scripts/query.py -t search_rvv_vector_extensions -q "__riscv_vsetvl"
```

## 第一步：单元测试（源实现 + RISC-V 共用）

- **按需准备环境**：要跑扫描类脚本时，在技能目录执行 `python3 -m pip install -r scripts/requirements.txt`。要编译/运行 **x86_64 或 ARM 原生** 测试时，在技能目录执行 `bash resources/x86_toolchain_env.sh` 或 `bash resources/arm_toolchain_env.sh`，再 **`source resources/env.sh`**（新终端需重做）。若缺系统级依赖（头文件、动态库等），用发行版包管理器补齐。
- 为迁移点编写或补全 **单元测试**：同一套测试既能编 **原始 x86/ARM**，也能在后续用于 **RISC-V**。
- 断言可比对结果（返回值、缓冲区、checksum 等）。**无可用测试则不应结束迁移。**

## 第二步：RISC-V 代码迁移

- **按需准备环境**：要编译或本地试编 RISC-V 时，执行 `bash resources/riscv_toolchain_env.sh`，并 **`source resources/env.sh`**。
- 读懂 x86/ARM 语义与边界；设计 RISC-V（寄存器、RVV `vl`、mask 等），**尽量用向量扩展**对标原向量实现。
- 新增源文件命名带 **`_riscv`** 后缀（与工程约定冲突时在说明里写清）。
- **编码规则**：算法一致；向量汇编 → **RVV 汇编**；向量 intrinsic → **RVV 1.0 intrinsic**；标量 → RISC-V 标量汇编可接受。**禁止**为省事把汇编整块改成纯 C（除非用户明确允许）。

## 第三步：执行对比与修复

- **按需准备环境**：用 **`qemu-riscv64`** 等 user-static 跑 RISC-V 二进制前，执行 `bash resources/qemu_static_env.sh`，并 **`source resources/env.sh`**。
- 分别构建并运行原生与 RISC-V 测试；RISC-V 侧常用 `qemu-riscv64 -cpu max <bin>`（以项目为准）。ARM 侧对比可用 `qemu-aarch64 -cpu max <bin>`。
- 对比输出；不一致则优先改 RISC-V 侧（或测试/构建脚本）直到一致。

## 第四步（阶段 E）：llvm-mca 性能分析与改进

**适用范围**：条目含 **手写汇编**、**RVV 汇编** 或 **intrinsic 热点循环**，且上文第三步（及项目要求的 QEMU/构建对比）已通过。

**目标**：用 `llvm-mca` 对热点段做静态吞吐/瓶颈分析，据此小步优化；**每轮优化后必须重复第三步**（同一组测试、输出一致），禁止只追性能导致语义回归。

**完成优化后**：清理临时产物，只保留 **最终** RISC-V 源文件与必要构建改动。

### 宿主与目标分工

- `llvm-mca` 运行在 **开发机（x86_64 / aarch64 等）**，输入是 **RISC-V 目标 ISA** 的汇编文本；不需要在 RISC-V 真机或 QEMU 内安装 `llvm-mca`。
- 生成待分析汇编时，须与工程 **真实交叉构建** 使用同一套 triple、`-march`、`-mabi`、`-mcmodel` 等（见下文「交叉编译」）。

### 本机没有 llvm-mca 时：`resources/llvm_mca_env.sh`

- 若 `command -v llvm-mca` 失败，在技能根执行：
  - `bash resources/llvm_mca_env.sh`  
  或当前 shell：`source resources/llvm_mca_env.sh`
- 脚本按 `uname -m` 选择 x86 或 arm 制品包，下载到 `<skill_root>/resources/llvm-mca/`（或复用已下载的 tar），解压后将 `llvm-mca` 所在目录加入 `PATH`，并写入 `resources/env.d` 下的 env 片段。
- **覆盖制品 URL**（与脚本约定一致）：`LLVM_MCA_URL`、`LLVM_MCA_X86_URL`、`LLVM_MCA_ARM_URL`。内网/离线可事先把对应 `llvm-mca-*.tar` 放到脚本期望路径，跳过下载后仍走解压与 `PATH` 配置。
- 交叉 `clang` 与宿主 `llvm-mca` 尽量同属 **相近 LLVM 大版本**，减少汇编语法/指令识别差异。
- **LLVM 版本要求**：
  - 本技能提供的 `llvm-mca` 基于 **LLVM 22.x**（截至编写时），支持 RISC-V V 扩展、Zvbc、Zvbb 等较新扩展。
  - 若项目交叉编译器为 LLVM 17 及以下，部分新扩展指令可能在 llvm-mca 中无法识别。
  - 建议交叉 `clang` 版本 ≥ **LLVM 18**；版本差异过大时，可先用 `-S` 输出汇编，手工确认指令助记符是否兼容后再喂入 llvm-mca。
  - 可通过 `llvm-mca --version` 和 `<RISCV_CLANG> --version` 交叉比对版本号。

### 交叉编译：如何得到 `hot.s`

- **不要用宿主默认 `clang`** 冒充 RISC-V，除非工程本就如此构建。应使用阶段 D / `resources/riscv_toolchain_env.sh` 提供的 **`riscv64-*-clang`**（或项目指定的交叉编译器），并带上与 **Makefile / CMake** 完全一致的优化级别与机器选项，例如：
  - `<RISCV_CLANG> -O3 -S -march=… -mabi=… -mcmodel=…（及其它与正式编译相同的 flag）-o hot.s hot.c`
- 若已有目标文件，也可用 `llvm-objdump -d` 截取循环片段再分析；注意 `llvm-mca` 对 **LLVM 汇编语法** 最友好，反汇编文本可能需要手工整理成可解析片段。
- **手写迁移后的 `.S/.s`**：可直接作为输入，只要指令集与工程 `-march` 一致。
- **⚠️ 注释格式警告**：LLVM MC 汇编解析器（llvm-mca 底层使用的解析器）在 `.text` 段内**不支持行尾 `/* */` 风格注释**，也不支持独立成行的 `/* ... */` 注释。这是 GNU as 与 LLVM MC 的已知差异，会导致 `unexpected token` 错误。
  - ✅ **正确**：`vadd.vv v2, v0, v0  // byte-reverse` 或 `# 注释`
  - ❌ **错误**：`vadd.vv v2, v0, v0  /* byte-reverse */`
  - 文件头部的多行 `/* ... */` 块（在 `.text` 之前）通常不受影响，但 `.text` 段内必须使用 `//` 或 `#`。
  - 如果无法修改源文件，可使用 `--skip-unsupported-instructions=parse-failure` 参数跳过解析失败的行（会丢失对应指令的分析）。

### `llvm-mca` 参数与 `-march` 对齐

- `-mtriple`：与交叉产物一致（如 `riscv64-unknown-linux-gnu` 与 `riscv64-none-elf` 不可混用）。
- `-mattr`：与 `-march` 中启用的扩展对应（如 `+v`、`+zvbc` 等）；扩展名不确定时回到知识库（MCP / `query.py`）查证后再写。
- `-mcpu`：影响调度模型。**注意：`generic` / `generic-rv64` 在 RISC-V 目标上没有调度模型，会导致 llvm-mca 报错退出。** 必须选择有调度模型的具体 CPU（见下表）。

#### 推荐的 `-mcpu` 值（按微架构类型）

| `-mcpu` 值　　　| 微架构类型　　 | 发射宽度 | 适用场景　　　　　　　　　　　　　　 |
| -----------------| ----------------| ----------| --------------------------------------|
| **`zhufeng2`**　| **六发射乱序** | **6**　　| **自研朱峰2号芯片**　　　　　　　　　|
| `rocket-rv64`　 | 单发射顺序　　 | 1　　　　| 资源极度受限的嵌入式场景　　　　　　 |
| `sifive-u74`　　| 双发射顺序　　 | 2　　　　| 顺序核基线评估（SiFive U74 / FU740） |
| `andes-ax45mpv` | 多核顺序　　　 | 1　　　　| Andes AX45MPV 平台　　　　　　　　　 |
| `sifive-p450`　 | 三发射乱序　　 | 3　　　　| 乱序核性能评估（SiFive P450）　　　　|
| `sifive-p550`　 | 三发射乱序　　 | 3　　　　| SiFive P550 平台　　　　　　　　　　 |
| `spacemit-x60`　| 乱序　　　　　 | —　　　　| Spacemit X60 平台　　　　　　　　　　|
| `sifive-x280`　 | 向量加速　　　 | —　　　　| 重度 RVV 向量场景　　　　　　　　　　|

> **选择原则**（按优先级从高到低）：
> 1. **用户 prompt 中明确指定了 `-mcpu` 或目标芯片** → 使用用户指定的值，不再覆盖。
> 2. **用户未指定** → 优先使用 `zhufeng2`（自研朱峰2号，六发射乱序）作为默认分析目标。
> 3. 若 `zhufeng2` 不适用（如明确面向第三方平台），则按实际目标部署芯片匹配对应 `-mcpu`。
> 4. 目标芯片完全不确定时，用 `sifive-p450`（乱序通用基线）或 `sifive-u74`（顺序通用基线）做优化前后的相对对比。
> 5. **不要用顺序核模型分析本应在乱序核上运行的代码**（反之亦然），两者对同一组向量指令的延迟/吞吐建模差异巨大，结论可能严重误导。
> 6. 可用 `llvm-mca --march=riscv64 --mcpu=help` 列出所有支持的 CPU。

### 命令模板

只截取 **最小热点循环** 所在片段，避免整文件喂入。

#### 如何从完整汇编中提取热点片段

```bash
# 方法1：手写 .S 时只保留核心循环体（推荐）
# 去掉 prologue/epilogue、callee-saved 保存/恢复、尾部处理等，
# 只保留最内层循环的指令序列。

# 方法2：用 llvm-objdump 从编译产物中提取特定函数
llvm-objdump -d --start-address=0x<start> --stop-address=0x<end> binary | grep -v '^$' > hot.s

# 方法3：用 sed 按行号截取（适用于手写 .S 文件）
sed -n '100,200p' full.S > hot.s

# 方法4：直接分析完整手写 .S 文件（含伪指令），配合 --skip-unsupported
# 注意：整文件喂入会让 llvm-mca 把 prologue/epilogue 也算入性能统计，
# 结果可能偏低。仅适用于无法提取片段的场景。
```

```bash
# 快速分析（已有 RISC-V 汇编）
llvm-mca -mtriple=riscv64 -mcpu=sifive-p450 -mattr=+v,+zvbc --all-stats --iterations=100 < hot.s

# 深度分析（含瓶颈分析 + 时间线视图）
llvm-mca -mtriple=riscv64 -mcpu=sifive-p450 -mattr=+v,+zvbc \
  --bottleneck-analysis --all-views --timeline --timeline-max-iterations=10 \
  --iterations=100 < hot.s

# C/intrinsic：交叉 clang 管道到宿主 llvm-mca（flags 与工程构建一致）
<RISCV_CLANG> -O3 -S -target riscv64-unknown-linux-gnu -march=rv64gcv_zvbc -mabi=lp64d hot.c -o - \
  | llvm-mca -mtriple=riscv64-unknown-linux-gnu -mcpu=sifive-p450 -mattr=+v,+zvbc --all-stats

# 若汇编含伪指令/标签跳转导致解析失败，加 --skip-unsupported-instructions=parse-failure 跳过
llvm-mca -mtriple=riscv64 -mcpu=sifive-p450 -mattr=+v \
  --skip-unsupported-instructions=parse-failure --all-stats < full_file.S
```

（`-target`/`-march`/triple 按项目替换；RVV 等扩展必须在 clang 与 `llvm-mca` 两侧一致。`-mattr` 中的扩展如 `+zvbc` 须按实际使用的指令集启用。）

### 结果解读与常见优化方向（小步、可回归）

#### 关键输出指标含义

| 指标 | 含义 | 理想范围 |
|---|---|---|
| **IPC** (Instructions Per Cycle) | 每周期执行指令数；越高越好 | 接近 Dispatch Width 为优 |
| **Block RThroughput** | 单次循环迭代的吞吐周期下限 | 越低越好 |
| **Total Cycles** | N 次迭代的总周期 | 用于优化前后对比 |
| **Dispatch Width** | 处理器每周期最大派发微操作数 | 由 `-mcpu` 决定，不可改 |
| **uOps Per Cycle** | 每周期派发的微操作数 | 越接近 Dispatch Width 越好 |
| **Resource Pressure** | 各执行端口的占用率 | 无单一端口过载（<80%） |

#### 如何判断瓶颈

1. **IPC 远低于 Dispatch Width** → 存在数据依赖或资源瓶颈；查看 `Dynamic Dispatch Stall Cycles`。
2. **某个 Resource Pressure 列值极高** → 对应端口过载；考虑用等价低压力指令替换。
3. **Average Wait times 中 Ready 等待时间长** → 调度器压力；考虑指令重排。
4. **`No resource or data dependency bottlenecks`** → 已接近该 CPU 的理论最优。

#### 常见优化方向

- **依赖链 / 关键路径过长**：重排、拆分累加链、多累加器、适度展开或软件流水。
- **端口或资源压力**：减少不必要变宽/变窄、跨 lane shuffle；换等价低压力指令形态。
- **RVV**：合并或外提 `vsetvli`，减少 SEW/LMUL 频繁切换；批处理同配置向量段。
- **访存**：对齐与批量加载、减少标量-向量往返、避免非必要的 gather/scatter。

#### 优化终止条件

当满足以下条件时，可认为该轮优化已足够：
- IPC 达到 Dispatch Width 的 **70% 以上**。
- Block RThroughput 不再随优化显著下降（连续两轮差距 < 5%）。
- `No resource or data dependency bottlenecks` 出现。
- 每次优化后必须回到第三步验证正确性，正确性不可妥协。

### 闭环产出（必须）

- **性能**：每轮保留 `llvm-mca` 输出中的关键摘要（吞吐、周期估计、瓶颈提示等），便于与上一轮对比。
- **正确性**：每轮修改后重复第三步（构建 + QEMU/对比测试），输出不一致则先修语义再继续调性能。

## 编译与链接（约定，可按项目改）

1. 在 **Makefile / CMake** 等中增加 riscv64 交叉目标。
2. 修编译错误至通过；缺什么装什么（系统包 + 上列 `resources/*.sh` 已覆盖的工具链/QEMU）。
3. `-march` 覆盖所用扩展；常见起点：`-march=rv64gcv_zbb_zbc_zvbc_zvkb_zvksed -mabi=lp64d`，不够再补。
4. 项目若要求 **静态链接**，遵守之。
5. 若任务固定产物名（如 `riscv64_test`），遵守任务说明。

编译器前缀以 `*_TOOLCHAIN_ROOT/bin` 下实际文件为准（如 `riscv64-unknown-linux-gnu-gcc`、`aarch64-unknown-linux-gnu-gcc`、x86 交叉前缀等）。

## 完成说明

向用户简短总结：处理了哪些条目、改了哪些文件、测试与对比命令及结果。若做过第四步，附带 **llvm-mca 前后对比要点** 与回归测试结论。除非用户要固定模板，否则不必单独写 `output.md`。
