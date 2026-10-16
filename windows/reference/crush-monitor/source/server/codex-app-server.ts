import {
  execFileSync,
  spawn,
  type ChildProcessWithoutNullStreams,
} from "node:child_process";
import { createInterface, type Interface as ReadLineInterface } from "node:readline";
import { win32 } from "node:path";
import { turnTimeoutForPrompt } from "./codex-timeout";

type JsonRecord = Record<string, unknown>;

export type CodexRateLimitWindow = {
  usedPercent: number | null;
  resetsAt: number | null;
  windowDurationMins: number | null;
};

export type CodexAccountStatus = {
  available: boolean;
  loggedIn: boolean;
  authMode: "chatgpt" | null;
  planType: string | null;
  rateLimits: readonly CodexRateLimitWindow[];
};

export type CodexModel = {
  id: string;
  displayName: string;
  description: string;
  isDefault: boolean;
};

export type CodexProcess = {
  stdin: NodeJS.WritableStream;
  stdout: NodeJS.ReadableStream;
  stderr: NodeJS.ReadableStream;
  once(
    event: "exit",
    listener: (code: number | null) => void,
  ): unknown;
  once(event: "error", listener: (error: Error) => void): unknown;
  kill(signal?: NodeJS.Signals): boolean;
};

export type CodexProcessFactory = () => CodexProcess;

export type CodexAppServerErrorCode =
  | "unavailable"
  | "handshake-timeout"
  | "request-timeout"
  | "process-exit"
  | "protocol"
  | "request-failed"
  | "not-chatgpt"
  | "not-logged-in"
  | "context-window"
  | "session-budget"
  | "server-overloaded"
  | "auth-failed"
  | "bad-request"
  | "sandbox-failed"
  | "cyber-policy"
  | "network-failed"
  | "turn-timeout"
  | "turn-failed"
  | "usage-limit"
  | "unsafe-tool"
  | "aborted"
  | "closed";

export class CodexAppServerError extends Error {
  constructor(
    readonly code: CodexAppServerErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "CodexAppServerError";
  }
}

export type CodexLaunchSpec = {
  command: string;
  args: readonly string[];
  windowsVerbatimArguments: boolean;
};

type PendingRequest = {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
  removeAbort: () => void;
};

type ActiveTurn = {
  threadId: string;
  turnId: string;
  finalText: string | null;
  retryableError: CodexAppServerError | null;
  interrupted: boolean;
  resolve: (value: string) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
  removeAbort: () => void;
};

const unsafeItemTypes = new Set([
  "commandExecution",
  "fileChange",
  "mcpToolCall",
  "dynamicToolCall",
  "collabAgentToolCall",
  "subAgentActivity",
  "webSearch",
  "imageView",
  "imageGeneration",
]);

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function textField(value: unknown, key: string): string | null {
  if (!isRecord(value)) return null;
  const field = value[key];
  return typeof field === "string" && field ? field : null;
}

function numberField(value: unknown, key: string): number | null {
  if (!isRecord(value)) return null;
  const field = value[key];
  return typeof field === "number" && Number.isFinite(field) ? field : null;
}

const codexErrorKinds = new Set([
  "contextWindowExceeded",
  "sessionBudgetExceeded",
  "usageLimitExceeded",
  "serverOverloaded",
  "cyberPolicy",
  "internalServerError",
  "unauthorized",
  "badRequest",
  "threadRollbackFailed",
  "sandboxError",
  "httpConnectionFailed",
  "responseStreamConnectionFailed",
  "responseStreamDisconnected",
  "responseTooManyFailedAttempts",
]);

function codexErrorKind(value: unknown, depth = 0): string | null {
  if (depth > 5) return null;
  if (typeof value === "string" && codexErrorKinds.has(value)) return value;
  if (Array.isArray(value)) {
    for (const entry of value) {
      const kind = codexErrorKind(entry, depth + 1);
      if (kind) return kind;
    }
    return null;
  }
  if (!isRecord(value)) return null;
  const direct = codexErrorKind(value.codexErrorInfo, depth + 1);
  if (direct) return direct;
  for (const key of codexErrorKinds) {
    if (Object.hasOwn(value, key)) return key;
  }
  for (const entry of Object.values(value)) {
    const kind = codexErrorKind(entry, depth + 1);
    if (kind) return kind;
  }
  return null;
}

function isUsageLimitError(value: unknown): boolean {
  const kind = codexErrorKind(value);
  return kind === "usageLimitExceeded" || kind === "sessionBudgetExceeded";
}

function codexError(
  code: CodexAppServerErrorCode,
  message: string,
): CodexAppServerError {
  return new CodexAppServerError(code, message);
}

function turnFailureError(value: unknown): CodexAppServerError {
  const kind = codexErrorKind(value);
  if (kind === "usageLimitExceeded")
    return codexError(
      "usage-limit",
      "ChatGPT Plus 当前额度已用尽。 / Usage limit reached.",
    );
  if (kind === "sessionBudgetExceeded")
    return codexError(
      "session-budget",
      "本次 Codex 会话额度已用尽。 / Codex session budget reached.",
    );
  if (kind === "contextWindowExceeded")
    return codexError(
      "context-window",
      "Codex 上下文窗口已超限。 / Codex context window exceeded.",
    );
  if (kind === "serverOverloaded" || kind === "internalServerError")
    return codexError(
      "server-overloaded",
      "Codex 服务暂时繁忙。 / Codex service is temporarily overloaded.",
    );
  if (kind === "unauthorized")
    return codexError(
      "auth-failed",
      "Codex 登录已失效。 / Codex authentication is no longer valid.",
    );
  if (kind === "badRequest")
    return codexError(
      "bad-request",
      "Codex 拒绝了本批请求。 / Codex rejected the request.",
    );
  if (kind === "sandboxError")
    return codexError(
      "sandbox-failed",
      "Codex 本地沙箱初始化失败。 / Codex sandbox initialization failed.",
    );
  if (kind === "cyberPolicy")
    return codexError(
      "cyber-policy",
      "Codex 安全策略拒绝了本批请求。 / Codex policy rejected the request.",
    );
  if (
    kind === "httpConnectionFailed" ||
    kind === "responseStreamConnectionFailed" ||
    kind === "responseStreamDisconnected" ||
    kind === "responseTooManyFailedAttempts"
  )
    return codexError(
      "network-failed",
      "Codex 连接中断，请稍后重试。 / Codex connection interrupted.",
    );
  return codexError("turn-failed", "Codex 返回分析错误，请重试。");
}

function abortedError(): CodexAppServerError {
  return codexError("aborted", "Codex 分析已取消。 / Codex analysis aborted.");
}

function closedError(): CodexAppServerError {
  return codexError("closed", "Codex 本机连接已关闭。 / Codex connection closed.");
}

function validateExecutablePath(value: string): string {
  const path = value.trim();
  if (!path || !win32.isAbsolute(path))
    throw new Error("Codex 可执行文件必须是绝对路径。");
  if (/[\x00-\x1f"|<>]/.test(path))
    throw new Error("Codex 可执行文件路径包含不安全字符。");
  const extension = win32.extname(path).toLowerCase();
  if (extension !== ".cmd" && extension !== ".exe")
    throw new Error("Codex 可执行文件路径扩展名不受支持。");
  return path;
}

export function selectCodexExecutable(whereOutput: string): string {
  const candidates = whereOutput
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  if (!candidates.length)
    throw new Error("未找到 Codex CLI，请先安装并登录 Codex。");
  let firstError: Error | null = null;
  for (const candidate of candidates) {
    try {
      return validateExecutablePath(candidate);
    } catch (error: unknown) {
      if (!firstError && error instanceof Error) firstError = error;
    }
  }
  throw firstError ?? new Error("未找到可启动的 Codex CLI。");
}

export function buildCodexLaunchSpec(
  executablePath: string,
  commandShell: string,
): CodexLaunchSpec {
  const executable = validateExecutablePath(executablePath);
  if (win32.extname(executable).toLowerCase() === ".exe")
    return {
      command: executable,
      args: ["app-server"],
      windowsVerbatimArguments: false,
    };
  const shell = validateExecutablePath(commandShell);
  return {
    command: shell,
    args: ["/d", "/s", "/c", `"${executable}" app-server`],
    windowsVerbatimArguments: true,
  };
}

function defaultCodexLocator(): string {
  const output = execFileSync("where.exe", ["codex"], {
    encoding: "utf8",
    windowsHide: true,
  });
  return selectCodexExecutable(output);
}

export function createCodexProcessFactory(
  locateExecutable: () => string,
  commandShell: string,
): CodexProcessFactory {
  return (): ChildProcessWithoutNullStreams => {
    const launch = buildCodexLaunchSpec(locateExecutable(), commandShell);
    return spawn(launch.command, [...launch.args], {
      windowsHide: true,
      windowsVerbatimArguments: launch.windowsVerbatimArguments,
      stdio: "pipe",
    });
  };
}

export function createDefaultCodexProcessFactory(): CodexProcessFactory {
  const commandShell =
    process.env.ComSpec ||
    win32.join(process.env.SystemRoot || "C:\\Windows", "System32", "cmd.exe");
  return createCodexProcessFactory(defaultCodexLocator, commandShell);
}

function responseError(value: unknown): CodexAppServerError {
  if (isUsageLimitError(value))
    return codexError(
      "usage-limit",
      "ChatGPT Plus 当前额度已用尽。 / Usage limit reached.",
    );
  const message = textField(value, "message");
  return codexError(
    "request-failed",
    message
      ? `Codex 请求失败：${message}`
      : "Codex 请求失败，未返回可识别错误。 / Codex request failed.",
  );
}

function parseRateLimitWindow(value: unknown): CodexRateLimitWindow | null {
  if (!isRecord(value)) return null;
  return {
    usedPercent: numberField(value, "usedPercent"),
    resetsAt: numberField(value, "resetsAt"),
    windowDurationMins: numberField(value, "windowDurationMins"),
  };
}

function rateLimitSnapshots(value: unknown): readonly JsonRecord[] {
  if (!isRecord(value)) return [];
  const byId = value.rateLimitsByLimitId;
  if (isRecord(byId)) {
    const snapshots = Object.values(byId).filter(isRecord);
    if (snapshots.length) return snapshots;
  }
  return isRecord(value.rateLimits) ? [value.rateLimits] : [];
}

function parseRateLimits(value: unknown): readonly CodexRateLimitWindow[] {
  const windows: CodexRateLimitWindow[] = [];
  for (const snapshot of rateLimitSnapshots(value)) {
    const primary = parseRateLimitWindow(snapshot.primary);
    const secondary = parseRateLimitWindow(snapshot.secondary);
    if (primary) windows.push(primary);
    if (secondary) windows.push(secondary);
  }
  return windows;
}

function parseThreadId(value: unknown): string {
  if (!isRecord(value) || !isRecord(value.thread))
    throw codexError("protocol", "Codex 没有返回 thread 标识。");
  const id = textField(value.thread, "id");
  if (!id) throw codexError("protocol", "Codex 没有返回 thread 标识。");
  return id;
}

function parseTurnId(value: unknown): string {
  if (!isRecord(value) || !isRecord(value.turn))
    throw codexError("protocol", "Codex 没有返回 turn 标识。");
  const id = textField(value.turn, "id");
  if (!id) throw codexError("protocol", "Codex 没有返回 turn 标识。");
  return id;
}

function parseModels(value: unknown): readonly CodexModel[] {
  if (!isRecord(value) || !Array.isArray(value.data))
    throw codexError("protocol", "Codex 没有返回可用模型目录。");
  const models: CodexModel[] = [];
  for (const entry of value.data) {
    if (!isRecord(entry) || entry.hidden === true) continue;
    const id = textField(entry, "id");
    const displayName = textField(entry, "displayName");
    const description = textField(entry, "description");
    if (!id || !displayName || !description) continue;
    models.push({
      id,
      displayName,
      description,
      isDefault: entry.isDefault === true,
    });
  }
  if (!models.length)
    throw codexError("protocol", "Codex 没有返回可选择的模型。");
  return models;
}

export class CodexAppServerClient {
  private process: CodexProcess | null = null;
  private reader: ReadLineInterface | null = null;
  private initialized = false;
  private initializing: Promise<void> | null = null;
  private closed = false;
  private nextRequestId = 1;
  private pending = new Map<number, PendingRequest>();
  private activeTurn: ActiveTurn | null = null;
  private notificationBacklog: JsonRecord[] = [];
  private queue: Promise<void> = Promise.resolve();

  constructor(
    private readonly processFactory: CodexProcessFactory,
    private readonly contextDirectory: string,
    private readonly handshakeTimeoutMs: number,
    private readonly turnTimeoutMs: number,
  ) {}

  status(signal: AbortSignal): Promise<CodexAccountStatus> {
    return this.enqueue(() => this.readStatus(signal));
  }

  models(signal: AbortSignal): Promise<readonly CodexModel[]> {
    return this.enqueue(() => this.readModels(signal));
  }

  evaluate(
    prompt: string,
    signal: AbortSignal,
    model?: string,
  ): Promise<string> {
    return this.enqueue(() => this.runTurn(prompt, signal, model));
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    const error = closedError();
    this.rejectAllPending(error);
    this.rejectActiveTurn(error);
    const process = this.process;
    this.process = null;
    this.initialized = false;
    this.reader?.close();
    this.reader = null;
    if (process) process.kill("SIGTERM");
    await this.queue.catch(() => undefined);
  }

  private enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.queue.then(async () => {
      if (this.closed) throw closedError();
      return operation();
    });
    this.queue = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  private async ensureInitialized(signal: AbortSignal): Promise<void> {
    if (this.closed) throw closedError();
    if (signal.aborted) throw abortedError();
    if (this.initialized && this.process) return;
    if (this.initializing) return this.withAbort(this.initializing, signal);

    this.initializing = this.startProcess(signal);
    try {
      await this.initializing;
    } finally {
      this.initializing = null;
    }
  }

  private async startProcess(signal: AbortSignal): Promise<void> {
    let process: CodexProcess;
    try {
      process = this.processFactory();
    } catch {
      throw codexError(
        "unavailable",
        "未找到或无法启动 Codex CLI，请确认已安装。 / Codex CLI unavailable.",
      );
    }
    this.process = process;
    this.reader = createInterface({ input: process.stdout });
    this.reader.on("line", (line) => this.handleLine(line));
    process.stderr.resume?.();
    process.once("error", () => this.handleProcessExit(null));
    process.once("exit", (code) => this.handleProcessExit(code));

    try {
      await this.request(
        "initialize",
        {
          clientInfo: {
            name: "crush-monitor",
            title: "Crush Monitor",
            version: "1.1.1",
          },
          capabilities: {
            experimentalApi: true,
            requestAttestation: false,
          },
        },
        this.handshakeTimeoutMs,
        "handshake-timeout",
        signal,
      );
      this.notify("initialized", {});
      this.initialized = true;
    } catch (error) {
      if (this.process === process) {
        this.process = null;
        this.reader?.close();
        this.reader = null;
        process.kill("SIGTERM");
      }
      throw error;
    }
  }

  private async readStatus(signal: AbortSignal): Promise<CodexAccountStatus> {
    await this.ensureInitialized(signal);
    const accountResponse = await this.request(
      "account/read",
      { refreshToken: false },
      this.handshakeTimeoutMs,
      "request-timeout",
      signal,
    );
    if (!isRecord(accountResponse) || accountResponse.account === null)
      throw codexError(
        "not-logged-in",
        "Codex 尚未登录，请先完成 ChatGPT 登录。 / Codex is not logged in.",
      );
    if (!isRecord(accountResponse.account))
      throw codexError("protocol", "Codex 返回了无法识别的账户状态。");
    if (accountResponse.account.type !== "chatgpt")
      throw codexError(
        "not-chatgpt",
        "当前 Codex 不是 ChatGPT 登录，不能使用 Plus 套餐额度。",
      );
    const planType = textField(accountResponse.account, "planType");
    const limitResponse = await this.request(
      "account/rateLimits/read",
      undefined,
      this.handshakeTimeoutMs,
      "request-timeout",
      signal,
    );
    return {
      available: true,
      loggedIn: true,
      authMode: "chatgpt",
      planType,
      rateLimits: parseRateLimits(limitResponse),
    };
  }

  private async readModels(
    signal: AbortSignal,
  ): Promise<readonly CodexModel[]> {
    await this.ensureInitialized(signal);
    const response = await this.request(
      "model/list",
      {},
      this.handshakeTimeoutMs,
      "request-timeout",
      signal,
    );
    return parseModels(response);
  }

  private async runTurn(
    prompt: string,
    signal: AbortSignal,
    model?: string,
  ): Promise<string> {
    await this.ensureInitialized(signal);
    let threadId: string | null = null;
    let turnId: string | null = null;
    this.notificationBacklog = [];
    try {
      const threadResponse = await this.request(
        "thread/start",
        {
          cwd: this.contextDirectory,
          approvalPolicy: "never",
          sandbox: "read-only",
          ephemeral: true,
          environments: [],
          dynamicTools: [],
          ...(model ? { model } : {}),
        },
        this.handshakeTimeoutMs,
        "request-timeout",
        signal,
      );
      threadId = parseThreadId(threadResponse);
      const turnResponse = await this.request(
        "turn/start",
        {
          threadId,
          input: [{ type: "text", text: prompt, text_elements: [] }],
          cwd: this.contextDirectory,
          approvalPolicy: "never",
          environments: [],
          effort: "low",
          sandboxPolicy: { type: "readOnly", networkAccess: false },
        },
        this.handshakeTimeoutMs,
        "request-timeout",
        signal,
      );
      turnId = parseTurnId(turnResponse);
      const result = this.waitForTurn(threadId, turnId, prompt.length, signal);
      for (const notification of this.notificationBacklog)
        this.handleNotification(notification);
      this.notificationBacklog = [];
      return await result;
    } catch (error) {
      if (threadId && turnId && !this.closed)
        await this.interruptTurn(threadId, turnId);
      if (error instanceof CodexAppServerError) throw error;
      if (signal.aborted) throw abortedError();
      throw codexError("protocol", "Codex 分析流程异常终止。");
    } finally {
      this.clearActiveTurn();
      this.notificationBacklog = [];
      if (threadId && !this.closed && this.process) {
        await this.request(
          "thread/archive",
          { threadId },
          this.handshakeTimeoutMs,
          "request-timeout",
        ).catch(() => undefined);
      }
    }
  }

  private waitForTurn(
    threadId: string,
    turnId: string,
    promptChars: number,
    signal: AbortSignal,
  ): Promise<string> {
    if (signal.aborted) throw abortedError();
    return new Promise<string>((resolve, reject) => {
      const onAbort = (): void => {
        void this.interruptTurn(threadId, turnId);
        this.rejectActiveTurn(abortedError());
      };
      signal.addEventListener("abort", onAbort, { once: true });
      const timer = setTimeout(() => {
        void this.interruptTurn(threadId, turnId);
        this.rejectActiveTurn(
          this.activeTurn?.retryableError ??
            codexError(
              "turn-timeout",
              "Codex 分析超时，请缩小聊天范围后重试。 / Codex turn timed out.",
            ),
        );
      }, turnTimeoutForPrompt(this.turnTimeoutMs, promptChars));
      this.activeTurn = {
        threadId,
        turnId,
        finalText: null,
        retryableError: null,
        interrupted: false,
        resolve,
        reject,
        timer,
        removeAbort: () => signal.removeEventListener("abort", onAbort),
      };
    });
  }

  private handleLine(line: string): void {
    let message: unknown;
    try {
      message = JSON.parse(line);
    } catch {
      this.rejectAllPending(
        codexError("protocol", "Codex 返回了无法解析的 JSONL 消息。"),
      );
      this.rejectActiveTurn(
        codexError("protocol", "Codex 返回了无法解析的 JSONL 消息。"),
      );
      return;
    }
    if (!isRecord(message)) return;
    if (typeof message.id === "number" && !message.method) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      pending.removeAbort();
      if (message.error !== undefined) pending.reject(responseError(message.error));
      else pending.resolve(message.result);
      return;
    }
    if (typeof message.method === "string") {
      if (message.id !== undefined) {
        this.sendRaw({
          id: message.id,
          error: {
            code: -32601,
            message: "Server requests are disabled for local analysis",
          },
        });
        this.rejectActiveTurn(
          codexError(
            "unsafe-tool",
            "Codex 尝试请求外部能力，本次分析已阻止。",
          ),
        );
        return;
      }
      if (
        !this.activeTurn &&
        ["item/started", "item/completed", "turn/completed", "error"].includes(
          message.method,
        )
      ) {
        this.notificationBacklog.push(message);
        if (this.notificationBacklog.length > 100)
          this.notificationBacklog.shift();
        return;
      }
      this.handleNotification(message);
    }
  }

  private handleNotification(message: JsonRecord): void {
    const method = textField(message, "method");
    const params = isRecord(message.params) ? message.params : null;
    const active = this.activeTurn;
    if (!method || !params || !active) return;
    const threadId = textField(params, "threadId");
    const directTurnId = textField(params, "turnId");
    const nestedTurn = isRecord(params.turn) ? params.turn : null;
    const turnId = directTurnId || textField(nestedTurn, "id");
    if (threadId !== active.threadId || turnId !== active.turnId) return;

    if (method === "item/started" || method === "item/completed") {
      const item = isRecord(params.item) ? params.item : null;
      const itemType = textField(item, "type");
      if (itemType && unsafeItemTypes.has(itemType)) {
        void this.interruptTurn(active.threadId, active.turnId);
        this.rejectActiveTurn(
          codexError(
            "unsafe-tool",
            `Codex 尝试使用 ${itemType}，本次分析已阻止。`,
          ),
        );
        return;
      }
      if (method === "item/completed" && itemType === "agentMessage") {
        const phase = textField(item, "phase");
        const text = textField(item, "text");
        if (text && (phase === "final_answer" || phase === null))
          active.finalText = text;
      }
      return;
    }

    if (method === "error") {
      const failure = turnFailureError(params.error ?? params);
      if (params.willRetry === true) {
        if (failure.code !== "turn-failed") active.retryableError = failure;
        return;
      }
      this.rejectActiveTurn(failure);
      return;
    }
    if (method !== "turn/completed") return;
    const status = textField(nestedTurn, "status");
    if (status !== "completed") {
      this.rejectActiveTurn(turnFailureError(nestedTurn?.error ?? nestedTurn));
      return;
    }
    if (!active.finalText) {
      this.rejectActiveTurn(
        codexError("protocol", "Codex 完成分析但没有返回最终文本。"),
      );
      return;
    }
    const output = active.finalText;
    this.resolveActiveTurn(output);
  }

  private resolveActiveTurn(value: string): void {
    const active = this.activeTurn;
    if (!active) return;
    this.clearActiveTurn();
    active.resolve(value);
  }

  private rejectActiveTurn(error: Error): void {
    const active = this.activeTurn;
    if (!active) return;
    this.clearActiveTurn();
    active.reject(error);
  }

  private clearActiveTurn(): void {
    const active = this.activeTurn;
    if (!active) return;
    clearTimeout(active.timer);
    active.removeAbort();
    this.activeTurn = null;
  }

  private async interruptTurn(
    threadId: string,
    turnId: string,
  ): Promise<void> {
    const active = this.activeTurn;
    if (active?.interrupted) return;
    if (active) active.interrupted = true;
    if (this.closed || !this.process) return;
    await this.request(
      "turn/interrupt",
      { threadId, turnId },
      this.handshakeTimeoutMs,
      "request-timeout",
    ).catch(() => undefined);
  }

  private request(
    method: string,
    params: JsonRecord | undefined,
    timeoutMs: number,
    timeoutCode: CodexAppServerErrorCode,
    signal?: AbortSignal,
  ): Promise<unknown> {
    if (this.closed) return Promise.reject(closedError());
    if (!this.process)
      return Promise.reject(
        codexError("process-exit", "Codex App Server 未运行。"),
      );
    if (signal?.aborted) return Promise.reject(abortedError());
    const id = this.nextRequestId++;
    return new Promise<unknown>((resolve, reject) => {
      const onAbort = (): void => {
        const pending = this.pending.get(id);
        if (!pending) return;
        this.pending.delete(id);
        clearTimeout(pending.timer);
        pending.removeAbort();
        reject(abortedError());
      };
      signal?.addEventListener("abort", onAbort, { once: true });
      const timer = setTimeout(() => {
        const pending = this.pending.get(id);
        if (!pending) return;
        this.pending.delete(id);
        pending.removeAbort();
        const error = codexError(
          timeoutCode,
          timeoutCode === "handshake-timeout"
            ? "Codex App Server 启动超时。"
            : "Codex App Server 请求超时。",
        );
        if (timeoutCode === "request-timeout")
          this.invalidateProcessAfterTimeout(error);
        reject(error);
      }, timeoutMs);
      this.pending.set(id, {
        resolve,
        reject,
        timer,
        removeAbort: () => signal?.removeEventListener("abort", onAbort),
      });
      try {
        this.sendRaw({ id, method, ...(params ? { params } : {}) });
      } catch (error) {
        this.pending.delete(id);
        clearTimeout(timer);
        signal?.removeEventListener("abort", onAbort);
        reject(error as Error);
      }
    });
  }

  private notify(method: string, params: JsonRecord): void {
    this.sendRaw({ method, params });
  }

  private sendRaw(value: JsonRecord): void {
    if (this.closed) throw closedError();
    const process = this.process;
    if (!process) throw codexError("process-exit", "Codex App Server 未运行。");
    process.stdin.write(`${JSON.stringify(value)}\n`);
  }

  private handleProcessExit(code: number | null): void {
    if (!this.process) return;
    this.process = null;
    this.initialized = false;
    this.reader?.close();
    this.reader = null;
    const error = this.closed
      ? closedError()
      : codexError(
          "process-exit",
          `Codex App Server 意外退出（code ${code ?? "unknown"}）。`,
        );
    this.rejectAllPending(error);
    this.rejectActiveTurn(error);
  }

  private invalidateProcessAfterTimeout(error: CodexAppServerError): void {
    const process = this.process;
    if (!process) return;
    this.process = null;
    this.initialized = false;
    this.reader?.close();
    this.reader = null;
    this.notificationBacklog = [];
    this.rejectAllPending(error);
    this.rejectActiveTurn(error);
    process.kill("SIGTERM");
  }

  private rejectAllPending(error: Error): void {
    const entries = [...this.pending.values()];
    this.pending.clear();
    for (const pending of entries) {
      clearTimeout(pending.timer);
      pending.removeAbort();
      pending.reject(error);
    }
  }

  private withAbort<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
    if (signal.aborted) return Promise.reject(abortedError());
    return new Promise<T>((resolve, reject) => {
      const onAbort = (): void => reject(abortedError());
      signal.addEventListener("abort", onAbort, { once: true });
      promise.then(
        (value) => {
          signal.removeEventListener("abort", onAbort);
          resolve(value);
        },
        (error: unknown) => {
          signal.removeEventListener("abort", onAbort);
          reject(error);
        },
      );
    });
  }
}
