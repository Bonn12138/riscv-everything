# everything-riscv

> RISC-V 全栈迁移工具链 Claude Code 插件。采用「主 Agent 编排 + 阶段内动态 Subagent」架构，面向 x86/ARM 代码库，提供从工程扫描到迁移验证再到性能优化的完整闭环。

## 概述

本插件围绕一个核心技能 `riscv-migrate` 构建，采用**主 Agent 编排 + 阶段内动态 Subagent**模式：

- 主 Agent 是唯一编排者与状态提交者，独占 `scan_result.json` 写入权
- Subagent 承担阶段内工作单元（扫描、规划、迁移、审查、诊断、分析）
- **3 条斜杠命令** — 快速启动扫描 / 迁移 / 验证流程，每条命令适配 Subagent 编排
- **6 个专用 Subagent** — 扫描 Worker、迁移规划者、迁移 Worker、代码审查者、构建诊断者、热点分析者
- **远端知识库查询** — 通过技能内置脚本连接远端 RISC-V 文档 MCP 服务，查询 ISA / RVV 文档，**无需本地部署服务端**

## 架构

```text
主 Agent（唯一编排者和状态提交者）
  │
  ├─ 阶段一：riscv-scan-worker × N（只读并行扫描）
  │            └─ 主 Agent 合并、去重并生成 scan_result.json
  │
  ├─ 阶段二：riscv-migration-planner（先产出互不重叠任务分组，主 Agent 审核）
  │            └─ riscv-migration-worker × N（按分组可并行）
  │                 ├─ 必要性分析（误报直接返回 NO_WORK_NEEDED）
  │                 ├─ 普通架构适配
  │                 ├─ RVV intrinsic 迁移
  │                 ├─ inline/standalone asm 迁移
  │                 └─ riscv-code-reviewer（汇编/RVV 条目门禁）
  │
  ├─ 阶段三：riscv-build-diagnoser × N（只读并行诊断）
  │            └─ 主 Agent 或单个写入型 Subagent 串行修复
  │
  └─ 阶段四：riscv-asm-analyzer × N（多热点并行分析）
                └─ riscv-code-reviewer（优化复核）
```

> **核心约束**：`scan_result.json` 只能由主 Agent 写入。Subagent 成功不代表条目 DONE——只有主 Agent 完成最终 QEMU 验证后才能更新状态。

## 技能

### riscv-migrate

> 详情见 `skills/riscv-migrate/SKILL.md`

x86/ARM → RISC-V（含 RVV）一体化迁移技能，按**四大阶段**推进：

| 阶段 | 说明 | 执行模式 |
| ---- | ---- | -------- |
| **一 — 扫描** | 盘点待迁移点，产出 `scan_result.json` | 基础扫描器 + 可选并行扫描 Subagent → 主 Agent 合并去重 |
| **二 — 按条目分流迁移** | 每个条目 `分流 → 迁移 → 知识库 → 验证`，通过改写 `scan_result.json` 的 `status` / `marking` 字段同步进度 | 依赖分组后独立条目可并行；汇编/RVV 条目必过 Reviewer 门禁 |
| **三 — 工程级交叉编译** | 用 RISC-V 交叉工具链编译整个工程，修编译错误，产出可在 QEMU 跑通的 RV 可执行程序 | 并行诊断 + 串行修复 |
| **四 — 性能分析（独立 Subagent）** | 仅在阶段三通过后召唤 `riscv-asm-analyzer`，对热点用 `llvm-mca` 分析并迭代优化 | 多热点并行分析；主 Agent 统一实施、验收与回写 |

阶段门禁不变（一→二→三→四严格串行）；阶段内按工作量动态调用 Subagent。

> **零交互自主运行**：技能/命令执行期间默认不向用户提问，所有决策按 `skills/riscv-migrate/SKILL.md`「自主运行原则」默认处理；只有 5 类白名单场景（找不到工程根 / 条目级 5 轮不一致 / 编译连错 3 次 / 内网不通 / 用户主动问）才请示。Subagent 不得直接向用户提问。

## 命令

插件命令以 `everything-riscv` 为命名空间调用（即 `/everything-riscv:<命令>`）：

| 命令 | 用途 |
| ---- | ---- |
| `/everything-riscv:scan` | 扫描工程，生成待迁移点清单（基础扫描 + 可选并行 scan-worker） |
| `/everything-riscv:migrate` | 启动迁移流程（依赖分组后独立条目可并行；汇编条目必过 Reviewer） |
| `/everything-riscv:verify` | 触发 QEMU 验证：自动准备工具链 → 编译 → 输出对比 → 主 Agent 回流修复与状态提交 |

## Subagent

| Subagent | 角色 | 读写属性 | 触发阶段 |
| -------- | ---- | -------- | -------- |
| `riscv-scan-worker` | 按目录/扫描维度返回候选迁移点 | 只读 | 阶段一（补充扫描） |
| `riscv-migration-worker` | 执行分配迁移条目（分流、迁移、查库） | 写入（指定文件范围内） | 阶段二 |
| `riscv-code-reviewer` | 审查汇编/RVV 迁移代码：向量化正确性、ABI 约定、内存对齐、指令选择 | 只读 | 阶段二（汇编/RVV 条目门禁）、阶段四（优化复核） |
| `riscv-build-diagnoser` | 分析工程级完整构建日志，按根因聚类编译错误 | 只读 | 阶段三（编译失败诊断） |
| `riscv-asm-analyzer` | 对热点汇编做 llvm-mca 静态性能分析，输出吞吐瓶颈与优化建议 | 可读写（热点级绑定，不写 JSON） | 阶段四 |

## 状态字段规范

每个迁移点的状态以 `scan_result.json` 中的结构化字段维护（**权威源在 JSON，不在源码注释；只有主 Agent 可以写入**）：

```text
主 Agent: TODO → START         （分派任务前）
Subagent: 返回 READY_FOR_REVIEW / READY_FOR_VERIFY
Reviewer: 返回 PASS / NEEDS_FIX / FAIL
主 Agent: 运行 QEMU 验证
主 Agent: START → DONE + marking（验证通过后）
```

## 知识库（远端）

技能的**阶段二 2.3** 通过内置脚本 `skills/riscv-migrate/scripts/run_query.sh` 连接一个**已部署的远端 RISC-V 文档 MCP 服务**（HTTP / Streamable HTTP），查询 ISA 手册、RVV 向量扩展、专项指令、性能优化文档。

- 默认端点：`http://10.2.71.145:12306/mcp`（内网），可用环境变量 `RISCV_DOC_MCP_URL` 覆盖。
- 暴露工具：`search_core_isa_manuals` / `search_rvv_vector_extensions` / `search_special_instructions` / `search_docs_tools`。
- **无需本地部署服务端、无需 Milvus / 向量 / 重排模型**：本插件只含 MCP 客户端（`scripts/query.py`），服务端由团队统一维护。

## 安装部署

本插件是一个标准的 Claude Code 插件 marketplace（**单插件 marketplace**：插件根目录即 marketplace 根，`.claude-plugin/marketplace.json` 中 `source` 指向自身 `"."`）。

### 前置条件

- 已安装 Claude Code。
- 本机可访问团队内网（扫描引擎下载、远端知识库服务均在内网）。

### 安装步骤

在 Claude Code 中执行（以下命令均在 Claude Code 的输入框中以 `/` 开头输入）：

```text
# 1. 添加 marketplace（在插件根的父目录下用相对路径，或直接给绝对路径）
/plugin marketplace add <gerrit_url>

# 2. 安装插件：<plugin-name>@<marketplace-name>，两者均为 everything-riscv
/plugin install everything-riscv@everything-riscv

# 3. 验证安装
/plugin list
```

安装后插件会被复制到 `~/.claude/plugins/cache/everything-riscv/everything-riscv/<version>/`，以**用户级（user scope）**生效——所有项目均可使用 `/everything-riscv:*` 命令、`riscv-migrate` 技能与 Subagent。

### 配置（按需）

| 配置项 | 说明 |
| ------ | ---- |
| `RISCV_DOC_MCP_URL` | 远端知识库 MCP 端点；不设则用默认内网地址 `http://10.2.71.145:12306/mcp`。若你的端点不同，在 `~/.claude/settings.json` 的 `env` 段或 shell `export` 注入。 |
| 内网可达性 | 扫描引擎（`scripts/run_scan.sh` 首次从内网 Artifactory 下载）与远端知识库服务均需内网访问。 |

### 更新与卸载

```text
# 源码改动后刷新 marketplace 元数据
/plugin marketplace update everything-riscv

# 卸载插件
/plugin uninstall everything-riscv@everything-riscv

# 移除 marketplace
/plugin marketplace remove everything-riscv
```

## langfuse 监控平台

- 本插件带有自动上传相关 riscv 迁移相关对话的 hook 脚本 `hooks/langfuse_hook.py`。
- 在脚本中内置了 langfuse 平台的上传凭证，无需配置，安装插件即可使用。

## 目录结构

```
everything-riscv/
├── .claude-plugin/
│   ├── plugin.json             # 插件元数据
│   └── marketplace.json        # 本地 marketplace 声明（source 指向自身）
├── README.md
├── LICENSE
├── agents/                     # Subagent 定义
│   ├── riscv-scan-worker.md    # 阶段一：只读扫描
│   ├── riscv-migration-worker.md # 阶段二：迁移执行
│   ├── riscv-code-reviewer.md  # 阶段二/四：只读审查
│   ├── riscv-build-diagnoser.md # 阶段三：只读编译诊断
│   └── riscv-asm-analyzer.md   # 阶段四：热点性能分析
├── commands/                   # 斜杠命令
│   ├── scan.md
│   ├── migrate.md
│   └── verify.md
├── hooks/
│   ├── hooks.json
│   └── langfuse_hook.py
└── skills/
    └── riscv-migrate/          # 核心技能
        ├── SKILL.md
        ├── README.md
        ├── referens/           # 参考文档（Schema、迁移方法、Subagent 调度设计）
        │   ├── project_scan.md
        │   ├── code_migrate.md
        │   └── subagent-orchestration-design.md
        ├── resources/          # 环境脚本（工具链/QEMU/llvm-mca）
        └── scripts/            # 扫描 / 知识库查询 / 验证环境准备
```
