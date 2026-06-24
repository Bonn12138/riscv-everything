// classify.workflow.js — 阶段 A.2 预分流：把 scan_result.json 的 suggestion 条目
// 按 file_path 聚合，每文件一个 agent 判定 asm_flag/strategy/tier，
// 产出 <target>/.riscv_migrate/classified.json 并初始化 progress.json。
// 设计约定见 referens/workflow_patterns.md。模板参考 Bun lifetime-classify。

export const meta = {
  name: "riscv-classify",
  description:
    "阶段 A.2 预分流：按文件聚合 scan_result.json 条目，每文件一个 agent 判定 汇编/非汇编/热点 + 迁移策略 + tier，产出 classified.json + 初始化 progress.json",
  phases: [
    { title: "Load", detail: "加载/扁平化 scan_result.json 的 suggestion 条目" },
    { title: "Classify", detail: "每文件一个 agent：读代码段，判 asm_flag/strategy/tier" },
    { title: "Commit", detail: "commit-agent 落地 classified.json + progress.json" },
  ],
};

const A = typeof args === "string" ? JSON.parse(args) : args || {};
const SKILL = A.skill_root;
const TARGET = A.target; // 允许留空，从 scan_result.target 兜底
const SCAN = A.scan_result_path;
const NOW = A.now || "unknown"; // command 层传入 ISO8601（脚本禁用 new Date()）
if (!SKILL) return { error: "missing args.skill_root" };

const WORKDIR = `${TARGET || "."}/.riscv_migrate`;

// ── schemas ──
const ENTRIES_SCHEMA = {
  type: "object",
  required: ["entries", "target"],
  properties: {
    target: { type: "string" },
    entries: {
      type: "array",
      items: {
        type: "object",
        required: ["file_path", "start_line", "end_line", "solver_type"],
        properties: {
          file_path: { type: "string" },
          file_name: { type: "string" },
          start_line: { type: "integer" },
          end_line: { type: "integer" },
          solver_type: { type: ["integer", "string"] },
          solver_description: { type: "string" },
          migration_suggestion: { type: "string" },
        },
      },
    },
  },
};
const CLASSIFY_SCHEMA = {
  type: "object",
  required: ["entries"],
  properties: {
    file: { type: "string" },
    entries: {
      type: "array",
      items: {
        type: "object",
        required: ["key", "file_path", "start_line", "end_line", "solver_type", "asm_flag", "strategy", "tier", "reason"],
        properties: {
          key: { type: "string", description: "file_path|start_line|end_line|solver_type" },
          file_path: { type: "string" },
          start_line: { type: "integer" },
          end_line: { type: "integer" },
          solver_type: { type: ["integer", "string"] },
          asm_flag: { enum: ["non_asm", "light_asm", "asm_hotspot"] },
          strategy: { enum: ["c_direct", "intrinsic", "rvv_asm"] },
          tier: { type: "integer", description: "0(先做)~4(后做)" },
          reason: { type: "string", description: "一句话，引用看到的代码特征" },
        },
      },
    },
  },
};
const COMMIT_SCHEMA = {
  type: "object",
  required: ["written"],
  properties: {
    written: { type: "array", items: { enum: ["classified.json", "progress.json"] } },
    dir: { type: "string" },
  },
};

// ── Load：优先用 command 层传入的 args.entries，否则 loader-agent 用 jq 读 scan_result_path ──
phase("Load");
let entries = Array.isArray(A.entries) ? A.entries : null;
let target = TARGET;
if (!entries) {
  if (!SCAN) return { error: "missing both args.entries and args.scan_result_path" };
  const loaded = await agent(
    `用 jq 从 scan_result.json 扁平化出 suggestion 条目。运行：
\`jq -c '{target, entries: .suggestion_class | map({file_path,file_name,start_line,end_line,solver_type,solver_description,migration_suggestion})}' ${SCAN}\`
把输出原样作为结构化结果返回（target 字段取 .target）。不要改、不要猜。`,
    { label: "load:scan_result", phase: "Load", schema: ENTRIES_SCHEMA },
  );
  if (!loaded) return { error: "loader failed" };
  entries = loaded.entries;
  target = target || loaded.target;
}
if (!target) return { error: "cannot determine target (neither args.target nor scan_result.target)" };
log(`load: ${entries.length} entries, target=${target}`);

// ── 按 file_path 聚合（同文件多条目 → 同一 agent，避免重复读 + 并发安全）──
const byFile = new Map();
for (const e of entries) {
  if (A.files_filter && A.files_filter.length && !A.files_filter.includes(e.file_path)) continue;
  if (!byFile.has(e.file_path)) byFile.set(e.file_path, []);
  byFile.get(e.file_path).push(e);
}
const groups = [...byFile.entries()].map(([file, items]) => ({ file, items }));
log(`group: ${groups.length} files`);

// ── Classify：每文件一个 agent ──
phase("Classify");
const classified = await parallel(
  groups.map(g => () =>
    agent(
      `你是 RISC-V 迁移预分流 agent。判定以下文件中每个待迁移条目的类型，供后续批量迁移编排使用。

**文件**：${g.file}
**条目（共 ${g.items.length}）**：
${g.items.map((it, i) => `${i + 1}. L${it.start_line}-${it.end_line} solver_type=${it.solver_type}${it.solver_description ? `(${it.solver_description})` : ""}${it.migration_suggestion ? `：${it.migration_suggestion}` : ""}`).join("\n")}

**做法**：用 Read 工具读 ${g.file} 的每个 [start_line,end_line] 段（offset=start_line-1, limit=end_line-start_line+2），或 \`sed -n '<start>,<end>p' ${g.file}\`。对每个条目判定：

**asm_flag**（任一命中即 asm 类；判定依据 SKILL.md 阶段 A.2）：
- \`non_asm\`：纯 C/C++/Rust 逻辑、架构宏判断(\`#ifdef __aarch64__\`/\`__x86_64__\` 等)、无 intrinsic/asm
- \`light_asm\`：含 \`__asm__\`/\`asm volatile\` 但非热点，或个别非向量 \`__builtin_*\`
- \`asm_hotspot\`：\`.S/.s/.asm\` 文件；x86 intrinsic(\`_mm*/_mm256*/_mm512*\`)；ARM NEON(\`vld1q*/vmlaq*/vst1q*\`)；\`__builtin_ia32*/__builtin_neon_*\`；向量循环热点

**strategy**：
- \`c_direct\`：asm_flag=non_asm → 直接改架构宏/头文件/编译选项，逻辑不动
- \`intrinsic\`：含 intrinsic 但可用 \`__riscv_*\` RVV intrinsic 等价映射
- \`rvv_asm\`：向量汇编/手写 .S → 必须用 RVV 1.0 汇编重写

**tier**（迁移顺序，小先做）：
- 0：\`src/\` 顶层基础设施(alloc/sys/threading/ptr 等)，或 solver_type∈{4,2}(内联汇编/内置函数)
- 1：solver_type∈{3}(宏定义)
- 2：solver_type∈{13}(toml 配置)
- 3：solver_type∈{12}(源代码) 的底层 crate
- 4：solver_type∈{9}(shell) 及上层工具脚本
- 拿不准：按文件路径深度——\`src/X/\` 直接子层=低 tier，嵌套越深 tier 越高

**每条输出**：key 用该条目的实际值拼成 \`file_path|start_line|end_line|solver_type\`（示例 \`src/foo.rs|90|98|12\`），并给出 asm_flag/strategy/tier/reason（reason 引用看到的代码特征，≤80 字）。

拿不准某符号是否 intrinsic 时，可查 KB：\`bash ${SKILL}/scripts/run_query.sh -t search_rvv_vector_extensions -q "<符号>"\`。`,
      { label: `classify:${g.file.split("/").pop()}`, phase: "Classify", schema: CLASSIFY_SCHEMA },
    ).then(r => (r ? r.entries || [] : [])),
  ),
);
const all = classified.filter(Boolean).flat();
log(`classify: ${all.length}/${entries.length} entries classified across ${groups.length} files`);

// ── 统计 ──
const byAsm = {}, byStrat = {}, byTier = {};
for (const e of all) {
  byAsm[e.asm_flag] = (byAsm[e.asm_flag] || 0) + 1;
  byStrat[e.strategy] = (byStrat[e.strategy] || 0) + 1;
  byTier[e.tier] = (byTier[e.tier] || 0) + 1;
}

// ── Commit：commit-agent 落地 classified.json + progress.json（脚本无 fs，由 agent Write）──
phase("Commit");
const classifiedJson = {
  target, generated: NOW, entries: all, by_asm_flag: byAsm, by_strategy: byStrat, by_tier: byTier,
};
// resume 安全：先读已有 progress.json，已存在的 key 保留 status/artifact/evidence/review/verify/mca，
// 只刷新分类(asm_flag/strategy/tier)。新 key 才置 pending。files_filter 未覆盖的旧条目原样保留。
let existing = {};
if (!A.fresh_progress) {
  const read = await agent(
    `读已有 progress.json 的 entries 对象（不存在则返回 {}）：\`test -f ${WORKDIR}/progress.json && jq -c '.entries' ${WORKDIR}/progress.json || echo '{}'\`。原样返回，不要改。`,
    { label: "read:progress", phase: "Commit", schema: { type: "object", required: ["entries"], properties: { entries: { type: "object" } } } },
  );
  existing = (read && read.entries) || {};
}
const progressEntries = { ...existing }; // 保留所有已有条目（含 files_filter 未覆盖的）
let reused = 0;
for (const e of all) {
  const old = existing[e.key];
  if (old) {
    reused++;
    progressEntries[e.key] = { ...old, asm_flag: e.asm_flag, strategy: e.strategy, tier: e.tier };
  } else {
    progressEntries[e.key] = {
      status: "pending", asm_flag: e.asm_flag, strategy: e.strategy, tier: e.tier,
      artifact: "", evidence: [], review: null, blocked_on: "", updated: NOW,
    };
  }
}
log(`progress: ${reused}/${all.length} reused existing, ${all.length - reused} new pending`);
const progressJson = { target, updated: NOW, entries: progressEntries };
await agent(
  `你是 commit agent。把下面两个 JSON 写入磁盘（脚本本身无文件访问权限，由你代写）。

1. 先建目录：\`mkdir -p ${WORKDIR}\`
2. 用 Write 工具写两文件：
   - 路径 \`${WORKDIR}/classified.json\`，内容为下方 CLASSIFIED_JSON
   - 路径 \`${WORKDIR}/progress.json\`，内容为下方 PROGRESS_JSON
原样写入，不要改内容、不要增删字段。

CLASSIFIED_JSON:
\`\`\`json
${JSON.stringify(classifiedJson, null, 2)}
\`\`\`

PROGRESS_JSON:
\`\`\`json
${JSON.stringify(progressJson, null, 2)}
\`\`\`

写完 return written:["classified.json","progress.json"] 与 dir。`,
  { label: "commit:classify", phase: "Commit", schema: COMMIT_SCHEMA },
);

return {
  total: all.length,
  files: groups.length,
  by_asm_flag: byAsm,
  by_strategy: byStrat,
  by_tier: byTier,
  workdir: WORKDIR,
  next: "运行 /everything-riscv:migrate-swarm 进入批量迁移（消费 classified.json / progress.json）",
};
