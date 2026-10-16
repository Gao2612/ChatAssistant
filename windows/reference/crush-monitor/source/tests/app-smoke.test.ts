import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const appPath = new URL("../src/App.tsx", import.meta.url);

async function readAppSource(): Promise<string> {
  return readFile(appPath, "utf8");
}

function sourceSection(source: string, start: string, end: string): string {
  const startAt = source.indexOf(start);
  assert.notEqual(startAt, -1, `未找到源码起点：${start}`);
  const endAt = source.indexOf(end, startAt + start.length);
  assert.notEqual(endAt, -1, `未找到源码终点：${end}`);
  return source.slice(startAt, endAt);
}

test("前端提供六种聊天文件导入入口并复用 readChatFile", async () => {
  const source = await readAppSource();

  assert.match(source, /readChatFile/);
  assert.match(source, /type="file"/);
  assert.match(source, /accept="\.txt,\.md,\.csv,\.html,\.json,\.pdf"/);
  assert.match(source, /导入文件/);
  assert.match(source, /prepare\(result\.text, result\.pdf\)/);
  assert.match(source, /importStructuredChat\(result\.structured\)/);
  assert.match(source, /sender: record\.sender/);
  assert.match(source, /mergeParticipantProfiles\(current, structured\.participants\)/);
  assert.match(source, /desktop\.readCompanionImage\(file, relativePath\)/);
  assert.match(source, /function ParticipantAvatar/);
  assert.match(source, /participant-avatar-image/);
  assert.match(source, /onError=\{\(\) => setFailed\(true\)\}/);
  assert.match(source, /profile=\{participants\.self\}/);
  assert.match(source, /profile=\{profile\}/);
  assert.match(source, /setParticipants\(defaultParticipants\(\)\)/);
  assert.match(source, /swapParticipantProfiles\(current\)/);
  assert.match(source, /selectSelfSpeaker\(names, messages\.length \? self : ""\)/);
  assert.match(source, /useState<ParticipantProfiles>/);
  assert.match(source, /schema: 2/);
  assert.match(source, /participants,/);
  assert.match(source, /add\(toMessages\(p\.messages, selectedSelf\)\)/);
  assert.match(source, /已自动按首个说话人分析，可在设置中交换身份/);
  assert.match(source, /本人/);
  assert.match(source, /"me"/);
  assert.match(source, /"i"/);
  assert.doesNotMatch(source, /确认聊天里的你/);
  assert.doesNotMatch(source, /我：内容.*对方：内容/);
  assert.match(source, /交换双方身份/);
  assert.match(source, /导出分析 PDF/);
  assert.match(source, /buildAnalysisReportHtml/);
});

test("聊天设置包含内置 API 配置字段和运行时接口入口", async () => {
  const source = await readAppSource();

  assert.match(source, /api-provider/);
  assert.match(source, /type="password"/);
  assert.match(source, /deepseek-model/);
  assert.match(source, /保存 API 配置/);
  assert.match(source, /hasApiKey/);
  assert.match(source, /已配置/);
  assert.match(source, /未配置/);
  assert.match(source, /"\/api\/config"/);
  assert.match(source, /method: "GET"/);
  assert.match(source, /method: "PUT"/);
  assert.match(source, /"\/api\/health"/);
  assert.match(source, /ChatGPT Plus（Codex）/);
  assert.match(source, /apiProvider !== "codex"/);
  assert.match(source, /provider: "codex"/);
  assert.match(source, /"\/api\/codex\/status"/);
  assert.match(source, /"\/api\/codex\/models"/);
  assert.match(source, /ChatGPT · Plus/);
  assert.match(source, /额度/);
  assert.match(source, /重试读取 Codex 状态/);
  assert.match(source, /codex-model/);
  assert.match(source, /analysis-progress-bar/);
  assert.match(source, /重置/);
  assert.match(source, /未知/);
});

test("导入或粘贴只更新聊天，必须由分析聊天按钮触发分析", async () => {
  const source = await readAppSource();
  const addSection = sourceSection(
    source,
    "function add(\n",
    "\n  function analyzeChat(",
  );

  assert.doesNotMatch(addSection, /\bstart\(/);
  assert.doesNotMatch(addSection, /\ba\.run\(/);
  assert.match(
    source,
    /disabled=\{!ready \|\| \(!input\.trim\(\) && !messages\.length\)\}/,
  );
  assert.match(source, /function analyzeChat\(\): void/);
  assert.match(source, /onClick=\{\(\) => void analyzeChat\(\)\}/);
});

test("已有聊天且输入为空时分析聊天按钮会重新分析", async () => {
  const source = await readAppSource();
  const analyzeSection = sourceSection(
    source,
    "function analyzeChat(): void",
    "\n  function prepare(",
  );

  assert.match(analyzeSection, /if \(!input\.trim\(\)\)/);
  assert.match(analyzeSection, /if \(messages\.length\) void a\.run\(messages, relation\)/);
});
