---
description: 按 scan_result.json 条目逐项迁移到 RISC-V（含 RVV），自动查知识库、自动触发验证
argument-hint: [scan_result.json]
---

# /everything-riscv:migrate — 启动迁移流程

加载 `scan_result.json`，按条目逐一将 x86/ARM 代码迁移到 RISC-V（含 RVV 向量扩展）。每个条目迁移完成后自动查知识库补齐证据、触发验证指导。

## 前置条件

- 已运行 `/riscv:scan` 产出 `scan_result.json`
- 或手动构造合法的 `scan_result.json`

## 执行流程

1. **加载清单**：读取 `scan_result.json`（如果 `$ARGUMENTS` 指定了路径则使用该路径，否则在当前目录查找）
2. **逐条目迁移**（一体化闭环）：
   - 读取原始代码片段
   - 判断迁移策略（intrinsic 替换 / 内联汇编改写 / 纯 C 降级 / RVV 向量化）
   - **主动查知识库**：遇到指令名 / 扩展名 / intrinsic / ABI / CSR 字段时查询 MCP 知识库（`search_core_isa_manuals` / `search_rvv_vector_extensions`）
   - 生成 RISC-V 替代代码并写入目标文件
   - 输出证据链（`file_path` / `header_path` / 文档引用）
   - **自动触发验证指导**：给出编译命令与预期产物
3. **汇总报告**：完成条目数、跳过条目数、需人工确认条目数

## 约束

- 禁止猜测指令 / 扩展对应关系，必须查知识库
- 每次改动后主动推进验证步骤
- 不依赖虚拟环境，使用系统 Python + 脚本自举依赖
