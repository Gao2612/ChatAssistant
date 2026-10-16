import { build as bundleElectronMain } from "esbuild";
import { build as buildPortable } from "electron-builder";
import { access, copyFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const buildDirectory = join(root, "build");
const releaseDirectory = join(root, "release");
const bundlePath = join(buildDirectory, "electron-main.cjs");
const preloadBundlePath = join(buildDirectory, "electron-preload.cjs");
const executablePath = join(releaseDirectory, "crush-monitor.exe");

await mkdir(buildDirectory, { recursive: true });
await mkdir(releaseDirectory, { recursive: true });

await bundleElectronMain({
  entryPoints: [join(root, "scripts", "electron-main.ts")],
  outfile: bundlePath,
  bundle: true,
  format: "cjs",
  platform: "node",
  target: "node22",
  external: ["electron"],
  sourcemap: false,
});

await bundleElectronMain({
  entryPoints: [join(root, "scripts", "electron-preload.ts")],
  outfile: preloadBundlePath,
  bundle: true,
  format: "cjs",
  platform: "node",
  target: "node22",
  external: ["electron"],
  sourcemap: false,
});

const buildResult = await buildPortable({
  projectDir: root,
  config: {
    appId: "com.crush.monitor",
    productName: "Crush Monitor",
    artifactName: "crush-monitor.${ext}",
    asar: true,
    directories: { output: releaseDirectory },
    files: [
      "build/electron-main.cjs",
      "build/electron-preload.cjs",
      "dist/**/*",
      "package.json",
      "!**/.env",
      "!**/.env.*",
    ],
    extraMetadata: { main: "build/electron-main.cjs" },
    win: {
      icon: join(root, "assets", "crush-monitor.ico"),
      signExecutable: false,
      target: [{ target: "portable", arch: ["x64"] }],
    },
    publish: null,
  },
});

const artifacts = Array.isArray(buildResult) ? buildResult : [];
if (!artifacts.some((artifact) => resolve(artifact) === executablePath))
  throw new Error(`Electron Builder 未生成目标文件：${executablePath}`);

await access(executablePath);
await copyFile(
  join(root, ".env.example"),
  join(releaseDirectory, ".env.example"),
);

console.log(`Windows exe: ${executablePath}`);
