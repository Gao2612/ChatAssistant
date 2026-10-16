import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  migrateSavedConversation,
  type SavedConversationV1,
  type SavedConversationV2,
} from "../src/storage";

const legacy: SavedConversationV1 = {
  schema: 1,
  rubric: "legacy-rubric",
  messages: [
    {
      id: "m1",
      sender: "other",
      text: "你好",
      timestamp: "2026-09-30 10:00:00",
      kind: "text",
    },
  ],
  self: "  小明  ",
  other: "  子夜  ",
  relation: "crush",
  lines: {},
  events: {},
  overview: null,
  trend: [{ at: "2026-09-30", value: 50, count: 1 }],
  analyzedCount: 1,
  completed: true,
};

test("schema 1 集中迁移到 schema 2 并原样保留分析数据", () => {
  const migrated = migrateSavedConversation(legacy);

  assert.deepEqual(migrated, {
    schema: 2,
    rubric: legacy.rubric,
    messages: legacy.messages,
    participants: {
      self: { displayName: "小明", avatarDataUrl: null },
      other: { displayName: "子夜", avatarDataUrl: null },
    },
    relation: legacy.relation,
    lines: legacy.lines,
    events: legacy.events,
    overview: legacy.overview,
    trend: legacy.trend,
    analyzedCount: legacy.analyzedCount,
    completed: legacy.completed,
  });
  assert.equal(legacy.schema, 1);
  assert.equal(legacy.self, "  小明  ");
});

test("schema 2 读取幂等，不重复迁移或丢字段", () => {
  const current: SavedConversationV2 = {
    schema: 2,
    rubric: legacy.rubric,
    messages: legacy.messages,
    participants: {
      self: { displayName: "我", avatarDataUrl: null },
      other: {
        displayName: "子夜",
        avatarDataUrl: "data:image/png;base64,avatar",
      },
    },
    relation: legacy.relation,
    lines: legacy.lines,
    events: legacy.events,
    overview: legacy.overview,
    trend: legacy.trend,
    analyzedCount: legacy.analyzedCount,
    completed: legacy.completed,
  };

  assert.equal(migrateSavedConversation(current), current);
  assert.equal(migrateSavedConversation(undefined), undefined);
});

test("未知或损坏 schema 显式报错", () => {
  for (const value of [
    null,
    {},
    { schema: 3 },
    { ...legacy, messages: "broken" },
    {
      schema: 2,
      participants: {
        self: { displayName: "我", avatarDataUrl: null },
      },
    },
  ])
    assert.throws(() => migrateSavedConversation(value), /聊天记录|存储|schema/i);
});

test("IndexedDB 升级到 2 且只在 workspace 不存在时创建", async () => {
  const source = await readFile(
    new URL("../src/storage.ts", import.meta.url),
    "utf8",
  );
  assert.match(source, /indexedDB\.open\("crush-monitor", 2\)/);
  assert.match(source, /objectStoreNames\.contains\("workspace"\)/);
  assert.doesNotMatch(source, /deleteObjectStore/);
});
