# 迁移点迁移（code_migrate）

本文对应 `riscv-migrate` 技能**阶段二 2.2 / 2.3 / 2.4 / 2.4.5** 的实施细则，并衔接**阶段三（工程级交叉编译 + 向量化审计）**与**阶段四（llvm-mca 性能分析，独立 Subagent）**。扫描与分流依据见 [project_scan.md](project_scan.md)，性能度量口径见 [perf_measure.md](perf_measure.md)。

## 在四大阶段中的位置

```
阶段一(扫描) → 阶段二(本文档核心)
                  ├─ 2.1 分流（含 AutoVecCandidate）
                  ├─ 2.2 迁移(主 Agent 将 status: TODO → START)
                  ├─ 2.3 知识库查询
                  ├─ 2.4 验证 (主 Agent 将 status: START → DONE + 填 marking/perf)
                  └─ 2.4.5 快速 mca 性能门禁 (asm/RVV/AutoVec 条目)
              → 阶段三(工程级交叉编译 + 3.4 向量化审计)
              → 阶段四(独立 Subagent: riscv-asm-analyzer, 默认执行)
```

每个条目的状态以 `scan_result.json` 中的 `status`、`marking`、`perf` 三个结构化字段同步（**权威源在 JSON，不在源码注释**）。详见末节「迁移点状态字段规范」。

---

## x86/ARM → RISC-V 指令映射速查表（迁移时先查这里）

> **用法**：迁移 intrinsic/asm 条目时先在下表找对应行；表内没有的模式再查知识库（2.3）。表的存在意义是——**扩展缺失很多时候不报编译错误，只是 agent 不知道有专用指令而写成通用序列**，性能直接损失。"-march 起点"已含下表全部扩展（见 SKILL.md A 节 #7）。

### 位操作 / 标量

| x86 / ARM | 语义 | RISC-V 落点 | 扩展 |
| --------- | ---- | ----------- | ---- |
| `_mm_popcnt_u32/64` / ARM `vcnt` | 位计数 | `cpop`（或 RVV `vcpop`） | Zbb |
| `__builtin_clz` / `_lzcnt_u32/64` | 前导零 | `clz`（RVV `vclz`） | Zbb |
| `__builtin_ctz` / `_tzcnt_u32/64` | 尾随零 | `ctz`（RVV `vctz`） | Zbb |
| `_bswap_32/64` | 字节反转 | `rev8`（RVV `vrev8`） | Zbb / Zbkb |
| `_rotl64` / `_rotr64` / ARM `ror` | 循环移位 | `rol` / `ror`（RVV `vrolnor`→`vror`） | Zbb |
| `_andn_u64`（BMI） | 与非 | `andn` | Zbb |
| PCLMULQDQ / ARM `vmull_p64`（标量） | 无进位乘法 | `clmul` / `clmulh` / `clmulr` | Zbc |
| `_blsi`/`_blsmsk`/`_blsr`（BMI） | 位隔离 | `min`/`and` 组合 或查库 | Zbb（部分需组合） |

### 向量（SIMD → RVV）

| x86 / ARM NEON | 语义 | RISC-V 落点 | 扩展 |
| -------------- | ---- | ----------- | ---- |
| `_mm_loadu_si128` / `vld1q_s8` | 向量加载 | `vle8/16/32/64.v`（`__riscv_vle...`） | V |
| `_mm_storeu_si128` / `vst1q_s32` | 向量存储 | `vse8/16/32/64.v` | V |
| `_mm_add_epi32` / `vaddq_s32` | 向量加 | `vadd.vv/vx` | V |
| `_mm_madd_epi16` / `vmlal_s16` | 乘加 | `vmacc.vx/vv` / `vmadd` | V |
| `_mm_max_epi8` / `vmaxq_s8` | 向量最值 | `vmax/vmin.vv/vx` | V |
| `_mm_shuffle_epi8`（SSSE3） | 字节查表重排 | `vperm.vv`（或 `vrgather`） | Zvbb / V |
| `_mm_slli_epi32` / `vshlq_n_s32` | 向量移位 | `vsll/vsrl/vsra.vi/vx/vv` | V |
| `_mm_cmpeq_epi32` / `vceqq_s32` | 比较→mask | `vmseq/vmslt...` + mask 操作 | V |
| `_mm_blendv_epi8` / `vbslq_u8` | 按 mask 混合 | `vmerge.vvm/vxm` | V |
| `_mm_extract_epi8` / ARM lane 提取 | lane 提取 | `vmv.x.s` / `vslide1down` | V |
| AVX gather / `vld1q_gather` | 收集访存 | `vluxei/vloxei`（注意延迟，慎用） | V |
| `_mm512_*`（AVX-512 mask 语义） | 掩码操作 | mask 寄存器 `v0.t` 尾部语义（`ta`/`ma`） | V |

### 加密（高收益区）

| x86 / ARM | 语义 | RISC-V 落点 | 扩展 |
| --------- | ---- | ----------- | ---- |
| `_mm_crc32_u8/16/32/64`（SSE4.2） | CRC32 | `vclmul.vv` 多系数并行（RVV 版优于逐字节标量） | Zvbc（`vclmul`）/ Zbc（标量 `clmul`） |
| `_mm_aesenc_si128` 等 AES-NI 全家 | AES 轮函数 | `vaesef/vaesdf/vaesem/vaesdm.vs/vv` + `vaesz.vs` | **Zvkned** |
| ARM `vaeseq_u8` / `vaesmcq_u8` | AES | 同上 | Zvkned |
| SHA-NI（`_mm_sha256rnds2_epu32`）/ ARM `vsha256hq_u32` | SHA-256 | `vsha2cl/vsha2ch.vs` + `vshatri` | **Zvksh** |
| ARM `vsha1*`（SHA-1 四件套） | SHA-1 | 同 Zvksh（`vsha2*` 支持 SHA-1/2 双模式） | Zvksh |
| ARM `vsm3tt1q`/`vsm3tt2q`（SM3） | SM3 | `vsm3c/vsm3me.vv/x` | **Zvksh**（SM3 子集） |
| ARM `vsm4ekeyq_u32`（SM4） | SM4 | `vsm4k.vi` / `vsm4r.vs/vv` | **Zvksed** |
| SHA-384/512（ARM `vsha512*`） | SHA-512 | `vsha2cl/vsha2ch`（SEW=64 模式） | **Zvknha/zvknhb**（zbkb+zvksh 组合） |
| 通用 GF 乘 / GCM 域乘 | GF(2^128) | `vclmul` + `vgmul` 组合（查库确认目标扩展集） | Zvkg（若目标支持） |

> **注意**：SHA-512 需要 **SEW=64 模式**，即 `zvknhb`（`zvknha` 只支持 SEW=32 的 SHA-256 轮函数）。A 节 #7 的 `-march` 起点只含 `zvknha`，迁移 SHA-384/512 条目时须手工追加 `zvknhb`。

> **注意**：向量 crypto 的 intrinsic 覆盖在 GCC 14.3 上不全（部分 `__riscv_vaes*/vsha2*` 缺失或需更高版本）。intrinsic 编不过时按 SKILL.md「分层实现策略」第三层判据**升级手写汇编**（`vaesef.vv` 等指令本身汇编器完整支持），并在 `perf.ab_variants.rejected_reason` 记录"intrinsic 不可用"证据。

---

## 工具与知识

- 指令/扩展不熟：**先查上文映射速查表**；表内没有再 **优先 riscv-doc-mcp**；无 MCP 时在技能根目录用 **`scripts/query.py`**（需先 `python3 -m pip install -r scripts/requirements-mcp.txt`）。**禁止凭感觉编指令**。
- 列工具：MCP 客户端工具列表，或 `python3 scripts/query.py --list-tools`。常用四组知识库：`search_core_isa_manuals` / `search_rvv_vector_extensions` / `search_special_instructions` / `search_docs_tools`。
- 任务开始前可简述：清单、算法要点、拟用指令或内建函数。

```bash
python3 scripts/query.py --list-tools
python3 scripts/query.py -t search_core_isa_manuals -q "vadd.vv"
python3 scripts/query.py -t search_rvv_vector_extensions -q "__riscv_vsetvl"
```

---

## 阶段二 2.2：迁移（按分流结果）

### 通用要求

- **测试先行**：为**原始 x86/ARM 实现**与**即将编写的 RISC-V 实现**补齐或编写可运行的单元测试（同一行为、可对比输出或 checksum）。无测试不得宣称迁移完成。
- **A/B 对比测试**：asm/RVV/AutoVec 条目测试必须含标量基线版 + RVV/汇编版，用 `resources/perf_cnt.h` 输出 `instret_per_elem`（口径见 [perf_measure.md](perf_measure.md)）。
- **新增源文件命名带 `_riscv` 后缀**（与工程约定冲突时在说明里写清）。
- **算法一致、语义一致**；向量源必须用 **RVV**（汇编或 intrinsic，RVV 1.0），不得把汇编问题退化成纯 C 替代。
- **分层实现（性能优先）**：默认 intrinsic + 编译参数调优；`tier=hot` 条目 intrinsic/asm 并行产出 A/B 择优；mca 证据显示编译器劣化时升级手写汇编（判据见 SKILL.md「默认策略」）。
- **状态字段同步**：**主 Agent** 在分派任务前把 `scan_result.json` 中该条目的 `status` 从 `TODO` 改为 `START`；迁移 Subagent 不写 JSON。完成并验证后由主 Agent改为 `DONE` 并填 `marking`（+ asm/RVV/AutoVec 条目的 `perf`）。**不要**在源码里写 `// [MIGRATE-*]` 注释。

### 汇编代码（完整子闭环）

1. 读懂 x86/ARM 语义与边界；设计 RISC-V 寄存器分配、RVV `vl` / mask。
2. 先查上文**指令映射速查表**，按分层实现策略编写 `*_riscv` 后缀的汇编或 intrinsic 实现。
3. **遇到指令/扩展/SEW&LMUL/intrinsic 对应/ABI 约束不确定时**：立即进入 2.3 查库，再继续编码。

### AutoVecCandidate（可向量化热点子闭环）

1. 先尝试编译器自动向量化：`-O3` + VLEN 锁定 + `__restrict`/`__builtin_assume_aligned`/`#pragma GCC ivdep`，`-fopt-info-vec` 检查向量化报告；
2. 成功且指令数相对标量基线降 ≥10% → 直接进 2.4，`perf.ab_variants` 记 `kind=autovec`；
3. 失败/被拒/不达标 → 改写为显式 RVV intrinsic（同汇编路径），`marking` 记 autovec 被拒原因；
4. 禁止只做宏替换后按轻量流程放行。

### 非汇编代码（轻量子流程）

1. **代码适配**：改架构宏/头文件/编译选项使 C/C++ 在 RISC-V 可编译——`#ifdef __x86_64__`/`__ARM_ARCH` 换 `__riscv`；移除 `<immintrin.h>`/`<arm_neon.h>` 等专有头文件；确认字节序/对齐/类型宽度在 RV64（小端、`long`=64-bit）下成立。
2. 一般不需要进入 2.3 知识库查询（除非出现指令/intrinsic 信号）。
3. 直接进入 2.4 验证。
4. **前置条件**：非汇编且**非** `AutoVecCandidate` 才允许走本流程。

### 单元测试细节

- **按需准备环境**：要跑扫描类脚本时，在技能目录执行 `python3 -m pip install -r scripts/requirements.txt`。要编译/运行 **x86_64 或 ARM 原生** 测试时，在技能目录执行 `bash resources/x86_toolchain_env.sh` 或 `bash resources/arm_toolchain_env.sh`，再 **`source resources/env.sh`**（新终端需重做）。若缺系统级依赖（头文件、动态库等），用发行版包管理器补齐。
- 为迁移点编写或补全 **单元测试**：同一套测试既能编 **原始 x86/ARM**，也能在后续用于 **RISC-V**。
- 断言可比对结果（返回值、缓冲区、checksum 等）。**无可用测试则不应结束迁移。**
- **测试充分性下限**：边界值（n=0 / n=1 / 极值）、随机（固定种子、可复现）、checksum 三类各至少一组；asm/RVV/AutoVec 条目另须覆盖**尾部不足一条向量**的规模（`n = k×VL + r`，`0 < r < VL`）与整倍数对照——strip-mining 尾部处理是向量迁移最高频出错点（详见 agent 定义 `riscv-migration-worker` 第 3 节）。

---

## 阶段二 2.3：知识库/手册查询（汇编条目必走；非汇编条目按需）

**触发条件**：迁移过程中出现任一信号：指令名/扩展名（如 `Zba`/`V`/`Zvbb`）、intrinsic（如 `__riscv_*`）、ABI/CSR/特权字段（如 `mstatus`）。

### 选工具的规则

- **`search_core_isa_manuals`**：核心 ISA/汇编/Profile（合并知识库，Milvus spec=`core-isa-manuals`）
  - 覆盖：`riscv/riscv-isa-manual`、`riscv-non-isa/riscv-asm-manual`、`riscv/riscv-profiles`
- **`search_rvv_vector_extensions`**：RVV/向量相关扩展与 vector crypto（合并知识库，Milvus spec=`rvv-vector-extensions`）
  - 覆盖：`riscv-non-isa/riscv-rvv-intrinsic-doc`、`riscv/integer-vector-absolute-difference`、`riscv/riscv-crypto`
- **`search_special_instructions`**：真正的指令扩展（合并知识库，Milvus spec=`special-instructions`）
  - 覆盖：`riscv-zabha`、`riscv-zalasr`、`riscv-zaamo-zalrsc`、`riscv-bitmanip`、`riscv-bfloat16`
- **`search_docs_tools`**：工具/指南/性能与优化（合并知识库，Milvus spec=`docs-tools`）
  - 覆盖：`riscv-performance-events`、`riscv-optimization-guide`

### 调用方式

- 列工具确认服务端暴露的工具名：`<skill_root>/scripts/run_query.sh --list-tools`
- 示例：
  - `<skill_root>/scripts/run_query.sh -t search_core_isa_manuals -q "mstatus MPP"`
  - `<skill_root>/scripts/run_query.sh -t search_rvv_vector_extensions -q "__riscv_vsetvl"`
  - `<skill_root>/scripts/run_query.sh -t search_special_instructions -q "Zba 有哪些指令"`
  - `<skill_root>/scripts/run_query.sh -t search_docs_tools -q "performance events"`

### 输出要求（证据链）

- **必须**在结论里保留 MCP 返回中的 `file_path` 与 `header_path`（或等价的标题路径信息），作为证据链。
- 如果返回未包含上述字段：优先让问题更具体（指令名/扩展名/操作数形态/SEW/LMUL），再重查；不要猜。

---

## 阶段二 2.4：执行对比与修复

1. **按需准备环境**：用 **`qemu-riscv64`** 等 user-static 跑 RISC-V 二进制前，执行 `bash resources/qemu_static_env.sh`，并 **`source resources/env.sh`**。
2. 分别构建并运行原生与 RISC-V 测试；RISC-V 侧常用 `qemu-riscv64 -cpu max,vlen=<目标VLEN> <bin>`（VLEN 与 A 节 #6 选定的 `-mcpu` 对应，zhufeng2 为 256；默认 `-cpu max` 的 VLEN=128 与目标不一致，须显式覆盖）。ARM 侧对比可用 `qemu-aarch64 -cpu max <bin>`。
3. 对比输出；不一致则优先改 RISC-V 侧（或测试/构建脚本）直到一致。
4. A/B 指令数度量（asm/RVV/AutoVec 条目）：运行标量基线版与 RVV 版，收集 `perf_cnt.h` 输出，填入条目 `perf` 字段。**禁止**用 QEMU wall-clock 或跨架构时间比做性能结论（口径见 [perf_measure.md](perf_measure.md)）。
5. **验证通过后必须更新 JSON 字段**（**主 Agent** 执行）：
   - 把 `scan_result.json` 中该条目的 `status` 从 `START` 改为 `DONE`
   - 同时填写 `marking`（+ asm/RVV/AutoVec 条目的 `perf`）：
     - 正常：`无异常；相对同目标标量基线：指令数 -N%，Block RThroughput -M%（<cpu>）`
     - 有异常：`语义差异：<具体点>，已通过测试规避` 或 `TODO(后续)：<具体项>`
     - 性能不占优：`相对标量基线指令数/吞吐未占优，原因：<…>，建议阶段四深度优化`

## 阶段二 2.4.5：快速 mca 性能门禁（asm/RVV/AutoVec 条目）

2.4 验证通过后、主 Agent 写 `DONE` 前，对 asm/RVV/AutoVec 条目执行**单点快速 llvm-mca 分析**（完整规则见 SKILL.md 2.4.5 节）：

```bash
# intrinsic/C 源：GCC -S 输出清洗后喂 llvm-mca
riscv64-unknown-linux-gnu-gcc -O3 -S -march=<与工程一致> -mabi=lp64d foo.c -o - \
  | bash <skill_root>/scripts/clean_asm_for_mca.sh - - > hot.s
llvm-mca -mtriple=riscv64 -mcpu=<A节#6> -mattr=+v,+zvbc,... --all-stats --iterations=100 < hot.s
```

门禁判据（满足其一回流 2.2）：向量版 Block RThroughput ≥ 标量基线；单一端口压力 >90% 且可消除；循环内冗余 `vsetvli`；向量寄存器溢出到栈。通过则把 IPC / Block RThroughput 写入 `perf`。

---

## 阶段三：工程级交叉编译 + 向量化审计

阶段二所有条目 DONE 后，用 RISC-V 交叉工具链编译整个工程，详见 [SKILL.md](../SKILL.md) 阶段三。

- 在 **Makefile / CMake** 等中增加 riscv64 交叉目标。
- 修编译错误至通过；缺什么装什么（系统包 + `<skill_root>/resources/*.sh` 已覆盖的工具链/QEMU）。
- `-march` 覆盖所用扩展；常见起点：`-march=rv64gcv_zbb_zbc_zbkb_zvbb_zvbc_zvkb_zvksed_zvkned_zvksh_zvknha_zvkt -mabi=lp64d`，不够再补。
- 项目若要求 **静态链接**，遵守之。
- 若任务固定产物名（如 `riscv64_test`），遵守任务说明。
- 编译器前缀以 `*_TOOLCHAIN_ROOT/bin` 下实际文件为准（如 `riscv64-unknown-linux-gnu-gcc`、`aarch64-unknown-linux-gnu-gcc`、x86 交叉前缀等）。
- 用 `qemu-riscv64 -cpu max,vlen=<目标VLEN> <bin>` 跑主流程/集成测试，至少确认主程序可启动、无立即崩溃、关键路径输出与 x86/ARM 侧一致。
- **3.4 向量化审计**：对 AutoVecCandidate 条目（及依赖编译器自动向量化的条目）`objdump -d` 后 `grep -E '\svsetvli|\svsetivli|\sv[a-z][a-z0-9]*\.'`（覆盖全部 RVV 指令形态，含段式/strided/gather 与纯向量计算指令）；未命中且阶段二结论为 autovec → 回流阶段二改写显式 intrinsic；统计**向量化覆盖率**并纳入阶段三摘要。

---

## 阶段四：llvm-mca 性能分析与改进（独立 Subagent，默认执行）

**触发条件**：阶段一/二/三全部完成。**默认对全部 asm/RVV/AutoVec 条目执行**（用户明确叫停才跳过）。

**目标**：用 `llvm-mca` 对热点段做静态吞吐/瓶颈分析，据此小步优化；**每轮优化后必须重复 2.4**（同一组测试、输出一致）**并做 L2 指令数回归**，禁止只追性能导致语义回归。

**完成优化后**：清理临时产物，只保留 **最终** RISC-V 源文件与必要构建改动。**必须**回到 `scan_result.json` 对应条目更新 `marking` 与 `perf`：`经阶段四 llvm-mca 优化后 IPC 由 <x> 提升到 <y>（<cpu>），指令数 -N%，关键改动：<具体点>`。

**独立 Subagent 说明**：本阶段由 `riscv-asm-analyzer` Subagent 负责，不在阶段二主流程内嵌运行；阶段三通过后主 Agent 默认召唤。

### 宿主与目标分工

- `llvm-mca` 运行在 **开发机（x86_64 / aarch64 等）**，输入是 **RISC-V 目标 ISA** 的汇编文本；不需要在 RISC-V 真机或 QEMU 内安装 `llvm-mca`。
- 生成待分析汇编时，须与工程 **真实交叉构建** 使用同一套 triple、`-march`、`-mabi`、`-mcmodel` 等（见下文「交叉编译」）。

### 本机没有 llvm-mca 时：`resources/llvm_mca_env.sh`

- 若 `command -v llvm-mca` 失败，在技能根执行：
  - `bash resources/llvm_mca_env.sh`  
  或当前 shell：`source resources/llvm_mca_env.sh`
- 脚本按 `uname -m` 选择 x86 或 arm 制品包，下载到 `<skill_root>/resources/llvm-mca/`（或复用已下载的 tar），解压后将 `llvm-mca` 所在目录加入 `PATH`，并写入 `resources/env.d` 下的 env 片段。
- **覆盖制品 URL**（与脚本约定一致）：`LLVM_MCA_URL`、`LLVM_MCA_X86_URL`、`LLVM_MCA_ARM_URL`。内网/离线可事先把对应 `llvm-mca-*.tar` 放到脚本期望路径，跳过下载后仍走解压与 `PATH` 配置。
- **LLVM 版本要求**：
  - 本技能提供的 `llvm-mca` 基于 **LLVM 22.x**（截至编写时），支持 RISC-V V 扩展、Zvbc、Zvbb 等较新扩展。
  - 若项目交叉编译器为 LLVM 17 及以下，部分新扩展指令可能在 llvm-mca 中无法识别。
  - **工具链事实**：本技能自带交叉工具链为 **GCC 14.3**（无 clang）。GCC 汇编与 LLVM MC 解析器存在伪指令/注释差异，**必须经 `scripts/clean_asm_for_mca.sh` 清洗后再喂 llvm-mca**（见下节）。若后续部署交叉 clang，建议 ≥ **LLVM 18** 并优先与 llvm-mca 版本相近。
  - 可通过 `llvm-mca --version` 和所用交叉编译器 `--version` 交叉比对版本号。

### 交叉编译：如何得到 `hot.s`

- **默认管线（GCC 14.3 + 清洗脚本）**——C/intrinsic 源统一走：
  ```bash
  riscv64-unknown-linux-gnu-gcc -O3 -S -march=<与工程一致> -mabi=lp64d -mcmodel=<工程一致> foo.c -o - \
    | bash <skill_root>/scripts/clean_asm_for_mca.sh - - > hot.s
  ```
  清洗脚本处理：剔除 GNU 描述性伪指令（`.attribute/.file/.loc/.size/.type/.ident/.section/.p2align/.option/.text` 等段描述与调试信息）与 `.cfi_*` 行；`.text` 内行尾 `/* */` 转 `//`；保留标签（llvm-mca 需要识别循环边界）。
- 若已有目标文件，也可用 `llvm-objdump -d` 截取循环片段再分析；注意 `llvm-mca` 对 **LLVM 汇编语法** 最友好，反汇编文本可能需要手工整理成可解析片段。
- **手写迁移后的 `.S/.s`**：可直接作为输入，只要指令集与工程 `-march` 一致；含 GNU 伪指令时同样先过清洗脚本。
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
| **`zhufeng2`**　| **六发射乱序** | **6**　　| **自研朱峰2号芯片**（**默认**）　　　|
| `rocket-rv64`　 | 单发射顺序　　 | 1　　　　| 资源极度受限的嵌入式场景　　　　　　 |
| `sifive-u74`　　| 双发射顺序　　 | 2　　　　| 顺序核基线评估（SiFive U74 / FU740） |
| `andes-ax45mpv` | 多核顺序　　　 | 1　　　　| Andes AX45MPV 平台　　　　　　　　　 |
| `sifive-p450`　 | 三发射乱序　　 | 3　　　　| 乱序核性能评估（SiFive P450）　　　　|
| `sifive-p550`　 | 三发射乱序　　 | 3　　　　| SiFive P550 平台　　　　　　　　　　 |
| `spacemit-x60`　| 乱序　　　　　 | —　　　　| Spacemit X60 平台　　　　　　　　　　|
| `sifive-x280`　 | 向量加速　　　 | —　　　　| 重度 RVV 向量场景　　　　　　　　　　|

> **选择原则**（按优先级从高到低；本表为唯一权威，SKILL.md A 节 #6 概述引用此处）：
> 1. **用户 prompt 中明确指定了 `-mcpu` 或目标芯片** → 使用用户指定的值，不再覆盖。
> 2. **用户未指定** → 优先使用 `zhufeng2`（自研朱峰2号，六发射乱序）作为默认分析目标。**注意**：`zhufeng2` 调度模型来自本技能定制的 llvm-mca 制品（`resources/llvm-mca/`，LLVM 22.x），标准 LLVM 发行版不含该模型——误用系统自带 `llvm-mca` 时会报 unknown CPU；请确认 `command -v llvm-mca` 指向技能部署的版本。
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
# 快速分析（已有清洗后的 RISC-V 汇编）
llvm-mca -mtriple=riscv64 -mcpu=sifive-p450 -mattr=+v,+zvbc --all-stats --iterations=100 < hot.s

# 深度分析（含瓶颈分析 + 时间线视图）
llvm-mca -mtriple=riscv64 -mcpu=sifive-p450 -mattr=+v,+zvbc \
  --bottleneck-analysis --all-views --timeline --timeline-max-iterations=10 \
  --iterations=100 < hot.s

# C/intrinsic：交叉 GCC 管道 + 清洗脚本到宿主 llvm-mca（flags 与工程构建一致）
riscv64-unknown-linux-gnu-gcc -O3 -S -march=rv64gcv_zvbc -mabi=lp64d hot.c -o - \
  | bash <skill_root>/scripts/clean_asm_for_mca.sh - - \
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

以 SKILL.md A 节 #10 为唯一口径，**三条件同时满足**时可认为该轮优化已足够：
- IPC 达到 Dispatch Width 的 **70% 以上**。
- Block RThroughput 连续两轮差距 < 5%。
- `No resource or data dependency bottlenecks` 出现。
- 每次优化后必须回到 2.4 验证正确性，正确性不可妥协。

### 闭环产出（必须）

- **性能**：每轮保留 `llvm-mca` 输出中的关键摘要（吞吐、周期估计、瓶颈提示等），便于与上一轮对比；L2 指令数同步记录。
- **正确性**：每轮修改后重复 2.4（构建 + QEMU/对比测试），输出不一致则先修语义再继续调性能。
- **A/B 择优**：多候选方案（intrinsic vs asm、不同 LMUL、软件流水与否）统一度量后择优，全部版本记入 `perf.ab_variants`（判据见 [perf_measure.md](perf_measure.md) 第 5 节）。
- **NOTE 更新**：优化通过后必须回到 `scan_result.json` 对应条目更新 `marking` 与 `perf` 字段标注优化效果。

---

## 完成说明

向用户简短总结：处理了哪些条目、改了哪些文件、测试与对比命令及结果。若做过阶段四，附带 **llvm-mca 前后对比要点** 与回归测试结论。除非用户要固定模板，否则不必单独写 `output.md`。

---

## 迁移点状态字段规范（status / marking / perf）

每个条目在 `scan_result.json` 中通过 `status`、`marking`、`perf` 三个结构化字段维护进度。**权威源在 JSON，不在源码**。三态**互斥**（同一时刻只能有一个）；`status=DONE` 时 `marking` 必填；asm/RVV/AutoVec 条目另需 `perf`。

```jsonc
// 阶段一扫描产出（扫描器输出小写 "todo" 且无 tier/perf 字段，此处为主 Agent 归一化后形态）：
{
  "file_path": "/abs/path/src/crc/crc32.c",
  "start_line": 120,
  "end_line": 200,
  "solver_type": 4,
  "status": "TODO",
  "marking": "",
  "perf": null
}

// 阶段二 2.2 主 Agent 分派任务前：
{ "status": "START", "marking": "", "perf": null }

// 阶段二 2.4 + 2.4.5 验证通过时（主 Agent 写入；schema 权威见 perf_measure.md 第 4 节）：
{
  "status": "DONE",
  "marking": "无异常；相对同目标标量基线：指令数 -68%，Block RThroughput -55%（zhufeng2）",
  "perf": {
    "baseline_kind": "scalar_c",
    "metrics": {
      "instret_per_elem_baseline": 12.4,
      "instret_per_elem_variant": 3.9,
      "block_rthroughput_baseline": 9.0,
      "block_rthroughput_variant": 4.0,
      "ipc_variant": 4.6,
      "mcpu": "zhufeng2"
    },
    "env": "qemu-tcg-vlen256",
    "ab_variants": []
  }
}

// 阶段四优化通过后（更新 marking 与 perf）：
{
  "status": "DONE",
  "marking": "经阶段四 llvm-mca 优化后 IPC 由 2.1 提升到 5.4（zhufeng2），指令数 -12%，关键改动：vsetvli 外提 + 多累加器拆分",
  "perf": { "...": "同上结构，metrics 更新为优化后数值" }
}
```

| `status` | 含义 | 何时设置 | `marking` | `perf` |
| ------- | ---- | -------- | --------- | ------ |
| `TODO` | 未处理 | 阶段一扫描产出（默认值） | 否 | 否 |
| `START` | 开始迁移 | 阶段二 2.2 主 Agent 分派任务前 | 否 | 否 |
| `DONE` | 迁移完成 | 阶段二 2.4 + 2.4.5 验证通过 | **是** | asm/RVV/AutoVec 条目**是** |

`marking` 常见内容（性能口径见 [perf_measure.md](perf_measure.md)，**禁止跨架构 wall-clock 百分比**）：

- 无异常：`无异常；相对同目标标量基线：指令数 -N%，Block RThroughput -M%（<cpu>）`
- 语义差异：`语义差异：<具体点>，已通过测试规避`
- 性能不占优：`相对标量基线指令数/吞吐未占优，原因：<…>，建议阶段四深度优化`
- 阶段四优化后：`经阶段四 llvm-mca 优化后 IPC 由 <x> 提升到 <y>（<cpu>），指令数 -N%，关键改动：<具体点>`
- 需要后续修复：`TODO(后续)：<具体项>`

更新方式：直接编辑 JSON 文件（或 agent 用 `jq`/Python 改写条目），写回后必须保持 schema 与缩进一致。**不要**在源码里写 `MIGRATE-*` 注释。