# 性能度量体系（perf_measure）

本文定义 `riscv-migrate` 技能的**性能度量口径**。阶段二 2.4 的 `marking` 性能结论、2.4.5 快速 mca 门禁、阶段四优化与 A/B 择优，**必须**采用本文口径。参考 [code_migrate.md](code_migrate.md)、[project_scan.md](project_scan.md)。

## 0. 核心禁令（先记住）

- **禁止用 QEMU user-mode 的 wall-clock 时间作为性能证据**。QEMU TCG 的运行时间由翻译开销主导，与目标微架构无关；"在 QEMU 里跑 5 次取中位数、相对 x86 ±5%" 这类跨架构 wall-clock 对比**没有物理意义**，不得写入 `marking`。
- **禁止用跨架构（x86 原生 vs RV/QEMU）的任何运行时间比值**宣称"性能持平/下降 N%"。
- 正确性验证（QEMU 逐字节 diff）不受本禁令影响——那是功能验证，不是性能度量。

## 1. 三级度量体系

| 级别 | 手段 | 环境 | 决定性 | 用途 |
| ---- | ---- | ---- | ------ | ---- |
| **L1 静态** | `llvm-mca`（IPC / Block RThroughput / Resource Pressure） | 开发机 | 是 | 2.4.5 快速门禁（必做）、阶段四深度迭代、A/B 择优判据 |
| **L2 动态指令数** | A/B 同环境对比：`rdinstret` 差分或 QEMU TCG insn plugin，统计**每输入元素动态指令数** | QEMU 或真机 | 是（同环境 A/B 内） | 热点排序、RVV 版 vs 标量版 整体收益确认 |
| **L3 真机周期** | `rdcycle` 差分（中位数），或 perf | 真机（如有） | 补充 | 最终验收参考；无真机时省略并标注 |

**优先级**：L1 必做且决定门禁；L2 用于确认"整体确实省指令"（防止 mca 局部好看但整体退化）；L3 有真机才做。QEMU 下的 `rdcycle` 数值仅可用于**同一 QEMU 进程内的 A/B 相对比较**，且须在 `marking` 标注 `TCG 环境，仅供参考`。

## 2. "性能持平"的重定义

`marking` 中的性能表述一律改为**同目标基线对比**，不再与 x86/ARM 跨架构比较：

- **基线（baseline）**：同一迁移条目的**标量 C 实现**（x86/ARM 原逻辑的直译版，在 RV 上编译运行），或迁移前的等价实现。
- **对比版（variant）**：RVV intrinsic / 手写汇编实现。

判定规则（按顺序）：

1. L1：variant 的 Block RThroughput（每迭代周期下限）**低于** baseline 的标量循环 → 静态占优；
2. L2：variant 的 `instret / 输入元素数` **低于** baseline（建议 ≥10% 才宣称显著）→ 动态占优；
3. 两项均占优 → `marking` 写"相对同目标标量基线：指令数 -N%，静态 Block RThroughput -M%（<cpu>）"；
4. 任一不占优 → `marking` 写明劣势项与原因，标 `建议阶段四深度优化`。

## 3. L2 具体做法

### 3.0 L2 权威来源优先级（先读这条）

1. **QEMU TCG insn plugin（`libinsn.so`）为权威来源**——它统计的是真实执行的 guest 指令数，不受翻译缓存/调度噪声影响；
2. `rdinstret` 差分（`resources/perf_cnt.h`）**仅作同进程 A/B 的快速校验**：QEMU user-mode 下 `rdinstret` 含翻译开销噪声，跨进程波动可达 2 倍以上，绝对值不可比；A/B 两个版本必须**在同一进程内先后执行** `perf_median5` 再取比值；
3. 两者分歧时以 plugin 为准，并在 `marking` 标注分歧幅度。

### 3.1 计数读取（`resources/perf_cnt.h`）

对比测试包含 `<skill_root>/resources/perf_cnt.h`。推荐用 `perf_median5`（kernel 为 `void(*)(void)`；带参 kernel 经 setup 回调/全局变量传参），它自带 5 次独立中位数（instret 与 cycle 分别取中位）：

```c
#include "perf_cnt.h"

static void kernel_rvv(void) { ... }       /* 被测版本，参数经全局变量传入 */
static void kernel_scalar(void) { ... }    /* 标量基线，同一进程内先/后执行 */

uint64_t s_ins, s_cyc, v_ins, v_cyc;
perf_median5(kernel_scalar, reset_input, &s_ins, &s_cyc);
perf_median5(kernel_rvv,   reset_input, &v_ins, &v_cyc);
perf_report("scalar", s_ins, s_cyc, n);   /* 输出 JSON 行 */
perf_report("rvv",    v_ins, v_cyc, n);
```

也可用 `perf_instret()` / `perf_cycle()` 手工差分计时（同进程内成对使用）。

注意事项：

- 计时区只包核心 kernel，排除初始化/IO；每次迭代重新构造或还原输入，防止编译器删除工作（可用 `PERF_DNO(p)` 消费结果）。
- **A/B 两版本必须在同一 QEMU 实例（同一次运行）内差分**——跨进程的 `rdinstret` 绝对值不可比（见 3.0）。
- **真机注意**：Linux 用户态读 `cycle/instret` 依赖内核配置，读出恒 0 时改用 L1 + QEMU plugin。
- x86/ARM 参考侧不需要计数——L2 只做 RV 内部 A/B，不做跨架构数字。

### 3.2 QEMU TCG insn plugin（L2 权威实现）

若所部署的 QEMU 构建支持 TCG plugin：

```bash
qemu-riscv64 -cpu max,vlen=256 -plugin libinsn.so -d plugin ./bench.elf
```

按线程输出动态指令数，是 L2 的权威来源。plugin 不可用时退回 3.1 的 `rdinstret` 差分（两者同为"动态指令数"口径）；两者分歧时以 plugin 为准（见 3.0）。

## 4. 结构化 `perf` 字段

阶段二/四产出的性能数据写入条目 `perf` 字段（schema 见 [project_scan.md](project_scan.md)），`marking` 只放一句话结论 + 指向 `perf`：

```jsonc
"perf": {
  "baseline_kind": "scalar_c",          // scalar_c / prev_impl
  "metrics": {
    "instret_per_elem_baseline": 12.4,
    "instret_per_elem_variant": 3.1,
    "block_rthroughput_baseline": 9.0,  // llvm-mca, 仅 asm/RVV/AutoVec 条目
    "block_rthroughput_variant": 4.0,
    "ipc_variant": 4.8,
    "mcpu": "zhufeng2"
  },
  "env": "qemu-tcg-vlen256 / 真机 <型号>",
  "ab_variants": [                      // A/B 择优时多个版本，winner 落盘
    { "kind": "intrinsic", "instret_per_elem": 3.1, "ipc": 4.8, "winner": true },
    { "kind": "asm",       "instret_per_elem": 2.9, "ipc": 5.1, "winner": false,
      "rejected_reason": "IPC 略优但可读性/维护性差，差距 <5% 不值得" }
  ]
}
```

## 5. 择优判据（A/B 择优，配合阶段二 2.2 / 阶段四）

1. 默认只产出 **intrinsic 版**（tier=hot 的条目额外产出 asm 版或备选 LMUL 方案）；
2. 统一用 L1 + L2 度量所有版本；
3. **winner = L2 指令数最低者**；L2 并列（差距 <5%）时取 L1 IPC 高者；再并列取 intrinsic 版（可维护性优先）；
4. 非 winner 版本不删除记录，写入 `perf.ab_variants`，`rejected_reason` 必填——这是后续阶段四再优化的起点。

## 6. 与各阶段的衔接

| 阶段 | 度量动作 |
| ---- | -------- |
| 阶段二 2.4 | L2（基线 vs variant）必做；asm/RVV/AutoVec 条目加做 2.4.5 快速 mca 门禁（L1 单点） |
| 阶段三 3.4 | 向量化审计：AutoVec 条目反汇编确认 RVV 指令存在（不计性能，只查覆盖） |
| 阶段四 | L1 深度迭代（`--bottleneck-analysis --all-views`）+ 每轮 L2 回归；优化终止条件见 [code_migrate.md](code_migrate.md) |
