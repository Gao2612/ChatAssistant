import { after, test } from "node:test";
import assert from "node:assert/strict";
import { readFile, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import {
  applyRuntimeConfig,
  readRuntimeConfig,
  saveRuntimeConfig,
  validateRuntimeConfigInput,
  type RuntimeConfig,
} from "../server/runtime-config";
import type { Provider } from "../server/provider-config";

type RuntimeEnvironment = Record<string, string | undefined>;
type JsonObject = Record<string, unknown>;
type ServerModule = typeof import("../server/index");
type RunningApp = {
  baseUrl: string;
  close: () => Promise<void>;
};

const localOrigin = "http://localhost:5178";
const originalAppData = process.env.APPDATA;
const originalNoListen = process.env.CRUSH_NO_LISTEN;
let serverModule: ServerModule | null = null;

after(() => {
  if (originalAppData === undefined) delete process.env.APPDATA;
  else process.env.APPDATA = originalAppData;
  if (originalNoListen === undefined) delete process.env.CRUSH_NO_LISTEN;
  else process.env.CRUSH_NO_LISTEN = originalNoListen;
});

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function readJson(response: Response): Promise<JsonObject> {
  const value: unknown = await response.json();
  if (!isJsonObject(value)) throw new Error("响应不是 JSON 对象");
  return value;
}

async function loadServerModule(
  appDataDirectory: string,
): Promise<ServerModule> {
  process.env.APPDATA = appDataDirectory;
  process.env.CRUSH_NO_LISTEN = "1";
  if (serverModule === null) serverModule = await import("../server/index");
  return serverModule;
}

async function closeServer(server: Server): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    server.close((error?: Error) => {
      if (error) reject(error);
      else resolve();
    });
  });
}

async function startApp(
  appDataDirectory: string,
  environment: RuntimeEnvironment,
): Promise<RunningApp> {
  const module = await loadServerModule(appDataDirectory);
  const app = module.createApp(environment, appDataDirectory);
  const server = await new Promise<Server>((resolve, reject) => {
    const listener = app.listen(0, "127.0.0.1", () => resolve(listener));
    listener.once("error", reject);
  });
  const address = server.address();
  if (address === null || typeof address === "string") {
    await closeServer(server);
    throw new Error("测试服务没有分配本机端口");
  }
  const port = (address as AddressInfo).port;
  return {
    baseUrl: `http://127.0.0.1:${port}`,
    close: () => closeServer(server),
  };
}

async function withTempDirectory(
  run: (directory: string) => Promise<void>,
): Promise<void> {
  const directory = await mkdtemp(join(tmpdir(), "crush-runtime-config-"));
  try {
    await run(directory);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test("空 API Key 被配置接口拒绝", async () => {
  await withTempDirectory(async (directory) => {
    const environment: RuntimeEnvironment = {
      APPDATA: directory,
      JEV_PROVIDER: "typesafe",
      JEV_API_KEY: undefined,
    };
    const app = await startApp(directory, environment);
    try {
      const response = await fetch(`${app.baseUrl}/api/config`, {
        method: "PUT",
        headers: {
          "content-type": "application/json",
          origin: localOrigin,
        },
        body: JSON.stringify({ provider: "typesafe", apiKey: "" }),
      });
      const body = await readJson(response);
      assert.equal(response.status, 400);
      assert.match(String(body.error), /API Key/);
      assert.equal(JSON.stringify(body).includes("apiKey"), false);
    } finally {
      await app.close();
    }
  });
});

test("非法平台被配置接口拒绝且响应不包含 Key", async () => {
  await withTempDirectory(async (directory) => {
    const environment: RuntimeEnvironment = {
      APPDATA: directory,
      JEV_PROVIDER: "typesafe",
      JEV_API_KEY: undefined,
    };
    const app = await startApp(directory, environment);
    const apiKey = "secret-invalid-provider";
    try {
      const response = await fetch(`${app.baseUrl}/api/config`, {
        method: "PUT",
        headers: {
          "content-type": "application/json",
          origin: localOrigin,
        },
        body: JSON.stringify({ provider: "not-a-provider", apiKey }),
      });
      const body = await readJson(response);
      assert.equal(response.status, 400);
      assert.match(String(body.error), /Unsupported|平台/);
      assert.equal(JSON.stringify(body).includes(apiKey), false);
    } finally {
      await app.close();
    }
  });
});

test("跨来源请求不能写入本机配置", async () => {
  await withTempDirectory(async (directory) => {
    const environment: RuntimeEnvironment = {
      APPDATA: directory,
      JEV_PROVIDER: "typesafe",
      JEV_API_KEY: undefined,
    };
    const app = await startApp(directory, environment);
    const apiKey = "secret-cross-origin";
    try {
      const response = await fetch(`${app.baseUrl}/api/config`, {
        method: "PUT",
        headers: {
          "content-type": "application/json",
          origin: "https://evil.example",
        },
        body: JSON.stringify({ provider: "typesafe", apiKey }),
      });
      const body = await readJson(response);
      assert.equal(response.status, 403);
      assert.equal(JSON.stringify(body).includes(apiKey), false);
      await assert.rejects(
        readFile(join(directory, "crush-monitor", "config.json")),
      );
    } finally {
      await app.close();
    }
  });
});

test("四个平台均可保存，立即更新状态且所有响应都不回显 Key", async () => {
  await withTempDirectory(async (directory) => {
    const environment: RuntimeEnvironment = {
      APPDATA: directory,
      JEV_PROVIDER: "typesafe",
      JEV_API_KEY: undefined,
    };
    await writeFile(
      join(directory, ".env"),
      "JEV_API_KEY=do-not-touch\n",
      "utf8",
    );
    const app = await startApp(directory, environment);
    const configurations: Array<{
      provider: Provider;
      apiKey: string;
      deepseekModel?: string;
    }> = [
      { provider: "typesafe", apiKey: "typesafe-test-key" },
      { provider: "vercel", apiKey: "vercel-test-key" },
      { provider: "openrouter", apiKey: "sk-or-test-key" },
      {
        provider: "deepseek",
        apiKey: "deepseek-test-key",
        deepseekModel: "deepseek-v4-pro",
      },
    ];
    try {
      for (const configuration of configurations) {
        const response = await fetch(`${app.baseUrl}/api/config`, {
          method: "PUT",
          headers: {
            "content-type": "application/json",
            origin: localOrigin,
          },
          body: JSON.stringify(configuration),
        });
        const body = await readJson(response);
        const expectedModel =
          configuration.deepseekModel ??
          (configuration.provider === "typesafe"
            ? "jev-1.13.0"
            : configuration.provider === "vercel"
              ? "typesafe-ai/jev"
              : configuration.provider === "openrouter"
                ? "typesafe/jev-1.13"
                : "deepseek-flash");
        assert.equal(response.status, 200);
        assert.deepEqual(body, {
          configured: true,
          provider: configuration.provider,
          model: expectedModel,
          hasApiKey: true,
          requiresApiKey: true,
        });
        assert.equal(
          JSON.stringify(body).includes(configuration.apiKey),
          false,
        );
        assert.equal(environment.JEV_PROVIDER, configuration.provider);
        assert.equal(environment.JEV_API_KEY, configuration.apiKey);
      }

      const statusResponse = await fetch(`${app.baseUrl}/api/config`, {
        headers: { origin: localOrigin },
      });
      const status = await readJson(statusResponse);
      assert.equal(statusResponse.status, 200);
      assert.deepEqual(status, {
        configured: true,
        provider: "deepseek",
        model: "deepseek-v4-pro",
        hasApiKey: true,
        requiresApiKey: true,
      });
      assert.deepEqual(Object.keys(status).sort(), [
        "configured",
        "hasApiKey",
        "model",
        "provider",
        "requiresApiKey",
      ]);
      assert.equal(JSON.stringify(status).includes("deepseek-test-key"), false);

      const healthResponse = await fetch(`${app.baseUrl}/api/health`, {
        headers: { origin: localOrigin },
      });
      const health = await readJson(healthResponse);
      assert.equal(JSON.stringify(health).includes("deepseek-test-key"), false);
      assert.equal(
        await readFile(join(directory, ".env"), "utf8"),
        "JEV_API_KEY=do-not-touch\n",
      );
      const persistedText = await readFile(
        join(directory, "crush-monitor", "config.json"),
        "utf8",
      );
      const persisted: unknown = JSON.parse(persistedText);
      assert.deepEqual(persisted, {
        provider: "deepseek",
        apiKey: "deepseek-test-key",
        deepseekModel: "deepseek-v4-pro",
      });
    } finally {
      await app.close();
    }
  });
});

test("Codex 配置无需 Key 且落盘不写入虚构凭据", async () => {
  await withTempDirectory(async (directory) => {
    const environment: RuntimeEnvironment = {
      APPDATA: directory,
      JEV_PROVIDER: "deepseek",
      JEV_API_KEY: "old-secret-key",
      DEEPSEEK_MODEL: "old-model",
    };
    const validated = validateRuntimeConfigInput({ provider: "codex" });
    assert.deepEqual(validated, { provider: "codex" });

    await saveRuntimeConfig(validated, directory);
    assert.deepEqual(readRuntimeConfig(directory), { provider: "codex" });
    assert.deepEqual(
      JSON.parse(
        await readFile(
          join(directory, "crush-monitor", "config.json"),
          "utf8",
        ),
      ),
      { provider: "codex" },
    );

    applyRuntimeConfig(validated, environment);
    assert.equal(environment.JEV_PROVIDER, "codex");
    assert.equal(environment.JEV_API_KEY, undefined);
    assert.equal(environment.DEEPSEEK_MODEL, undefined);

    const app = await startApp(directory, environment);
    try {
      const response = await fetch(`${app.baseUrl}/api/config`, {
        headers: { origin: localOrigin },
      });
      assert.equal(response.status, 200);
      assert.deepEqual(await readJson(response), {
        configured: true,
        provider: "codex",
        model: "codex-default",
        hasApiKey: false,
        requiresApiKey: false,
      });
    } finally {
      await app.close();
    }
  });
});

test("Codex 选择的模型会独立持久化并传回运行时", async () => {
  await withTempDirectory(async (directory) => {
    const environment: RuntimeEnvironment = {
      APPDATA: directory,
      JEV_PROVIDER: "deepseek",
      JEV_API_KEY: "old-secret-key",
      DEEPSEEK_MODEL: "old-model",
    };
    const selected = validateRuntimeConfigInput({
      provider: "codex",
      codexModel: "gpt-6-astra",
    });
    assert.deepEqual(selected, {
      provider: "codex",
      codexModel: "gpt-6-astra",
    });

    await saveRuntimeConfig(selected, directory);
    assert.deepEqual(readRuntimeConfig(directory), selected);
    applyRuntimeConfig(selected, environment);
    assert.equal(environment.JEV_PROVIDER, "codex");
    assert.equal(environment.CODEX_MODEL, "gpt-6-astra");
    assert.equal(environment.JEV_API_KEY, undefined);
    assert.equal(environment.DEEPSEEK_MODEL, undefined);
  });
});

test("重启后从 APPDATA 配置文件恢复运行时配置", async () => {
  await withTempDirectory(async (directory) => {
    const stored = validateRuntimeConfigInput({
      provider: "deepseek",
      apiKey: "deepseek-restart-key",
      deepseekModel: "deepseek-restart",
    });
    if (stored.provider !== "deepseek")
      throw new Error("测试配置没有保留 DeepSeek 判别字段");
    await saveRuntimeConfig(stored, directory);
    const environment: RuntimeEnvironment = {
      APPDATA: directory,
      JEV_PROVIDER: "typesafe",
      JEV_API_KEY: undefined,
    };
    const app = await startApp(directory, environment);
    try {
      const restored = readRuntimeConfig(directory);
      assert.deepEqual(restored, stored);
      assertRuntimeConfigApplied(environment, stored);
      const response = await fetch(`${app.baseUrl}/api/config`, {
        headers: { origin: localOrigin },
      });
      const body = await readJson(response);
      assert.equal(response.status, 200);
      assert.deepEqual(body, {
        configured: true,
        provider: "deepseek",
        model: "deepseek-restart",
        hasApiKey: true,
        requiresApiKey: true,
      });
      assert.equal(JSON.stringify(body).includes(stored.apiKey), false);
    } finally {
      await app.close();
    }
  });
});

test("分析请求沿用运行时 DeepSeek 配置而不回退 TypeSafe", async (t) => {
  await withTempDirectory(async (directory) => {
    const stored = validateRuntimeConfigInput({
      provider: "deepseek",
      apiKey: "deepseek-route-key",
      deepseekModel: "deepseek-route-model",
    });
    await saveRuntimeConfig(stored, directory);
    const environment: RuntimeEnvironment = {
      APPDATA: directory,
      JEV_PROVIDER: "typesafe",
      JEV_API_KEY: undefined,
    };
    const originalProvider = process.env.JEV_PROVIDER;
    const originalKey = process.env.JEV_API_KEY;
    const originalTypeSafeKey = process.env.TYPESAFE_API_KEY;
    const originalGatewayKey = process.env.AI_GATEWAY_API_KEY;
    const originalOpenRouterKey = process.env.OPENROUTER_API_KEY;
    const originalDeepSeekKey = process.env.DEEPSEEK_API_KEY;
    const originalModel = process.env.DEEPSEEK_MODEL;
    const originalFetch = globalThis.fetch;
    process.env.JEV_PROVIDER = "typesafe";
    delete process.env.JEV_API_KEY;
    delete process.env.TYPESAFE_API_KEY;
    delete process.env.AI_GATEWAY_API_KEY;
    delete process.env.OPENROUTER_API_KEY;
    delete process.env.DEEPSEEK_API_KEY;
    delete process.env.DEEPSEEK_MODEL;
    let calls = 0;
    let app: RunningApp;
    globalThis.fetch = async (
      input: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      if (String(input).startsWith(app.baseUrl))
        return originalFetch(input, init);
      calls++;
      const wire = JSON.parse(String(init?.body)) as {
        model: string;
        messages?: Array<{ content?: string }>;
      };
      const body = JSON.parse(wire.messages?.[1]?.content ?? "{}") as {
        questions: Record<
          string,
          { type: string; criteria: Record<string, unknown> }
        >;
      };
      const answers = Object.fromEntries(
        Object.entries(body.questions).map(([id, question]) => {
          if (question.type === "noul")
            return [id, { type: "noul", noul: 0.1 }];
          const keys = Object.keys(question.criteria);
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
      return Response.json({
        model: wire.model,
        choices: [
          {
            finish_reason: "stop",
            message: { content: JSON.stringify({ answers }) },
          },
        ],
        usage: { prompt_tokens: 10, completion_tokens: 5 },
      });
    };
    t.after(() => {
      globalThis.fetch = originalFetch;
      if (originalProvider === undefined) delete process.env.JEV_PROVIDER;
      else process.env.JEV_PROVIDER = originalProvider;
      if (originalKey === undefined) delete process.env.JEV_API_KEY;
      else process.env.JEV_API_KEY = originalKey;
      if (originalTypeSafeKey === undefined) delete process.env.TYPESAFE_API_KEY;
      else process.env.TYPESAFE_API_KEY = originalTypeSafeKey;
      if (originalGatewayKey === undefined) delete process.env.AI_GATEWAY_API_KEY;
      else process.env.AI_GATEWAY_API_KEY = originalGatewayKey;
      if (originalOpenRouterKey === undefined) delete process.env.OPENROUTER_API_KEY;
      else process.env.OPENROUTER_API_KEY = originalOpenRouterKey;
      if (originalDeepSeekKey === undefined) delete process.env.DEEPSEEK_API_KEY;
      else process.env.DEEPSEEK_API_KEY = originalDeepSeekKey;
      if (originalModel === undefined) delete process.env.DEEPSEEK_MODEL;
      else process.env.DEEPSEEK_MODEL = originalModel;
    });
    app = await startApp(directory, environment);
    try {
      const response = await fetch(`${app.baseUrl}/api/analyze`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          revision: 1,
          relation: "crush",
          task: "overview",
          targetIds: [],
          messages: [
            {
              id: "message-1",
              sender: "other",
              text: "你好",
              timestamp: null,
              kind: "text",
            },
          ],
        }),
      });
      const body = await readJson(response);
      assert.equal(response.status, 200);
      assert.equal(body.model, "deepseek-route-model");
      assert.equal(calls, 1);
      assert.equal(JSON.stringify(body).includes("deepseek-route-key"), false);
    } finally {
      await app.close();
    }
  });
});

function assertRuntimeConfigApplied(
  environment: RuntimeEnvironment,
  config: RuntimeConfig,
): void {
  const before = { ...environment };
  applyRuntimeConfig(config, environment);
  assert.equal(environment.JEV_PROVIDER, config.provider);
  if (config.provider === "codex") {
    assert.equal(environment.JEV_API_KEY, undefined);
    assert.equal(environment.DEEPSEEK_MODEL, undefined);
    assert.equal(before.APPDATA, environment.APPDATA);
    return;
  }
  assert.equal(environment.JEV_API_KEY, config.apiKey);
  if (config.deepseekModel === undefined) {
    assert.equal(environment.DEEPSEEK_MODEL, undefined);
  } else {
    assert.equal(environment.DEEPSEEK_MODEL, config.deepseekModel);
  }
  assert.equal(before.APPDATA, environment.APPDATA);
}
