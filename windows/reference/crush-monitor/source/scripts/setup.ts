import { readFile, writeFile, rename, unlink } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import { createInterface } from "node:readline/promises";
import { Writable } from "node:stream";
import { parse } from "dotenv";
import {
  PROVIDERS,
  getProviderConfig,
  ConfigurationError,
  type HttpProvider,
  type HttpProviderConfig,
} from "../server/provider-config";
import { checkProvider, providerErrorMessage } from "../server/provider";

export function updateConfiguration(
  text: string,
  provider: HttpProvider,
  key: string,
) {
  getProviderConfig({ JEV_PROVIDER: provider, JEV_API_KEY: key });
  const values = parse(text);
  // 保留无关设置、注释和旧平台凭据；显式 JEV_API_KEY 始终优先。
  const lines = text
    .split(/\r?\n/)
    .filter(
      (line) => !/^\s*(?:export\s+)?JEV_(PROVIDER|API_KEY)\s*=/.test(line),
    );
  const suffix = lines.join("\n").trim();
  return `JEV_PROVIDER=${provider}\nJEV_API_KEY='${key.trim()}'\n${suffix ? suffix + "\n" : ""}${values.PORT ? "" : "PORT=3178\n"}${values.HOST ? "" : "HOST=127.0.0.1\n"}`;
}

export async function saveConfiguration(path: string, text: string) {
  const temporary = `${path}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporary, text, { mode: 0o600, flag: "wx" });
    await rename(temporary, path);
  } finally {
    await unlink(temporary).catch(() => {});
  }
}

async function main() {
  const english = process.argv.includes("--en");
  const say = (zh: string, en: string) => (english ? en : zh);
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const path = resolve(root, ".env");
  const previous = await readFile(path, "utf8").catch(
    (e: NodeJS.ErrnoException) => {
      if (e.code !== "ENOENT") throw e;
      return "";
    },
  );
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    if (!previous) {
      const template = await readFile(resolve(root, ".env.example"), "utf8");
      await writeFile(path, template, { mode: 0o600, flag: "wx" });
    }
    console.log(
      say(
        "请在终端运行 npm run setup，或在 .env 填写 JEV_PROVIDER 和 JEV_API_KEY。已有配置未改动。",
        "Run npm run setup in a terminal, or edit JEV_PROVIDER and JEV_API_KEY in .env. Existing settings were not changed.",
      ),
    );
    return;
  }
  let muted = false;
  const output = new Writable({
    write(chunk, _encoding, callback) {
      if (!muted) process.stdout.write(chunk);
      callback();
    },
  });
  const rl = createInterface({ input: process.stdin, output, terminal: true });
  const controller = new AbortController();
  rl.on("SIGINT", () => controller.abort());
  const ask = async (prompt: string, secret = false) => {
    process.stdout.write(prompt);
    muted = secret;
    try {
      return (await rl.question("", { signal: controller.signal })).trim();
    } finally {
      muted = false;
      if (secret) process.stdout.write("\n");
    }
  };
  try {
    const env = parse(previous);
    let current: HttpProviderConfig | undefined;
    try {
      const configured = getProviderConfig(env);
      if (configured.kind === "http") current = configured;
    } catch {
      /* fresh or incomplete config */
    }
    console.log(
      say(
        "选择 AI 平台（地址和模型自动配置）：",
        "Choose an AI provider (endpoint and model are automatic):",
      ),
    );
    const ids = Object.keys(PROVIDERS) as HttpProvider[];
    ids.forEach((id, i) => console.log(`  ${i + 1}. ${PROVIDERS[id].name}`));
    if (current)
      console.log(
        say(
          `当前：${current.name}。直接回车保留。`,
          `Current: ${current.name}. Press Enter to keep it.`,
        ),
      );
    let provider: HttpProvider | undefined;
    const choices = ids.map((_id, index) => index + 1).join(" / ");
    while (!provider) {
      const selection = await ask(
        say(`选择 ${choices}：`, `Select ${choices}: `),
      );
      const index = Number(selection) - 1;
      provider = !selection
        ? current?.provider
        : Number.isInteger(index) && index >= 0 && index < ids.length
          ? ids[index]
          : undefined;
      if (!provider)
        console.log(say(`请输入 ${choices}。`, `Enter ${choices}.`));
    }
    console.log(`${PROVIDERS[provider].name}: ${PROVIDERS[provider].keyUrl}`);
    if (provider === "vercel")
      console.log(
        say(
          "请使用 AI Gateway API Key，不是 Vercel Access Token。",
          "Use an AI Gateway API key, not a Vercel Access Token.",
        ),
      );
    const same = current?.provider === provider;
    let apiKey = "";
    while (!apiKey) {
      const entered = await ask(
        say(
          same
            ? "粘贴 Key（不显示；回车保留当前 Key）："
            : "粘贴 Key（输入不显示）：",
          same
            ? "Paste key (hidden; Enter keeps current key): "
            : "Paste key (input hidden): ",
        ),
        true,
      );
      const candidate = entered || (same ? current!.apiKey : "");
      try {
        getProviderConfig({ JEV_PROVIDER: provider, JEV_API_KEY: candidate });
        apiKey = candidate;
      } catch (error) {
        console.log((error as ConfigurationError).message);
      }
    }
    controller.signal.throwIfAborted();
    await saveConfiguration(
      path,
      updateConfiguration(previous, provider, apiKey),
    );
    console.log(
      say(
        "已保存 .env。正在发送一条测试请求，检查连接（会使用少量 API 额度）…",
        "Saved .env. Checking the connection with one test request (uses a small amount of API credits)…",
      ),
    );
    const config = getProviderConfig({
      JEV_PROVIDER: provider,
      JEV_API_KEY: apiKey,
    });
    try {
      const result = await checkProvider(config, controller.signal);
      console.log(
        say(
          `连接成功：${config.name} · ${result.model}。`,
          `Connection verified: ${config.name} · ${result.model}.`,
        ),
      );
    } catch (error) {
      if (controller.signal.aborted) throw error;
      console.log(providerErrorMessage(error));
      console.log(
        say(
          "配置已保存，但连接未通过。修正后运行 npm run check:api 重试。",
          "Configuration saved, but verification failed. Fix the issue and run npm run check:api again.",
        ),
      );
      process.exitCode = 1;
    }
    if (process.env.JEV_PROVIDER || process.env.JEV_API_KEY)
      console.log(
        say(
          "当前终端还设置了 JEV 环境变量，它们会优先于 .env；如与新配置不同，请先清除。",
          "JEV variables in this shell override .env. Clear them if they differ from the saved settings.",
        ),
      );
    console.log(
      say(
        "接下来运行 npm run build 和 npm start；已有服务请先重启。",
        "Next: npm run build and npm start. Restart any running service.",
      ),
    );
  } catch (error) {
    if (controller.signal.aborted) console.log(say("已取消。", "Cancelled."));
    else throw error;
  } finally {
    rl.close();
  }
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  main().catch(() => {
    console.error(
      "配置未完成，请检查 .env 文件权限。 / Setup failed; check file permissions.",
    );
    process.exitCode = 1;
  });
}
