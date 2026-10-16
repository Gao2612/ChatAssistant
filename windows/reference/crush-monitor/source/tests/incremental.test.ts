import { test } from "node:test";
import assert from "node:assert/strict";
import {
  boundedContext,
  collectEvents,
  type MemoryEvent,
} from "../shared/memory";
import { incrementalJobs, overviewJob } from "../shared/incremental";
import { requestSchema, buildRequest } from "../server/analysis";
import { buildCodexPrompt } from "../server/codex-provider";
import { CODEX_PROMPT_SOFT_LIMIT_CHARS } from "../server/codex-timeout";
import {
  stripAnalysisMedia,
  type Message,
  type LineResult,
} from "../shared/types";
const ms = (n: number): Message[] =>
  Array.from({ length: n }, (_, i) => ({
    id: `id${i}`,
    sender: i % 2 ? "self" : "other",
    text: `第${i}句，今天在看一本书`,
    timestamp: null,
    kind: "text",
  }));
const result = (id: string): LineResult => ({
  id,
  score: { value: 70, confidence: 0.8, status: "clear", probabilities: {} },
});
test("一万条历史不截断，新追加只分析缺失和近期对方，旧我方评级不重算", () => {
  const all = ms(10010),
    lines = Object.fromEntries(
      all.slice(0, 10000).map((m) => [m.id, result(m.id)]),
    );
  const jobs = incrementalJobs(all, "crush", 1, lines, {}, true);
  assert.ok(jobs.length <= 8);
  for (const job of jobs) {
    assert.ok(job.messages.length <= 100);
    assert.equal(requestSchema.safeParse(job).success, true);
    if (job.task === "self_message")
      assert.ok(job.targetIds.every((id) => Number(id.slice(2)) >= 10000));
  }
  const overview = overviewJob(all, "crush", 1, {});
  assert.equal(overview.messages.length, 100);
  assert.equal(all.length, 10010);
});

test("2566条首次分析限制单批目标并保持请求边界", () => {
  const all = ms(2566);
  const jobs = incrementalJobs(all, "crush", 1, {}, {}, true);
  const targetIds = jobs.flatMap((job) => job.targetIds);

  assert.equal(new Set(targetIds).size, 2566);
  assert.equal(targetIds.length, 2566);
  assert.ok(jobs.length <= 270, `批次数过多：${jobs.length}`);
  assert.ok(jobs.every((job) => job.targetIds.length <= 10));
  for (const job of jobs) {
    assert.equal(requestSchema.safeParse(job).success, true);
    assert.ok(JSON.stringify(buildRequest(job)).length <= 50_000);
    assert.ok(
      buildCodexPrompt(buildRequest(job)).length <=
        CODEX_PROMPT_SOFT_LIMIT_CHARS,
    );
  }
});

test("同记录重试跳过已完成逐句，未完成的可以续跑", () => {
  const all = ms(20),
    lines = Object.fromEntries(all.map((m) => [m.id, result(m.id)]));
  assert.equal(incrementalJobs(all, "crush", 1, lines, {}, false).length, 0);
  delete lines.id5;
  const jobs = incrementalJobs(all, "crush", 1, lines, {}, false);
  assert.deepEqual(
    jobs.flatMap((j) => j.targetIds),
    ["id5"],
  );
});
test("跨很久的拒绝原话不会被近期普通寒暄冲掉", () => {
  const all = ms(10000);
  all[2].text = "我只想做普通朋友，请不要再追我";
  const events: Record<string, MemoryEvent> = {
    id2: { id: "id2", kind: "boundary", confidence: 0.95, status: "active" },
  };
  const job = overviewJob(all, "crush", 1, events);
  assert.ok(job.messages.some((m) => m.id === "id2"));
  assert.equal(job.memory?.[0].kind, "boundary");
  assert.equal(requestSchema.safeParse(job).success, true);
  assert.ok(!JSON.stringify(job).includes("confidence"));
});
test("已解决事件带上撤回原话，早期我方上下文不能偷看将来的撤回", () => {
  const all = ms(300);
  all[2].text = "只做普通朋友";
  all[250].text = "我收回之前只当朋友的话，我喜欢你";
  const events: Record<string, MemoryEvent> = {
    id2: {
      id: "id2",
      kind: "boundary",
      confidence: 0.9,
      status: "resolved",
      resolvedBy: "id250",
    },
  };
  const recent = boundedContext(all, 280, 300, events);
  assert.ok(recent.messages.some((m) => m.id === "id250"));
  const early = boundedContext(all, 100, 150, events, true);
  assert.ok(!early.messages.some((m) => m.id === "id250"));
  assert.equal(early.memory[0].status, "active");
  assert.equal(early.memory[0].resolvedBy, undefined);
  const req = buildRequest({
    task: "self_message",
    revision: 1,
    relation: "crush",
    targetIds: ["id149"],
    ...early,
  });
  assert.ok(!JSON.stringify(req).includes("我收回"));
});
test("历史索引只保存有明确事件判断的记录，低置信推测不变成记忆", () => {
  const a = result("a");
  a.event = { kind: "boundary", confidence: 0.4 };
  const b = result("b");
  b.event = { kind: "preference", confidence: 0.9 };
  const events = collectEvents({ a, b }, {});
  assert.equal(events.a, undefined);
  assert.equal(events.b.kind, "preference");
  assert.equal(events.b.status, "active");
});
test("大段正文严格受请求预算约束，所有未超长的目标均被覆盖", () => {
  const all = ms(40).map((m) => ({ ...m, text: "长".repeat(2000) }));
  const jobs = incrementalJobs(all, "crush", 1, {}, {}, false);
  assert.equal(new Set(jobs.flatMap((j) => j.targetIds)).size, 40);
  for (const job of jobs) {
    assert.equal(requestSchema.safeParse(job).success, true);
    assert.ok(
      job.targetIds.every((id) => job.messages.some((m) => m.id === id)),
    );
  }
});
test("历史事件缺少原文时后端拒绝，不允许模型结论单独作为证据", () => {
  const job = overviewJob(ms(5), "crush", 1, {});
  job.memory = [{ id: "missing", kind: "boundary", status: "active" }];
  assert.equal(requestSchema.safeParse(job).success, false);
});

test("持久化恢复的字段顺序不同，不会导致请求校验误报", async () => {
  const { requestContextKey } = await import("../shared/types");
  const job = overviewJob(ms(3), "crush", 1, {});
  const reverse = {
    ...job,
    messages: job.messages.map((m) => ({
      kind: m.kind,
      timestamp: m.timestamp,
      text: m.text,
      sender: m.sender,
      id: m.id,
    })),
  };
  assert.equal(requestContextKey(job), requestContextKey(reverse));
});

test("分析请求只携带消息，永不携带参与者昵称或头像", () => {
  const request = overviewJob(ms(3), "crush", 1, {});
  const stateWithLocalProfiles = {
    ...request,
    participants: {
      self: { displayName: "我", avatarDataUrl: `data:image/png;base64,${"a".repeat(10000)}` },
      other: { displayName: "田子夜", avatarDataUrl: "data:image/jpeg;base64,other" },
    },
  };
  const payload = buildRequest(stateWithLocalProfiles);
  const serialized = JSON.stringify(payload);

  assert.equal(requestSchema.safeParse(request).success, true);
  assert.doesNotMatch(serialized, /participants|avatarDataUrl|displayName|data:image/);
});

test("发送分析请求时剥离本地媒体数据且不修改聊天记录", () => {
  const request = overviewJob(ms(1), "crush", 1, {});
  request.messages[0].media = {
    kind: "image",
    sourceType: "3",
    relativePath: "image/private.jpg",
    resolvedPath: "image/private.jpg",
    thumbnailPath: null,
    mimeType: "image/jpeg",
    fileName: "private.jpg",
    fileType: "jpg",
    fileSize: 600_000,
    durationMs: null,
    dataUrl: `data:image/jpeg;base64,${"a".repeat(600_000)}`,
  };

  const sanitized = stripAnalysisMedia(request);

  assert.notEqual(sanitized, request);
  assert.equal(sanitized.messages[0].media, undefined);
  assert.equal(request.messages[0].media?.dataUrl?.length, 600_023);
  assert.doesNotMatch(JSON.stringify(sanitized), /data:image|private\.jpg/);
});
