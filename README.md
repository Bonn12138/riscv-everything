# everything-riscv

RISC-V 全栈迁移工具链 Claude Code 插件。面向 x86/ARM 代码库，提供从工程扫描到迁移验证再到性能优化的完整闭环。

## 概述

本插件围绕一个核心技能 `riscv-migrate` 构建，配套：

- **3 条斜杠命令** — 快速启动扫描 / 迁移 / 验证流程
- **2 个专用智能体** — 迁移代码审查 + 汇编热点分析
- **远端知识库查询** — 通过技能内置脚本连接远端 RISC-V 文档 MCP 服务，查询 ISA / RVV 文档，**无需本地部署服务端**

## 技能

### riscv-migrate

> 详情见 `skills/riscv-migrate/SKILL.md`

扫描 x86/ARM 代码库并迁移到 RISC-V（含 RVV），按**三大阶段**推进，性能分析作为独立 agent 仅在迁移完成后启动：

| 阶段 | 说明 |
|---|---|
| **一 — 扫描** | 盘点待迁移点，产出 `scan_result.json` |
| **二 — 按条目分流迁移** | 每个条目严格串行 `分流 → 迁移 → 知识库 → 验证`，通过改写 `scan_result.json` 的 `status` / `marking` 字段同步进度（`TODO → START → DONE + marking`） |
| **三 — 工程级交叉编译** | 用 RISC-V 交叉工具链编译整个工程，修编译错误，产出可在 QEMU 跑通的 RV 可执行程序 |
| **四 — 性能分析（独立 agent）** | 仅在阶段三通过后召唤 `riscv-asm-analyzer`，对热点用 `llvm-mca` 分析并迭代优化 |

各阶段串行执行；二全部条目 DONE 后才能进入三；三通过后才召唤四。

> **零交互自主运行**：技能/命令执行期间默认不向用户提问，所有决策按 `skills/riscv-migrate/SKILL.md`「自主运行原则」默认处理；只有 5 类白名单场景（找不到工程根 / 条目级 5 轮不一致 / 编译连错 3 次 / 内网不通 / 用户主动问）才请示。

## 命令

插件命令以 `everything-riscv` 为命名空间调用（即 `/everything-riscv:<命令>`）：

| 命令 | 用途 |
|---|---|
| `/everything-riscv:scan` | 扫描工程，生成待迁移点清单 |
| `/everything-riscv:migrate` | 启动迁移流程（按条目逐项迁移） |
| `/everything-riscv:verify` | 触发 QEMU 验证：自动准备工具链 → 编译 → 输出对比 → 回流修复 |

## 智能体

| 智能体 | 用途 |
|---|---|
| `riscv-code-reviewer` | 审查迁移后的 RISC-V 代码：向量化正确性、ABI 约定、内存对齐、指令选择 |
| `riscv-asm-analyzer` | 阶段四独立 agent：对热点汇编做 llvm-mca 静态分析，给出吞吐瓶颈与优化建议；每轮优化后回到阶段二 2.4 验证 |

## 知识库（远端）

技能的**阶段二 2.3** 通过内置脚本 `skills/riscv-migrate/scripts/run_query.sh` 连接一个**已部署的远端 RISC-V 文档 MCP 服务**（HTTP / Streamable HTTP），查询 ISA 手册、RVV 向量扩展、专项指令、性能优化文档。

- 默认端点：`http://10.2.71.145:12306/mcp`（内网），可用环境变量 `RISCV_DOC_MCP_URL` 覆盖。
- 暴露工具：`search_core_isa_manuals` / `search_rvv_vector_extensions` / `search_special_instructions` / `search_docs_tools`。
- **无需本地部署服务端、无需 Milvus / 向量 / 重排模型**：本插件只含 MCP 客户端（`scripts/query.py`），服务端由团队统一维护。
- 查询示例：

  ```bash
  # 列出远端服务暴露的工具
  bash skills/riscv-migrate/scripts/run_query.sh --list-tools
  # 查询某条指令 / 扩展
  bash skills/riscv-migrate/scripts/run_query.sh -t search_core_isa_manuals -q "mstatus MPP"
  bash skills/riscv-migrate/scripts/run_query.sh -t search_rvv_vector_extensions -q "__riscv_vsetvl"
  ```

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

安装后插件会被复制到 `~/.claude/plugins/cache/everything-riscv/everything-riscv/<version>/`，以**用户级（user scope）**生效——所有项目均可使用 `/everything-riscv:*` 命令、`riscv-migrate` 技能与两个子代理。

### 配置（按需）

| 配置项 | 说明 |
|---|---|
| `RISCV_DOC_MCP_URL` | 远端知识库 MCP 端点；不设则用默认内网地址 `http://10.2.71.145:12306/mcp`。若你的端点不同，在 `~/.claude/settings.json` 的 `env` 段或 shell `export` 注入。 |
| 内网可达性 | 扫描引擎（`scripts/run_scan.sh` 首次从内网 Artifactory 下载）与远端知识库服务均需内网访问。 |

`~/.claude/settings.json` 示例（可选）：

```json
{
  "env": {
    "RISCV_DOC_MCP_URL": "http://10.2.71.145:12306/mcp"
  }
}
```

### 更新与卸载

```text
# 源码改动后刷新 marketplace 元数据
/plugin marketplace update everything-riscv

# 卸载插件
/plugin uninstall everything-riscv@everything-riscv

# 移除 marketplace
/plugin marketplace remove everything-riscv
```

> 若修改了插件源码，可执行 `/plugin marketplace update everything-riscv` 刷新，或重启 Claude Code 会话使其重新加载。如加载异常，用 `claude --debug` 启动查看插件加载日志。

## langfuse 监控平台

  - 本插件带有自动上传相关riscv迁移相关对话的hook脚本 hooks/langfuse_hook.py。
  - 在脚本中内置了langfuse平台的上传凭证，无需配置，安装插件即可使用

## 目录结构

```
everything-riscv/
├── .claude-plugin/
│   ├── plugin.json             # 插件元数据
│   └── marketplace.json        # 本地 marketplace 声明（source 指向自身）
├── README.md
├── LICENSE
├── skills/
│   └── riscv-migrate/          # 核心技能
│       ├── SKILL.md
│       ├── scripts/            # 扫描 / 知识库查询 / 验证环境准备脚本
│       ├── referens/           # 迁移与扫描细则
│       └── resources/          # 工具链 / QEMU / llvm-mca 环境部署脚本
├── commands/                   # 斜杠命令（/everything-riscv:<name>）
│   ├── scan.md
│   ├── migrate.md
│   └── verify.md
└── agents/                     # 专用智能体
    ├── riscv-code-reviewer.md
    └── riscv-asm-analyzer.md
```

## 依赖

- **扫描 / 迁移**：Bash（扫描引擎二进制由 `scripts/run_scan.sh` 首次从内网 Artifactory 下载；知识库查询由 `scripts/run_query.sh` 自举 Python 依赖，无需 venv）。
- **编译验证**：RISC-V GCC 工具链（`riscv64-unknown-linux-gnu-gcc`）+ QEMU user mode（由 `resources/*_toolchain_env.sh` 在阶段二 2.4 / 阶段三自动部署与加载）。
- **性能分析**：LLVM 工具链（`llvm-mca`，由 `resources/llvm_mca_env.sh` 在阶段四自动部署）。
- **知识库查询**：Python 3（`mcp` + `httpx`，已列入 `scripts/requirements-mcp.txt`，由 `run_query.sh` 自动安装）。

## 许可证

见 [LICENSE](./LICENSE)
