import "dotenv/config";
import express from "express";
import { join } from "node:path";
import { mkdirSync } from "node:fs";
import { analyze, requestSchema } from "./analysis";
import {
  getProviderConfig,
  providerStatus,
  ConfigurationError,
} from "./provider-config";
import { ProviderError, providerErrorMessage } from "./provider";
import {
  CodexAppServerClient,
  CodexAppServerError,
  createDefaultCodexProcessFactory,
  type CodexAccountStatus,
} from "./codex-app-server";
import {
  CODEX_BASE_TURN_TIMEOUT_MS,
  CODEX_HANDSHAKE_TIMEOUT_MS,
} from "./codex-timeout";
import { readCachedStatus, type UpstreamCache } from "./upstream";
import {
  RuntimeConfigError,
  applyRuntimeConfig,
  isLocalOrigin,
  readRuntimeConfig,
  runtimeConfigStatus,
  saveRuntimeConfig,
  validateRuntimeConfigInput,
  type RuntimeEnvironment,
} from "./runtime-config";

export type CodexClient = Pick<
  CodexAppServerClient,
  "status" | "models" | "evaluate" | "close"
>;

export type ServerDependencies = {
  getCodexClient?: () => CodexClient;
};

export type ProviderResourceManager = {
  getCodexClient: () => CodexClient;
  close: () => Promise<void>;
};

export function createProviderResourceManager(
  factory: () => CodexClient,
): ProviderResourceManager {
  let client: CodexClient | null = null;
  let closePromise: Promise<void> | null = null;
  return {
    getCodexClient: () => {
      if (closePromise)
        throw new Error("提供商资源已经关闭。 / Provider resources closed.");
      if (!client) client = factory();
      return client;
    },
    close: () => {
      if (closePromise) return closePromise;
      const current = client;
      client = null;
      closePromise = current ? current.close() : Promise.resolve();
      return closePromise;
    },
  };
}

function createProductionCodexClient(): CodexClient {
  const appData = process.env.APPDATA?.trim();
  if (!appData)
    throw new CodexAppServerError(
      "unavailable",
      "缺少 APPDATA，无法创建 Codex 隔离目录。",
    );
  const contextDirectory = join(appData, "crush-monitor", "codex-context");
  mkdirSync(contextDirectory, { recursive: true });
  return new CodexAppServerClient(
    createDefaultCodexProcessFactory(),
    contextDirectory,
    CODEX_HANDSHAKE_TIMEOUT_MS,
    CODEX_BASE_TURN_TIMEOUT_MS,
  );
}

const productionResources = createProviderResourceManager(
  createProductionCodexClient,
);

export function closeProviderResources(): Promise<void> {
  return productionResources.close();
}

function emptyCodexStatus(
  available: boolean,
  loggedIn: boolean,
  error: string,
): CodexAccountStatus & { error: string } {
  return {
    available,
    loggedIn,
    authMode: null,
    planType: null,
    rateLimits: [],
    error,
  };
}

function safeCodexStatusError(
  error: unknown,
): (CodexAccountStatus & { error: string }) | null {
  if (!(error instanceof CodexAppServerError)) return null;
  if (error.code === "unavailable")
    return emptyCodexStatus(
      false,
      false,
      "未找到或无法启动 Codex CLI，请先安装并登录 Codex。",
    );
  if (error.code === "not-logged-in")
    return emptyCodexStatus(
      true,
      false,
      "Codex 尚未登录，请先完成 ChatGPT 登录。",
    );
  if (error.code === "not-chatgpt")
    return emptyCodexStatus(
      true,
      true,
      "当前 Codex 不是 ChatGPT 登录，不能使用 Plus 套餐额度。",
    );
  return null;
}

export function createApp(
  environment: RuntimeEnvironment,
  appDataDirectory?: string,
  dependencies?: ServerDependencies,
): express.Express {
  const configDirectory = appDataDirectory ?? environment.APPDATA;
  const stored = readRuntimeConfig(configDirectory);
  if (stored) applyRuntimeConfig(stored, environment);
  const app = express();
  app.disable("x-powered-by");
  app.use(express.json({ limit: "512kb" }));
  app.use((_req, res, next) => {
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Referrer-Policy", "no-referrer");
    res.setHeader("Cache-Control", "no-store");
    next();
  });
  app.get("/api/health", (_req, res) => res.json(providerStatus(environment)));
  app.get("/api/config", (req, res) => {
    if (!isLocalOrigin(req.get("origin"))) {
      res.status(403).json({ error: "请求来源不允许" });
      return;
    }
    res.json(runtimeConfigStatus(environment));
  });
  app.get("/api/codex/status", async (req, res) => {
    if (!isLocalOrigin(req.get("origin"))) {
      res.status(403).json({ error: "请求来源不允许" });
      return;
    }
    const controller = new AbortController();
    res.on("close", () => {
      if (!res.writableEnded) controller.abort();
    });
    try {
      const client =
        dependencies?.getCodexClient?.() ??
        productionResources.getCodexClient();
      res.json(await client.status(controller.signal));
    } catch (error: unknown) {
      if (controller.signal.aborted || res.headersSent) return;
      const status = safeCodexStatusError(error);
      if (status) {
        res.json(status);
        return;
      }
      res.status(502).json({
        error: "Codex 本机状态读取失败，请重试。 / Codex status failed.",
      });
    }
  });
  app.get("/api/codex/models", async (req, res) => {
    if (!isLocalOrigin(req.get("origin"))) {
      res.status(403).json({ error: "请求来源不允许" });
      return;
    }
    const controller = new AbortController();
    res.on("close", () => {
      if (!res.writableEnded) controller.abort();
    });
    try {
      const client =
        dependencies?.getCodexClient?.() ??
        productionResources.getCodexClient();
      res.json({ models: await client.models(controller.signal) });
    } catch (error: unknown) {
      if (controller.signal.aborted || res.headersSent) return;
      res.status(502).json({
        error: "Codex 模型目录读取失败，请重试。 / Codex model list failed.",
      });
    }
  });
  app.put("/api/config", async (req, res) => {
    if (!isLocalOrigin(req.get("origin"))) {
      res.status(403).json({ error: "请求来源不允许" });
      return;
    }
    try {
      const config = validateRuntimeConfigInput(req.body);
      await saveRuntimeConfig(config, configDirectory);
      applyRuntimeConfig(config, environment);
      res.json(runtimeConfigStatus(environment));
    } catch (error: unknown) {
      if (error instanceof RuntimeConfigError) {
        res.status(error.status).json({ error: error.message });
        return;
      }
      res.status(500).json({ error: "本机 API 配置保存失败" });
    }
  });
let upstreamCache: UpstreamCache | null = null;
  app.get("/api/upstream", async (_req, res) => {
    const result = await readCachedStatus(upstreamCache, Date.now(), fetch);
    upstreamCache = result.cache;
    res.json(result.status);
  });
let calls = 0;
let windowAt = Date.now();
let active = 0;
const budgets = new Map<string, { count: number; at: number }>();
  app.post("/api/analyze", async (req, res) => {
  const origin = req.headers.origin;
  if (
    origin &&
    origin !== `${req.protocol}://${req.headers.host}` &&
    !["http://127.0.0.1:5178", "http://localhost:5178"].includes(origin)
  ) {
    res.status(403).json({ error: "请求来源不允许" });
    return;
  }
  const valid = requestSchema.safeParse(req.body);
  if (!valid.success) {
    res.status(400).json({ error: "聊天结构或长度不符合要求，请校正后重试" });
    return;
  }
  const configuration = providerStatus(environment);
  if (!configuration.configured) {
    res.status(503).json({ error: configuration.error });
    return;
  }
  const providerConfig = getProviderConfig(environment);
  const now = Date.now();
  if (now - windowAt > 3600000) {
    calls = 0;
    windowAt = now;
    budgets.clear();
  }
  const key = req.ip || "local";
  let entry = budgets.get(key);
  if (!entry || now - entry.at > 60000) {
    entry = { count: 0, at: now };
    budgets.set(key, entry);
  }
  if (entry.count >= 180 || calls >= 3000 || active >= 8) {
    res.setHeader(
      "Retry-After",
      String(
        calls >= 3000
          ? Math.max(1, Math.ceil((windowAt + 3600000 - now) / 1000))
          : Math.max(1, Math.ceil((entry.at + 60000 - now) / 1000)),
      ),
    );
    res.status(429).json({ error: "分析请求较多，已保留进度，请稍后继续" });
    return;
  }
  entry.count++;
  calls++;
  active++;
  const controller = new AbortController();
  res.on("close", () => {
    if (!res.writableEnded) controller.abort();
  });
  try {
    const providerDependencies =
      providerConfig.kind === "codex"
        ? {
            codex:
              dependencies?.getCodexClient?.() ??
              productionResources.getCodexClient(),
          }
        : undefined;
    res.json(
      await analyze(
        valid.data,
        controller.signal,
        providerConfig,
        providerDependencies,
      ),
    );
  } catch (error) {
    const code = Number((error as { status?: number }).status) || 502;
    if (!res.headersSent && !controller.signal.aborted)
      res.status(code >= 400 && code < 600 ? code : 502).json({
        error:
          error instanceof ConfigurationError || error instanceof ProviderError
            ? error.message
            : providerErrorMessage(error),
      });
  } finally {
    active--;
  }
  });
const dist =
  process.env.CRUSH_PACKAGED === "1"
    ? join(__dirname, "../dist")
    : join(process.cwd(), "dist");
app.use(express.static(dist));
app.get("/", (_req, res) => res.sendFile(join(dist, "index.html")));
app.use(
  (
    err: unknown,
    _req: express.Request,
    res: express.Response,
    _next: express.NextFunction,
  ) => {
    res
      .status(
        (err as { type?: string }).type === "entity.too.large" ? 413 : 400,
      )
      .json({ error: "输入格式或体积不受支持" });
  },
);
  return app;
}

export const app = createApp(process.env, process.env.APPDATA);

if (process.env.CRUSH_NO_LISTEN !== "1") {
  const port = Number(process.env.PORT || 3178);
  const host = process.env.HOST || "127.0.0.1";
  const server = app.listen(port, host, () => {
    const status = providerStatus(process.env);
    console.log(`Crush API: http://${host}:${port}`);
    console.log(
      status.configured
        ? `Model: ${status.provider} · ${status.model} · ` +
          "Key configured (not yet verified)"
        : status.error,
    );
  });
  const shutdown = (): void => {
    server.close(() => {
      void closeProviderResources().finally(() => process.exit(0));
    });
  };
  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);
}
