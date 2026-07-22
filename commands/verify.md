---
description: 触发迁移验证：阶段二 2.4（条目级 QEMU 对比）+ 阶段三产物级 QEMU 验证；自动准备工具链与 QEMU → 编译 → 逐字节比对输出，定位不一致并回流修复
argument-hint: [source-dir | riscv-src]
---

# /everything-riscv:verify — 迁移验证

准备 RISC-V 工具链与 QEMU（由技能自动部署、自动加载），对迁移后的代码编译并在 `qemu-riscv64` 下运行，与原始 x86/ARM 实现做输出 / 校验和对比，定位不一致并回流到迁移流程修复。

## 在三大阶段中的位置

验证在两个阶段发生，**作用域不同**：

| 触发场景 | 所属阶段 | 验证范围 |
| -------- | -------- | -------- |
| **阶段二 2.4** | 每个迁移条目 `status` 改为 `DONE` 之前 | 单个迁移点：原始 vs RISC-V 条目级测试，行为一致即在 JSON 中写 `status="DONE"` + `marking` |
| **阶段三 3.3** | 工程级编译之后 | 整体产物：`qemu-riscv64 -cpu max <bin>` 跑主流程，至少能启动、关键路径输出与 x86/ARM 侧一致 |

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

对同一行为的原始实现与 RISC-V 实现分别编译，用 `qemu-riscv64 -cpu max` 运行并比对输出（或 checksum）：

```bash
# 原始参考实现（x86 示例）
gcc -O2 -msse4.2 -o ref.out ref.c
# RISC-V 实现
riscv64-unknown-linux-gnu-gcc -O2 -march=rv64gcv_zbc -mabi=lp64d -static -o riscv.out riscv.c
# 逐字节 / 校验和对比
./ref.out > ref.txt
qemu-riscv64 -cpu max ./riscv.out > riscv.txt
diff ref.txt riscv.txt
```

若用户提供了测试规格（输入向量、预期输出），按规格构造用例逐条运行对比。

### 3. 阶段二 2.4 验证通过后的 JSON 字段更新（必须）

对比一致后，**必须**更新 `scan_result.json` 中对应条目的字段（**权威源在 JSON，不在源码注释**）：

```jsonc
{
  "status": "DONE",
  "marking": "<异常/性能说明>"
}
```

`marking` 内容：

- 正常：`无异常，性能持平`
- 异常：`语义差异：<具体点>，已通过测试规避` 或 `TODO(后续)：<具体项>`
- 性能影响：`性能低于原实现 N%，原因：<…>，建议进入阶段四 llvm-mca 优化`

更新方式：直接编辑 JSON 文件，或 agent 用 `jq`/Python 改写条目；写回后必须保持 schema 与缩进一致。

## 验证后分析

1. 对第一处不一致的输出，定位首差行，逆向追溯到 RISC-V 实现的代码差异
2. 将定位结果回流到迁移流程（阶段二 2.2）修复，修复后回到本步骤复测
3. 闭环至全部用例输出一致

## 阶段四（性能分析）触发判定

- 若验证发现某条目性能低于原实现且 `marking` 标注"建议进入阶段四"，则在阶段三通过后召唤 `riscv-asm-analyzer` agent 做 llvm-mca 优化。
- 性能分析不在本命令内执行；本命令只保证**正确性**。

## 约束

- 不依赖虚拟环境；工具链 / QEMU 由技能侧自动部署与加载
- 每轮迁移改动后应主动触发本验证，不等用户提醒
- 阶段二每个条目 `status=DONE` 时 `marking` 必填；阶段三产物可运行后才能进入阶段四
- **不要**在源码里写 `// [MIGRATE-*]` 注释；所有状态以 `scan_result.json` 为唯一权威源