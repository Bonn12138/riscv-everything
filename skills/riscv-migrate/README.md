# riscv-migrate

> x86/ARM → RISC-V（含 RVV）一体化迁移技能，覆盖「扫描→迁移→知识库→验证→性能分析」全闭环。

## 部署

将本目录放入 agent 的 skills 路径（如 `~/.deepseek/skills/riscv-migrate/` 或 `~/.claude/skills/riscv-migrate/`），agent 即可通过技能名 `riscv-migrate` 调用。无需额外构建或配置。

## 使用

用户只需告诉 agent 做什么，技能会驱动 agent 自动推进。五件事可独立用，也可自动串联。典型用法：

> 「扫描这个项目，把所有 SSE/AVX 的 CRC32 实现迁移到 RISC-V，针对 zhufeng2 CPU 做性能优化。」

agent 会自动完成全流程：扫描 → 为 CRC32 热点写 RVV 1.0 版本 → 查手册确认 Zvbc 等扩展 → QEMU 对比验证 → 以 `zhufeng2` 为目标用 llvm-mca 分析吞吐瓶颈 → 小步优化直到 IPC 接近 6 → 回归验证通过。

单项操作：

- **扫描工程**：说「扫描这个项目」→ agent 执行扫描，产出 `scan_result.json`
- **迁移代码**：说「迁移这个函数」→ agent 写 RISC-V 版本，遇指令/扩展问题自动查手册
- **查询知识库**：问「vadd.vv 属于哪个扩展」→ agent 查手册，返回证据链
- **验证**：说「跑测试对比」→ agent 自动准备工具链+QEMU，比对输出
- **性能分析**：说「分析热点，目标芯片是朱峰2号」→ agent 用 `llvm-mca -mcpu=zhufeng2` 分析汇编，给出优化建议

agent 会自主管理环境：工具链、QEMU、llvm-mca 均由 `resources/` 下的脚本按需部署，幂等且尽量不联网。


## 工作流

```
扫描(A) → 迁移(B) → 查库(C) → 验证(D) → 性能分析(E)
  │         │          │          │            │
  ▼         ▼          ▼          ▼            ▼
scan.json  *_riscv   证据链    QEMU对比    优化→回D验证
```

- 阶段 C（查证）在 B 遇到指令/扩展/ABI 问题时自动触发
- 阶段 D（验证）在每轮迁移改动后自动触发
- 阶段 E（性能分析）仅对手写/RVV 汇编热点执行，优化后必须回 D 复测

## 目录

```
riscv-migrate/
├── SKILL.md                    # 技能定义
├── referens/
│   ├── project_scan.md         # scan_result.json Schema
│   └── code_migrate.md         # 迁移四步法、编译/llvm-mca 详述
├── resources/                  # 环境脚本（工具链/QEMU/llvm-mca）
│   ├── env.sh                  # 聚合入口
│   ├── lib.sh                  # 公共函数库
│   ├── x86_toolchain_env.sh    # x86 工具链
│   ├── arm_toolchain_env.sh    # ARM 工具链
│   ├── riscv_toolchain_env.sh  # RISC-V 交叉工具链
│   ├── qemu_static_env.sh      # QEMU user-static
│   └── llvm_mca_env.sh         # llvm-mca
└── scripts/
    ├── run_scan.sh             # 扫描入口
    ├── run_query.sh            # 知识库查询入口
    ├── query.py                # MCP 查询客户端
    └── prepare_verify_env.sh   # 一键环境准备
```

## 设计原则

1. **禁止猜测** — 涉及指令/扩展/intrinsic 必须查手册取证
2. **向量用 RVV 1.0** — 不允许退化纯 C
3. **测试先行** — 同一测试同时编译原始和 RISC-V 实现
4. **正确性不妥协** — 性能优化后必须回验证步骤
5. **不依赖 venv** — 系统 Python + 脚本自举依赖
