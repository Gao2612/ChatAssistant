import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { analysisProgressPercent } from "../src/useAnalysis";

const appPath = new URL("../src/App.tsx", import.meta.url);
const hookPath = new URL("../src/useAnalysis.ts", import.meta.url);
const stylePath = new URL("../src/style.css", import.meta.url);

async function readText(path: URL): Promise<string> {
  return readFile(path, "utf8");
}

test("进度百分比只由真实 done/total 计算，total=0 返回不定量", () => {
  assert.equal(analysisProgressPercent({ done: 0, total: 0 }), null);
  assert.equal(analysisProgressPercent({ done: 2, total: 4 }), 50);
  assert.equal(analysisProgressPercent({ done: 8, total: 4 }), 100);
});

test("下一步详情复用进度条并覆盖停止、失败和重跑状态", async () => {
  const [app, hook, style] = await Promise.all([
    readText(appPath),
    readText(hookPath),
    readText(stylePath),
  ]);

  assert.match(app, /function AnalysisProgressBar/);
  assert.match(app, /function AnalysisProgressDetails/);
  assert.match(app, /onCancel=\{a\.cancel\}/);
  assert.match(app, /status === "cancelled"/);
  assert.match(app, /detail === "action"/);
  assert.match(app, /<AnalysisProgressDetails/);
  assert.match(hook, /createProgress\("preparing"/);
  assert.match(hook, /"cancelled"/);
  assert.match(hook, /failed/);
  assert.match(style, /\.analysis-progress-bar\.indeterminate/);
  assert.doesNotMatch(
    style,
    /\.composer-feedback:has\(\[role="status"\]\)[^}]*\.analysis-status/,
  );
});

test("停止分析依据活动控制器保持取消状态并提供继续分析", async () => {
  const [app, hook] = await Promise.all([
    readText(appPath),
    readText(hookPath),
  ]);

  assert.match(hook, /const wasLoading = controller\.current !== null;/);
  assert.match(
    hook,
    /if \(wasLoading\) \{\s*setStatus\("cancelled"\);\s*setProgress\(\(current\) => \(\{ \.\.\.current, phase: "cancelled" \}\)\);\s*return;/s,
  );
  assert.match(
    app,
    /a\.status === "cancelled"[\s\S]*?onClick=\{\(\) => void a\.run\(messages, relation\)\}[\s\S]*?继续分析/,
  );
});
