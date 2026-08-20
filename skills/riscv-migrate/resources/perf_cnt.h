/*
 * perf_cnt.h — RISC-V 迁移 A/B 对比测试的计数辅助。
 *
 * 用途：在 RISC-V 目标（QEMU user-static 或真机）上读取 rdcycle/rdinstret，
 *       做「同环境 A/B 对比」（标量基线 vs RVV/汇编版本），输出结构化 JSON 行。
 *       口径定义见 referens/perf_measure.md。
 *
 * 注意：
 *   - QEMU 下 rdcycle 数值仅可用于同一进程内的相对比较，不是真实微架构周期；
 *   - QEMU user-mode 的 rdinstret 含翻译/调度噪声，跨进程波动可达 2 倍以上，
 *     绝对值不可比；只可用于同一进程内的 A/B 相对比较（两次 perf_median5
 *     必须在同一次运行中先后执行），跨进程结论以 QEMU TCG insn plugin 为准；
 *   - 真机 Linux 用户态读计数依赖内核配置，读出恒 0 时改用静态 llvm-mca 口径；
 *   - 计时区只包核心 kernel；用 DoNotOptimize 防止编译器删除被测工作。
 */
#ifndef RISCV_MIGRATE_PERF_CNT_H
#define RISCV_MIGRATE_PERF_CNT_H

#include <stdint.h>
#include <stdio.h>

#if defined(__riscv) && __riscv_xlen >= 64
#  define PERF_CNT_AVAILABLE 1

static inline uint64_t perf_cycle(void)
{
    uint64_t t;
    __asm__ volatile("rdcycle %0" : "=r"(t));
    return t;
}

static inline uint64_t perf_instret(void)
{
    uint64_t t;
    __asm__ volatile("rdinstret %0" : "=r"(t));
    return t;
}

#else /* 非 RISC-V 目标（如 x86 参考编译）：提供桩，保证测试源码可移植 */
#  define PERF_CNT_AVAILABLE 0

static inline uint64_t perf_cycle(void)   { return 0; }
static inline uint64_t perf_instret(void) { return 0; }

#endif

/* 防优化：让编译器认为 p 被读取/写入，防止被测 kernel 被删除。 */
#define PERF_DNO(p) __asm__ volatile("" : : "r"(p) : "memory")

/* 5 次取中位数（奇数次），返回 instret 差分与 cycle 差分。
 * kernel 形如 void (*)(void)，迭代前的输入准备由 setup 回调完成。
 * 注意：ins 与 cyc 分别独立取中位数（两次插入排序），不做「伴随中位数」——
 * instret 稳定而 cycle 波动时，伴随取法会让 cycle_out 随机偏移。 */
typedef void (*perf_kernel_fn)(void);
typedef void (*perf_setup_fn)(void);

static inline void perf_median5(perf_kernel_fn kernel, perf_setup_fn setup,
                                uint64_t *instret_out, uint64_t *cycle_out)
{
    uint64_t ins[5], cyc[5];
    for (int i = 0; i < 5; i++) {
        if (setup) setup();
        uint64_t c0 = perf_cycle(), n0 = perf_instret();
        kernel();
        uint64_t c1 = perf_cycle(), n1 = perf_instret();
        ins[i] = n1 - n0;
        cyc[i] = c1 - c0;
    }
    /* 对 ins 插入排序（仅 5 个元素） */
    for (int i = 1; i < 5; i++) {
        uint64_t k = ins[i];
        int j = i - 1;
        while (j >= 0 && ins[j] > k) { ins[j + 1] = ins[j]; j--; }
        ins[j + 1] = k;
    }
    /* 对 cyc 独立插入排序取中位数 */
    for (int i = 1; i < 5; i++) {
        uint64_t k = cyc[i];
        int j = i - 1;
        while (j >= 0 && cyc[j] > k) { cyc[j + 1] = cyc[j]; j--; }
        cyc[j + 1] = k;
    }
    *instret_out = ins[2];
    *cycle_out = cyc[2];
}

/* 输出一行结构化结果，供主 Agent 汇总进条目 perf 字段。 */
static inline void perf_report(const char *name,
                               uint64_t instret, uint64_t cycle,
                               uint64_t elems)
{
    double per_elem = elems ? (double)instret / (double)elems : 0.0;
    printf("{\"variant\":\"%s\",\"instret\":%llu,\"cycle\":%llu,\"elems\":%llu,"
           "\"instret_per_elem\":%.3f}\n",
           name, (unsigned long long)instret, (unsigned long long)cycle,
           (unsigned long long)elems, per_elem);
}

#endif /* RISCV_MIGRATE_PERF_CNT_H */
