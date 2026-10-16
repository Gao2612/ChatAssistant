import { dirname } from "node:path";

type PackagedProcess = NodeJS.Process & { pkg?: boolean };

function isPackaged(): boolean {
  return Boolean((process as PackagedProcess).pkg);
}

function runtimeDirectory(): string {
  return isPackaged() ? dirname(process.execPath) : process.cwd();
}

function browserHost(host: string): string {
  return host === "0.0.0.0" || host === "::" ? "127.0.0.1" : host;
}

async function main(): Promise<void> {
  process.chdir(runtimeDirectory());
  process.env.CRUSH_PACKAGED = "1";
  process.env.HOST = "127.0.0.1";
  await import("../server/index");
  const host = process.env.HOST?.trim() || "127.0.0.1";
  const port = Number(process.env.PORT || 3178);
  console.log(`Crush API: http://${browserHost(host)}:${port}/`);
}

void main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : "未知启动错误";
  console.error("Crush 启动失败", { message });
  process.exitCode = 1;
});
