export const UPSTREAM_REPOSITORY = "FerryCorleone/crush-monitor";
export const UPSTREAM_BRANCH = "main";
export const UPSTREAM_COMMITS_URL =
  [
    "https://api.github.com/repos/",
    UPSTREAM_REPOSITORY,
    "/commits/",
    UPSTREAM_BRANCH,
  ].join("");
export const UPSTREAM_COMPARE_URL =
  `https://api.github.com/repos/${UPSTREAM_REPOSITORY}/compare`;
export const UPSTREAM_COMPARE_WEB_URL =
  `https://github.com/${UPSTREAM_REPOSITORY}/compare`;

export type UpstreamFileChange = {
  path: string;
  status: string;
  additions: number;
  deletions: number;
};

export type UpstreamStatus = {
  repository: string;
  branch: string;
  available: boolean;
  hasUpdate: boolean;
  baselineCommit: string | null;
  latestCommit: string | null;
  latestMessage: string | null;
  latestUrl: string | null;
  compareUrl: string | null;
  changedFiles: UpstreamFileChange[];
  checkedAt: string;
  error: string | null;
};

export type UpstreamCache = {
  status: UpstreamStatus;
  expiresAt: number;
};

const upstreamCacheTtlMs = 6 * 60 * 60 * 1000;

type RecordValue = Record<string, unknown>;

class UpstreamRequestError extends Error {
  constructor() {
    super("GitHub upstream request failed");
  }
}

function isRecord(value: unknown): value is RecordValue {
  return typeof value === "object" && value !== null;
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function readNonNegativeInteger(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : 0;
}

function readBaselineCommit(): string | null {
  const value = process.env.CRUSH_BUILD_COMMIT?.trim() || "";
  return /^[0-9a-f]{40}$/i.test(value) ? value.toLowerCase() : null;
}

function createStatus(
  baselineCommit: string | null,
  checkedAt: string,
  overrides: Partial<UpstreamStatus>,
): UpstreamStatus {
  return {
    repository: UPSTREAM_REPOSITORY,
    branch: UPSTREAM_BRANCH,
    available: false,
    hasUpdate: false,
    baselineCommit,
    latestCommit: null,
    latestMessage: null,
    latestUrl: null,
    compareUrl: null,
    changedFiles: [],
    checkedAt,
    error: "暂时无法检查原仓库更新，请检查网络后重试。",
    ...overrides,
  };
}

async function requestJson(
  fetchImpl: typeof fetch,
  url: string,
): Promise<unknown> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const response = await fetchImpl(url, {
        method: "GET",
        headers: {
          Accept: "application/vnd.github+json",
          "User-Agent": "crush-monitor-upstream-check",
        },
        signal: AbortSignal.timeout(8000),
      });
      if (!response.ok) throw new UpstreamRequestError();
      return await response.json();
    } catch (error) {
      lastError = error;
      if (attempt === 0)
        await new Promise<void>((resolve) => setTimeout(resolve, 250));
    }
  }
  throw lastError instanceof UpstreamRequestError
    ? lastError
    : new UpstreamRequestError();
}

function readLatestCommit(value: unknown) {
  if (!isRecord(value)) throw new UpstreamRequestError();
  const sha = readString(value.sha);
  if (!sha || !/^[0-9a-f]{40}$/i.test(sha))
    throw new UpstreamRequestError();
  const commit = isRecord(value.commit) ? value.commit : {};
  const author = isRecord(commit.author) ? commit.author : {};
  return {
    sha: sha.toLowerCase(),
    message: readString(commit.message),
    url: readString(value.html_url),
    date: readString(author.date),
  };
}

function readChangedFiles(value: unknown): UpstreamFileChange[] | null {
  if (!isRecord(value) || !Array.isArray(value.files)) return null;
  const files: UpstreamFileChange[] = [];
  for (const entry of value.files.slice(0, 50)) {
    if (!isRecord(entry)) continue;
    const path = readString(entry.filename);
    const status = readString(entry.status);
    if (!path || !status) continue;
    files.push({
      path,
      status,
      additions: readNonNegativeInteger(entry.additions),
      deletions: readNonNegativeInteger(entry.deletions),
    });
  }
  return files;
}

export async function checkUpstreamUpdate(
  fetchImpl: typeof fetch,
): Promise<UpstreamStatus> {
  const checkedAt = new Date().toISOString();
  const baselineCommit = readBaselineCommit();
  let latest: ReturnType<typeof readLatestCommit>;
  try {
    latest = readLatestCommit(
      await requestJson(fetchImpl, UPSTREAM_COMMITS_URL),
    );
  } catch {
    return createStatus(baselineCommit, checkedAt, {});
  }
  if (!baselineCommit) {
    return createStatus(baselineCommit, checkedAt, {
      available: true,
      latestCommit: latest.sha,
      latestMessage: latest.message,
      latestUrl: latest.url,
      error: "当前运行版本没有构建基准，暂时无法比较上游更新。",
    });
  }
  if (latest.sha === baselineCommit) {
    return createStatus(baselineCommit, checkedAt, {
      available: true,
      latestCommit: latest.sha,
      latestMessage: latest.message,
      latestUrl: latest.url,
      error: null,
    });
  }
  const compareApiUrl = [
    UPSTREAM_COMPARE_URL,
    "/",
    baselineCommit,
    "...",
    latest.sha,
  ].join("");
  const compareUrl = [
    UPSTREAM_COMPARE_WEB_URL,
    "/",
    baselineCommit,
    "...",
    latest.sha,
  ].join("");
  try {
    const files = readChangedFiles(await requestJson(fetchImpl, compareApiUrl));
    return createStatus(baselineCommit, checkedAt, {
      available: true,
      hasUpdate: true,
      latestCommit: latest.sha,
      latestMessage: latest.message,
      latestUrl: latest.url,
      compareUrl,
      changedFiles: files ?? [],
      error: files ? null : "已发现上游更新，但变更文件明细暂时不可用。",
    });
  } catch {
    return createStatus(baselineCommit, checkedAt, {
      available: true,
      hasUpdate: true,
      latestCommit: latest.sha,
      latestMessage: latest.message,
      latestUrl: latest.url,
      compareUrl,
      error: "已发现上游更新，但变更文件明细暂时不可用。",
    });
  }
}

export async function readCachedStatus(
  cache: UpstreamCache | null,
  now: number,
  fetchImpl: typeof fetch,
): Promise<{ status: UpstreamStatus; cache: UpstreamCache }> {
  if (cache && cache.expiresAt > now) return { status: cache.status, cache };
  const status = await checkUpstreamUpdate(fetchImpl);
  return {
    status,
    cache: { status, expiresAt: now + upstreamCacheTtlMs },
  };
}
