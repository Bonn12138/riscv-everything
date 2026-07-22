# riscv-migrate

> x86/ARM → RISC-V（含 RVV）一体化迁移技能。按**三大阶段**推进（扫描 → 分流迁移 → 工程编译），性能分析作为独立 agent 仅在前三阶段完成后启动。每个迁移点的状态以 `scan_result.json` 条目里的 `status` / `marking` 结构化字段维护，权威源在 JSON，不在源码注释。

## 部署

将本目录放入 agent 的 skills 路径（如 `~/.deepseek/skills/riscv-migrate/` 或 `~/.claude/skills/riscv-migrate/`），agent 即可通过技能名 `riscv-migrate` 调用。无需额外构建或配置。

## 使用

用户只需告诉 agent 做什么，技能会驱动 agent 自动推进。典型用法：

> 「把这个 x86 项目迁移到 RISC-V，确保能在 RV 上跑起来，目标芯片是朱峰2号。」

agent 会自动完成全流程：

1. **阶段一**：扫描工程，产出 `scan_result.json`，每个条目带 `status="TODO"`。
2. **阶段二**：按条目分流（汇编/非汇编）迁移到 RV，每条目串行 `分流 → 迁移 → 查库 → 验证`，通过改写 `scan_result.json` 的 `status` 与 `marking` 字段同步进度（`TODO → START → DONE + marking`）。
3. **阶段三**：用交叉工具链编译整个工程，修编译错误，直至产出可在 QEMU 跑通的 RISC-V 可执行程序。
4. **阶段四（独立 agent）**：前三阶段全部通过后召唤性能分析 agent，对热点用 `llvm-mca -mcpu=zhufeng2` 分析并迭代优化，优化通过后更新对应条目的 `marking`。

单项操作：

- **扫描工程**：说「扫描这个项目」→ agent 执行扫描，产出 `scan_result.json`
- **迁移代码**：说「迁移这个函数」→ agent 写 RISC-V 版本，并把条目 `status` 改为 `START`；遇指令/扩展问题自动查手册；验证通过后改 `status=DONE` 并填 `marking`
- **查询知识库**：问「vadd.vv 属于哪个扩展」→ agent 查手册，返回证据链
- **工程级编译**：「用 RV 工具链编这个工程」→ agent 自动加载工具链，修编译错误，产出 RV 可执行程序
- **性能分析**（独立 agent）：说「分析热点，目标芯片是朱峰2号」→ agent 用 `llvm-mca -mcpu=zhufeng2` 分析汇编，给出优化建议，并通过更新 `marking` 字段同步结果

agent 会自主管理环境：工具链、QEMU、llvm-mca 均由 `resources/` 下的脚本按需部署，幂等且尽量不联网。

## 零交互自主运行

技能执行期间**默认不向用户提问**，所有决策按 `SKILL.md`「自主运行原则」默认处理（详见 SKILL.md 对应章节）。只有以下 5 类白名单场景才请示用户：

1. 找不到目标工程根目录
2. 阶段二某条目 5 轮 QEMU 对比仍不一致
3. 阶段三编译连续 3 个同类错误
4. 内网不可达且无离线制品包
5. 用户主动问进度

**禁止**在 `-mcpu` / `-march` / `llvm-mca` 是否安装 / 汇编 vs intrinsic / 测试怎么写 等典型决策上向用户请示。

## 工作流

```
阶段一: 扫描 ─────────────► scan_result.json (每条目 status=TODO)
            │
阶段二: 按条目分流迁移 ─────► *_riscv 源码 + QEMU 验证通过
            │                  同步更新 status: TODO → START → DONE + marking
            ▼
阶段三: 工程级交叉编译 ─────► RV 可执行程序（QEMU 中可运行）
            │
阶段四(独立 agent): 性能分析 ► llvm-mca 报告 + 优化提交（更新 marking）
```

- 阶段二每个条目内部：分流 → 迁移 → 知识库 → 验证
- 阶段二所有条目 status=DONE 后才能进入阶段三
- 阶段三通过后才召唤阶段四性能分析 agent

## 迁移点状态字段规范

每个条目在 `scan_result.json` 中通过以下两个结构化字段维护进度：

| 字段 | 取值 | 说明 |
| ---- | ---- | ---- |
| `status` | `TODO` / `START` / `DONE` | 三态互斥 |
| `marking` | string | 备注文本：异常说明 / 性能影响 / TODO(后续) 等；`status=DONE` 时必填 |

```jsonc
// 阶段一扫描产出（默认）：
{ "status": "TODO", "marking": "" }

// 阶段二 2.2 进入条目：
{ "status": "START", "marking": "" }

// 阶段二 2.4 验证通过：
{
  "status": "DONE",
  "marking": "无异常，性能持平；建议进入阶段四用 llvm-mca 确认吞吐"
}

// 阶段四优化通过后（更新 marking）：
{
  "status": "DONE",
  "marking": "经阶段四 llvm-mca 优化后 IPC 由 2.1 提升到 5.4（zhufeng2），关键改动：vsetvli 外提 + 多累加器拆分"
}
```

> **权威源在 JSON，不在源码**：不要在源码里写 `// [MIGRATE-*]` 注释。

## 目录

```
riscv-migrate/
├── SKILL.md                    # 技能定义（三大阶段 + 性能分析独立 agent）
├── referens/
│   ├── project_scan.md         # scan_result.json Schema（含 status/marking 字段）
│   └── code_migrate.md         # 迁移三步法、编译/llvm-mca 详述
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
6. **三阶段串行 + 性能分析独立** — 性能分析不嵌在迁移阶段内，迁移全部完成才召唤
7. **状态字段强约束** — 每个迁移点的状态以 `scan_result.json` 中的 `status` / `marking` 为唯一权威源，源码不写注释