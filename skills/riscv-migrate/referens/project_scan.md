# 工程扫描（scan / project_scan）

对应 `riscv-migrate` 技能的**阶段一**：扫描迁移点。

## 输出文件

- **路径**：`<project_root>/scan_result.json`（`project_root` 为被迁移的工程根目录）。
- **语义**：列出待迁移点，供**阶段二**按条目分流（汇编/非汇编）并按 `分流 → 迁移 → 知识库 → 验证` 串行处理。

## 在四大阶段中的位置

```
阶段一(本文档) → 阶段二(按条目分流迁移) → 阶段三(交叉编译) → 阶段四(性能分析)
```

- 阶段一产出 `scan_result.json`；
- 阶段二加载 `scan_result.json` 逐条处理，每个条目通过结构化字段 `status` 与 `marking` 同步进度（**权威源在 JSON 中**，不再向源码写注释）。

## 推荐运行方式

扫描器是 Python 脚本。请在 **本技能根目录** `<skill_root>` 安装依赖并执行扫描：

```bash
cd "<skill_root>"
python3 -m pip install -r scripts/requirements.txt

# 在目标工程根目录（或任意目录）执行
python3 "<skill_root>/scripts/riscv_scan" "<project_root>" -o "<project_root>/scan_result.json"
```

扫描阶段 **不需要** 交叉工具链或 QEMU。进入迁移与对比阶段时，可按需在 `<skill_root>/resources/` 下执行各初始化脚本（详见 [code_migrate.md](code_migrate.md)），例如：`x86_toolchain_env.sh`、`arm_toolchain_env.sh`、`riscv_toolchain_env.sh`、`qemu_static_env.sh`。

## 是否重新扫描

- **已存在** `scan_result.json`：**默认不执行**扫描（不调用脚本、不覆盖），直接读取。
- **用户明确要求重新扫描**：删除或重命名旧文件后再执行扫描脚本或手工更新。

## `scan_result.json` Schema

顶层对象：

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | string | 扫描目标路径（文件或目录，绝对路径） |
| `suggestion_class_count` | int | `suggestion_class` 条目数 |
| `missing_class_count` | int | `missing_class` 条目数 |
| `suggestion_class` | array | 迁移建议条目列表 |
| `missing_class` | array | 缺失架构实现（目录/文件）条目列表 |

### `suggestion_class[]` 条目字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `class_type` | string | 固定为 `suggestion_class` |
| `file_name` | string | 文件名 |
| `file_path` | string | 文件绝对路径 |
| `solver_name` | string | 规则/solver 名 |
| `solver_type` | int | solver 类型，**整数枚举**（扫描器实际输出，映射表见下）；阶段二 2.1 据此分流（汇编/非汇编/**AutoVecCandidate**） |
| `solver_description` | string | solver 类型说明 |
| `start_line` | int | 起始行（1-based） |
| `end_line` | int | 结束行（含） |
| `migration_suggestion` | string | 迁移建议文本 |
| `tier` | string | 热点优先级：`hot` / `warm` / `cold`（决定是否做 A/B 择优与优化顺序；默认 `warm`，`AutoVecCandidate`/intrinsic 热点建议标 `hot`） |
| `status` | string | 迁移状态：`TODO` / `START` / `DONE`（三态之一，互斥；详见「迁移点状态字段」一节） |
| `marking` | string | 备注文本：异常说明 / 性能结论一句话 / TODO(后续) 等；`status=DONE` 时必填 |
| `perf` | object/null | 结构化性能数据（口径见 [perf_measure.md](perf_measure.md) 第 4 节）：`baseline_kind` / `metrics` / `env` / `ab_variants[]`；asm/RVV/AutoVec 条目 `DONE` 时必填，`TODO`/`START` 时为 `null` |

#### `solver_type` 整数枚举映射表（与扫描器实现一致）

**扫描器输出的是整数，不是字符串**。读取 `scan_result.json` 做分流/匹配时必须按下表换算，直接按字符串 `"InlineAsm"` 等匹配会全部失配：

| `solver_type` 值 | 对应 solver | 阶段二 2.1 分流 |
| --- | --- | --- |
| `1` | `IntrinsicsSolver`（内联函数/intrinsic） | **汇编完整闭环** |
| `3` | `MacrosSolver`（宏定义） | 轻量适配（含架构宏时） |
| `4` | `InlineAsmSolver`（内联汇编） | **汇编完整闭环** |

> 基础扫描器（`scripts/riscv_scan`）只产出上述三类；`AutoVecCandidate` 等其余类型由阶段一并行扫描 Subagent（`autovec_hotspot` 等维度）补充产出，补充条目同样遵守本 schema。遇到未知整数值时，**回退到源码特征分流**（SKILL.md 2.1 第 1–4 条），不得因枚举不认识而跳过条目。

#### 扫描器原始输出 vs 主 Agent 归一化

**扫描器直接输出的 JSON 与本 schema 有三处差异**，阶段一完成、主 Agent 写入（或合并去重后回写）`scan_result.json` 时必须归一化：

| 字段 | 扫描器原始输出 | 归一化后（schema 要求） |
| ---- | -------------- | ---------------------- |
| `status` | 小写 `"todo"` | 大写 `"TODO"`（后续状态转换统一用大写） |
| `tier` | **不输出该字段** | 主 Agent 补齐（默认 `"warm"`，可向量化热点/intrinsic 热点标 `"hot"`） |
| `perf` | **不输出该字段** | 主 Agent 补 `"perf": null` 占位 |

**状态匹配必须大小写不敏感**：读旧文件或未归一化文件时，`"todo"`/`"Todo"`/`"TODO"` 均视为 `TODO`，避免归一化遗漏导致条目被当成"未知状态"跳过。

### `missing_class[]` 条目字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `class_type` | string | 固定为 `missing_class` |
| `missing_path` | string | 缺失的 riscv64 文件/目录路径（绝对路径） |
| `is_file` | bool | 是否为文件缺失 |
| `existing_arch_paths` | array | 已存在的其他架构文件/目录路径（绝对路径列表） |
| `migration_suggestion` | string | 建议文本 |
| `status` | string | 迁移状态：`TODO` / `START` / `DONE` |
| `marking` | string | 备注文本（与 `suggestion_class` 同义） |

### 示例

以下示例展示**归一化后**的条目（扫描器原始输出的 `"status": "todo"` 已由主 Agent 归一化为大写、补齐 `tier`/`perf`；`solver_type` 保持扫描器的整数输出）：

```json
{
  "target": "/path/to/project",
  "suggestion_class_count": 2,
  "missing_class_count": 0,
  "suggestion_class": [
    {
      "class_type": "suggestion_class",
      "file_name": "foo.c",
      "file_path": "/path/to/project/src/foo.c",
      "solver_name": "IntrinsicsSolver",
      "solver_type": 1,
      "solver_description": "内联函数",
      "start_line": 40,
      "end_line": 55,
      "migration_suggestion": "NEON 向量加法，需 RVV 等价实现",
      "tier": "hot",
      "status": "TODO",
      "marking": "",
      "perf": null
    },
    {
      "class_type": "suggestion_class",
      "file_name": "sum.c",
      "file_path": "/path/to/project/src/sum.c",
      "solver_name": "AutoVecHeuristic",
      "solver_type": "AutoVecCandidate",
      "solver_description": "纯 C 数据并行循环，x86 上靠自动向量化获得性能",
      "start_line": 88,
      "end_line": 102,
      "migration_suggestion": "数组逐元素累加循环，无跨迭代依赖迹象；RV 侧须确认向量化或改写显式 RVV intrinsic",
      "tier": "hot",
      "status": "TODO",
      "marking": "",
      "perf": null
    }
  ],
  "missing_class": []
}
```

## 迁移点状态字段（status / marking / perf）

每个条目（`suggestion_class[]` 与 `missing_class[]`）都携带以下结构化字段，**权威源在 JSON 中，不再向源码写注释**：

| 字段 | 取值 | 说明 |
| ---- | ---- | ---- |
| `status` | `TODO` / `START` / `DONE` | 迁移状态三态互斥 |
| `marking` | string | 备注文本：异常说明 / 性能结论一句话 / TODO(后续) 等；`status=DONE` 时必填 |
| `perf` | object/null | 结构化性能数据（asm/RVV/AutoVec 条目 `DONE` 时必填；口径与结构见 [perf_measure.md](perf_measure.md) 第 4 节） |

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

更新方式：直接编辑 JSON 文件（或 agent 用 `jq`/Python 改写条目），写回后必须保持 schema 与缩进一致；不要在源码里写 `MIGRATE-*` 注释。

## `riscv_scan`（推荐）约定

- **入口**：`scripts/riscv_scan`（技能安装后一般位于 `.cursor/skills/riscv-migrate/scripts/riscv_scan`，但不应在文档里写死该路径）
- **生成输出**：在技能目录下 `python3 scripts/riscv_scan <target> -o <output_path>`（不指定 `-o` 时输出到当前目录 `./scan_result.json`）

## 阶段二 2.1 分流依据（速查）

满足以下**任一**即视为汇编代码，走完整子闭环 `2.2 迁移 → 2.3 知识库 → 2.4 验证 → 2.4.5 快速 mca 门禁`；其余（源码/Shell/宏/Toml、纯 C/C++ 逻辑、标准库调用、无架构绑定、**且非可向量化热点**）走轻量子流程（仅 2.2 代码适配 + 2.4 验证）。

> **`solver_type` 取值形态**：基础扫描器输出**整数**（`1`=IntrinsicsSolver、`3`=MacrosSolver、`4`=InlineAsmSolver，见上文映射表）；并行扫描 Subagent 补充的条目（如 AutoVecCandidate）用**字符串标签**。分流代码对两种形态都要能匹配——`solver_type in (4, "InlineAsm")` 这类**混合判断**，不要写成只认字符串或只认整数。

1. **文件类型**：`.S` / `.s` / `.asm` 等汇编源文件。
2. **内联汇编**：源文件中含 `__asm__` / `__asm` / `asm volatile`，且迁移点落在该段内。
3. **intrinsic 热点**：含 x86 intrinsic（`_mm_*` / `_mm256_*` / `_mm512_*`）或 ARM NEON intrinsic（`vld1q_*` / `vmlaq_*` / `vst1q_*`），且是性能热点。
4. **架构强绑定 built-in**：含 `__builtin_ia32_*` / `__builtin_neon_*` 等。
5. **`solver_type` 为汇编类**：整数 `4`（InlineAsmSolver）/ `1`（IntrinsicsSolver），或字符串 `InlineAsm` / `Builtin` / `AutoVecCandidate`（补充扫描条目）。
6. **纯 C 可向量化热点**：`solver_type="AutoVecCandidate"`（数据并行纯 C 循环；先试编译器自动向量化 + `-fopt-info-vec`，失败则改写显式 RVV intrinsic）。

登记字段：`file_path`、`start_line`/`end_line`、`asm_type`（`inline_asm`/`standalone_asm`/`intrinsic`/`builtin`/`autovec`）、`arch_source`（`x86`/`x86_64`/`arm`/`aarch64`/`none`）、`tier`（`hot`/`warm`/`cold`）、`brief`（一句话描述，用于条目 `marking` 文本）、`status`、`marking`、`perf`。