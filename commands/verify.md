---
description: 触发迁移验证：阶段二 2.4（条目级 QEMU 对比）+ 阶段三产物级 QEMU 验证；自动准备工具链与 QEMU → 编译 → 按输出类型比对（整数/字节流逐字节、浮点容差），定位不一致并回流修复。QEMU 验证必须由主 Agent 执行，验证通过后主 Agent 更新 scan_result.json。
argument-hint: [source-dir | riscv-src]
---

# /everything-riscv:verify — 迁移验证

准备 RISC-V 工具链与 QEMU（由技能自动部署、自动加载），对迁移后的代码编译并在 `qemu-riscv64` 下运行，与原始 x86/ARM 实现做输出 / 校验和对比，定位不一致并回流到迁移流程修复。

> **QEMU 验证必须由主 Agent 执行**，不能委托给 Subagent。Subagent 只能返回局部编译结果和 `READY_FOR_REVIEW` / `READY_FOR_VERIFY`；只有主 Agent 完成 QEMU 对比后才能将条目写为 `DONE`。

## 在四大阶段中的位置

验证在两个阶段发生，**作用域不同**：

| 触发场景 | 所属阶段 | 验证范围 | 执行者 |
| -------- | -------- | -------- | ------ |
| **阶段二 2.4** | 每个迁移条目 `status` 改为 `DONE` 之前 | 单个迁移点：原始 vs RISC-V 条目级测试，行为一致 + A/B 指令数度量后主 Agent 在 JSON 中写 `status="DONE"` + `marking` + `perf` | **主 Agent** |
| **阶段三 3.3** | 工程级编译之后 | 整体产物：`qemu-riscv64 -cpu max,vlen=<目标VLEN> <bin>` 跑主流程，至少能启动、关键路径输出与 x86/ARM 侧一致 | **主 Agent** |

> **条目级 + 产物级互补**：条目级保证单个迁移点正确；产物级保证整体可运行。

## 前置条件

- 系统本地 GCC（用于编译原始 x86/ARM 参考实现）
- RISC-V GCC 工具链（`riscv64-unknown-linux-gnu-gcc`）与 QEMU user mode（`qemu-riscv64`）由技能侧自动准备，缺失时无需手动安装

## 执行流程

### 1. 准备验证环境（首次从技能 `resources/` 解包工具链与 QEMU，已存在则复用，不联网下载）

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/riscv-migrate/scripts/prepare_verify_env.sh"
```

技能会在当前会话**自动加载**所需环境（工具链与 `qemu-*` 写入 `PATH`），无需手动 `source`。

### 2. 编译 + QEMU 对比

对同一行为的原始实现与 RISC-V 实现分别编译，用 `qemu-riscv64 -cpu max,vlen=<目标VLEN>` 运行并比对输出（或 checksum）：

```bash
# 原始参考实现（x86 示例）
gcc -O2 -msse4.2 -o ref.out ref.c   # x86 参考侧只看正确性，优化级别不要求一致
# RISC-V 实现
riscv64-unknown-linux-gnu-gcc -O3 -march=rv64gcv_zbb_zbc_zbkb_zvbb_zvbc_zvkb_zvksed_zvkned_zvksh_zvknha_zvkt -mabi=lp64d -static -o riscv.out riscv.c
# 逐字节 / 校验和对比
./ref.out > ref.txt
qemu-riscv64 -cpu max,vlen=<目标VLEN> ./riscv.out > riscv.txt
diff ref.txt riscv.txt
```

**对比口径按输出类型区分**：

- **整数 / 字节流 / 枚举 / checksum**：逐字节 `diff`，必须完全一致。
- **浮点输出**：禁止盲目逐字节 diff——x86（SSE，80-bit 精度中间值）与 RV（lp64d，IEEE 双精度）的 FPU 收敛路径不同，逐位一致既不可达也不必要。改用**容差对比**：
  - 优先比较 checksum/归一化摘要（如求和误差按 ULP 计）；
  - 逐值对比时用相对误差 `|a-b| ≤ max(abs_err, rel_err×|b|)`，默认 `rel_err=1e-9`（双精度）、`abs_err=1e-12`；
  - 容差内不一致**不算失败**，但差异模式（如系统性偏移、仅尾数位不同）要写入该条目 `marking` 备查。
  - 若原始实现的语义本身要求位精确（如序列化格式、哈希输入），必须逐位一致，此时在 RISC-V 侧实现时就要选用保证位精确的算法，而不是靠容差放行。

**VLEN 对齐（必须）**：`-cpu max` 默认 VLEN=128；目标芯片（如 zhufeng2）VLEN 通常为 256。手写 asm 按目标 VLEN 选 LMUL 时，用默认值验证可能假失败/假通过。`vlen=` 取值必须与 SKILL.md A 节 #6 选定 `-mcpu` 时确定的目标 VLEN 一致。

若用户提供了测试规格（输入向量、预期输出），按规格构造用例逐条运行对比。

**A/B 指令数度量（asm/RVV/AutoVec 条目）**：对比测试同时包含标量基线版与 RVV/汇编版（`#include <skill_root>/resources/perf_cnt.h`），收集 `instret_per_elem` 供主 Agent 填入 `perf` 字段。**禁止**用 QEMU wall-clock 或跨架构时间比作性能结论（口径见 `skills/riscv-migrate/referens/perf_measure.md`）。

### 3. 阶段二 2.4 验证通过后的 JSON 字段更新（必须由主 Agent 执行）

对比一致后，**主 Agent 必须**更新 `scan_result.json` 中对应条目的字段（**权威源在 JSON，不在源码注释；只有主 Agent 可以写入**）：

```jsonc
{
  "status": "DONE",
  "marking": "<异常/性能结论一句话>",
  "perf": { "...": "asm/RVV/AutoVec 条目必填，结构见 perf_measure.md 第 4 节" }
}
```

`marking` 内容（性能口径见 `skills/riscv-migrate/referens/perf_measure.md`）：

- 正常：`无异常；相对同目标标量基线：指令数 -N%，Block RThroughput -M%（<cpu>）`
- 异常：`语义差异：<具体点>，已通过测试规避` 或 `TODO(后续)：<具体项>`
- 性能不占优：`相对标量基线指令数/吞吐未占优，原因：<…>，建议阶段四深度优化`

> **禁止**在 `marking` 中写跨架构 wall-clock 百分比（如"相对 x86 慢 30%"）——QEMU TCG 时间与目标微架构无关。

> **Subagent 不得写入 `scan_result.json`**。迁移 Subagent 返回 `READY_FOR_VERIFY` 后，由主 Agent 执行本步骤完成最终状态提交。

## 验证后分析

1. 对第一处不一致的输出，定位首差行，逆向追溯到 RISC-V 实现的代码差异
2. 将定位结果回流到迁移流程（阶段二 2.2）修复，修复后回到本步骤复测
3. 闭环至全部用例输出一致

## 阶段四（性能分析）触发判定

- 阶段三通过后**默认对全部 asm/RVV/AutoVec 条目执行**（用户叫停才跳过），无需本命令标注触发。
- 本命令只保证**正确性**（整数/字节流逐字节一致、浮点容差内一致）与 **A/B 指令数度量数据**；性能分析不在本命令内执行。
- 阶段四优化后的 `marking` 与 `perf` 由主 Agent 在 QEMU 回归 + L2 回归验证后统一写入。

## 约束

- 不依赖虚拟环境；工具链 / QEMU 由技能侧自动部署与加载
- 每轮迁移改动后应主动触发本验证，不等用户提醒
- 阶段二每个条目 `status=DONE` 时 `marking` 必填；asm/RVV/AutoVec 条目另需 `perf`；阶段三通过后默认进入阶段四
- QEMU 运行统一带 `-cpu max,vlen=<目标VLEN>`，与 `-mcpu` 决策联动
- **只有主 Agent 可以写入 `scan_result.json`**
- **不要**在源码里写 `// [MIGRATE-*]` 注释；所有状态以 `scan_result.json` 为唯一权威源
