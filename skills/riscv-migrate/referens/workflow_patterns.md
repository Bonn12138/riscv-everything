# Workflow 编排约定（workflow_patterns）

本文件是 `skills/riscv-migrate/workflows/*.workflow.js` 的**设计与编码规范**。新增或修改 workflow 前先读本文。模式参考自 Bun 仓库的项目级 workflow（`lifetime-classify` / `phase-a-port` / `phase-g-mega-swarm` 等），但按本插件「x86/ARM → RISC-V 迁移」场景与插件机制做了改造。

## 1. 为什么用 workflow（与单 agent 模式的关系）

本插件的阶段 A–E（见 `SKILL.md`）默认是**单 agent 串行**，适合小批量、需要人工介入精细判断的场景。`workflows/` 提供**批量并行 + 对抗验证 + 循环收敛**的编排，适合 50+ 条目的大规模迁移：

- 按条目/文件扇出多个 agent 并行
- 每次迁移用 2 票对抗式 review 把关（default refuted）
- 验证阶段循环到 QEMU 输出与原始 baseline 全部一致才收敛
- HARD RULES 压住规模化时模型失控与 reward-hacking

> 两套模式共享同一批脚本（`scripts/`）、同一批 agent（`agents/`）、同一份迁移细则（`code_migrate.md`）。workflow 不替换单 agent 命令，而是其并行版本。

## 2. 调用机制（scriptPath 模式，关键）

**插件不支持顶层 `workflows/` 自动加载**（官方插件仅识别 `skills/commands/agents/hooks/output-styles/themes/monitors`）。因此：

- workflow 脚本放在 **`skills/riscv-migrate/workflows/<name>.workflow.js`**
- 由对应的 `commands/<name>-swarm.md` 引导 Claude 用 **Workflow 工具**发起：
  - `scriptPath` = `${CLAUDE_PLUGIN_ROOT}/skills/riscv-migrate/workflows/<name>.workflow.js`
  - `args` = `{ target, skill_root, scan_result_path, ... }`
- **每次运行需用户 opt-in**（插件安装不预授权）——这是预期行为，command 文档里说明。

`skill_root` 等绝对路径**必须由 command 层先解析成绝对路径再经 `args` 传入**（workflow 脚本无 fs/node 访问，不能自己 `__dirname`）。command 层做法：先 `echo ${CLAUDE_PLUGIN_ROOT}` 拿到插件根，拼出 `skill_root` 绝对路径，放进 `args`。

## 3. 脚本骨架模板

```js
export const meta = {
  name: "<name>",
  description: "<一句话，会被 /workflows 列出>",
  phases: [ { title: "X", detail: "..." }, ... ],   // 纯字面量，与 phase() 调用一致
};

const A = typeof args === "string" ? JSON.parse(args) : (args || {});
const TARGET   = A.target;            // 目标工程根（必填）
const SKILL    = A.skill_root;        // skill 绝对路径（必填，command 层解析后传入）
const WORKDIR  = `${TARGET}/.riscv_migrate`;
// ... 参数校验、默认值 ...

const RESULT_SCHEMA = { type:"object", required:[...], properties:{...} };

phase("X");
const results = await pipeline(ITEMS,
  (item) => agent(promptFor(item), { label:`x:${item.id}`, phase:"X", schema: RESULT_SCHEMA }),
  (prev, item) => agent(verifyPrompt(item, prev), { phase:"Y", schema: VERIFY_SCHEMA }),
);
// 末尾：commit-agent 落地元数据（见 §5）
phase("Commit");
await agent(commitPrompt(aggregatedJson), { phase:"Commit", label:"commit" });
return { stats... };
```

## 4. agent 调用插件脚本的方式

### 4.1 绝对路径 + 自定位

所有 `scripts/` 与 `resources/` 脚本用 `BASH_SOURCE` 自定位 skill 根，**不依赖 `CLAUDE_PLUGIN_ROOT`**，可在任意 cwd 调用。agent prompt 里直接给绝对路径：

```bash
# 知识库查询（幂等，无副作用；JSON stdout 含 file_path/header_path 证据链）
bash <SKILL>/scripts/run_query.sh -t search_rvv_vector_extensions -q "__riscv_vsetvl"
bash <SKILL>/scripts/run_query.sh --list-tools
# 环境变量覆盖端点：RISCV_DOC_MCP_URL=... ；传输：RISCV_DOC_MCP_TRANSPORT=http|sse
```

### 4.2 env source 前缀（shell-scoped，关键）

`source resources/env.sh` 只对当前 shell 生效，**env 不跨 agent 共享**。因此任何要编译 / 跑 QEMU / 跑 llvm-mca 的 agent，其 Bash 命令**必须自带 source 前缀**：

```bash
source <SKILL>/resources/env.sh && riscv64-unknown-linux-gnu-gcc -O2 -march=rv64gcv -mabi=lp64d -static -o riscv.out riscv.c
source <SKILL>/resources/env.sh && qemu-riscv64 -cpu max ./riscv.out
```

`resources/env.sh` 由 `prepare_verify_env.sh` 首次生成（聚合 `resources/env.d/*.sh`：10-arm / 15-x86 / 20-riscv / 25-llvm-mca / 30-qemu）。**verify/mca workflow 在循环外整轮跑一次 `prepare_verify_env.sh`**（幂等但首次 5–15 分钟下载），之后所有 agent 只 source 已生成的 env.sh。

### 4.3 各脚本契约速查

| 脚本 | 调用 | 输出 | 副作用 |
|---|---|---|---|
| `scripts/run_query.sh` | `-t <tool> -q "<q>"` / `--list-tools` | JSON（含证据链）/ 文本 | 幂等；首次 pip 装依赖；联网查 KB |
| `scripts/run_scan.sh` | `<source-dir>` | 写 `<source-dir>/scan_result.json` | **重跑覆盖**；首次下载扫描引擎 |
| `scripts/prepare_verify_env.sh` | （无参） | stdout 提示 | 幂等；生成 `resources/env.sh`+`env.d/`；首次下载工具链/QEMU/llvm-mca |

> `resources/python_venv.sh` 未完成，**禁用**。本插件一律系统 Python + 脚本自举依赖。

## 5. 文件落地模式（脚本无 fs，关键）

workflow 脚本本身**不能读写文件**（无 fs/node）。落地分两类：

1. **代码产物**（`*_riscv` 源文件、测试）：由 migrate/fix agent 在流程内用 **Write/Edit** 直接写。各 agent 改不同文件 → 无竞态。
2. **元数据**（`progress.json` / `classified.json`）：由 workflow 末尾**单个 commit-agent** 串行写。脚本把聚合后的 JSON 字符串拼进 commit-agent 的 prompt，agent 用 **Write** 落地，return 简短确认。这样大 JSON 不回流污染主循环 context，且单 agent 写无竞态。

> **绝不修改 `scan_result.json`**（重扫会被覆盖）。所有进度写独立的 `<target>/.riscv_migrate/`。

## 6. 数据 schema

### 6.1 `<target>/.riscv_migrate/progress.json`

**条目状态机（单调推进；status 是阶段闸门，富结果进 sidecar 字段）：**

```
pending ──migrate──► done | skipped | blocked
done    ──verify───► verified                 （+ sidecar .verify）
verified ──mca─────► perf_done                （+ sidecar .mca；仅 asm_hotspot）
```

- `migrating` 为在途瞬态，通常不落盘（migrate 直接写终态 `done/skipped/blocked`）。
- `skipped`（无需迁移）/ `blocked`（依赖未就绪，可重跑重试）是 migrate 的终态；`verified`/`perf_done` 一旦写入即单调，不回退。
- 非热点的 `verified` 条目即终态（mca 只吃 `asm_hotspot`）。

**每个 swarm 的 Survey/Load 步骤统一成「读 progress.json → 按本阶段谓词过滤 → 得工作集」：**

| 阶段 (workflow) | 读 | 工作集谓词（只处理满足者） | 写入终态 |
|---|---|---|---|
| `classify`  | `scan_result.json` | 条目 key 不在 `progress.json`（首次分类；已存在则保留旧 status/产物只刷新分类） | `pending`（+ asm_flag/strategy/tier） |
| `migrate`   | `progress.json` | `status ∈ {pending, blocked}` | `done` \| `skipped` \| `blocked`（+ .review/.evidence） |
| `verify`    | `progress.json` | `status == "done"` | `verified`（+ .verify） |
| `mca`       | `progress.json` | `status == "verified"` ∧ `asm_flag == "asm_hotspot"` | `perf_done`（+ .mca） |

> 「项目当前在第几阶段」= `progress.json` 里 `status` 的分布：`pending` 多 → 迁移期；`done` 多 → 待验证；`verified` 多 → 待优化；`perf_done` 多 → 收尾。各阶段谓词互斥单调，故无需中央状态机——每个 workflow 自己 Survey 出活干，没活即该阶段完成。key = 唯一键（见 §6.3）。

```jsonc
{
  "target": "<abs path>",
  "updated": "<ISO8601>",          // commit-agent 用 `date -Iseconds` 写
  "entries": {
    "<file_path>|<start_line>|<end_line>|<solver_type>": {
      "status": "pending|done|verified|perf_done|skipped|blocked",
      "asm_flag": "non_asm|light_asm|asm_hotspot",
      "strategy": "c_direct|intrinsic|rvv_asm",
      "tier": 0,
      "artifact": "<_riscv 产物绝对路径>",
      "evidence": [                          // migrate 写：KB 证据链
        { "tool": "search_rvv_vector_extensions", "query": "__riscv_vsetvl",
          "file_path": "...", "header_path": "..." }
      ],
      "review": { "accepted": true, "issues_fixed": 2, "remaining": 0 },   // migrate 写
      "verify": { "passing": true, "triaged": false, "round": 3, "diag": "..." }, // verify 写
      "mca": { "mcpu": "zhufeng2", "ipc_before": 0.4, "ipc_after": 0.7, "regression_ok": true }, // mca 写
      "blocked_on": "<reason 或空>",
      "updated": "<ISO8601>"
    }
  }
}
```

### 6.2 `<target>/.riscv_migrate/classified.json`

classify workflow 产出：

```jsonc
{
  "target": "<abs path>",
  "generated": "<ISO8601>",
  "entries": [
    { "key": "...", "file_path": "...", "start_line": 90, "end_line": 98,
      "solver_type": 12, "asm_flag": "non_asm", "strategy": "c_direct",
      "tier": 3, "reason": "纯架构宏判断，无汇编/intrinsic" }
  ],
  "by_asm_flag": { "non_asm": 180, "light_asm": 15, "asm_hotspot": 7 },
  "by_strategy": { "c_direct": 180, "intrinsic": 12, "rvv_asm": 10 }
}
```

### 6.3 唯一键

`file_path + "|" + start_line + "|" + end_line + "|" + solver_type`。scan_result.json 重扫后字段稳定，此键可跨运行续传。

## 7. 并发安全策略

- **按 `file_path` 聚合**：同一文件的多个条目在编排层合并给**同一个 agent**（避免并发改同一文件）；不同文件并行。
- **按 tier 分批**：migrate 先做汇编类（solver_type 4=内联汇编、2=内置函数）、再做非汇编类（12=Rust 源、9=Shell）。同批内并行、批间串行，让上层能引用已迁移的下层。
- **预算保护**：单 workflow 总 agent ≤ 1000（平台硬限）。大批量按 `args.max_parallel`（默认 16）切片；verify/review 用 `.slice(0, N)` 守住。
- **chunked write**：单次 Write/Edit ≤ ~800 行，避免 token 限流被 kill（大文件分块：首 Write + 循环 Edit 追加）。

## 8. HARD RULES（写进每个会改代码的 agent prompt）

改编自 Bun workflow，针对 RISC-V 迁移：

```
**HARD RULES（违反即判失败）：**
1. 只改分配给你的文件。同文件多条目已由编排层合并到一个 agent，你不要碰别的文件。
2. 新增 RISC-V 实现用 `_riscv` 后缀（与工程约定冲突时在 progress.note 说明，不要硬塞）。
3. 出现任一信号——指令名/扩展名(如 Zba/V/Zvbb)、intrinsic(__riscv_*)、ABI/CSR/特权字段(mstatus…)
   ——必须先用 `bash <SKILL>/scripts/run_query.sh -t <tool> -q "<q>"` 查知识库再下结论，
   并在 evidence 里保留返回的 file_path / header_path。禁止凭感觉编指令/扩展。
4. 向量源必须用 RVV 1.0（汇编或 intrinsic）。禁止为省事把汇编整块退化成纯 C（除非用户明确允许）。
5. 禁止 reward-hack 注释：不写 `// PORT NOTE`、`// TODO(port)`、`reshaped for borrowck`、
   或长篇 `// SAFETY:` 来"解释为什么这个 workaround 可以"。需要一段话辩护 = 代码错了，直接修代码。
6. 若 target 是 git 仓库：显式路径提交 `git add <你改的文件>` && `git commit -m "..."`；
   禁止 `git add .` / reset / checkout / stash / rebase；commit message ≤80 字符描述改了什么；禁止 --allow-empty。
7. 每轮验证改动后，RISC-V 侧 QEMU 输出必须与原始 x86/ARM baseline 一致——正确性不妥协。
```

### 8.1 anti-reward-hack 检测（review agent 执行）

```
检查本次改动是否 reward-hacking：
- `git show <commit> | grep -cE 'PORT NOTE|TODO\(port\)|reshaped|SAFETY:.{80,}'` 命中 → 判 severity:"reward-hack"，拒绝。
- 新增非 FFI 的内联 asm 绕过 / unsafe 包裹同一 extern 在 >2 处 → 拒绝。
- 把本应 RVV 的向量段改成标量循环 → 拒绝（severity:"degrade-to-c"）。
default refuted：只有给出 .zig/原实现:line + _riscv:line + 可观察发散才报真问题。
```

## 9. 禁用 API（workflow 脚本里）

脚本运行环境**禁止**以下，调用会抛错：

- ❌ `Math.random()` / `Date.now()` / `new Date()`（无参构造）
- ❌ 任何 fs / node 模块

替代：

- 时间戳：command 层把 `args.now`（ISO 字符串）传入；或让 commit-agent 用 `date -Iseconds` 写。
- 随机采样：用确定性 `index % N === 0`，不用 `Math.random()`。

## 10. agent 复用（嵌入审查/分析清单，避免重造）

- **review 阶段**：agent prompt 嵌入 `agents/riscv-code-reviewer.md` 的 5 类审查清单（RVV 正确性 / ABI / 内存对齐原子性 / 指令选择扩展依赖 / 平台约束），每条发现带 `路径:行号 + 问题 + 证据(KB引用) + 修复`。
- **mca 阶段**：agent prompt 嵌入 `agents/riscv-asm-analyzer.md` 的分析流程（提 hot.s → llvm-mca → 瓶颈排行 → 优化建议 → 回归验证）。
- **KB 工具选择**：嵌入 `code_migrate.md` / `SKILL.md` 阶段 C 的选工具规则（core_isa / rvv / special / docs_tools 四组知识库覆盖范围）。

## 11. 产物目录约定

所有 workflow 运行时产物集中到 `<target>/.riscv_migrate/`（独立目录，不污染目标仓库）：

```
<target>/.riscv_migrate/
├── progress.json          # 条目状态（跨运行续传）
├── classified.json        # classify 产出
├── baseline/              # verify：原始 x86/ARM 输出缓存（首次生成，不重跑）
├── diag/                  # verify：每轮诊断输出
├── passing.txt            # verify：已通过条目（累积，不重跑）
└── triaged-slow.txt       # verify：baseline 也挂/环境慢的条目（排除，不当 bug）
```

README 提示用户：该目录可整体删除重建；若 target 是 git 仓库建议加进 `.gitignore`。

## 12. 收敛与终止条件（verify/mca 循环用）

- **verify-swarm**：`failing=0 && uncovered=0` → `done:true` 提前返回；否则跑到 `max_rounds`。`passing.txt` 累积永不重跑。
- **mca-analyze**：单热点满足以下任一即停——IPC ≥ Dispatch Width × 70%；连续两轮 Block RThroughput 降幅 < 5%；出现 `No resource or data dependency bottlenecks`。每轮优化后必须回 QEMU 验证正确性不回归。
