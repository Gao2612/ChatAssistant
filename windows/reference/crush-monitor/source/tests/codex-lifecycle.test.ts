import { after, test } from "node:test";
import assert from "node:assert/strict";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import { CodexAppServerError } from "../server/codex-app-server";

type JsonRecord = Record<string, unknown>;
type ServerModule = typeof import("../server/index");
type CodexClient = {
  status(signal: AbortSignal): Promise<{
    available: boolean;
    loggedIn: boolean;
    authMode: "chatgpt" | null;
    planType: string | null;
    rateLimits: readonly {
      usedPercent: number | null;
      resetsAt: number | null;
      windowDurationMins: number | null;
    }[];
  }>;
  models(signal: AbortSignal): Promise<
    readonly {
      id: string;
      displayName: string;
      description: string;
      isDefault: boolean;
    }[]
  >;
  evaluate(prompt: string, signal: AbortSignal): Promise<string>;
  close(): Promise<void>;
};

const originalNoListen = process.env.CRUSH_NO_LISTEN;
const originalAppData = process.env.APPDATA;
let serverModule: ServerModule | null = null;

after(() => {
  if (originalNoListen === undefined) delete process.env.CRUSH_NO_LISTEN;
  else process.env.CRUSH_NO_LISTEN = originalNoListen;
  if (originalAppData === undefined) delete process.env.APPDATA;
  else process.env.APPDATA = originalAppData;
});

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function json(response: Response): Promise<JsonRecord> {
  const value: unknown = await response.json();
  if (!isRecord(value)) throw new Error("响应不是对象");
  return value;
}

async function loadServer(directory: string): Promise<ServerModule> {
  process.env.CRUSH_NO_LISTEN = "1";
  process.env.APPDATA = directory;
  if (!serverModule) serverModule = await import("../server/index");
  return serverModule;
}

async function start(
  module: ServerModule,
  directory: string,
  client: CodexClient,
): Promise<{ baseUrl: string; close: () => Promise<void> }> {
  const environment: Record<string, string | undefined> = {
    APPDATA: directory,
    JEV_PROVIDER: "codex",
  };
  const app = module.createApp(environment, directory, {
    getCodexClient: () => client,
  });
  const server = await new Promise<Server>((resolve, reject) => {
    const listener = app.listen(0, "127.0.0.1", () => resolve(listener));
    listener.once("error", reject);
  });
  const address = server.address();
  if (!address || typeof address === "string")
    throw new Error("测试服务没有端口");
  return {
    baseUrl: `http://127.0.0.1:${(address as AddressInfo).port}`,
    close: () =>
      new Promise<void>((resolve, reject) => {
        server.close((error?: Error) => (error ? reject(error) : resolve()));
      }),
  };
}

function outputForPrompt(prompt: string): string {
  const match = prompt.match(/不可信数据开始：\n([\s\S]*?)\n不可信数据结束。$/);
  if (!match) throw new Error("测试没有找到 Codex 输入边界");
  const input = JSON.parse(match[1]) as {
    questions: Record<
      string,
      { type: string; criteria?: Record<string, unknown> | readonly string[] }
    >;
  };
  const answers = Object.fromEntries(
    Object.entries(input.questions).map(([id, question]) => {
      if (question.type === "noul")
        return [id, { type: "noul", noul: 0.1 }];
      const keys = Object.keys(question.criteria ?? {});
      const probabilities = Object.fromEntries(
        keys.map((key, index) => [key, index === 0 ? 1 : 0]),
      );
      return [
        id,
        {
          type: question.type,
          confidence: 1,
          probabilities,
          ...(question.type === "choice"
            ? { choice: keys[0] }
            : { score: 0 }),
        },
      ];
    }),
  );
  return JSON.stringify({ answers });
}

function workingClient(): CodexClient {
  return {
    status: async () => ({
      available: true,
      loggedIn: true,
      authMode: "chatgpt",
      planType: "plus",
      rateLimits: [
        {
          usedPercent: 25,
          resetsAt: 1_800_000_000,
          windowDurationMins: 300,
        },
      ],
    }),
    models: async () => [
      {
        id: "gpt-6-sol",
        displayName: "GPT-6 Sol",
        description: "workhorse",
        isDefault: true,
      },
    ],
    evaluate: async (prompt) => outputForPrompt(prompt),
    close: async () => undefined,
  };
}

test("Codex 状态接口只允许本机来源并返回白名单字段", async () => {
  const directory = await mkdtemp(join(tmpdir(), "crush-codex-status-"));
  try {
    const module = await loadServer(directory);
    const running = await start(module, directory, workingClient());
    try {
      const denied = await fetch(`${running.baseUrl}/api/codex/status`, {
        headers: { origin: "https://evil.example" },
      });
      assert.equal(denied.status, 403);

      const response = await fetch(`${running.baseUrl}/api/codex/status`, {
        headers: { origin: "http://localhost:5178" },
      });
      const body = await json(response);
      assert.equal(response.status, 200);
      assert.deepEqual(body, {
        available: true,
        loggedIn: true,
        authMode: "chatgpt",
        planType: "plus",
        rateLimits: [
          {
            usedPercent: 25,
            resetsAt: 1_800_000_000,
            windowDurationMins: 300,
          },
        ],
      });
      assert.doesNotMatch(
        JSON.stringify(body),
        /email|token|accessToken|path|directory|private/i,
      );
    } finally {
      await running.close();
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("Codex 模型目录只允许本机来源且不泄露本机信息", async () => {
  const directory = await mkdtemp(join(tmpdir(), "crush-codex-models-"));
  try {
    const module = await loadServer(directory);
    const running = await start(module, directory, workingClient());
    try {
      const denied = await fetch(`${running.baseUrl}/api/codex/models`, {
        headers: { origin: "https://evil.example" },
      });
      assert.equal(denied.status, 403);

      const response = await fetch(`${running.baseUrl}/api/codex/models`, {
        headers: { origin: "http://localhost:5178" },
      });
      const body = await json(response);
      assert.equal(response.status, 200);
      assert.deepEqual(body, {
        models: [
          {
            id: "gpt-6-sol",
            displayName: "GPT-6 Sol",
            description: "workhorse",
            isDefault: true,
          },
        ],
      });
      assert.doesNotMatch(JSON.stringify(body), /email|token|path|directory/i);
    } finally {
      await running.close();
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("Codex 未安装、未登录和非 ChatGPT 登录返回安全状态", async () => {
  const cases: readonly [
    ConstructorParameters<typeof CodexAppServerError>[0],
    boolean,
    boolean,
    RegExp,
  ][] = [
    ["unavailable", false, false, /安装|Codex CLI/],
    ["not-logged-in", true, false, /登录/],
    ["not-chatgpt", true, true, /ChatGPT/],
  ];
  const directory = await mkdtemp(join(tmpdir(), "crush-codex-errors-"));
  try {
    const module = await loadServer(directory);
    for (const [code, available, loggedIn, expected] of cases) {
      const client: CodexClient = {
        ...workingClient(),
        status: async () => {
          throw new CodexAppServerError(code, "private local path or email");
        },
      };
      const running = await start(module, directory, client);
      try {
        const response = await fetch(`${running.baseUrl}/api/codex/status`);
        const body = await json(response);
        assert.equal(response.status, 200);
        assert.equal(body.available, available);
        assert.equal(body.loggedIn, loggedIn);
        assert.match(String(body.error), expected);
        assert.doesNotMatch(JSON.stringify(body), /private local path|email/i);
      } finally {
        await running.close();
      }
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("分析路由选择 Codex 时复用注入客户端", async () => {
  const directory = await mkdtemp(join(tmpdir(), "crush-codex-analysis-"));
  let evaluations = 0;
  const client: CodexClient = {
    ...workingClient(),
    evaluate: async (prompt) => {
      evaluations++;
      return outputForPrompt(prompt);
    },
  };
  try {
    const module = await loadServer(directory);
    const running = await start(module, directory, client);
    try {
      const response = await fetch(`${running.baseUrl}/api/analyze`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          revision: 1,
          relation: "crush",
          task: "overview",
          targetIds: [],
          messages: [
            {
              id: "m1",
              sender: "other",
              text: "你好",
              timestamp: null,
              kind: "text",
            },
          ],
        }),
      });
      const body = await json(response);
      assert.equal(response.status, 200);
      assert.equal(body.model, "codex-default");
      assert.equal(evaluations, 1);
    } finally {
      await running.close();
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("提供商资源管理器复用客户端并幂等关闭一次", async () => {
  const directory = await mkdtemp(join(tmpdir(), "crush-codex-manager-"));
  try {
    const module = await loadServer(directory);
    let created = 0;
    let closed = 0;
    const client: CodexClient = {
      ...workingClient(),
      close: async () => {
        closed++;
      },
    };
    const manager = module.createProviderResourceManager(() => {
      created++;
      return client;
    });

    assert.equal(manager.getCodexClient(), client);
    assert.equal(manager.getCodexClient(), client);
    await manager.close();
    await manager.close();
    assert.equal(created, 1);
    assert.equal(closed, 1);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("Electron 本机服务清理路径包含提供商 shutdown", async () => {
  const source = await readFile(
    new URL("../scripts/electron-main.ts", import.meta.url),
    "utf8",
  );
  assert.match(source, /shutdown: \(\) => Promise<void>/);
  assert.match(source, /closeProviderResources/);
  assert.match(source, /await .*shutdown\(\)/);
});
