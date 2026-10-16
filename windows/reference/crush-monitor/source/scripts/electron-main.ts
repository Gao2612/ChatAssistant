import {
  app as electronApp,
  BrowserWindow,
  dialog,
  ipcMain,
} from "electron";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import {
  readCompanionImage,
  readCompanionMedia,
} from "./companion-image";

const LOOPBACK_HOST = "127.0.0.1";
const DEFAULT_PORT = 3178;
const LOOPBACK_HOSTNAMES = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

type ServerApplication = {
  listen: (port: number, host: string) => Server;
  shutdown: () => Promise<void>;
};

type CancellableEvent = {
  preventDefault: () => void;
};

type StartedServer = {
  server: Server;
  url: string;
};

type PdfExportRequest = {
  html: string;
  fileName: string;
};

type PdfExportResult = {
  saved: boolean;
  path?: string;
};

type CompanionImageRequest = {
  sourceFilePath: string;
  relativePath: string;
};

type CompanionMediaRequest = CompanionImageRequest;

let mainWindow: BrowserWindow | null = null;
let localServer: Server | null = null;
let localApplication: ServerApplication | null = null;
let shuttingDown = false;

function runtimeDirectory(): string {
  return electronApp.isPackaged ? dirname(process.execPath) : process.cwd();
}

function prepareRuntimeEnvironment(): void {
  process.chdir(runtimeDirectory());
  process.env.CRUSH_PACKAGED = "1";
  process.env.CRUSH_NO_LISTEN = "1";
  process.env.HOST = LOOPBACK_HOST;
}

function isLoopbackUrl(value: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return false;
  }
  return (
    (parsed.protocol === "http:" || parsed.protocol === "https:") &&
    LOOPBACK_HOSTNAMES.has(parsed.hostname.toLowerCase()) &&
    !parsed.username &&
    !parsed.password
  );
}

function requireLoopbackUrl(value: string): string {
  const normalized = value.trim();
  if (!normalized || !isLoopbackUrl(normalized))
    throw new Error("Electron 只允许加载本机服务地址。 / Only loopback URLs are allowed.");
  return new URL(normalized).toString();
}

function parsePdfExportRequest(value: unknown): PdfExportRequest {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error("PDF 导出参数无效。 / Invalid PDF export request.");
  const record = value as Record<string, unknown>;
  if (typeof record.html !== "string" || !record.html.trim())
    throw new Error("PDF 导出内容为空。 / PDF export HTML is empty.");
  if (record.html.length > 20_000_000)
    throw new Error("PDF 导出内容过大，请缩小聊天范围后重试。 / PDF export is too large.");
  if (typeof record.fileName !== "string" || !record.fileName.trim())
    throw new Error("PDF 文件名无效。 / Invalid PDF file name.");
  return { html: record.html, fileName: record.fileName };
}

function parseCompanionImageRequest(value: unknown): CompanionImageRequest {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error("头像读取参数无效。 / Invalid companion image request.");
  const record = value as Record<string, unknown>;
  if (typeof record.sourceFilePath !== "string" || !record.sourceFilePath.trim())
    throw new Error("头像来源无效。 / Invalid companion image source.");
  if (typeof record.relativePath !== "string" || !record.relativePath.trim())
    throw new Error("头像路径无效。 / Invalid companion image path.");
  return {
    sourceFilePath: record.sourceFilePath,
    relativePath: record.relativePath,
  };
}

function safePdfFileName(fileName: string): string {
  const sanitized = fileName
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
    .replace(/[ .]+$/g, "")
    .trim()
    .slice(0, 120);
  if (!sanitized) return "crush-analysis.pdf";
  return sanitized.toLowerCase().endsWith(".pdf")
    ? sanitized
    : `${sanitized}.pdf`;
}

function argumentValue(prefixes: string[]): string | null {
  for (const argument of process.argv.slice(1)) {
    const prefix = prefixes.find((candidate) => argument.startsWith(candidate));
    if (prefix) return argument.slice(prefix.length).trim() || null;
  }
  return null;
}

function existingDevelopmentUrl(): string | null {
  if (electronApp.isPackaged) return null;
  const argumentUrl = argumentValue(["--url=", "--crush-url="]);
  const configuredUrl = argumentUrl || process.env.CRUSH_DESKTOP_URL?.trim();
  return configuredUrl ? requireLoopbackUrl(configuredUrl) : null;
}

function requestedPort(): number {
  const value = process.env.PORT?.trim();
  if (!value) return DEFAULT_PORT;
  if (!/^\d+$/.test(value))
    throw new Error("PORT 必须是 0 到 65535 之间的整数。 / PORT must be an integer.");
  const port = Number(value);
  if (!Number.isInteger(port) || port < 0 || port > 65535)
    throw new Error("PORT 必须是 0 到 65535 之间的整数。 / PORT must be an integer.");
  return port;
}

function errorCode(error: unknown): string | undefined {
  if (typeof error !== "object" || error === null) return undefined;
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" ? code : undefined;
}

function isAddressInUse(error: unknown): boolean {
  return errorCode(error) === "EADDRINUSE";
}

function listenOnPort(
  application: ServerApplication,
  port: number,
): Promise<StartedServer> {
  return new Promise<StartedServer>((resolve, reject) => {
    let server: Server;
    try {
      server = application.listen(port, LOOPBACK_HOST);
    } catch (error: unknown) {
      reject(error);
      return;
    }
    const onError = (error: Error): void => {
      server.removeListener("listening", onListening);
      reject(error);
    };
    const onListening = (): void => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.removeListener("error", onError);
        reject(
          new Error(
            "本机服务未返回有效端口。 / The local server did not expose a port.",
          ),
        );
        return;
      }
      server.removeListener("error", onError);
      const portNumber = (address as AddressInfo).port;
      resolve({
        server,
        url: `http://${LOOPBACK_HOST}:${portNumber}/`,
      });
    };
    server.once("error", onError);
    server.once("listening", onListening);
  });
}

async function loadServerApplication(): Promise<ServerApplication> {
  const serverModule = await import("../server/index");
  return {
    listen: serverModule.app.listen.bind(serverModule.app),
    shutdown: serverModule.closeProviderResources,
  };
}

async function startLocalServer(): Promise<StartedServer> {
  const application = await loadServerApplication();
  localApplication = application;
  const port = requestedPort();
  try {
    return await listenOnPort(application, port);
  } catch (error: unknown) {
    if (port === 0 || !isAddressInUse(error)) throw error;
    return listenOnPort(application, 0);
  }
}

function closeServer(server: Server): Promise<void> {
  if (!server.listening) return Promise.resolve();
  return new Promise<void>((resolve, reject) => {
    server.close((error?: Error) => {
      if (error) reject(error);
      else resolve();
    });
  });
}

async function stopLocalServer(): Promise<void> {
  const server = localServer;
  const application = localApplication;
  localServer = null;
  localApplication = null;
  let closeError: unknown = null;
  try {
    if (server) await closeServer(server);
  } catch (error: unknown) {
    closeError = error;
  }
  if (application) await application.shutdown();
  if (closeError) throw closeError;
}

function createMainWindow(url: string): Promise<void> {
  const window = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 960,
    minHeight: 700,
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: join(electronApp.getAppPath(), "build", "electron-preload.cjs"),
    },
  });
  mainWindow = window;
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on(
    "will-navigate",
    (event: CancellableEvent, targetUrl: string) => {
      if (!isLoopbackUrl(targetUrl)) event.preventDefault();
    },
  );
  window.once("ready-to-show", () => window.show());
  window.on("closed", () => {
    if (mainWindow === window) mainWindow = null;
  });
  return window.loadURL(url).then(
    () => undefined,
    (error: unknown) => {
      if (!window.isDestroyed()) window.destroy();
      const message = error instanceof Error ? error.message : "页面加载失败";
      throw new Error(`内置页面加载失败：${message}`);
    },
  );
}

ipcMain.handle(
  "export-analysis-pdf",
  async (event, value: unknown): Promise<PdfExportResult> => {
    const owner = BrowserWindow.fromWebContents(event.sender);
    if (!owner || owner !== mainWindow)
      throw new Error("PDF 导出请求来源无效。 / Invalid PDF export sender.");
    const request = parsePdfExportRequest(value);
    const saveResult = await dialog.showSaveDialog(owner, {
      title: "导出聊天分析 PDF",
      defaultPath: join(
        electronApp.getPath("documents"),
        safePdfFileName(request.fileName),
      ),
      filters: [{ name: "PDF 文件", extensions: ["pdf"] }],
    });
    if (saveResult.canceled || !saveResult.filePath) return { saved: false };

    const reportWindow = new BrowserWindow({
      show: false,
      width: 1200,
      height: 900,
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    try {
      await reportWindow.loadURL(
        `data:text/html;charset=utf-8,${encodeURIComponent(request.html)}`,
      );
      const pdf = await reportWindow.webContents.printToPDF({
        printBackground: true,
        preferCSSPageSize: true,
      });
      await writeFile(saveResult.filePath, pdf);
      return { saved: true, path: saveResult.filePath };
    } finally {
      if (!reportWindow.isDestroyed()) reportWindow.destroy();
    }
  },
);

ipcMain.handle(
  "read-companion-image",
  async (event, value: unknown): Promise<string | null> => {
    const owner = BrowserWindow.fromWebContents(event.sender);
    if (!owner || owner !== mainWindow)
      throw new Error("头像读取请求来源无效。 / Invalid companion image sender.");
    return readCompanionImage(parseCompanionImageRequest(value));
  },
);

ipcMain.handle(
  "read-companion-media",
  async (event, value: unknown): Promise<string | null> => {
    const owner = BrowserWindow.fromWebContents(event.sender);
    if (!owner || owner !== mainWindow)
      throw new Error("媒体读取请求来源无效。 / Invalid companion media sender.");
    return readCompanionMedia(
      parseCompanionImageRequest(value) as CompanionMediaRequest,
    );
  },
);

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "未知启动错误";
}

async function quitAfterStartupFailure(error: unknown): Promise<void> {
  if (shuttingDown) return;
  shuttingDown = true;
  let cleanupError: unknown = null;
  try {
    await stopLocalServer();
  } catch (stopError: unknown) {
    cleanupError = stopError;
  }
  const message = cleanupError
    ? `${errorMessage(error)}；清理本机服务失败：${errorMessage(cleanupError)}`
    : errorMessage(error);
  dialog.showErrorBox("Crush 启动失败", message);
  electronApp.quit();
}

async function quitAndCleanUp(): Promise<void> {
  if (shuttingDown) return;
  shuttingDown = true;
  try {
    await stopLocalServer();
  } catch (error: unknown) {
    dialog.showErrorBox("Crush 清理失败", errorMessage(error));
  }
  electronApp.quit();
}

async function launch(): Promise<void> {
  prepareRuntimeEnvironment();
  const developmentUrl = existingDevelopmentUrl();
  let url: string;
  if (developmentUrl) {
    url = developmentUrl;
  } else {
    const started = await startLocalServer();
    localServer = started.server;
    url = started.url;
  }
  await createMainWindow(url);
}

electronApp.on("before-quit", (event: CancellableEvent) => {
  if (shuttingDown) return;
  event.preventDefault();
  void quitAndCleanUp();
});

electronApp.on("window-all-closed", () => {
  void quitAndCleanUp();
});

void electronApp.whenReady().then(launch).catch((error: unknown) => {
  void quitAfterStartupFailure(error);
});
