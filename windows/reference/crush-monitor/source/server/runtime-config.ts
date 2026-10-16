import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import {
  mkdir,
  rename,
  unlink,
  writeFile,
} from "node:fs/promises";
import { dirname, join } from "node:path";
import {
  ConfigurationError,
  getProviderConfig,
  isHttpProvider,
  isProvider,
  PROVIDERS,
  providerStatus,
  type HttpProvider,
  type Provider,
} from "./provider-config";

export type RuntimeEnvironment = Record<string, string | undefined>;

export type RuntimeConfig =
  | { provider: "codex"; codexModel?: string }
  | {
      provider: HttpProvider;
      apiKey: string;
      deepseekModel?: string;
    };

export type RuntimeConfigStatus = {
  configured: boolean;
  provider: Provider | null;
  model: string | null;
  hasApiKey: boolean;
  requiresApiKey: boolean;
};

export class RuntimeConfigError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "RuntimeConfigError";
    this.status = status;
  }
}

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorCode(error: unknown): string | undefined {
  if (!isRecord(error) || typeof error.code !== "string") return undefined;
  return error.code;
}

function appDataDirectoryValue(
  appDataDirectory: string | undefined,
): string | null {
  const value = appDataDirectory?.trim() || process.env.APPDATA?.trim();
  return value || null;
}

export function getRuntimeConfigPath(appDataDirectory?: string): string {
  const directory = appDataDirectoryValue(appDataDirectory);
  if (!directory)
    throw new RuntimeConfigError(
      500,
      "无法确定 Windows 用户配置目录，未保存本机 API 配置。",
    );
  return join(directory, "crush-monitor", "config.json");
}

function assertAllowedFields(value: JsonRecord): void {
  const allowed = new Set([
    "provider",
    "apiKey",
    "deepseekModel",
    "codexModel",
  ]);
  if (Object.keys(value).some((key) => !allowed.has(key)))
    throw new RuntimeConfigError(400, "配置字段不受支持。 / Unsupported config field.");
}

function validateCodexModel(value: unknown): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== "string")
    throw new RuntimeConfigError(400, "Codex 模型名无效。 / Invalid Codex model.");
  const model = value.trim();
  if (
    !model ||
    model.length > 120 ||
    !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(model)
  )
    throw new RuntimeConfigError(400, "Codex 模型名无效。 / Invalid Codex model.");
  return model;
}

function validateDeepSeekModel(value: unknown): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== "string")
    throw new RuntimeConfigError(
      400,
      "DeepSeek 模型名无效。 / Invalid DeepSeek model.",
    );
  const model = value.trim();
  if (
    model.length > 120 ||
    /[\x00-\x1f\x7f]/.test(model) ||
    model.includes('"') ||
    model.includes("'")
  )
    throw new RuntimeConfigError(
      400,
      "DeepSeek 模型名无效。 / Invalid DeepSeek model.",
    );
  return model || undefined;
}

export function validateRuntimeConfigInput(value: unknown): RuntimeConfig {
  if (!isRecord(value))
    throw new RuntimeConfigError(400, "配置格式无效。 / Invalid config payload.");
  assertAllowedFields(value);
  if (typeof value.provider !== "string" || !value.provider.trim())
    throw new RuntimeConfigError(400, "请选择支持的平台。 / Provider required.");
  const providerValue = value.provider.trim().toLowerCase();
  if (!isProvider(providerValue))
    throw new RuntimeConfigError(
      400,
      "平台只支持 typesafe、vercel、openrouter、deepseek、codex。 / Unsupported provider.",
    );
  if (providerValue === "codex") {
    if (value.apiKey !== undefined || value.deepseekModel !== undefined)
      throw new RuntimeConfigError(400, "Codex 不接受 API Key 或 DeepSeek 模型设置。");
    const codexModel = validateCodexModel(value.codexModel);
    return { provider: "codex", ...(codexModel ? { codexModel } : {}) };
  }
  if (value.codexModel !== undefined)
    throw new RuntimeConfigError(400, "只有 Codex 可以选择 Codex 模型。");
  if (typeof value.apiKey !== "string")
    throw new RuntimeConfigError(400, "API Key 必须是文本。 / API key required.");

  const provider = providerValue;
  const apiKey = value.apiKey.trim();
  const deepseekModel = validateDeepSeekModel(value.deepseekModel);
  const environment: RuntimeEnvironment = {
    JEV_PROVIDER: provider,
    JEV_API_KEY: apiKey,
    ...(deepseekModel ? { DEEPSEEK_MODEL: deepseekModel } : {}),
  };
  try {
    getProviderConfig(environment);
  } catch (error: unknown) {
    if (error instanceof ConfigurationError)
      throw new RuntimeConfigError(400, error.message);
    throw error;
  }
  return {
    provider,
    apiKey,
    ...(provider === "deepseek" && deepseekModel
      ? { deepseekModel }
      : {}),
  };
}

export function runtimeEnvironmentFor(
  config: RuntimeConfig,
  environment: RuntimeEnvironment,
): RuntimeEnvironment {
  const result: RuntimeEnvironment = {
    ...environment,
    JEV_PROVIDER: config.provider,
  };
  if (config.provider === "codex") {
    delete result.JEV_API_KEY;
    delete result.DEEPSEEK_MODEL;
    if (config.codexModel) result.CODEX_MODEL = config.codexModel;
    else delete result.CODEX_MODEL;
    return result;
  }
  result.JEV_API_KEY = config.apiKey;
  if (config.provider === "deepseek" && config.deepseekModel)
    result.DEEPSEEK_MODEL = config.deepseekModel;
  else delete result.DEEPSEEK_MODEL;
  delete result.CODEX_MODEL;
  return result;
}

export function applyRuntimeConfig(
  config: RuntimeConfig,
  environment?: RuntimeEnvironment,
): void {
  const target = environment ?? process.env;
  const next = runtimeEnvironmentFor(config, target);
  for (const [key, value] of Object.entries(next)) {
    if (value === undefined) delete target[key];
    else target[key] = value;
  }
  if (!Object.hasOwn(next, "JEV_API_KEY")) delete target.JEV_API_KEY;
  if (!Object.hasOwn(next, "DEEPSEEK_MODEL")) delete target.DEEPSEEK_MODEL;
  if (!Object.hasOwn(next, "CODEX_MODEL")) delete target.CODEX_MODEL;
}

export function readRuntimeConfig(
  appDataDirectory?: string,
): RuntimeConfig | null {
  const directory = appDataDirectoryValue(appDataDirectory);
  if (!directory) return null;
  const filePath = join(directory, "crush-monitor", "config.json");
  let text: string;
  try {
    text = readFileSync(filePath, "utf8");
  } catch (error: unknown) {
    if (errorCode(error) === "ENOENT") return null;
    throw new RuntimeConfigError(500, "无法读取本机 API 配置文件。");
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new RuntimeConfigError(500, "本机 API 配置文件格式无效。");
  }
  try {
    return validateRuntimeConfigInput(value);
  } catch (error: unknown) {
    if (error instanceof RuntimeConfigError)
      throw new RuntimeConfigError(500, "本机 API 配置文件内容无效。");
    throw error;
  }
}

function canReplaceExistingFile(error: unknown): boolean {
  return ["EEXIST", "ENOTEMPTY", "EPERM"].includes(errorCode(error) || "");
}

export async function saveRuntimeConfig(
  config: RuntimeConfig,
  appDataDirectory?: string,
): Promise<void> {
  const validated = validateRuntimeConfigInput(config);
  const filePath = getRuntimeConfigPath(appDataDirectory);
  const directory = dirname(filePath);
  const temporaryPath = join(
    directory,
    `.config-${process.pid}-${randomUUID()}.tmp`,
  );
  let temporaryCreated = false;
  try {
    await mkdir(directory, { recursive: true });
    await writeFile(
      temporaryPath,
      `${JSON.stringify(
        validated.provider === "codex"
          ? {
              provider: "codex",
              ...(validated.codexModel
                ? { codexModel: validated.codexModel }
                : {}),
            }
          : {
              provider: validated.provider,
              apiKey: validated.apiKey,
              ...(validated.deepseekModel
                ? { deepseekModel: validated.deepseekModel }
                : {}),
            },
      )}\n`,
      { encoding: "utf8", flag: "wx", mode: 0o600 },
    );
    temporaryCreated = true;
    try {
      await rename(temporaryPath, filePath);
      temporaryCreated = false;
    } catch (error: unknown) {
      if (!canReplaceExistingFile(error)) throw error;
      await unlink(filePath);
      await rename(temporaryPath, filePath);
      temporaryCreated = false;
    }
  } catch (error: unknown) {
    if (temporaryCreated) {
      try {
        await unlink(temporaryPath);
      } catch (cleanupError: unknown) {
        if (errorCode(cleanupError) !== "ENOENT")
          throw new RuntimeConfigError(500, "无法清理本机 API 配置临时文件。");
      }
    }
    throw new RuntimeConfigError(500, "无法保存本机 API 配置文件。");
  }
}

export function runtimeConfigStatus(
  environment?: RuntimeEnvironment,
): RuntimeConfigStatus {
  const source = environment ?? process.env;
  const selected = source.JEV_PROVIDER?.trim().toLowerCase() || "typesafe";
  const provider = isProvider(selected) ? selected : null;
  const preset =
    provider && isHttpProvider(provider) ? PROVIDERS[provider] : null;
  const apiKey =
    provider && preset
      ? (source.JEV_API_KEY || source[preset.keyEnv] || "").trim()
      : "";
  const fallbackModel =
    provider === "deepseek"
      ? source.DEEPSEEK_MODEL?.trim() || preset?.model || null
      : provider === "codex"
        ? source.CODEX_MODEL?.trim() || "codex-default"
        : preset?.model || null;
  const status = providerStatus(source);
  return {
    configured: status.configured,
    provider: "provider" in status ? status.provider ?? provider : provider,
    model: "model" in status ? status.model ?? fallbackModel : fallbackModel,
    hasApiKey: Boolean(apiKey),
    requiresApiKey: provider !== "codex",
  };
}

export function isLocalOrigin(origin: string | undefined): boolean {
  if (!origin) return true;
  let parsed: URL;
  try {
    parsed = new URL(origin);
  } catch {
    return false;
  }
  if (!(["http:", "https:"] as string[]).includes(parsed.protocol))
    return false;
  if (parsed.username || parsed.password || parsed.pathname !== "/")
    return false;
  return ["localhost", "127.0.0.1", "::1", "[::1]"].includes(
    parsed.hostname.toLowerCase(),
  );
}
