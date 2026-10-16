import { test } from "node:test";
import assert from "node:assert/strict";
import {
  createParticipantProfiles,
  mergeParticipantProfiles,
  swapParticipantProfiles,
} from "../shared/participants";

test("参与者资料提供稳定默认昵称且头像默认为空", () => {
  assert.deepEqual(createParticipantProfiles("", "  "), {
    self: { displayName: "我", avatarDataUrl: null },
    other: { displayName: "Crush", avatarDataUrl: null },
  });
  assert.deepEqual(createParticipantProfiles("  小明  ", "  子夜 "), {
    self: { displayName: "小明", avatarDataUrl: null },
    other: { displayName: "子夜", avatarDataUrl: null },
  });
});

test("非空昵称和头像覆盖旧资料，空值不会抹掉旧值", () => {
  const current = {
    self: { displayName: "我", avatarDataUrl: "data:image/png;base64,self" },
    other: {
      displayName: "旧昵称",
      avatarDataUrl: "data:image/jpeg;base64,other",
    },
  };
  const merged = mergeParticipantProfiles(current, {
    self: { displayName: "  ", avatarDataUrl: null },
    other: {
      displayName: "  子夜  ",
      avatarDataUrl: "data:image/png;base64,new",
    },
  });

  assert.deepEqual(merged, {
    self: { displayName: "我", avatarDataUrl: "data:image/png;base64,self" },
    other: {
      displayName: "子夜",
      avatarDataUrl: "data:image/png;base64,new",
    },
  });
  assert.notEqual(merged, current);
  assert.equal(current.other.displayName, "旧昵称");
});

test("交换资料会同时交换昵称头像且不修改输入", () => {
  const profiles = {
    self: { displayName: "我", avatarDataUrl: "data:image/png;base64,a" },
    other: { displayName: "子夜", avatarDataUrl: "data:image/png;base64,b" },
  };
  const snapshot = structuredClone(profiles);

  assert.deepEqual(swapParticipantProfiles(profiles), {
    self: { displayName: "子夜", avatarDataUrl: "data:image/png;base64,b" },
    other: { displayName: "我", avatarDataUrl: "data:image/png;base64,a" },
  });
  assert.deepEqual(profiles, snapshot);
});
