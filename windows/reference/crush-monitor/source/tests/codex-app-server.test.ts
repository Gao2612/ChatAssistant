import { EventEmitter } from "node:events";
import { PassThrough, Writable } from "node:stream";
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CodexAppServerClient,
  CodexAppServerError,
  buildCodexLaunchSpec,
  selectCodexExecutable,
  type CodexProcess,
} from "../server/codex-app-server";
import {
  CODEX_BASE_TURN_TIMEOUT_MS,
  CODEX_MAX_TURN_TIMEOUT_MS,
  turnTimeoutForPrompt,
} from "../server/codex-timeout";

type JsonRecord = Record<string, unknown>;
type ClientMessage = {
  id?: number;
  method: string;
  params?: JsonRecord;
};

type MessageHandler = (
  message: ClientMessage,
  process: FakeCodexProcess,
) => void;

class FakeCodexProcess extends EventEmitter implements CodexProcess {
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
  readonly stdin: Writable;
  readonly messages: ClientMessage[] = [];
  killCount = 0;
  private input = "";

  constructor(private readonly handler: MessageHandler) {
    super();
    this.stdin = new Writable({
      write: (chunk, _encoding, done) => {
        this.input += chunk.toString();
        const lines = this.input.split("\n");
        this.input = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.trim()) continue;
          const message = JSON.parse(line) as ClientMessage;
          this.messages.push(message);
          this.handler(message, this);
        }
        done();
      },
    });
  }

  respond(id: number | undefined, result: unknown): void {
    if (id === undefined) throw new Error("响应缺少请求 ID");
    this.stdout.write(`${JSON.stringify({ id, result })}\n`);
  }

  reject(id: number | undefined, code: number, message: string): void {
    if (id === undefined) throw new Error("响应缺少请求 ID");
    this.stdout.write(`${JSON.stringify({ id, error: { code, message } })}\n`);
  }

  notify(method: string, params: JsonRecord): void {
    this.stdout.write(`${JSON.stringify({ method, params })}\n`);
  }

  exit(code: number | null): void {
    this.emit("exit", code);
  }

  kill(_signal?: NodeJS.Signals): boolean {
    this.killCount++;
    this.emit("exit", 0);
    return true;
  }
}

function initializeHandler(
  message: ClientMessage,
  process: FakeCodexProcess,
): boolean {
  if (message.method !== "initialize") return false;
  process.respond(message.id, {
    userAgent: "codex-test",
    codexHome: "C:/hidden",
    platformFamily: "windows",
    platformOs: "windows",
  });
  return true;
}

function accountHandler(
  message: ClientMessage,
  process: FakeCodexProcess,
): boolean {
  if (message.method === "account/read") {
    process.respond(message.id, {
      account: {
        type: "chatgpt",
        email: "must-not-leak@example.com",
        planType: "plus",
      },
      requiresOpenaiAuth: true,
    });
    return true;
  }
  if (message.method === "account/rateLimits/read") {
    process.respond(message.id, {
      rateLimits: {
        limitId: "codex",
        primary: {
          usedPercent: 12,
          windowDurationMins: 300,
          resetsAt: 1_800_000_000,
        },
        secondary: {
          usedPercent: 34,
          windowDurationMins: 10_080,
          resetsAt: 1_800_600_000,
        },
        planType: "plus",
      },
      rateLimitsByLimitId: null,
      rateLimitResetCredits: null,
    });
    return true;
  }
  return false;
}

function successfulHandler(
  message: ClientMessage,
  process: FakeCodexProcess,
): void {
  if (initializeHandler(message, process) || accountHandler(message, process))
    return;
  if (message.method === "model/list") {
    process.respond(message.id, {
      data: [
        {
          id: "gpt-6-sol",
          displayName: "GPT-6 Sol",
          description: "workhorse",
          hidden: false,
          isDefault: true,
        },
      ],
    });
    return;
  }
  if (message.method === "thread/start") {
    process.respond(message.id, {
      thread: { id: "thread-1" },
      model: "default",
      modelProvider: "openai",
      cwd: "C:/isolated",
    });
    return;
  }
  if (message.method === "turn/start") {
    process.respond(message.id, {
      turn: { id: "turn-1", status: "inProgress", items: [] },
    });
    queueMicrotask(() => {
      process.notify("unrelated/notification", { value: true });
      process.notify("item/completed", {
        threadId: "thread-1",
        turnId: "turn-1",
        item: {
          type: "agentMessage",
          id: "item-1",
          text: '{"answers":{}}',
          phase: "final_answer",
        },
      });
      process.notify("turn/completed", {
        threadId: "thread-1",
        turn: {
          id: "turn-1",
          status: "completed",
          items: [],
          error: null,
        },
      });
    });
    return;
  }
  if (message.method === "thread/archive") {
    process.respond(message.id, {});
    return;
  }
  if (message.method === "turn/interrupt") {
    process.respond(message.id, {});
  }
}

function signal(): AbortSignal {
  return new AbortController().signal;
}

test("长 prompt 的 turn 预算有限增长且有固定上限", () => {
  assert.equal(
    turnTimeoutForPrompt(CODEX_BASE_TURN_TIMEOUT_MS, 0),
    CODEX_BASE_TURN_TIMEOUT_MS,
  );
  assert.equal(
    turnTimeoutForPrompt(CODEX_BASE_TURN_TIMEOUT_MS, 1_000_000),
    CODEX_MAX_TURN_TIMEOUT_MS,
  );
});

async function settle(): Promise<void> {
  await new Promise<void>((resolve) => setImmediate(resolve));
}

test("状态握手忽略 stderr 并只返回 ChatGPT Plus 白名单字段", async () => {
  const process = new FakeCodexProcess((message, current) => {
    if (initializeHandler(message, current) || accountHandler(message, current))
      return;
  });
  process.stderr.write("non-fatal model refresh warning\n");
  const client = new CodexAppServerClient(
    () => process,
    "C:/isolated",
    100,
    100,
  );

  const status = await client.status(signal());

  assert.deepEqual(status, {
    available: true,
    loggedIn: true,
    authMode: "chatgpt",
    planType: "plus",
    rateLimits: [
      {
        usedPercent: 12,
        windowDurationMins: 300,
        resetsAt: 1_800_000_000,
      },
      {
        usedPercent: 34,
        windowDurationMins: 10_080,
        resetsAt: 1_800_600_000,
      },
    ],
  });
  assert.equal(JSON.stringify(status).includes("must-not-leak"), false);
  assert.deepEqual(
    process.messages.map((message) => message.method),
    [
      "initialize",
      "initialized",
      "account/read",
      "account/rateLimits/read",
    ],
  );
  await client.close();
});

test("非 ChatGPT 登录不能用于 Plus 分析", async () => {
  const process = new FakeCodexProcess((message, current) => {
    if (initializeHandler(message, current)) return;
    if (message.method === "account/read") {
      current.respond(message.id, {
        account: { type: "apiKey" },
        requiresOpenaiAuth: true,
      });
    }
  });
  const client = new CodexAppServerClient(
    () => process,
    "C:/isolated",
    100,
    100,
  );

  await assert.rejects(
    client.status(signal()),
    (error: unknown) =>
      error instanceof CodexAppServerError && error.code === "not-chatgpt",
  );
  await client.close();
});

test("分析使用隔离只读参数并归档临时 thread", async () => {
  const process = new FakeCodexProcess(successfulHandler);
  const client = new CodexAppServerClient(
    () => process,
    "C:/isolated",
    100,
    100,
  );

  const output = await client.evaluate("untrusted chat", signal());

  assert.equal(output, '{"answers":{}}');
  const initialize = process.messages.find(
    (message) => message.method === "initialize",
  );
  assert.deepEqual(initialize?.params?.capabilities, {
    experimentalApi: true,
    requestAttestation: false,
  });
  const thread = process.messages.find(
    (message) => message.method === "thread/start",
  );
  assert.deepEqual(thread?.params, {
    cwd: "C:/isolated",
    approvalPolicy: "never",
    sandbox: "read-only",
    ephemeral: true,
    environments: [],
    dynamicTools: [],
  });
  const turn = process.messages.find(
    (message) => message.method === "turn/start",
  );
  assert.deepEqual(turn?.params, {
    threadId: "thread-1",
    input: [
      { type: "text", text: "untrusted chat", text_elements: [] },
    ],
    cwd: "C:/isolated",
    approvalPolicy: "never",
    environments: [],
    effort: "low",
    sandboxPolicy: { type: "readOnly", networkAccess: false },
  });
  assert.equal(
    process.messages.at(-1)?.method,
    "thread/archive",
  );
  await client.close();
});

test("Codex 模型目录可读取，指定模型会传入新 thread", async () => {
  const process = new FakeCodexProcess(successfulHandler);
  const client = new CodexAppServerClient(
    () => process,
    "C:/isolated",
    100,
    100,
  );

  const models = await (
    client as unknown as {
      models(signal: AbortSignal): Promise<unknown>;
    }
  ).models(signal());
  await client.evaluate("untrusted chat", signal(), "gpt-6-astra");

  assert.deepEqual(models, [
    {
      id: "gpt-6-sol",
      displayName: "GPT-6 Sol",
      description: "workhorse",
      isDefault: true,
    },
  ]);
  const thread = process.messages.find(
    (message) => message.method === "thread/start",
  );
  assert.equal(thread?.params?.model, "gpt-6-astra");
  await client.close();
});

test("并发分析严格串行，不同时创建两个 thread", async () => {
  let turnCount = 0;
  let releaseFirst = (): void => {
    throw new Error("first turn was not started");
  };
  const process = new FakeCodexProcess((message, current) => {
    if (initializeHandler(message, current)) return;
    if (message.method === "thread/start") {
      const index = turnCount + 1;
      current.respond(message.id, { thread: { id: `thread-${index}` } });
      return;
    }
    if (message.method === "turn/start") {
      turnCount++;
      const index = turnCount;
      current.respond(message.id, { turn: { id: `turn-${index}` } });
      const complete = () => {
        current.notify("item/completed", {
          threadId: `thread-${index}`,
          turnId: `turn-${index}`,
          item: {
            type: "agentMessage",
            id: `item-${index}`,
            text: `result-${index}`,
            phase: "final_answer",
          },
        });
        current.notify("turn/completed", {
          threadId: `thread-${index}`,
          turn: { id: `turn-${index}`, status: "completed", error: null },
        });
      };
      if (index === 1) releaseFirst = complete;
      else queueMicrotask(complete);
      return;
    }
    if (message.method === "thread/archive") current.respond(message.id, {});
  });
  const client = new CodexAppServerClient(
    () => process,
    "C:/isolated",
    100,
    200,
  );

  const first = client.evaluate("one", signal());
  const second = client.evaluate("two", signal());
  await settle();
  assert.equal(
    process.messages.filter((message) => message.method === "thread/start")
      .length,
    1,
  );
  releaseFirst();
  assert.equal(await first, "result-1");
  assert.equal(await second, "result-2");
  await client.close();
});

test("工具 item 立即中断并且失败后仍归档 thread", async () => {
  const process = new FakeCodexProcess((message, current) => {
    if (initializeHandler(message, current)) return;
    if (message.method === "thread/start") {
      current.respond(message.id, { thread: { id: "thread-risk" } });
      return;
    }
    if (message.method === "turn/start") {
      current.respond(message.id, { turn: { id: "turn-risk" } });
      queueMicrotask(() => {
        current.notify("item/started", {
          threadId: "thread-risk",
          turnId: "turn-risk",
          item: { type: "commandExecution", id: "danger" },
        });
      });
      return;
    }
    if (
      message.method === "turn/interrupt" ||
      message.method === "thread/archive"
    )
      current.respond(message.id, {});
  });
  const client = new CodexAppServerClient(
    () => process,
    "C:/isolated",
    100,
    100,
  );

  await assert.rejects(
    client.evaluate("attempt tool use", signal()),
    (error: unknown) =>
      error instanceof CodexAppServerError && error.code === "unsafe-tool",
  );
  assert.ok(
    process.messages.some(
      (message) => message.method === "turn/interrupt",
    ),
  );
  assert.ok(
    process.messages.some(
      (message) => message.method === "thread/archive",
    ),
  );
  await client.close();
});

test("协议中的 Plus 额度耗尽会保留为独立错误分类", async () => {
  const process = new FakeCodexProcess((message, current) => {
    if (initializeHandler(message, current)) return;
    if (message.method === "thread/start") {
      current.respond(message.id, { thread: { id: "thread-limit" } });
      return;
    }
    if (message.method === "turn/start") {
      current.respond(message.id, {
        turn: { id: "turn-limit", status: "inProgress", items: [] },
      });
      queueMicrotask(() => {
        current.notify("error", {
          threadId: "thread-limit",
          turnId: "turn-limit",
          error: {
            message: "private upstream text",
            codexErrorInfo: "usageLimitExceeded",
            additionalDetails: null,
          },
          willRetry: false,
        });
      });
      return;
    }
    if (
      message.method === "turn/interrupt" ||
      message.method === "thread/archive"
    )
      current.respond(message.id, {});
  });
  const client = new CodexAppServerClient(
    () => process,
    "C:/isolated",
    100,
    100,
  );

  await assert.rejects(
    client.evaluate("test limit", signal()),
    (error: unknown) =>
      error instanceof CodexAppServerError && error.code === "usage-limit",
  );
  await client.close();
});

test("Codex 会自动重试的错误通知不会提前结束分析", async () => {
  const process = new FakeCodexProcess((message, current) => {
    if (initializeHandler(message, current)) return;
    if (message.method === "thread/start") {
      current.respond(message.id, { thread: { id: "thread-retry" } });
      return;
    }
    if (message.method === "turn/start") {
      current.respond(message.id, { turn: { id: "turn-retry" } });
      queueMicrotask(() => {
        current.notify("error", {
          threadId: "thread-retry",
          turnId: "turn-retry",
          error: {
            message: "temporary upstream failure",
            codexErrorInfo: "serverOverloaded",
          },
          willRetry: true,
        });
        current.notify("item/completed", {
          threadId: "thread-retry",
          turnId: "turn-retry",
          item: {
            type: "agentMessage",
            id: "item-retry",
            text: '{"answers":{}}',
            phase: "final_answer",
          },
        });
        current.notify("turn/completed", {
          threadId: "thread-retry",
          turn: { id: "turn-retry", status: "completed", error: null },
        });
      });
      return;
    }
    if (message.method === "thread/archive") current.respond(message.id, {});
  });
  const client = new CodexAppServerClient(
    () => process,
    "C:/isolated",
    100,
    100,
  );

  assert.equal(await client.evaluate("retryable error", signal()), '{"answers":{}}');
  await client.close();
});

test("willRetry 后仍未完成时保留 codexErrorInfo 的可操作分类", async () => {
  const process = new FakeCodexProcess((message, current) => {
    if (initializeHandler(message, current)) return;
    if (message.method === "thread/start") {
      current.respond(message.id, { thread: { id: "thread-overload" } });
      return;
    }
    if (message.method === "turn/start") {
      current.respond(message.id, { turn: { id: "turn-overload" } });
      queueMicrotask(() => {
        current.notify("error", {
          threadId: "thread-overload",
          turnId: "turn-overload",
          error: {
            message: "private overload detail",
            codexErrorInfo: "serverOverloaded",
          },
          willRetry: true,
        });
      });
      return;
    }
    if (
      message.method === "turn/interrupt" ||
      message.method === "thread/archive"
    )
      current.respond(message.id, {});
  });
  const client = new CodexAppServerClient(
    () => process,
    "C:/isolated",
    100,
    20,
  );

  await assert.rejects(
    client.evaluate("retryable overload", signal()),
    (error: unknown) =>
      error instanceof CodexAppServerError &&
      error.code === "server-overloaded",
  );
  await client.close();
});

test("上下文窗口错误保留为独立错误分类", async () => {
  const process = new FakeCodexProcess((message, current) => {
    if (initializeHandler(message, current)) return;
    if (message.method === "thread/start") {
      current.respond(message.id, { thread: { id: "thread-context" } });
      return;
    }
    if (message.method === "turn/start") {
      current.respond(message.id, { turn: { id: "turn-context" } });
      queueMicrotask(() => {
        current.notify("turn/completed", {
          threadId: "thread-context",
          turn: {
            id: "turn-context",
            status: "failed",
            error: {
              message: "private context detail",
              codexErrorInfo: "contextWindowExceeded",
            },
          },
        });
      });
      return;
    }
    if (message.method === "thread/archive") current.respond(message.id, {});
  });
  const client = new CodexAppServerClient(
    () => process,
    "C:/isolated",
    100,
    100,
  );

  await assert.rejects(
    client.evaluate("oversized context", signal()),
    (error: unknown) =>
      error instanceof CodexAppServerError && error.code === "context-window",
  );
  await client.close();
});

test("外部取消会中断 turn，超时错误与取消错误分开", async () => {
  const hangingHandler: MessageHandler = (message, current) => {
    if (initializeHandler(message, current)) return;
    if (message.method === "thread/start") {
      current.respond(message.id, { thread: { id: "thread-hang" } });
      return;
    }
    if (message.method === "turn/start") {
      current.respond(message.id, { turn: { id: "turn-hang" } });
      return;
    }
    if (
      message.method === "turn/interrupt" ||
      message.method === "thread/archive"
    )
      current.respond(message.id, {});
  };
  const abortProcess = new FakeCodexProcess(hangingHandler);
  const abortClient = new CodexAppServerClient(
    () => abortProcess,
    "C:/isolated",
    100,
    100,
  );
  const controller = new AbortController();
  const aborted = abortClient.evaluate("cancel", controller.signal);
  await settle();
  controller.abort();
  await assert.rejects(
    aborted,
    (error: unknown) =>
      error instanceof CodexAppServerError && error.code === "aborted",
  );
  await abortClient.close();

  const timeoutProcess = new FakeCodexProcess(hangingHandler);
  const timeoutClient = new CodexAppServerClient(
    () => timeoutProcess,
    "C:/isolated",
    100,
    10,
  );
  await assert.rejects(
    timeoutClient.evaluate("timeout", signal()),
    (error: unknown) =>
      error instanceof CodexAppServerError && error.code === "turn-timeout",
  );
  await timeoutClient.close();
});

test("握手超时和进程退出可区分，下一次调用会启动新进程", async () => {
  const handshakeClient = new CodexAppServerClient(
    () => new FakeCodexProcess(() => {}),
    "C:/isolated",
    10,
    100,
  );
  await assert.rejects(
    handshakeClient.status(signal()),
    (error: unknown) =>
      error instanceof CodexAppServerError &&
      error.code === "handshake-timeout",
  );
  await handshakeClient.close();

  const first = new FakeCodexProcess((message, current) => {
    if (initializeHandler(message, current)) return;
    if (message.method === "account/read") current.exit(9);
  });
  const second = new FakeCodexProcess((message, current) => {
    if (initializeHandler(message, current) || accountHandler(message, current))
      return;
  });
  const processes = [first, second];
  const restartClient = new CodexAppServerClient(
    () => {
      const process = processes.shift();
      if (!process) throw new Error("unexpected third launch");
      return process;
    },
    "C:/isolated",
    100,
    100,
  );
  await assert.rejects(
    restartClient.status(signal()),
    (error: unknown) =>
      error instanceof CodexAppServerError && error.code === "process-exit",
  );
  assert.equal((await restartClient.status(signal())).planType, "plus");
  await restartClient.close();
});

test("请求超时会重启失去响应的进程，后续调用不复用卡住的进程", async () => {
  const first = new FakeCodexProcess((message, current) => {
    if (initializeHandler(message, current)) return;
    if (message.method === "thread/start") return;
  });
  const second = new FakeCodexProcess(successfulHandler);
  const processes = [first, second];
  const client = new CodexAppServerClient(
    () => {
      const process = processes.shift();
      if (!process) throw new Error("unexpected third launch");
      return process;
    },
    "C:/isolated",
    10,
    100,
  );

  await assert.rejects(
    client.evaluate("stuck request", signal()),
    (error: unknown) =>
      error instanceof CodexAppServerError && error.code === "request-timeout",
  );
  assert.equal(first.killCount, 1);
  assert.equal(
    await client.evaluate("retry after restart", signal()),
    '{"answers":{}}',
  );

  await client.close();
});

test("close 中断当前任务并拒绝排队任务", async () => {
  const process = new FakeCodexProcess((message, current) => {
    if (initializeHandler(message, current)) return;
    if (message.method === "thread/start")
      current.respond(message.id, { thread: { id: "thread-close" } });
    if (message.method === "turn/start")
      current.respond(message.id, { turn: { id: "turn-close" } });
  });
  const client = new CodexAppServerClient(
    () => process,
    "C:/isolated",
    100,
    10_000,
  );
  const running = client.evaluate("running", signal());
  const queued = client.evaluate("queued", signal());
  await settle();

  await client.close();

  await assert.rejects(running, CodexAppServerError);
  await assert.rejects(queued, CodexAppServerError);
  assert.equal(process.killCount, 1);
});

test("Windows 启动规格只接受安全绝对 codex 路径", () => {
  assert.equal(
    selectCodexExecutable(
      "C:\\Users\\EDY\\.hermes\\node\\codex\r\n" +
        "C:\\Users\\EDY\\.hermes\\node\\codex.cmd\r\n" +
        "C:\\Tools\\codex.exe\r\n",
    ),
    "C:\\Users\\EDY\\.hermes\\node\\codex.cmd",
  );
  assert.equal(
    selectCodexExecutable(
      "C:\\Tools With Spaces\\codex.cmd\r\nC:\\Other\\codex.exe\r\n",
    ),
    "C:\\Tools With Spaces\\codex.cmd",
  );
  assert.deepEqual(
    buildCodexLaunchSpec(
      "C:\\Tools With Spaces\\codex.cmd",
      "C:\\Windows\\System32\\cmd.exe",
    ),
    {
      command: "C:\\Windows\\System32\\cmd.exe",
      args: [
        "/d",
        "/s",
        "/c",
        '"C:\\Tools With Spaces\\codex.cmd" app-server',
      ],
      windowsVerbatimArguments: true,
    },
  );
  assert.deepEqual(
    buildCodexLaunchSpec(
      "C:\\Tools\\codex.exe",
      "C:\\Windows\\System32\\cmd.exe",
    ),
    {
      command: "C:\\Tools\\codex.exe",
      args: ["app-server"],
      windowsVerbatimArguments: false,
    },
  );
  assert.throws(() => selectCodexExecutable("codex.cmd\r\n"), /绝对路径/);
  assert.throws(
    () =>
      buildCodexLaunchSpec(
        'C:\\Tools\\bad"name.cmd',
        "C:\\Windows\\System32\\cmd.exe",
      ),
    /路径/,
  );
});
