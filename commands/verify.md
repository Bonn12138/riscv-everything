---
description: 触发 RISC-V 验证：自动准备工具链与 QEMU → 编译 → 逐字节比对输出，定位迁移不一致并回流修复
argument-hint: [source-dir | riscv-src]
---

# /everything-riscv:verify — 迁移验证

准备 RISC-V 工具链与 QEMU（由技能自动部署、自动加载），对迁移后的代码编译并在 `qemu-riscv64` 下运行，与原始 x86/ARM 实现做输出 / 校验和对比，定位不一致并回流到迁移流程修复。对应技能 `riscv-migrate` 的**阶段 D**。

## 前置条件

- 系统本地 GCC（用于编译原始 x86/ARM 参考实现）
- RISC-V GCC 工具链（`riscv64-unknown-linux-gnu-gcc`）与 QEMU user mode（`qemu-riscv64`）由技能阶段 D 自动准备，缺失时无需手动安装

## 执行流程

1. **准备验证环境**（首次从技能 `resources/` 解包工具链与 QEMU，已存在则复用，不联网下载）：

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/skills/riscv-migrate/scripts/prepare_verify_env.sh"
   ```

2. 技能会在当前会话**自动加载**所需环境（工具链与 `qemu-*` 写入 `PATH`），无需手动 `source`。

3. **编译 + QEMU 对比**：对同一行为的原始实现与 RISC-V 实现分别编译，用 `qemu-riscv64 -cpu max` 运行并比对输出（或 checksum）：

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

4. 若用户提供了测试规格（输入向量、预期输出），按规格构造用例逐条运行对比。

## 验证后分析

1. 对第一处不一致的输出，定位首差行，逆向追溯到 RISC-V 实现的代码差异
2. 将定位结果回流到迁移流程（阶段 B）修复，修复后回到本步骤复测
3. 闭环至全部用例输出一致

## 约束

- 不依赖虚拟环境；工具链 / QEMU 由技能侧自动部署与加载
- 每轮迁移改动后应主动触发本验证，不等用户提醒
