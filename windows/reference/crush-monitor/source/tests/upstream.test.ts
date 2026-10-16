import { test } from "node:test";
import assert from "node:assert/strict";
import {
  checkUpstreamUpdate,
  UPSTREAM_COMPARE_URL,
  UPSTREAM_COMMITS_URL,
  type UpstreamStatus,
} from "../server/upstream";

const baselineCommit = "a".repeat(40);
const latestCommit = "b".repeat(40);

test("上游 commit 相同，不提示更新", async (t) => {
  const previous = process.env.CRUSH_BUILD_COMMIT;
  process.env.CRUSH_BUILD_COMMIT = baselineCommit;
  t.after(() => {
    if (previous === undefined) delete process.env.CRUSH_BUILD_COMMIT;
    else process.env.CRUSH_BUILD_COMMIT = previous;
  });
  const requested: string[] = [];
  const result = await checkUpstreamUpdate(async (url) => {
    requested.push(String(url));
    return Response.json({
      sha: baselineCommit,
      html_url: [
        "https://github.com/FerryCorleone/crush-monitor/commit/",
        baselineCommit,
      ].join(""),
      commit: { message: "same", author: { date: "2026-09-24T00:00:00Z" } },
    });
  });
  assert.equal(result.available, true);
  assert.equal(result.hasUpdate, false);
  assert.equal(result.baselineCommit, baselineCommit);
  assert.equal(result.latestCommit, baselineCommit);
  assert.deepEqual(result.changedFiles, []);
  assert.deepEqual(requested, [UPSTREAM_COMMITS_URL]);
});

test("上游有新 commit，返回对比链接和变更文件", async (t) => {
  const previous = process.env.CRUSH_BUILD_COMMIT;
  process.env.CRUSH_BUILD_COMMIT = baselineCommit;
  t.after(() => {
    if (previous === undefined) delete process.env.CRUSH_BUILD_COMMIT;
    else process.env.CRUSH_BUILD_COMMIT = previous;
  });
  const requested: string[] = [];
  const result = await checkUpstreamUpdate(async (url) => {
    const target = String(url);
    requested.push(target);
    if (target === UPSTREAM_COMMITS_URL)
      return Response.json({
        sha: latestCommit,
        html_url: [
          "https://github.com/FerryCorleone/crush-monitor/commit/",
          latestCommit,
        ].join(""),
        commit: {
          message: "add upstream change",
          author: { date: "2026-09-24T01:00:00Z" },
        },
      });
    assert.equal(
      target,
      `${UPSTREAM_COMPARE_URL}/${baselineCommit}...${latestCommit}`,
    );
    return Response.json({
      html_url: [
        "https://github.com/FerryCorleone/crush-monitor/compare/",
        baselineCommit,
        "...",
        latestCommit,
      ].join(""),
      files: [
        {
          filename: "server/index.ts",
          status: "modified",
          additions: 3,
          deletions: 1,
        },
        { filename: "README.md", status: "added", additions: 10, deletions: 0 },
      ],
    });
  });
  assert.equal(result.available, true);
  assert.equal(result.hasUpdate, true);
  assert.equal(result.latestCommit, latestCommit);
  assert.equal(result.changedFiles.length, 2);
  assert.equal(
    result.compareUrl,
    [
      "https://github.com/FerryCorleone/crush-monitor/compare/",
      baselineCommit,
      "...",
      latestCommit,
    ].join(""),
  );
  assert.deepEqual(requested, [
    UPSTREAM_COMMITS_URL,
    `${UPSTREAM_COMPARE_URL}/${baselineCommit}...${latestCommit}`,
  ]);
});

test("没有构建基准时不误报更新", async (t) => {
  const previous = process.env.CRUSH_BUILD_COMMIT;
  delete process.env.CRUSH_BUILD_COMMIT;
  t.after(() => {
    if (previous === undefined) delete process.env.CRUSH_BUILD_COMMIT;
    else process.env.CRUSH_BUILD_COMMIT = previous;
  });
  const result = await checkUpstreamUpdate(async () =>
    Response.json({ sha: latestCommit, commit: { message: "latest" } }),
  );
  assert.equal(result.available, true);
  assert.equal(result.hasUpdate, false);
  assert.equal(result.baselineCommit, null);
  assert.match(result.error ?? "", /基准|构建/);
});

test("上游检查失败会重试一次并隐藏底层错误", async () => {
  let calls = 0;
  const result = await checkUpstreamUpdate(async () => {
    calls++;
    throw new Error("secret upstream response");
  });
  assert.equal(calls, 2);
  assert.equal(result.available, false);
  assert.equal(result.hasUpdate, false);
  assert.match(result.error ?? "", /无法检查/);
  assert.doesNotMatch(result.error ?? "", /secret/);
});

test("上游状态在六小时内复用缓存，过期后重新检查", async () => {
  const module = await import("../server/upstream");
  type Cache = { status: UpstreamStatus; expiresAt: number };
  type ReadCachedStatus = (
    cache: Cache | null,
    now: number,
    fetchImpl: typeof fetch,
  ) => Promise<{ status: UpstreamStatus; cache: Cache }>;
  const readCachedStatus = (module as Record<string, unknown>)
    .readCachedStatus as ReadCachedStatus | undefined;
  assert.equal(typeof readCachedStatus, "function");
  if (!readCachedStatus) return;

  const previous = process.env.CRUSH_BUILD_COMMIT;
  process.env.CRUSH_BUILD_COMMIT = baselineCommit;
  try {
    let calls = 0;
    const fetchImpl: typeof fetch = async () => {
      calls++;
      return Response.json({
        sha: baselineCommit,
        commit: { message: "same" },
      });
    };
    const first = await readCachedStatus(null, 1000, fetchImpl);
    const second = await readCachedStatus(first.cache, 1001, async () => {
      throw new Error("cache should be used");
    });
    assert.equal(calls, 1);
    assert.deepEqual(second.status, first.status);
    const third = await readCachedStatus(
      first.cache,
      1000 + 6 * 60 * 60 * 1000,
      fetchImpl,
    );
    assert.equal(calls, 2);
    assert.equal(third.status.available, true);
  } finally {
    if (previous === undefined) delete process.env.CRUSH_BUILD_COMMIT;
    else process.env.CRUSH_BUILD_COMMIT = previous;
  }
});
