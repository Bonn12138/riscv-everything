# 工程扫描（scan / project_scan）

对应 `riscv-migrate` 技能的**阶段一**：扫描迁移点。

## 输出文件

- **路径**：`<project_root>/scan_result.json`（`project_root` 为被迁移的工程根目录）。
- **语义**：列出待迁移点，供**阶段二**按条目分流（汇编/非汇编）并按 `分流 → 迁移 → 知识库 → 验证` 串行处理。

## 在三大阶段中的位置

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
| `solver_type` | string/int | solver 类型（与实现一致）；阶段二 2.1 据此分流（汇编/非汇编） |
| `solver_description` | string | solver 类型说明 |
| `start_line` | int | 起始行（1-based） |
| `end_line` | int | 结束行（含） |
| `migration_suggestion` | string | 迁移建议文本 |
| `status` | string | 迁移状态：`TODO` / `START` / `DONE`（三态之一，互斥；详见「迁移点状态字段」一节） |
| `marking` | string | 备注文本：异常说明 / 性能影响 / TODO(后续) 等；`status=DONE` 时必填 |

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

```json
{
  "target": "/path/to/project",
  "suggestion_class_count": 1,
  "missing_class_count": 0,
  "suggestion_class": [
    {
      "class_type": "suggestion_class",
      "file_name": "foo.c",
      "file_path": "/path/to/project/src/foo.c",
      "solver_name": "SomeSolver",
      "solver_type": "text",
      "solver_description": "text suggestion",
      "start_line": 40,
      "end_line": 55,
      "migration_suggestion": "NEON 向量加法，需 RVV 等价实现",
      "status": "TODO",
      "marking": ""
    }
  ],
  "missing_class": []
}
```

## 迁移点状态字段（status / marking）

每个条目（`suggestion_class[]` 与 `missing_class[]`）都携带以下两个结构化字段，**权威源在 JSON 中，不再向源码写注释**：

| 字段 | 取值 | 说明 |
| ---- | ---- | ---- |
| `status` | `TODO` / `START` / `DONE` | 迁移状态三态互斥 |
| `marking` | string | 备注文本：异常说明 / 性能影响 / TODO(后续) 等；`status=DONE` 时必填 |

| `status` | 含义 | 何时设置 | `marking` 是否必填 |
| ------- | ---- | -------- | ----------------- |
| `TODO` | 未处理 | 阶段一扫描产出（默认值） | 否 |
| `START` | 开始迁移 | 阶段二 2.2 进入该条目时立即改写 | 否 |
| `DONE` | 迁移完成 | 阶段二 2.4 验证通过 | **是** |

`marking` 常见内容：

- 无异常：`无异常，性能持平`
- 语义差异：`语义差异：<具体点>，已通过测试规避`
- 性能下降：`性能低于原实现 N%，原因：<…>，建议进入阶段四 llvm-mca 优化`
- 阶段四优化后：`经阶段四 llvm-mca 优化后 IPC 由 <x> 提升到 <y>（<cpu>），关键改动：<具体点>`
- 需要后续修复：`TODO(后续)：<具体项>`

更新方式：直接编辑 JSON 文件（或 agent 用 `jq`/Python 改写条目），写回后必须保持 schema 与缩进一致；不要在源码里写 `MIGRATE-*` 注释。

## `riscv_scan`（推荐）约定

- **入口**：`scripts/riscv_scan`（技能安装后一般位于 `.cursor/skills/riscv-migrate/scripts/riscv_scan`，但不应在文档里写死该路径）
- **生成输出**：在技能目录下 `python3 scripts/riscv_scan <target> -o <output_path>`（不指定 `-o` 时输出到当前目录 `./scan_result.json`）

## 阶段二 2.1 分流依据（速查）

满足以下**任一**即视为汇编代码，走完整子闭环 `2.2 迁移 → 2.3 知识库 → 2.4 验证`；其余（源码/Shell/宏/Toml、纯 C/C++ 逻辑、标准库调用、无架构绑定）走轻量子流程（仅 2.2 代码适配 + 2.4 验证）。

1. **文件类型**：`.S` / `.s` / `.asm` 等汇编源文件。
2. **内联汇编**：源文件中含 `__asm__` / `__asm` / `asm volatile`，且迁移点落在该段内。
3. **intrinsic 热点**：含 x86 intrinsic（`_mm_*` / `_mm256_*` / `_mm512_*`）或 ARM NEON intrinsic（`vld1q_*` / `vmlaq_*` / `vst1q_*`），且是性能热点。
4. **架构强绑定 built-in**：含 `__builtin_ia32_*` / `__builtin_neon_*` 等。
5. **`solver_type` 为汇编类**：如 InlineAsm / Builtin。

登记字段：`file_path`、`start_line`/`end_line`、`asm_type`（`inline_asm`/`standalone_asm`/`intrinsic`/`builtin`）、`arch_source`（`x86`/`x86_64`/`arm`/`aarch64`）、`brief`（一句话描述，用于条目 `marking` 文本）、`status`、`marking`。