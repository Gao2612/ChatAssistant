import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parse } from "dotenv";
import {
  PROVIDERS,
  getProviderConfig,
  providerStatus,
} from "../server/provider-config";
import {
  evaluate,
  validateResult,
  ProviderError,
  providerErrorMessage,
} from "../server/provider";
import { updateConfiguration, saveConfiguration } from "../scripts/setup";
import { analyze, buildRequest } from "../server/analysis";
import type { AnalysisRequest } from "../shared/types";

const payload = {
  state: { message: "Synthetic connection test" },
  questions: {
    mood: {
      type: "choice" as const,
      instructions: "Pick a mood",
      criteria: { happy: null, sad: null },
    },
    quality: {
      type: "score" as const,
      instructions: "Rate quality",
      criteria: ["low", "mid", "high"] as const,
    },
    clear: {
      type: "noul" as const,
      instructions: "Is it clear?",
      criteria: { true: "clear", false: "unclear" },
    },
  },
};
const fixture = {
  model: "jev-1.13.0",
  answers: {
    mood: {
      type: "choice",
      choice: "happy",
      confidence: 0.6,
      probabilities: { happy: 0.8, sad: 0.2 },
    },
    quality: {
      type: "score",
      score: 1.8,
      confidence: 0.7,
      probabilities: { "0": 0, "1": 0.2, "2": 0.8 },
    },
    clear: { type: "noul", noul: 0.95 },
  },
  usage: { input_tokens: 100, output_tokens: 50 },
};

test("旧 TypeSafe Key 继续可用，切平台不借用旧 Key", () => {
  assert.equal(
    getProviderConfig({ TYPESAFE_API_KEY: "old-key" }).provider,
    "typesafe",
  );
  assert.throws(
    () =>
      getProviderConfig({
        JEV_PROVIDER: "vercel",
        TYPESAFE_API_KEY: "old-key",
      }),
    /API Key/,
  );
  assert.equal(
    getProviderConfig({
      JEV_PROVIDER: "vercel",
      AI_GATEWAY_API_KEY: "gateway-key",
    }).apiKey,
    "gateway-key",
  );
  assert.equal(
    getProviderConfig({
      JEV_PROVIDER: "openrouter",
      OPENROUTER_API_KEY: "router-key",
    }).apiKey,
    "router-key",
  );
  assert.equal(
    getProviderConfig({
      JEV_PROVIDER: " VERCEL ",
      JEV_API_KEY: " current-key ",
      TYPESAFE_API_KEY: "old-key",
    }).apiKey,
    "current-key",
  );
});

test("拼错平台、空 Key、占位符和粘贴整行配置提前报错；健康状态不含 Key", () => {
  for (const provider of ["typo", "__proto__", "cloudflare", "netlify"])
    assert.throws(
      () =>
        getProviderConfig({ JEV_PROVIDER: provider, JEV_API_KEY: "test-key" }),
      /Unsupported/,
    );
  for (const key of [
    "",
    "your_api_key",
    "Bearer test-key",
    "JEV_API_KEY=test-key",
    "key\nother",
    "'key'",
  ])
    assert.equal(providerStatus({ JEV_API_KEY: key }).configured, false);
  assert.throws(
    () =>
      getProviderConfig({
        JEV_PROVIDER: "vercel",
        JEV_API_KEY: "sk-or-example",
      }),
    /OpenRouter/,
  );
  assert.deepEqual(
    providerStatus({
      JEV_PROVIDER: "openrouter",
      JEV_API_KEY: "secret-test-value",
    }),
    { configured: true, provider: "openrouter", model: "typesafe/jev-1.13" },
  );
});

test("DeepSeek 使用独立 Key，并允许覆盖模型名", () => {
  const config = getProviderConfig({
    JEV_PROVIDER: "deepseek",
    DEEPSEEK_API_KEY: "deepseek-test-key",
    DEEPSEEK_MODEL: "deepseek-v4-pro",
  });
  assert.equal(config.provider, "deepseek");
  assert.equal(config.apiKey, "deepseek-test-key");
  assert.equal(config.model, "deepseek-v4-pro");
  assert.equal(config.endpoint, "https://api.deepseek.com/chat/completions");
});

test("Codex 使用本机登录且不读取或要求 API Key", () => {
  const config = getProviderConfig({
    JEV_PROVIDER: "codex",
    JEV_API_KEY: "must-not-be-used",
  });

  assert.deepEqual(config, {
    provider: "codex",
    kind: "codex",
    name: "ChatGPT Plus（Codex）",
    model: "codex-default",
  });
  assert.deepEqual(providerStatus({ JEV_PROVIDER: "codex" }), {
    configured: true,
    provider: "codex",
    model: "codex-default",
  });
});

for (const provider of ["typesafe", "vercel", "openrouter"] as const) {
  test(`${provider} 路由、认证、三种判断和概率保真`, async () => {
    const config = getProviderConfig({
      JEV_PROVIDER: provider,
      JEV_API_KEY: "test-only-key",
    });
    let calls = 0;
    const mock: typeof fetch = async (url, options) => {
      calls++;
      assert.equal(url, PROVIDERS[provider].endpoint);
      assert.equal(options?.redirect, "error");
      assert.equal(
        new Headers(options?.headers).get("authorization"),
        "Bearer test-only-key",
      );
      assert.deepEqual(JSON.parse(String(options?.body)), {
        ...payload,
        model: config.model,
      });
      assert.ok(options?.signal);
      return Response.json(fixture);
    };
    const result = await evaluate(payload, undefined, config, mock);
    assert.deepEqual(result, fixture);
    assert.equal(calls, 1);
  });
}

test("DeepSeek Chat Completions 转换为统一判断结果并映射用量", async () => {
  const config = getProviderConfig({
    JEV_PROVIDER: "deepseek",
    JEV_API_KEY: "deepseek-test-key",
  });
  const answers = {
    mood: {
      type: "choice",
      choice: "happy",
      confidence: 0.6,
      probabilities: { happy: 0.8, sad: 0.2 },
    },
    quality: {
      type: "score",
      score: 1.8,
      confidence: 0.7,
      probabilities: { "0": 0, "1": 0.2, "2": 0.8 },
    },
    clear: { type: "noul", noul: 0.95 },
  };
  const requestPayload = { ...payload, model: "jev-1.13.0" };
  let calls = 0;
  const mock: typeof fetch = async (url, options) => {
    calls++;
    assert.equal(url, "https://api.deepseek.com/chat/completions");
    assert.equal(
      new Headers(options?.headers).get("authorization"),
      "Bearer deepseek-test-key",
    );
    const request = JSON.parse(String(options?.body)) as {
      model: string;
      messages: { role: string; content: string }[];
      response_format: { type: string };
      stream: boolean;
    };
    assert.equal(request.model, "deepseek-flash");
    assert.equal(request.messages.length, 2);
    assert.equal(request.messages[0].role, "system");
    assert.match(request.messages[0].content, /JSON/);
    assert.equal(request.messages[1].role, "user");
    assert.deepEqual(JSON.parse(request.messages[1].content), {
      state: payload.state,
      questions: payload.questions,
    });
    assert.deepEqual(request.response_format, { type: "json_object" });
    assert.equal(request.stream, false);
    return Response.json({
      model: "deepseek-flash",
      choices: [
        {
          finish_reason: "stop",
          message: {
            role: "assistant",
            content: JSON.stringify({ answers }),
          },
        },
      ],
      usage: { prompt_tokens: 123, completion_tokens: 45 },
    });
  };
  const result = await evaluate(requestPayload, undefined, config, mock);
  assert.deepEqual(result, {
    model: "deepseek-flash",
    answers,
    usage: { input_tokens: 123, output_tokens: 45 },
  });
  assert.equal(calls, 1);
});

test("DeepSeek 返回空内容时拒绝生成不完整评分", async () => {
  const config = getProviderConfig({
    JEV_PROVIDER: "deepseek",
    JEV_API_KEY: "deepseek-test-key",
  });
  await assert.rejects(
    evaluate(payload, undefined, config, async () =>
      Response.json({
        model: "deepseek-flash",
        choices: [
          {
            finish_reason: "stop",
            message: { role: "assistant", content: "" },
          },
        ],
        usage: { prompt_tokens: 10, completion_tokens: 0 },
      }),
    ),
    (error: unknown) =>
      error instanceof ProviderError &&
      error.status === 502 &&
      error.message.includes("DeepSeek"),
  );
});

test("DeepSeek JSON 响应带 BOM 或代码围栏时仍能解析", async () => {
  const config = getProviderConfig({
    JEV_PROVIDER: "deepseek",
    DEEPSEEK_API_KEY: "deepseek-test-key",
  });
  const answers = {
    mood: {
      type: "choice",
      choice: "happy",
      confidence: 0.6,
      probabilities: { happy: 0.8, sad: 0.2 },
    },
    quality: {
      type: "score",
      score: 1.8,
      confidence: 0.7,
      probabilities: { "0": 0, "1": 0.2, "2": 0.8 },
    },
    clear: { type: "noul", noul: 0.95 },
  };
  const content = `\uFEFF\`\`\`json\n${JSON.stringify({ answers })}\n\`\`\``;

  const result = await evaluate(
    payload,
    undefined,
    config,
    async () =>
      new Response(
        JSON.stringify({
          model: "deepseek-flash",
          choices: [
            {
              finish_reason: "stop",
              message: { role: "assistant", content },
            },
          ],
          usage: { prompt_tokens: 10, completion_tokens: 10 },
        }),
        { headers: { "content-type": "application/json" } },
      ),
  );

  assert.deepEqual(result.answers, answers);
});

test("DeepSeek 短暂返回空内容时自动重试并保留最终结果", async () => {
  const config = getProviderConfig({
    JEV_PROVIDER: "deepseek",
    DEEPSEEK_API_KEY: "deepseek-test-key",
  });
  let calls = 0;

  const result = await evaluate(payload, undefined, config, async () => {
    calls++;
    if (calls === 1) {
      return Response.json({
        model: "deepseek-flash",
        choices: [
          {
            finish_reason: "stop",
            message: { role: "assistant", content: "" },
          },
        ],
      });
    }
    return Response.json({
      model: "deepseek-flash",
      choices: [
        {
          finish_reason: "stop",
          message: {
            role: "assistant",
            content: JSON.stringify({ answers: fixture.answers }),
          },
        },
      ],
      usage: { prompt_tokens: 10, completion_tokens: 10 },
    });
  });

  assert.equal(calls, 2);
  assert.deepEqual(result.answers, fixture.answers);
});

test("网关缺少确定度、概率、答案或返回错误分类时拒绝评分", () => {
  const changes = [
    (f: any) => delete f.answers.mood.confidence,
    (f: any) => delete f.answers.mood.probabilities.sad,
    (f: any) => delete f.answers.clear,
    (f: any) => {
      f.answers.clear.type = "boolean";
    },
    (f: any) => {
      f.answers.mood.choice = "unknown";
    },
    (f: any) => {
      f.answers.quality.score = 4;
    },
    (f: any) => {
      f.answers.mood.probabilities.happy = 0;
    },
    (f: any) => {
      f.answers.clear.noul = 1.1;
    },
  ];
  for (const change of changes) {
    const broken = structuredClone(fixture);
    change(broken);
    assert.throws(
      () => validateResult(broken, payload.questions),
      /Incomplete model response/,
    );
  }
});

test("401/402/403 不重试且不透出上游正文或 Key", async () => {
  const config = getProviderConfig({ JEV_API_KEY: "test-only-key" });
  for (const status of [401, 402, 403]) {
    let calls = 0;
    await assert.rejects(
      evaluate(payload, undefined, config, async () => {
        calls++;
        return new Response("private-chat-or-key", { status });
      }),
      (e: unknown) =>
        e instanceof ProviderError &&
        e.status === status &&
        !e.message.includes("private-chat-or-key"),
    );
    assert.equal(calls, 1);
  }
  assert.ok(
    !providerErrorMessage(new Error("private-chat-or-key")).includes(
      "private-chat-or-key",
    ),
  );
});

test("Vercel 真实账号验证错误明确提示绑卡，不误报 Key 错误或回显正文", async () => {
  const config = getProviderConfig({
    JEV_PROVIDER: "vercel",
    JEV_API_KEY: "test-only-key",
  });
  await assert.rejects(
    evaluate(payload, undefined, config, async () =>
      Response.json(
        {
          error: {
            type: "customer_verification_required",
            message: "private-upstream-text",
          },
        },
        { status: 403 },
      ),
    ),
    (error: unknown) =>
      error instanceof ProviderError &&
      error.status === 403 &&
      error.message.includes("绑定有效信用卡") &&
      !error.message.includes("private-upstream-text"),
  );
});

test("短暂限流只重试一次；较长 Retry-After 直接交还调用方", async () => {
  const config = getProviderConfig({ JEV_API_KEY: "test-only-key" });
  let calls = 0;
  await evaluate(payload, undefined, config, async () =>
    ++calls === 1
      ? new Response("", { status: 429, headers: { "Retry-After": "0" } })
      : Response.json(fixture),
  );
  assert.equal(calls, 2);
  calls = 0;
  await assert.rejects(
    evaluate(payload, undefined, config, async () => {
      calls++;
      return new Response("", {
        status: 429,
        headers: { "Retry-After": "60" },
      });
    }),
    (e: unknown) => e instanceof ProviderError && e.status === 429,
  );
  assert.equal(calls, 1);
});

test("取消不会发请求，非 JSON 成功响应不成为分析结果", async () => {
  const config = getProviderConfig({ JEV_API_KEY: "test-only-key" });
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    evaluate(payload, controller.signal, config, async () => {
      assert.fail("must not fetch");
    }),
  );
  await assert.rejects(
    evaluate(
      payload,
      undefined,
      config,
      async () =>
        new Response("<html>error</html>", {
          status: 200,
          headers: { "content-type": "text/html" },
        }),
    ),
    (error: unknown) =>
      error instanceof ProviderError &&
      error.message.includes("Invalid response") &&
      error.message.includes("HTTP 200") &&
      error.message.includes("text/html"),
  );
});

test("持续服务失败最多两次，不切换平台；重试等待可以取消", async () => {
  const config = getProviderConfig({
    JEV_PROVIDER: "openrouter",
    JEV_API_KEY: "test-only-key",
  });
  let calls = 0;
  await assert.rejects(
    evaluate(payload, undefined, config, async (url) => {
      calls++;
      assert.equal(url, PROVIDERS.openrouter.endpoint);
      return new Response("", { status: 503, headers: { "Retry-After": "0" } });
    }),
    (error: unknown) => error instanceof ProviderError && error.status === 503,
  );
  assert.equal(calls, 2);
  const controller = new AbortController();
  calls = 0;
  await assert.rejects(
    evaluate(payload, controller.signal, config, async () => {
      calls++;
      controller.abort();
      return new Response("", { status: 429, headers: { "Retry-After": "1" } });
    }),
  );
  assert.equal(calls, 1);
});

test("配置更新保留端口、注释和其他变量；重复运行不会重复键", async () => {
  const previous =
    "# My config\r\nPORT=4321\r\nHOST=127.0.0.1\r\nTYPESAFE_API_KEY=legacy-key\r\nEXTRA=value\r\nexport JEV_PROVIDER=typesafe\r\nJEV_API_KEY=old-key\r\n";
  const text = updateConfiguration(previous, "openrouter", "sk-or-test-key");
  assert.equal(updateConfiguration(text, "openrouter", "sk-or-test-key"), text);
  assert.equal((text.match(/^JEV_API_KEY=/gm) || []).length, 1);
  assert.ok(text.includes("# My config"));
  assert.equal(parse(text).PORT, "4321");
  assert.equal(parse(text).EXTRA, "value");
  assert.equal(getProviderConfig(parse(text)).provider, "openrouter");
  assert.equal(getProviderConfig(parse(text)).apiKey, "sk-or-test-key");
  const folder = await mkdtemp(join(tmpdir(), "crush-setup-"));
  try {
    const file = join(folder, ".env");
    await saveConfiguration(file, text);
    await saveConfiguration(file, text);
    assert.equal(await readFile(file, "utf8"), text);
    if (process.platform !== "win32")
      assert.equal((await stat(file)).mode & 0o777, 0o600);
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
});

test("四个平台贯通总览、对方情绪意图、我方评级，使用同一评分规则", async (t) => {
  const originalFetch = globalThis.fetch;
  const previous = {
    provider: process.env.JEV_PROVIDER,
    key: process.env.JEV_API_KEY,
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
    if (previous.provider === undefined) delete process.env.JEV_PROVIDER;
    else process.env.JEV_PROVIDER = previous.provider;
    if (previous.key === undefined) delete process.env.JEV_API_KEY;
    else process.env.JEV_API_KEY = previous.key;
  });
  globalThis.fetch = async (_url, options) => {
    const request = JSON.parse(String(options?.body)) as {
      model: string;
      questions: Record<string, { type: string; criteria: unknown }>;
      messages?: { role: string; content: string }[];
    };
    const source =
      process.env.JEV_PROVIDER === "deepseek"
        ? (JSON.parse(request.messages?.[1]?.content ?? "{}") as {
            questions: Record<string, { type: string; criteria: unknown }>;
          })
        : request;
    const answers = Object.fromEntries(
      Object.entries(source.questions).map(([id, value]) => {
        const q = value;
        if (q.type === "noul") return [id, { type: "noul", noul: 0.1 }];
        const keys = Object.keys(q.criteria as object);
        const probabilities = Object.fromEntries(
          keys.map((key, i) => [key, i === 0 ? 1 : 0]),
        );
        return [
          id,
          {
            type: q.type,
            confidence: 1,
            probabilities,
            ...(q.type === "choice" ? { choice: keys[0] } : { score: 0 }),
          },
        ];
      }),
    );
    return process.env.JEV_PROVIDER === "deepseek"
      ? Response.json({
          model: request.model,
          choices: [
            {
              finish_reason: "stop",
              message: {
                role: "assistant",
                content: JSON.stringify({ answers }),
              },
            },
          ],
          usage: {
            prompt_tokens: fixture.usage.input_tokens,
            completion_tokens: fixture.usage.output_tokens,
          },
        })
      : Response.json({
          model: request.model,
          answers,
          usage: fixture.usage,
        });
  };
  for (const task of ["overview", "other_messages", "self_message"] as const) {
    const request: AnalysisRequest = {
      revision: 1,
      relation: "crush",
      task,
      targetIds:
        task === "overview" ? [] : [task === "other_messages" ? "m1" : "m2"],
      messages: [
        {
          id: "m1",
          sender: "other",
          text: "今天怎么样？",
          timestamp: null,
          kind: "text",
        },
        {
          id: "m2",
          sender: "self",
          text: "挺好的，你呢？",
          timestamp: null,
          kind: "text",
        },
      ],
    };
    assert.ok(Object.keys(buildRequest(request).questions).length > 0);
    const results = [];
    for (const provider of [
      "typesafe",
      "vercel",
      "openrouter",
      "deepseek",
    ]) {
      process.env.JEV_PROVIDER = provider;
      process.env.JEV_API_KEY = "test-only-key";
      const { model, latencyMs, ...result } = await analyze(request);
      assert.ok(model);
      results.push(result);
    }
    assert.deepEqual(results[0], results[1]);
    assert.deepEqual(results[0], results[2]);
  }
});
