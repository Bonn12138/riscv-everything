// migrate-batch.workflow.js — 阶段 B+C：把 classified 条目从 x86/ARM 批量迁移到 RISC-V（含 RVV）。
// 按 file_path 聚合（同文件一个 agent）+ 按 tier 分批（底层先做），
// 每文件组走 pipeline(迁移 → 2票对抗 review → 修复)，末尾 commit-agent 更新 progress.json。
// 设计约定见 referens/workflow_patterns.md。模板参考 Bun phase-a-port + phase-g-mega-swarm。

export const meta = {
  name: "riscv-migrate-batch",
  description:
    "阶段B+C批量迁移：按文件聚合+tier分批，每文件组 迁移→查KB补证据→2票对抗review→修复，更新 progress.json。支持断点续传",
  phases: [
    { title: "Load", detail: "加载 classified.json + progress.json，跳过已 done" },
    { title: "Migrate", detail: "按 tier 分批；每文件一个 agent：读码→查KB→写 _riscv 实现" },
    { title: "Review", detail: "每文件组 2 票对抗审查（嵌入 code-reviewer 清单 + reward-hack 检测）" },
    { title: "Fix", detail: "应用 must-fix（surgical edit）" },
    { title: "Commit", detail: "commit-agent 更新 progress.json" },
  ],
};

const A = typeof args === "string" ? JSON.parse(args) : args || {};
const SKILL = A.skill_root;
const TARGET = A.target;
const NOW = A.now || "unknown";
const CLASSIFIED = A.classified_path || `${TARGET}/.riscv_migrate/classified.json`;
const PROGRESS = `${TARGET}/.riscv_migrate/progress.json`;
if (!SKILL || !TARGET) return { error: "missing args.skill_root or args.target" };

const HARD_RULES = `**HARD RULES（违反即判失败）：**
1. 只改分配给你的文件（即本任务 prompt 顶部「文件」指明的那个；编排层已把同文件多条目合并给你）。不要碰别的文件。
2. 新增 RISC-V 实现用 \`_riscv\` 后缀（与工程约定冲突时在产物 note 说明，不要硬塞）。
3. 出现指令名/扩展名(Zba/V/Zvbb)/intrinsic(__riscv_*)/ABI/CSR(mstatus…) 信号——必须先查 KB 再写：
   \`bash ${SKILL}/scripts/run_query.sh -t <tool> -q "<q>"\`，工具：search_core_isa_manuals / search_rvv_vector_extensions / search_special_instructions / search_docs_tools。
   在 evidence 里保留返回的 file_path/header_path。禁止凭感觉编指令/扩展。
4. 向量源必须 RVV 1.0（汇编或 intrinsic）。禁止把汇编整块退化成纯 C（除非用户明确允许）。
5. 禁止 reward-hack 注释：不写 \`// PORT NOTE\`、\`// TODO(port)\`、\`reshaped for borrowck\`、长篇 \`// SAFETY:\` 解释 workaround。
6. target 是 git 仓库时：显式路径提交 \`git add <你改的文件> && git commit -m "..."\`；禁 \`git add .\`/reset/checkout/stash/rebase；msg ≤80 字符；禁 --allow-empty。
7. 每轮改动后 RISC-V 侧输出须可与原始 x86/ARM 对比（正确性不妥协；逐字节/checksum 一致交给 verify-swarm）。`;

// ── schemas ──
const LOAD_SCHEMA = {
  type: "object",
  required: ["classified", "progress_entries"],
  properties: {
    target: { type: "string" },
    classified: {
      type: "array",
      items: {
        type: "object",
        required: ["key", "file_path", "start_line", "end_line", "asm_flag", "strategy", "tier"],
        properties: {
          key: { type: "string" },
          file_path: { type: "string" },
          start_line: { type: "integer" },
          end_line: { type: "integer" },
          solver_type: { type: ["integer", "string"] },
          asm_flag: { enum: ["non_asm", "light_asm", "asm_hotspot"] },
          strategy: { enum: ["c_direct", "intrinsic", "rvv_asm"] },
          tier: { type: "integer" },
          migration_suggestion: { type: "string" },
        },
      },
    },
    progress_entries: { type: "object", description: "已有 progress.json 的 entries（空则 {}）" },
  },
};
const MIGRATE_SCHEMA = {
  type: "object",
  required: ["file", "items"],
  properties: {
    file: { type: "string" },
    items: {
      type: "array",
      items: {
        type: "object",
        required: ["key", "status"],
        properties: {
          key: { type: "string" },
          status: { enum: ["done", "skipped", "blocked"] },
          artifact: { type: "string", description: "新增/修改的 _riscv 文件绝对路径" },
          evidence: {
            type: "array",
            items: {
              type: "object",
              properties: { tool: { type: "string" }, query: { type: "string" }, file_path: { type: "string" }, header_path: { type: "string" } },
            },
          },
          skipped_reason: { type: "string" },
          blocked_on: { type: "string" },
        },
      },
    },
  },
};
const REVIEW_SCHEMA = {
  type: "object",
  required: ["file", "verdict", "issues"],
  properties: {
    file: { type: "string" },
    verdict: { enum: ["accept", "reject"] },
    issues: {
      type: "array",
      items: {
        type: "object",
        required: ["location", "severity", "what", "fix"],
        properties: {
          location: { type: "string", description: "产物文件:行号" },
          severity: { enum: ["must-fix", "should-fix", "nit", "reward-hack", "degrade-to-c"] },
          what: { type: "string" },
          fix: { type: "string" },
        },
      },
    },
  },
};
const FIX_SCHEMA = {
  type: "object",
  required: ["file", "applied"],
  properties: { file: { type: "string" }, applied: { type: "integer" }, remaining: { type: "integer" }, notes: { type: "string" } },
};
const COMMIT_SCHEMA = {
  type: "object",
  required: ["written"],
  properties: { written: { type: "array", items: { enum: ["progress.json"] } }, dir: { type: "string" } },
};

// ── Load ──
phase("Load");
const loaded = await agent(
  `加载 classified.json 与（可能不存在的）progress.json，原样返回，不要改。
\`test -f ${CLASSIFIED} && cat ${CLASSIFIED}\`
\`test -f ${PROGRESS} && jq -c '.entries' ${PROGRESS} || echo '{}'\`
把 classified 的 entries 数组放进 classified，把 progress 的 entries 对象放进 progress_entries（无则 {}）。target 取 classified.target。`,
  { label: "load:classified+progress", phase: "Load", schema: LOAD_SCHEMA },
);
if (!loaded || !loaded.classified) return { error: "load failed (先跑 /everything-riscv:classify-swarm 生成 classified.json)" };
const progEntries = loaded.progress_entries || {};
// 工作集谓词：status ∈ {pending, blocked}（pending=待迁移；blocked=重试）。跳过所有终态。
const TERMINAL = new Set(["done", "verified", "perf_done", "skipped"]);
const fileMap = new Map();
for (const e of loaded.classified) {
  const st = progEntries[e.key] && progEntries[e.key].status;
  if (st && TERMINAL.has(st)) continue;
  if (A.files_filter && A.files_filter.length && !A.files_filter.includes(e.file_path)) continue;
  if (!fileMap.has(e.file_path)) fileMap.set(e.file_path, []);
  fileMap.get(e.file_path).push(e);
}
const allGroups = [...fileMap.entries()].map(([file, items]) => ({ file, items }));
if (allGroups.length === 0) {
  return { skipped: true, reason: "no pending entries (all done or filtered out)", workdir: `${TARGET}/.riscv_migrate` };
}
// 按 tier 分批（文件组 tier = 其条目最小 tier）
const byTier = new Map();
for (const g of allGroups) {
  const t = Math.min(...g.items.map(i => i.tier));
  if (!byTier.has(t)) byTier.set(t, []);
  byTier.get(t).push(g);
}
const tierKeys = [...byTier.keys()].sort((a, b) => a - b);
log(`load: ${allGroups.length} files pending, ${loaded.classified.length} total; tiers=[${tierKeys.join(",")}]`);

// ── 迁移 prompt ──
const migratePrompt = g =>
  `你是 RISC-V 迁移 agent（阶段 B+C）。把以下文件中的待迁移条目从 x86/ARM 迁移到 RISC-V（含 RVV）。

**文件**：${g.file}
**条目（共 ${g.items.length}）**：
${g.items.map((it, i) => `${i + 1}. [${it.asm_flag}/${it.strategy} tier${it.tier}] L${it.start_line}-${it.end_line} (solver_type=${it.solver_type})${it.migration_suggestion ? `：${it.migration_suggestion}` : ""}`).join("\n")}

**每条做法**：
1. Read ${g.file} 的 [start_line,end_line] 段 + 上下文，读懂原 x86/ARM 语义与边界。
2. 按提示策略迁移：c_direct=改架构宏/头文件/编译选项加 __riscv 分支；intrinsic=x86/ARM intrinsic→__riscv_* RVV intrinsic；rvv_asm=向量汇编/手写.S→RVV 1.0 汇编。
3. **查 KB（强制，第3条 HARD RULE）**：遇指令/扩展/intrinsic/CSR/ABI 信号，\`bash ${SKILL}/scripts/run_query.sh -t <tool> -q "<q>"\`，留 evidence(file_path/header_path)。
4. 写 \`_riscv\` 实现（参照 ${SKILL}/referens/code_migrate.md 的编码规则）；为原始+RISC-V 补可对比单元测试（同行为、可比 checksum/输出）。

${HARD_RULES}

**return**：每条目的 key/status。done=迁移完成(给 artifact+evidence)；skipped=无需迁移(给 skipped_reason)；blocked=依赖未就绪上游(给 blocked_on)。`;

const reviewPrompt = (g, mg) =>
  `你是 RISC-V 迁移对抗式审查 agent。**default refuted**——只有确凿问题才报，找不到则 accept。

**原实现**：${g.file}
**本批产物**：${(mg.items || []).filter(i => i.status === "done").map(i => `${i.artifact}(key=${i.key})`).join("\n")}

读每个产物 + ${g.file} 对应原段。审查清单（细节见 ${SKILL}/../agents/riscv-code-reviewer.md）：
1. **RVV 正确性**：vl/vsetvli、vtype(vsew/vlmul/vta/vma)、tail 处理、strip-mining、vstart/vxrm 隐式依赖
2. **ABI**：calling convention(a0-a7)、向量寄存器 caller-saved、栈 16B 对齐、gp/tp
3. **内存对齐/原子**：RVV load/store 对齐、lr/sc 配对、非对齐访问
4. **指令选择/扩展**：不存在的指令/未声明扩展、intrinsic 映射(_mm_*→__riscv_*)、更优替代(Zbb/Zbc)
5. **平台**：CSR(mstatus/mepc/mtvec)、特权级、FENCE/FENCE.I

**reward-hack 强制 reject**（severity 标 reward-hack / degrade-to-c）：
- 向量段退化成标量 C 循环 → degrade-to-c
- \`// PORT NOTE\`/\`// TODO(port)\`/\`reshaped for borrowck\`/长篇 \`// SAFETY:\` → reward-hack
- 该查 KB 的指令没查、evidence 缺失 → reward-hack

每条 issue：location(产物:行号)/severity/what/fix。无 must-fix/reward-hack/degrade-to-c → verdict:accept。**不要报编译错误（留给 verify-swarm）**。`;

const fixPrompt = (g, issues) =>
  `你是 RISC-V 迁移修复 agent。把以下审查发现应用到 ${g.file} 本批产物。只做 surgical Edit，不要整文件重写。

issues（JSON）：
${JSON.stringify(issues, null, 2)}

逐条：读产物 + ${g.file} 原段确认 → Edit 修复。reward-hack=删违规注释/改正确实现；degrade-to-c=改回 RVV；must-fix=按 fix 字段改。修完确认单元测试仍可比对。拿不准的 issue 跳过并在 notes 说明。

${HARD_RULES}

return applied/remaining/notes。`;

// ── 按 tier 分批，每批 pipeline(migrate→review→fix) ──
const allResults = [];
const tierHistory = [];
for (const t of tierKeys) {
  const batch = byTier.get(t);
  log(`tier ${t}: ${batch.length} files`);
  const results = await pipeline(
    batch,
    // ── Migrate ──
    g => agent(migratePrompt(g), { label: `migrate:${g.file.split("/").pop()}`, phase: "Migrate", schema: MIGRATE_SCHEMA }),
    // ── Review (2-vote，仅当有 done 产物) ──
    (mg, g) => {
      if (!mg || !(mg.items || []).some(i => i.status === "done")) return { file: g.file, mg, skip: true, accepted: false, issues: [] };
      return parallel(
        [0, 1].map(i => () => agent(reviewPrompt(g, mg), { label: `review${i}:${g.file.split("/").pop()}`, phase: "Review", schema: REVIEW_SCHEMA })),
      ).then(votes => {
        const v = (votes || []).filter(Boolean);
        const issues = v.flatMap(x => x.issues || []);
        const dedup = [];
        const seen = {};
        for (const is of issues) {
          const k = `${is.severity}|${is.location}|${(is.what || "").slice(0, 60)}`;
          if (!seen[k]) { seen[k] = 1; dedup.push(is); }
        }
        const blocking = dedup.filter(is => ["must-fix", "reward-hack", "degrade-to-c"].includes(is.severity));
        const accepted = v.length >= 2 && v.every(x => x.verdict === "accept") && blocking.length === 0;
        return { file: g.file, mg, accepted, issues: dedup, blocking };
      });
    },
    // ── Fix（仅当有 blocking issues）──
    (rv, g) => {
      if (!rv || rv.skip || !rv.blocking || rv.blocking.length === 0) return rv;
      return agent(fixPrompt(g, rv.blocking), { label: `fix:${g.file.split("/").pop()}`, phase: "Fix", schema: FIX_SCHEMA })
        .then(fx => ({ ...rv, fix: fx || { applied: 0, remaining: rv.blocking.length } }));
    },
  );
  const ok = results.filter(r => r && r.accepted).length;
  const blocked = results.flatMap(r => (r && r.mg ? (r.mg.items || []).filter(i => i.status === "blocked") : []));
  tierHistory.push({ tier: t, files: batch.length, accepted: ok, blocked: blocked.map(b => b.key) });
  allResults.push(...results.filter(Boolean));
  log(`tier ${t}: ${ok}/${batch.length} accepted`);
}

// ── 聚合 → 更新 progress entries ──
for (const r of allResults) {
  const mg = r.mg;
  if (!mg || !mg.items) continue;
  for (const it of mg.items) {
    const review = r.accepted
      ? { accepted: true, issues_fixed: (r.fix ? r.fix.applied : 0) || 0, remaining: (r.fix ? r.fix.remaining : 0) || 0 }
      : { accepted: false, issues_fixed: (r.fix ? r.fix.applied : 0) || 0, remaining: (r.blocking || []).length };
    progEntries[it.key] = {
      status: it.status,
      asm_flag: (loaded.classified.find(c => c.key === it.key) || {}).asm_flag || "",
      strategy: (loaded.classified.find(c => c.key === it.key) || {}).strategy || "",
      tier: (loaded.classified.find(c => c.key === it.key) || {}).tier || 0,
      artifact: it.artifact || "",
      evidence: it.evidence || [],
      review,
      blocked_on: it.blocked_on || "",
      updated: NOW,
    };
  }
}
const progressJson = { target: TARGET, updated: NOW, entries: progEntries };

// ── Commit ──
phase("Commit");
await agent(
  `你是 commit agent。用 Write 工具把下面 JSON 覆盖写入 \`${PROGRESS}\`（先 \`mkdir -p ${TARGET}/.riscv_migrate\`）。原样写，不改字段。

PROGRESS_JSON:
\`\`\`json
${JSON.stringify(progressJson, null, 2)}
\`\`\`

return written:["progress.json"]。`,
  { label: "commit:migrate", phase: "Commit", schema: COMMIT_SCHEMA },
);

const done = allResults.flatMap(r => (r.mg ? (r.mg.items || []).filter(i => i.status === "done").map(i => i.key) : [])).length;
const blockedCount = allResults.flatMap(r => (r.mg ? (r.mg.items || []).filter(i => i.status === "blocked") : [])).length;
return {
  files: allGroups.length,
  migrated: done,
  blocked: blockedCount,
  accepted: allResults.filter(r => r.accepted).length,
  tiers: tierHistory,
  workdir: `${TARGET}/.riscv_migrate`,
  next: blockedCount > 0 ? "有 blocked 条目，待上游就绪后重跑；其余运行 /everything-riscv:verify-swarm 验证" : "运行 /everything-riscv:verify-swarm 做 QEMU 对比收敛",
};
