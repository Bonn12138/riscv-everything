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

扫描 x86/ARM 代码库并迁移到 RISC-V（含 RVV），实现五阶段闭环：

| 阶段 | 说明 |
|---|---|
| A — 扫描 | 盘点待迁移点，产出 `scan_result.json` |
| B — 迁移 | 逐条目改代码，主动查知识库补齐证据 |
| C — 知识库 | 连接远端 MCP 服务，查询指令 / 扩展 / intrinsic / CSR 对应关系 |
| D — 验证 | 自动准备工具链与 QEMU，做输出对比 |
| E — 性能 | llvm-mca 静态分析吞吐 / 瓶颈并优化 |

各阶段可独立运行，也可自动串联。

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
| `riscv-asm-analyzer` | 对热点汇编做 llvm-mca 静态分析，给出吞吐瓶颈与优化建议 |

## 知识库（远端）

技能的 **阶段 C** 通过内置脚本 `skills/riscv-migrate/scripts/run_query.sh` 连接一个**已部署的远端 RISC-V 文档 MCP 服务**（HTTP / Streamable HTTP），查询 ISA 手册、RVV 向量扩展、专项指令、性能优化文档。

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

### 关于 `source: "."` 的说明

本插件采用「插件根即 marketplace 根」的单插件布局，`marketplace.json` 中 `"source": "."` 指向自身。绝大多数 Claude Code 版本可正确识别；若你的版本不识别 `source: "."`，可改用子目录布局（将 `skills/` `commands/` `agents/` `.claude-plugin/` 移入一个子目录，并将 `source` 改为该子目录相对路径，如 `"./everything-riscv"`）。

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
- **编译验证**：RISC-V GCC 工具链（`riscv64-unknown-linux-gnu-gcc`）+ QEMU user mode（由 `resources/*_toolchain_env.sh` 在阶段 D 自动部署与加载）。
- **性能分析**：LLVM 工具链（`llvm-mca`，由 `resources/llvm_mca_env.sh` 在阶段 E 自动部署）。
- **知识库查询**：Python 3（`mcp` + `httpx`，已列入 `scripts/requirements-mcp.txt`，由 `run_query.sh` 自动安装）。

## 许可证

见 [LICENSE](./LICENSE)
