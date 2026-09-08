/** Regenerate concise STS2 collector health report from local run artifacts. */
import fs from "node:fs";
import path from "node:path";

const ROOT = process.cwd();
const RUNS = path.join(ROOT, "runs");
const OUTPUT = path.join(ROOT, "writeups", "running-report.md");
const WINDOW = 15;

function rows(file) {
  if (!fs.existsSync(file)) return [];
  return fs
    .readFileSync(file, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .map(JSON.parse);
}

function count(items, key) {
  return items.reduce((out, item) => {
    const name = key(item);
    out[name] = (out[name] ?? 0) + 1;
    return out;
  }, {});
}

function pct(numerator, denominator) {
  return denominator ? `${((numerator / denominator) * 100).toFixed(0)}%` : "—";
}

function readRun(name) {
  const dir = path.join(RUNS, name);
  const turns = rows(path.join(dir, "turns.jsonl"));
  const episodes = rows(path.join(dir, "episodes.jsonl"));
  const sft = rows(path.join(dir, "sft_traces.jsonl"));
  const final = turns.at(-1) ?? {};
  const vision = { perceiveRaw: 0, perceiveUnparseable: 0, comparableScreens: 0, screenConflicts: 0, emptyControls: 0, reasonRaw: 0, reasonUnparseable: 0 };
  for (const turn of turns) {
    if (turn.perceive_raw) {
      vision.perceiveRaw += 1;
      try {
        const scene = JSON.parse(turn.perceive_raw);
        if (!(scene.controls ?? []).length) vision.emptyControls += 1;
        const hostScreen = (turn.metrics ?? {}).screen;
        if (hostScreen && hostScreen !== "?" && scene.screen) {
          vision.comparableScreens += 1;
          if (hostScreen !== scene.screen) vision.screenConflicts += 1;
        }
      } catch {
        vision.perceiveUnparseable += 1;
      }
    }
    if (turn.reason_raw) {
      vision.reasonRaw += 1;
      try { JSON.parse(turn.reason_raw); } catch { vision.reasonUnparseable += 1; }
    }
  }
  return {
    name,
    turns,
    episodes,
    sft: sft.length,
    architecture: turns[0]?.architecture ?? "—",
    promptId: turns[0]?.prompt_id ?? "—",
    score: Number(final.cumulative_score ?? 0),
    progress: turns.filter((turn) => Number(turn.score_delta ?? 0) > 0).length,
    noop: turns.filter((turn) => !(turn.result ?? {}).pixel_changed).length,
    unknownScreen: turns.filter((turn) => !(turn.metrics ?? {}).screen).length,
    vision,
    terminal: episodes.at(-1)?.terminal ?? "incomplete",
    success: episodes.at(-1)?.success,
  };
}

function main() {
  const names = fs
    .readdirSync(RUNS, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && entry.name.endsWith("_sts2_collect"))
    .map((entry) => entry.name)
    .sort();
  const latest = names.slice(-WINDOW).map(readRun);
  const turns = latest.flatMap((run) => run.turns);
  const completed = latest.filter((run) => run.episodes.length);
  const scores = latest.reduce((sum, run) => sum + run.score, 0);
  const progress = latest.reduce((sum, run) => sum + run.progress, 0);
  const noop = latest.reduce((sum, run) => sum + run.noop, 0);
  const unknown = latest.reduce((sum, run) => sum + run.unknownScreen, 0);
  const sft = latest.reduce((sum, run) => sum + run.sft, 0);
  const terminals = count(completed, (run) => run.terminal);
  const observed = latest.filter((run) => run.turns.length);
  const architectures = [...new Set(observed.map((run) => run.architecture))];
  const promptIds = [...new Set(observed.map((run) => run.promptId))];
  const vision = latest.reduce((total, run) => Object.fromEntries(
    Object.keys(total).map((key) => [key, total[key] + run.vision[key]])
  ), { perceiveRaw: 0, perceiveUnparseable: 0, comparableScreens: 0, screenConflicts: 0, emptyControls: 0, reasonRaw: 0, reasonUnparseable: 0 });

  const issues = [];
  if (latest.length - completed.length) issues.push(`${latest.length - completed.length}/${latest.length} newest runs incomplete; do not use for policy comparison.`);
  if (!turns.length) issues.push("No recorded turns in reporting window.");
  if (turns.length && unknown / turns.length >= 0.2) issues.push(`HUD screen unknown ${unknown}/${turns.length} (${pct(unknown, turns.length)}); reward/state attribution weak.`);
  if (turns.length && noop / turns.length >= 0.2) issues.push(`No-op rate ${noop}/${turns.length} (${pct(noop, turns.length)}); inspect aim/bind/stale gates.`);
  if (sft < Math.max(10, turns.length * 0.1)) issues.push(`Only ${sft} SFT traces for ${turns.length} turns; data too thin for training claim.`);
  if (vision.perceiveUnparseable) issues.push(`LLM1 raw capture unparseable ${vision.perceiveUnparseable}/${vision.perceiveRaw}; recorded text ends mid-JSON, blocking post-hoc scene audit.`);
  if (vision.screenConflicts) issues.push(`Host/LLM1 screen conflict ${vision.screenConflicts}/${vision.comparableScreens}; host wins, but reasoner sees unstable scene labels.`);
  if (promptIds.length > 1) issues.push(`Window mixes ${promptIds.length} prompt IDs; aggregate trend is directional only, not a controlled comparison.`);
  else issues.push("No paired baseline/candidate eval present; same prompt ID alone is not improvement evidence.");

  const lines = [
    "# STS2 Collector — Running Report",
    "",
    `Updated: ${new Date().toISOString()}  `,
    `Source: latest ${latest.length} \`runs/*_sts2_collect\` directories. Regenerate: \`node scripts/update_running_report.mjs\`.`,
    "Root-cause audit: [`writeups/harness-audit.md`](harness-audit.md).",
    "",
    "## Current",
    "",
    `- Pipeline: ${architectures.join(", ") || "—"}; prompt ID: ${promptIds.join(", ") || "—"}.`,
    `- Window: ${turns.length} turns; score sum ${scores.toFixed(2)}; positive-reward turns ${progress}/${turns.length} (${pct(progress, turns.length)}).`,
    `- Terminal records: ${completed.length}/${latest.length}; ${Object.entries(terminals).map(([kind, n]) => `${kind}=${n}`).join(", ") || "none"}.`,
    "",
    "## Vision + reason",
    "",
    `- LLM1 raw: ${vision.perceiveRaw}; parseable log records ${vision.perceiveRaw - vision.perceiveUnparseable}/${vision.perceiveRaw}; empty-control scenes ${vision.emptyControls}.`,
    `- Host/LLM1 comparable screens: ${vision.comparableScreens}; conflicts ${vision.screenConflicts}. Reason JSON parseable: ${vision.reasonRaw - vision.reasonUnparseable}/${vision.reasonRaw}.`,
    "",
    "## Recent runs",
    "",
    ...latest.map((run) => `- ${run.name}: ${run.turns.length} turns; score ${run.score.toFixed(2)}; progress ${run.progress}; no-op ${run.noop}; screen? ${run.unknownScreen}; ${run.terminal}${run.success === undefined ? "" : `; success=${run.success}`}; SFT ${run.sft}.`),
    "",
    "## Current issues",
    "",
    ...(issues.length ? issues.map((issue) => `- ${issue}`) : ["- None triggered by report thresholds."]),
    "- P0: card→target chord records second coordinates, but STS2 OS executor sends primary click only.",
    "- P0: unknown target suppresses auto-inspect; label-only inspect mapping cannot bind entity ID to bbox mark.",
    "- P0: invalid LLM1 schema skips LLM2 instead of sending canonical fallback scene.",
    "",
    "## Read rules",
    "",
    "- `success=true` with `terminal=timeout` means configured progress threshold, not full run clear.",
    "- Compare only completed runs with same architecture, prompt ID, fixture, and starting condition.",
    "- Report tracks harness evidence; it does not prove model quality without held-out paired eval.",
    "",
  ];
  fs.mkdirSync(path.dirname(OUTPUT), { recursive: true });
  fs.writeFileSync(OUTPUT, `${lines.join("\n")}\n`, "utf8");
  console.log(path.relative(ROOT, OUTPUT));
}

main();
