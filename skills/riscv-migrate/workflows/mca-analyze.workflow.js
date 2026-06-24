// mca-analyze.workflow.js — 阶段 E：对 asm_hotspot 产物做 llvm-mca 分析 + 迭代优化。
// 每热点一个 agent 内部循环（analyze→optimize→QEMU 回归→re-analyze）到 IPC 达标或收敛。
// 整轮准备一次环境（含 llvm-mca）。末尾 commit-agent 把 mca 结果写回 progress.json。
// 设计约定见 referens/workflow_patterns.md。流程嵌入 agents/riscv-asm-analyzer.md。

export const meta = {
  name: "riscv-mca-analyze",
  description:
    "阶段E性能分析：对 status==verified 的 asm_hotspot 产物做 llvm-mca 分析+迭代优化（每热点一个 agent：提hot.s→分析瓶颈→小步优化→QEMU回归），到 IPC≥70%dispatch 或收敛；写回 status=perf_done + .mca",
  phases: [
    { title: "Load", detail: "从 args.hotspots 或 progress.json 取 asm_hotspot 产物" },
    { title: "Env", detail: "整轮跑一次 prepare_verify_env.sh（含 llvm-mca + 工具链 + QEMU）" },
    { title: "Optimize", detail: "每热点一个 agent：llvm-mca 分析→优化→QEMU 回归循环" },
    { title: "Commit", detail: "commit-agent 把 mca 结果写回 progress.json" },
  ],
};

const A = typeof args === "string" ? JSON.parse(args) : args || {};
const SKILL = A.skill_root;
const TARGET = A.target;
const NOW = A.now || "unknown";
const MCPU = A.mcpu || "zhufeng2";
const MARCH = A.march || "rv64gcv";
const MAX_ROUNDS = A.max_rounds || 5;
const PROGRESS = `${TARGET}/.riscv_migrate/progress.json`;
const WORKDIR = `${TARGET}/.riscv_migrate`;
if (!SKILL || !TARGET) return { error: "missing args.skill_root or args.target" };

const MCA_HARD = `**HARD RULES：**
1. **正确性不可回归**：每轮优化后必须 QEMU 重跑，输出与优化前（baseline）逐字节一致；回归则回退该轮改动。
2. 拿不准的指令/扩展/intrinsic：先 \`bash ${SKILL}/scripts/run_query.sh -t search_rvv_vector_extensions -q "<...>"\` 查 KB，按返回调整；禁凭感觉编指令。
3. 禁 reward-hack 注释（\`// PORT NOTE\`/\`// SAFETY:\` 长篇等）；小步优化，每轮可独立验证。
4. target 是 git 仓库：显式路径 \`git add <你改的文件> && git commit -m "..."\`；禁 \`git add .\`/reset/checkout/stash/rebase；msg ≤80 字符。
5. 只优化分配给你的热点产物，不动别的文件。`;

// ── schemas ──
const LOAD_SCHEMA = {
  type: "object",
  required: ["hotspots"],
  properties: {
    hotspots: {
      type: "array",
      items: {
        type: "object",
        required: ["artifact"],
        properties: { key: { type: "string" }, artifact: { type: "string" }, fn: { type: "string" } },
      },
    },
  },
};
const ENV_SCHEMA = { type: "object", required: ["ok"], properties: { ok: { type: "boolean" }, errors: { type: "string" } } };
const OPT_SCHEMA = {
  type: "object",
  required: ["artifact", "ipc_before", "ipc_after", "regression_ok"],
  properties: {
    artifact: { type: "string" },
    key: { type: "string" },
    fn: { type: "string" },
    ipc_before: { type: "number" }, ipc_after: { type: "number" },
    rthroughput_before: { type: "number" }, rthroughput_after: { type: "number" },
    dispatch_width: { type: "integer" },
    bottleneck: { type: "string" },
    optimized_file: { type: "string" },
    regression_ok: { type: "boolean" },
    rounds: { type: "integer" },
    notes: { type: "string" },
  },
};
const COMMIT_SCHEMA = { type: "object", required: ["written"], properties: { written: { type: "array", items: { enum: ["progress.json"] } } } };

const slug = p => (p || "").replace(/\//g, "_").replace(/^_+/, "");

// ── Load ──
phase("Load");
let hotspots = Array.isArray(A.hotspots) ? A.hotspots : null;
if (!hotspots) {
  const loaded = await agent(
    `从 progress.json 取所有 asm_hotspot 且 status==verified 的产物作热点（工作集谓词：status=="verified" ∧ asm_flag=="asm_hotspot"）。
\`jq -c '[.entries | to_entries[] | select(.value.status=="verified" and .value.asm_flag=="asm_hotspot") | {key:.key, artifact:.value.artifact, fn:(.value.artifact|split("/")|last)}]' ${PROGRESS}\`
若 progress.json 不存在或无匹配，return hotspots:[]。原样返回数组。`,
    { label: "load:hotspots", phase: "Load", schema: LOAD_SCHEMA },
  );
  hotspots = loaded ? loaded.hotspots : [];
}
if (!hotspots.length) {
  return { skipped: true, reason: "no verified asm_hotspot artifacts in progress.json（先跑 verify-swarm 把 status 推进到 verified，或用 args.hotspots 显式指定）", workdir: WORKDIR };
}
log(`load: ${hotspots.length} hotspots (mcpu=${MCPU}, march=${MARCH})`);

// ── Env（整轮一次）──
phase("Env");
const env = await agent(
  `准备环境（含 llvm-mca + riscv 工具链 + qemu；幂等，首次约 5-15 分钟）。
\`bash ${SKILL}/scripts/prepare_verify_env.sh && source ${SKILL}/resources/env.sh && command -v llvm-mca && command -v riscv64-unknown-linux-gnu-gcc && command -v qemu-riscv64\`
任一缺失 return {ok:false, errors:"<缺什么>"}；否则 {ok:true}。`,
  { label: "env:mca", phase: "Env", schema: ENV_SCHEMA },
);
if (!env || !env.ok) return { error: "env prepare failed", detail: env ? env.errors : "env agent error" };

// ── Optimize：每热点一个 agent 内部迭代 ──
phase("Optimize");
const results = await parallel(
  hotspots.map(h => () =>
    agent(
      `你是 RISC-V 性能优化 agent（阶段 E）。对热点产物 ${h.artifact}${h.fn ? `（关注函数/段：${h.fn}）` : ""} 做 llvm-mca 分析 + 迭代优化。**正确性不可回归。**

**目标**：\`-mcpu=${MCPU}\`、\`-march=${MARCH}\`（按工程实际扩展调整 -mattr，如 +v/+zvbc/+zbb；拿不准查 KB）。

**环境**：每条命令前 \`source ${SKILL}/resources/env.sh\`（已含 riscv 工具链/qemu/llvm-mca）。

**流程**（参照 ${SKILL}/referens/code_migrate.md 第四节 + ${SKILL}/../agents/riscv-asm-analyzer.md）：

1. **提 hot.s**：
   - 手写 .S：只取最内层循环体（去 prologue/epilogue/callee-saved）。
   - C/intrinsic：\`riscv64-unknown-linux-gnu-clang -O3 -S -march=${MARCH} -mabi=lp64d ${h.artifact} -o hot.s\`（flags 与工程一致），再截取目标循环。
   - 或 \`llvm-objdump -d\` 按地址截取。注释用 \`//\` 或 \`#\`（**禁 .text 内 /* */ 行尾注释**，会致 llvm-mca 解析失败）。

2. **baseline 分析**：
   \`llvm-mca -mtriple=riscv64 -mcpu=${MCPU} -mattr=+v,+zvbc --all-stats --bottleneck-analysis --iterations=100 < hot.s\`
   记 IPC0、Block RThroughput0、Dispatch Width、瓶颈（Dynamic Dispatch Stall / Resource Pressure / Wait time）。

3. **迭代优化**（≤ ${MAX_ROUNDS} 轮，每轮）：
   a. 按瓶颈小步改（依赖链→多累加器/软件流水/适度展开；vsetvli→外提合并/减少 SEW-LMUL 切换；端口压力→换等价低压力指令；访存→对齐批量加载/减 gather-scatter）。指令拿不准先查 KB。
   b. 重新 llvm-mca → IPCn、RTn。
   c. **正确性回归**（每轮必做）：\`riscv64-unknown-linux-gnu-gcc -O2 -march=${MARCH} -mabi=lp64d -static ${h.artifact} -o /tmp/mca_<slug>.riscv && qemu-riscv64 -cpu max /tmp/mca_<slug>.riscv > /tmp/mca_<slug>.out\`，与 ${WORKDIR}/baseline/${slug(h.artifact)}.txt 比对（无 baseline 则与优化前输出比对）；不一致→**回退该轮**，记 notes。
   d. 终止：IPC ≥ 0.7×Dispatch Width，或连续两轮 RT 降幅 <5%，或出现 \`No resource or data dependency bottlenecks\`。

4. 清理临时产物，保留最终 ${h.artifact}（或其 _riscv 文件）。

${MCA_HARD}

return artifact/ipc_before/ipc_after/rthroughput_before/rthroughput_after/dispatch_width/bottleneck/optimized_file/regression_ok(bool)/rounds/notes。`,
      { label: `mca:${slug(h.artifact).slice(-24)}`, phase: "Optimize", schema: OPT_SCHEMA },
    ),
  ),
);
const opt = results.filter(Boolean);
log(`optimize: ${opt.length}/${hotspots.length} done; regression_ok=${opt.filter(o => o.regression_ok).length}`);

// ── Commit：把 mca 结果写回 progress entries ──
phase("Commit");
const loaded2 = await agent(
  `读 progress.json 原样返回 entries 对象：\`test -f ${PROGRESS} && jq -c '.entries' ${PROGRESS} || echo '{}'\`。`,
  { label: "read:progress", phase: "Commit", schema: { type: "object", required: ["entries"], properties: { entries: { type: "object" } } } },
);
const progEntries = (loaded2 && loaded2.entries) || {};
for (const o of opt) {
  const key = o.key || (Object.entries(progEntries).find(([, v]) => v.artifact === o.artifact) || [])[0];
  if (key && progEntries[key]) {
    progEntries[key].status = "perf_done"; // 阶段 E 终态：已分析优化（正确性见 .mca.regression_ok）
    progEntries[key].mca = {
      mcpu: MCPU, ipc_before: o.ipc_before, ipc_after: o.ipc_after,
      rthroughput_before: o.rthroughput_before, rthroughput_after: o.rthroughput_after,
      dispatch_width: o.dispatch_width, bottleneck: o.bottleneck,
      regression_ok: !!o.regression_ok, rounds: o.rounds, updated: NOW,
    };
    progEntries[key].updated = NOW;
  }
}
const progressJson = { target: TARGET, updated: NOW, entries: progEntries };
await agent(
  `你是 commit agent。用 Write 覆盖写入 \`${PROGRESS}\`（先 \`mkdir -p ${WORKDIR}\`）。原样写。
PROGRESS_JSON:
\`\`\`json
${JSON.stringify(progressJson, null, 2)}
\`\`\`
return written:["progress.json"]。`,
  { label: "commit:mca", phase: "Commit", schema: COMMIT_SCHEMA },
);

return {
  hotspots: opt.length,
  regression_ok: opt.filter(o => o.regression_ok).length,
  ipc_improved: opt.filter(o => o.ipc_after > o.ipc_before).length,
  details: opt.map(o => ({ artifact: o.artifact, ipc: `${o.ipc_before}→${o.ipc_after}`, rt: `${o.rthroughput_before}→${o.rthroughput_after}`, bottleneck: o.bottleneck, regression_ok: o.regression_ok })),
  workdir: WORKDIR,
};
