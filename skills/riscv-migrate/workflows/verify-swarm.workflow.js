// verify-swarm.workflow.js — 阶段 D：QEMU 对比收敛循环。
// 每轮：[round1 准备环境] → survey 所有 _riscv 产物(gcc ref→qemu riscv→diff vs baseline)
//       → 并行 fix 真发散 → 2票 review → apply。循环到 failing=0 && uncovered=0。
// baseline 缓存、passing/triaged-slow 累积不重跑。设计约定见 referens/workflow_patterns.md。
// 模板参考 Bun phase-g-mega-swarm。

export const meta = {
  name: "riscv-verify-swarm",
  description:
    "阶段D验证收敛：循环 每轮准备环境(仅首轮)→survey所有 status==done 产物(QEMU对比baseline)→并行修真发散→2票review→apply，直到 failing=0&&uncovered=0；收敛后把 passing/triaged 条目 done→verified 写回 progress.json",
  phases: [
    { title: "Env", detail: "首轮跑一次 prepare_verify_env.sh（部署工具链+QEMU）" },
    { title: "Survey", detail: "工作集=status==done（排除 passing/triaged）；并行 xargs 跑所有产物：原始 vs RISC-V(QEMU)，对比 baseline，筛真发散" },
    { title: "Fix", detail: "并行修每个发散产物（NO_BUILD，surgical edit）" },
    { title: "Review", detail: "2 票审查每条修复（reward-hack/unsafe 检测）" },
    { title: "Apply", detail: "应用 reviewer corrections" },
    { title: "Commit", detail: "把 passing/triaged 的条目 done→verified + .verify sidecar 写回 progress.json" },
  ],
};

const A = typeof args === "string" ? JSON.parse(args) : args || {};
const SKILL = A.skill_root;
const TARGET = A.target;
const NOW = A.now || "unknown";
const MAX_ROUNDS = A.max_rounds || 10;
const MAX_FIX = A.max_fix || 20;
const PROGRESS = `${TARGET}/.riscv_migrate/progress.json`;
const WORKDIR = `${TARGET}/.riscv_migrate`;
const BASELINE = `${WORKDIR}/baseline`;
const DIAG = `${WORKDIR}/diag`;
if (!SKILL || !TARGET) return { error: "missing args.skill_root or args.target" };

const NO_BUILD = `**DO NOT 跑全量编译/QEMU 流水线**——编排层每轮 survey 一次。你只读诊断+源码做 surgical Edit。`;
const VERIFY_HARD = `**HARD RULES：**
1. 只改 RISC-V 侧（_riscv 产物）。**禁止改原始 x86/ARM 实现**（它是基线真相）。
2. 出现指令/扩展/intrinsic/CSR 信号，先 \`bash ${SKILL}/scripts/run_query.sh -t <tool> -q "<q>"\` 查 KB，留 evidence。
3. 禁止 reward-hack：不写 \`// PORT NOTE\`/\`// TODO(port)\`/长篇 \`// SAFETY:\`；禁止用新 \`unsafe{}\`/内联 asm 绕过来糊弄发散——找 root cause 正确修。
4. target 是 git 仓库：显式路径 \`git add <你改的文件> && git commit -m "..."\`；禁 \`git add .\`/reset/checkout/stash/rebase；msg ≤80 字符；禁 --allow-empty。
5. **正确性优先**：修完 RISC-V 输出须能与 baseline 一致；不追性能（性能交给 mca-swarm）。
${NO_BUILD}`;

// ── schemas ──
const ENV_SCHEMA = { type: "object", required: ["ok"], properties: { ok: { type: "boolean" }, errors: { type: "string" } } };
const SURVEY_SCHEMA = {
  type: "object",
  required: ["passing", "failing", "total", "uncovered"],
  properties: {
    passing: { type: "integer" },
    failing: {
      type: "array",
      items: {
        type: "object",
        required: ["artifact", "diag", "kind"],
        properties: {
          key: { type: "string" },
          artifact: { type: "string", description: "_riscv 产物绝对路径" },
          diag: { type: "string", description: "diag/<slug>.log 路径" },
          kind: { enum: ["crash", "diverge", "real-hang"] },
          summary: { type: "string" },
        },
      },
    },
    total: { type: "integer" },
    uncovered: { type: "integer", description: "progress 里 done 但本轮未探测的产物数" },
  },
};
const FIX_SCHEMA = {
  type: "object",
  required: ["artifact", "root_cause", "src_edited", "commit"],
  properties: {
    artifact: { type: "string" },
    root_cause: { type: "string" },
    src_edited: { type: "array", items: { type: "string" } },
    commit: { type: "string" },
    confidence: { type: "string" },
  },
};
const REVIEW_SCHEMA = {
  type: "object",
  required: ["accept", "corrections"],
  properties: {
    accept: { type: "boolean" },
    corrections: {
      type: "array",
      items: {
        type: "object",
        required: ["src", "what", "fix", "severity"],
        properties: { src: { type: "string" }, what: { type: "string" }, fix: { type: "string" }, severity: { type: "string" } },
      },
    },
    new_unsafe: { type: "integer" },
  },
};
const APPLY_SCHEMA = { type: "object", required: ["applied"], properties: { applied: { type: "integer" }, commit: { type: "string" } } };
const PROGRESS_READ_SCHEMA = { type: "object", required: ["entries"], properties: { entries: { type: "object" } } };
const VERIFY_COMMIT_SCHEMA = {
  type: "object", required: ["written", "verified"],
  properties: { written: { type: "array", items: { enum: ["progress.json"] } }, verified: { type: "integer" }, triaged: { type: "integer" } },
};

const slug = p => (p || "").replace(/\//g, "_").replace(/^_+/, "");

let history = [];
let envReady = false;
let converged = false;
let lastPassing = 0;
let lastTotal = 0;

for (let round = 1; round <= MAX_ROUNDS; round++) {
  // ── Env（仅首轮）──
  if (!envReady) {
    phase("Env");
    const env = await agent(
      `准备 RISC-V 验证环境（幂等；首次下载工具链+QEMU 约 5-15 分钟）。
\`bash ${SKILL}/scripts/prepare_verify_env.sh && source ${SKILL}/resources/env.sh && which riscv64-unknown-linux-gnu-gcc qemu-riscv64\`
若任一命令缺失，return {ok:false, errors:"..."}。否则 {ok:true}。`,
      { label: `env-r${round}`, phase: "Env", schema: ENV_SCHEMA },
    );
    if (!env || !env.ok) {
      history.push({ round, env_failed: env ? env.errors : "env agent error" });
      continue;
    }
    envReady = true;
  }

  // ── Survey ──
  phase("Survey");
  const survey = await agent(
    `你是 verify survey agent。盘点 ${TARGET} 所有 RISC-V 迁移产物的正确性，找出与原始 x86/ARM 的**真发散**。

\`mkdir -p ${BASELINE} ${DIAG}; touch ${WORKDIR}/passing.txt ${WORKDIR}/triaged-slow.txt\`
\`source ${SKILL}/resources/env.sh\`（已由首轮 prepare 生成 env.sh）。

读 ${PROGRESS}（\`jq -c '.entries | to_entries | map(select(.value.status=="done")) | map({key:.key, artifact:.value.artifact})'\`）拿所有 status=done 的 artifact。

**排除**：\`passing.txt\`（已通过）和 \`triaged-slow.txt\`（baseline 也挂/环境慢）里的 artifact。剩余即本轮 working set。

对每个 artifact（slug=$(basename)）：
1. **找原始实现**：artifact 去掉 \`_riscv\` 后缀（如 \`foo_riscv.c\`→\`foo.c\`），或同目录同源原始文件；找测试 harness（\`*_test.c\`/\`test_*.c\`）。找不到原始配对 → 记 uncovered，跳过。
2. **Baseline（首次缓存，已存在跳过）**：
   \`test -f ${BASELINE}/<slug>.txt || { x86_64-linux-gnu-gcc -O2 -msse4.2 <原始> [harness] -o /tmp/<slug>.ref 2>/dev/null && /tmp/<slug>.ref > ${BASELINE}/<slug>.txt 2>&1; }\`
   （ARM 源用 aarch64 工具链：\`bash ${SKILL}/resources/arm_toolchain_env.sh && source ${SKILL}/resources/env.sh\` 再 aarch64-linux-gnu-gcc；拿不准架构看原始代码 #ifdef）
3. **RISC-V**：\`riscv64-unknown-linux-gnu-gcc -O2 -march=rv64gcv_zbc -mabi=lp64d -static <artifact> [harness] -o /tmp/<slug>.riscv 2>${DIAG}/<slug>.build.log && qemu-riscv64 -cpu max /tmp/<slug>.riscv > ${DIAG}/<slug>.log 2>&1; echo "rc=$?"\`
4. **并行**：\`cat working.txt | xargs -P8 -I{} sh -c '<上述对比>'\`

**判定**（rust_rc=RISC-V退出码, base_rc=baseline退出码=baseline.txt 是否含正常输出）：
| rust_rc | base_rc | 判定 |
|---|---|---|
| ≥128 或 segfault | 正常 | **crash** → failing |
| ≠0 或输出 diff 非空 | 正常 | **diverge** → failing |
| 0 且无 diff | 正常 | passing（追加 ${WORKDIR}/passing.txt）|
| 任意 | baseline 也异常 | **baseline-also-fails** → 追加 ${WORKDIR}/triaged-slow.txt（非迁移 bug）|

summary = diag 前 2 行 / backtrace 尾 / "no output"。

return {passing, failing:[{key,artifact,diag:"${DIAG}/<slug>.log",kind,summary}], total:done数, uncovered}。**只返回真发散，不改 src。**`,
    { label: `survey-r${round}`, phase: "Survey", schema: SURVEY_SCHEMA },
  );
  if (!survey) { history.push({ round, error: "survey failed" }); continue; }
  lastPassing = survey.passing; lastTotal = survey.total;
  log(`r${round}: ${survey.passing}/${survey.total} passing, ${survey.failing.length} failing, ${survey.uncovered} uncovered`);

  if (survey.failing.length === 0 && (survey.uncovered || 0) === 0) {
    converged = true;
    break;
  }
  if (survey.failing.length === 0) { history.push({ round, passing: survey.passing, total: survey.total, advanced: true }); continue; }

  // ── Fix → Review → Apply（pipeline per failing，MAX_FIX 宽）──
  const targets = survey.failing.slice(0, MAX_FIX);
  await pipeline(
    targets,
    f => agent(
      `修 ${f.artifact} 的发散（kind: ${f.kind}）。${TARGET}
**诊断**：\`cat ${f.diag}\`；**baseline**：\`cat ${BASELINE}/${slug(f.artifact)}.txt\`——你唯一的运行时证据。
${f.summary}

1. 读 diag → 首差行/崩溃点。2. 读 ${f.artifact} + 原始实现 → 定位 root cause（从源码推理）。3. surgical Edit 修 RISC-V 侧。4. 显式路径 commit。

**若 diag 显示断言通过但超时（kind:hang）且同类证明代码路径正确**：这是 debug-slow 非迁移 bug。**不 commit**，return root_cause:"debug-slow: <一句>"，commit:"NONE"，并 \`echo ${f.artifact} >> ${WORKDIR}/triaged-slow.txt\`。**禁 git commit --allow-empty。**

${VERIFY_HARD}
return artifact/root_cause/src_edited/commit/confidence。`,
      { label: `fix:${slug(f.artifact).slice(-30)}`, phase: "Fix", schema: FIX_SCHEMA },
    ),
    (fix, f) => fix && (fix.src_edited || []).length > 0
      ? parallel([0, 1].map(i => () => agent(
        `审查 ${f.artifact} 的修复。${TARGET}。Diff：\`git show ${fix.commit}\`。诊断：\`cat ${f.diag}\`。
1. 新增非 FFI unsafe / 内联 asm 绕过？\`git show ${fix.commit} | grep -cE '^\\+.*unsafe \\{'\`。
2. reward-hack 注释？\`git show ${fix.commit} | grep -cE '^\\+.*(PORT NOTE|TODO\\(port\\)|reshaped|SAFETY:.{80,})'\` 命中 → severity:"reward-hack"。
3. 是否真修了 diag 的发散？读 ${f.artifact} + 原始对比。
4. 改了原始实现？（禁止）→ severity:"touched-baseline"。
accept:true 仅当 0 新 unsafe + 无 reward-hack + 真修发散 + 未动原始。${NO_BUILD}
return accept/corrections:[{src,what,fix,severity}]/new_unsafe。`,
        { label: `rev${i}:${slug(f.artifact).slice(-20)}`, phase: "Review", schema: REVIEW_SCHEMA },
      ))).then(vs => {
        const corr = (vs || []).filter(Boolean).flatMap(v => v.corrections || []);
        const dedup = []; const seen = {};
        for (const c of corr) { const k = `${c.src}|${(c.what || "").slice(0, 50)}`; if (!seen[k]) { seen[k] = 1; dedup.push(c); } }
        return { artifact: f.artifact, fix, accepted: (vs || []).filter(Boolean).length >= 2 && vs.every(v => v && v.accept && (v.new_unsafe || 0) === 0), corrections: dedup };
      })
      : null,
    (vr, f) => vr && !vr.accepted && vr.corrections.length > 0
      ? agent(
        `应用 ${vr.corrections.length} 条修正到 ${f.artifact}。${TARGET}。
${vr.corrections.map((c, i) => `${i + 1}. [${c.severity}] ${c.src}: ${c.what}\n   FIX: ${c.fix}`).join("\n")}
${VERIFY_HARD}
return applied/commit。`,
        { label: `apply:${slug(f.artifact).slice(-20)}`, phase: "Apply", schema: APPLY_SCHEMA },
      )
      : vr,
  );

  history.push({ round, passing: survey.passing, total: survey.total, fixed: targets.length });
}

// ── Commit：把 passing/triaged 的条目从 done 推进到 verified（+ .verify sidecar）──
phase("Commit");
const verified = await agent(
  `你是 verify commit agent。把本轮收敛结果写回 ${PROGRESS}：把 passing/triaged-slow 里的条目从 status=="done" 推进到 "verified"，并补 .verify sidecar。脚本无 fs，由你执行。

\`cd ${WORKDIR}\`
\`touch passing.txt triaged-slow.txt\`
\`PASSING=$(sort -u passing.txt triaged-slow.txt 2>/dev/null)\`

用 jq 原地改 ${PROGRESS}（不要手改、不要增删其它字段；.updated 与被改条目 .updated 设为 "${NOW}"）：

对 .entries 的每个 value：若 \`.status=="done"\` 且 \`.artifact\` 出现在 \$PASSING 里，则
- \`.status = "verified"\`
- \`.verify = {passing: (.artifact 不在 triaged-slow.txt), triaged: (.artifact 在 triaged-slow.txt), round: ${history.length || 1}, updated: "${NOW}"}\`
- \`.updated = "${NOW}"\`
其余条目原样保留。

实现（参考）：\`jq --arg now "${NOW}" --slurpfile p <(sort -u passing.txt triaged-slow.txt) '...' \`——或你用 Python 读改写也行，**只要结果正确、只动该动的字段**。

改完写回 ${PROGRESS}（\`jq ... > tmp && mv tmp ${PROGRESS}\`）。然后统计：
\`jq '[.entries[]|select(.status=="verified")]|length' ${PROGRESS}\` → verified 数。

return {written:["progress.json"], verified:<int>, triaged:<int>}。若 ${PROGRESS} 不存在 return {written:[], verified:0, triaged:0}。`,
  { label: "commit:verify", phase: "Commit", schema: VERIFY_COMMIT_SCHEMA },
);

return {
  rounds: history.length,
  done: converged,
  passing: lastPassing,
  total: lastTotal,
  verified: verified ? verified.verified : 0,
  history,
  workdir: WORKDIR,
};
