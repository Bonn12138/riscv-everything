# 工程扫描（scan / project_scan）

## 输出文件

- **路径**：`<project_root>/scan_result.json`（`project_root` 为被迁移的工程根目录）。
- **语义**：列出待迁移点，供阶段 B 逐条处理。

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
| `solver_type` | string/int | solver 类型（与实现一致） |
| `solver_description` | string | solver 类型说明 |
| `start_line` | int | 起始行（1-based） |
| `end_line` | int | 结束行（含） |
| `migration_suggestion` | string | 迁移建议文本 |

### `missing_class[]` 条目字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `class_type` | string | 固定为 `missing_class` |
| `missing_path` | string | 缺失的 riscv64 文件/目录路径（绝对路径） |
| `is_file` | bool | 是否为文件缺失 |
| `existing_arch_paths` | array | 已存在的其他架构文件/目录路径（绝对路径列表） |
| `migration_suggestion` | string | 建议文本 |

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
      "migration_suggestion": "NEON 向量加法，需 RVV 等价实现"
    }
  ],
  "missing_class": []
}
```

## `riscv_scan`（推荐）约定

- **入口**：`scripts/riscv_scan`（技能安装后一般位于 `.cursor/skills/riscv-migrate/scripts/riscv_scan`，但不应在文档里写死该路径）
- **生成输出**：在技能目录下 `python3 scripts/riscv_scan <target> -o <output_path>`（不指定 `-o` 时输出到当前目录 `./scan_result.json`）
