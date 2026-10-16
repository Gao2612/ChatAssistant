export const PROVIDERS = {
  typesafe: {
    name: "TypeSafe",
    endpoint: "https://api.typesafe.ai/v1/systemone",
    model: "jev-1.13.0",
    keyEnv: "TYPESAFE_API_KEY",
    keyUrl: "https://console.typesafe.ai/",
  },
  vercel: {
    name: "Vercel AI Gateway",
    endpoint: "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
    model: "typesafe-ai/jev",
    keyEnv: "AI_GATEWAY_API_KEY",
    keyUrl: "https://vercel.com/d?to=/%5Bteam%5D/~/ai-gateway/api-keys",
  },
  openrouter: {
    name: "OpenRouter",
    endpoint: "https://openrouter.ai/api/alpha/decisions",
    model: "typesafe/jev-1.13",
    keyEnv: "OPENROUTER_API_KEY",
    keyUrl: "https://openrouter.ai/settings/keys",
  },
  deepseek: {
    name: "DeepSeek",
    endpoint: "https://api.deepseek.com/chat/completions",
    model: "deepseek-flash",
    keyEnv: "DEEPSEEK_API_KEY",
    keyUrl: "https://platform.deepseek.com/api_keys",
  },
} as const;

export type HttpProvider = keyof typeof PROVIDERS;
export type Provider = HttpProvider | "codex";

export type HttpProviderConfig = {
  provider: HttpProvider;
  kind: "http";
  apiKey: string;
  name: string;
  endpoint: string;
  model: string;
  keyEnv: string;
  keyUrl: string;
};

export type CodexProviderConfig = {
  provider: "codex";
  kind: "codex";
  name: "ChatGPT Plus（Codex）";
  model: string;
  apiKey?: never;
  endpoint?: never;
  keyEnv?: never;
  keyUrl?: never;
};

export type ProviderConfig = HttpProviderConfig | CodexProviderConfig;

const providerIds: readonly Provider[] = [
  "typesafe",
  "vercel",
  "openrouter",
  "deepseek",
  "codex",
];

export class ConfigurationError extends Error {
  readonly status = 503;
}

export function isHttpProvider(value: string): value is HttpProvider {
  return Object.hasOwn(PROVIDERS, value);
}

export function isProvider(value: string): value is Provider {
  return value === "codex" || isHttpProvider(value);
}

export function getProviderConfig(
  env: Record<string, string | undefined> = process.env,
): ProviderConfig {
  const selected = env.JEV_PROVIDER?.trim().toLowerCase() || "typesafe";
  if (!isProvider(selected))
    throw new ConfigurationError(
      `平台只支持 ${providerIds.join("、")}。请运行 npm run setup。 / Unsupported provider.`,
    );
  if (selected === "codex")
    return {
      provider: "codex",
      kind: "codex",
      name: "ChatGPT Plus（Codex）",
      model: env.CODEX_MODEL?.trim() || "codex-default",
    };

  const preset = PROVIDERS[selected];
  const apiKey = (env.JEV_API_KEY || env[preset.keyEnv] || "").trim();
  if (!apiKey || /^(your[_-].*|replace[_-].*|xxx+|<.*>)$/i.test(apiKey))
    throw new ConfigurationError(
      `${preset.name} 未配置 API Key，请运行 npm run setup。 / API key required.`,
    );
  if (/[\s\x00-\x1f\x7f"'`]/.test(apiKey) || apiKey.includes("="))
    throw new ConfigurationError(
      "请只粘贴 Key 本身，不要带变量名、引号或 Bearer。 / Paste the key only.",
    );
  if (apiKey.startsWith("sk-or-") && selected !== "openrouter")
    throw new ConfigurationError(
      "这看起来是 OpenRouter Key，请选择 openrouter。 / Select OpenRouter for this key.",
    );
  const model =
    selected === "deepseek"
      ? env.DEEPSEEK_MODEL?.trim() || preset.model
      : preset.model;
  return {
    provider: selected,
    kind: "http",
    apiKey,
    name: preset.name,
    endpoint: preset.endpoint,
    model,
    keyEnv: preset.keyEnv,
    keyUrl: preset.keyUrl,
  };
}

export function providerStatus(
  env: Record<string, string | undefined> = process.env,
) {
  try {
    const config = getProviderConfig(env);
    return { configured: true, provider: config.provider, model: config.model };
  } catch (error) {
    if (!(error instanceof ConfigurationError)) throw error;
    return { configured: false, error: error.message };
  }
}
