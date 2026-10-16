import { test } from "node:test";
import assert from "node:assert/strict";
import { buildAnalysisReportHtml } from "../src/export-report";
import type { LineResult, Message, Overview } from "../shared/types";
import type { ParticipantProfiles } from "../shared/participants";

test("分析报告 HTML 同时包含原始聊天、情绪和意图并转义文本", () => {
  const messages: Message[] = [
    {
      id: "m1",
      sender: "other",
      text: "你好 <script>alert(1)</script>",
      timestamp: "2026-08-27 13:57:12",
      kind: "text",
    },
    {
      id: "m2",
      sender: "self",
      text: "我会处理",
      timestamp: "2026-08-27 13:58:00",
      kind: "text",
    },
  ];
  const lines: Record<string, LineResult> = {
    m1: {
      id: "m1",
      score: {
        value: 72,
        confidence: 0.9,
        status: "clear",
        probabilities: {},
      },
      emotions: { happy: 0.7 },
      intents: { ask: 0.8 },
    },
    m2: {
      id: "m2",
      score: {
        value: 81,
        confidence: 0.8,
        status: "clear",
        probabilities: {},
      },
      replyType: "接住并推进",
      replyConfidence: 0.85,
    },
  };
  const overview: Overview = {
    affinity: {
      value: 68,
      confidence: 0.8,
      status: "clear",
      probabilities: {},
    },
    stage: "flow",
    action: "continue",
    evidenceId: "m1",
    actionEvidenceId: "m2",
  };
  const participants: ParticipantProfiles = {
    self: {
      displayName: "我",
      avatarDataUrl: "data:image/png;base64,c2VsZg==",
    },
    other: {
      displayName: "对方 <img>",
      avatarDataUrl: "data:image/jpeg;base64,b3RoZXI=",
    },
  };

  const html = buildAnalysisReportHtml({
    messages,
    lines,
    overview,
    participants,
    relation: "crush",
    generatedAt: "2026-09-29 12:00:00",
  });

  assert.match(html, /聊天分析报告/);
  assert.match(html, /原始聊天记录/);
  assert.match(html, /情绪/);
  assert.match(html, /意图/);
  assert.match(html, /询问信息/);
  assert.match(html, /开心/);
  assert.match(html, /你好 &lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>alert\(1\)<\/script>/);
  assert.match(html, /接住并推进/);
  assert.match(html, /68/);
  assert.match(html, /data:image\/png;base64,c2VsZg==/);
  assert.match(html, /data:image\/jpeg;base64,b3RoZXI=/);
  assert.match(html, /对方 &lt;img&gt;/);
});

test("报告仅嵌入安全图片 data URL，其他头像回退为昵称首字", () => {
  const html = buildAnalysisReportHtml({
    messages: [
      {
        id: "m1",
        sender: "other",
        text: "文本",
        timestamp: null,
        kind: "text",
      },
    ],
    lines: {},
    overview: null,
    participants: {
      self: {
        displayName: "我 <script>",
        avatarDataUrl: "javascript:alert(1)",
      },
      other: {
        displayName: "田子夜 <svg>",
        avatarDataUrl: "data:image/svg+xml;base64,PHN2Zz4=",
      },
    },
    relation: "crush",
    generatedAt: "2026-09-29 12:00:00",
  });

  assert.match(html, /avatar-fallback/);
  assert.match(html, /我 &lt;script&gt;/);
  assert.match(html, /田子夜 &lt;svg&gt;/);
  assert.doesNotMatch(html, /javascript:|image\/svg\+xml|<svg>/);
  assert.match(html, /情绪|意图|原始聊天记录/);
});
