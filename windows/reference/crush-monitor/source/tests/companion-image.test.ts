import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  readCompanionImage,
  readCompanionMedia,
} from "../scripts/companion-image";

type Fixture = {
  root: string;
  source: string;
  avatar: string;
};

async function withFixture(
  action: (fixture: Fixture) => Promise<void>,
): Promise<void> {
  const root = await mkdtemp(join(tmpdir(), "crush-companion-image-"));
  const avatar = join(root, "avatar");
  const source = join(root, "chat.html");
  await mkdir(avatar);
  await writeFile(source, "<!doctype html>");
  try {
    await action({ root, source, avatar });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function imageBytes(extension: "jpg" | "png" | "webp" | "gif"): Uint8Array {
  if (extension === "jpg") return Uint8Array.from([0xff, 0xd8, 0xff, 0xd9]);
  if (extension === "png")
    return Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  if (extension === "gif") return new TextEncoder().encode("GIF89a");
  return Uint8Array.from([
    0x52,
    0x49,
    0x46,
    0x46,
    0x00,
    0x00,
    0x00,
    0x00,
    0x57,
    0x45,
    0x42,
    0x50,
  ]);
}

test("同级 avatar 的 JPG、PNG、WebP、GIF 会被转换为正确 data URL", async () => {
  await withFixture(async ({ source, avatar }) => {
    const cases = [
      ["person.jpg", "jpg", "image/jpeg"],
      ["person.png", "png", "image/png"],
      ["person.webp", "webp", "image/webp"],
      ["person.gif", "gif", "image/gif"],
    ] as const;
    for (const [name, extension, mime] of cases) {
      await writeFile(join(avatar, name), imageBytes(extension));
      const result = await readCompanionImage({
        sourceFilePath: source,
        relativePath: `avatar/${name}`,
      });
      assert.match(result, new RegExp(`^data:${mime};base64,`));
    }
  });
});

test("头像读取拒绝无效来源、危险路径及 avatar 目录外的文件", async () => {
  await withFixture(async ({ root, source, avatar }) => {
    await writeFile(join(avatar, "person.jpg"), imageBytes("jpg"));
    const invalidSource = join(root, "chat.txt");
    await writeFile(invalidSource, "plain");
    const requests = [
      { sourceFilePath: invalidSource, relativePath: "avatar/person.jpg" },
      { sourceFilePath: source, relativePath: "" },
      { sourceFilePath: source, relativePath: "../person.jpg" },
      { sourceFilePath: source, relativePath: "avatar/../person.jpg" },
      { sourceFilePath: source, relativePath: "C:/Windows/person.jpg" },
      { sourceFilePath: source, relativePath: "\\\\server\\share\\person.jpg" },
      { sourceFilePath: source, relativePath: "https://example.com/person.jpg" },
      { sourceFilePath: source, relativePath: "//example.com/person.jpg" },
      { sourceFilePath: source, relativePath: "documents/person.jpg" },
    ];
    for (const request of requests) {
      await assert.rejects(readCompanionImage(request), /头像|来源|路径/);
    }
  });
});

test("头像读取校验扩展名、文件签名、大小和真实路径范围", async (context) => {
  await withFixture(async ({ root, source, avatar }) => {
    await writeFile(join(avatar, "fake.jpg"), imageBytes("png"));
    await writeFile(join(avatar, "large.png"), Buffer.alloc(2 * 1024 * 1024 + 1));
    await assert.rejects(
      readCompanionImage({ sourceFilePath: source, relativePath: "avatar/fake.jpg" }),
      /格式|签名/,
    );
    await assert.rejects(
      readCompanionImage({ sourceFilePath: source, relativePath: "avatar/large.png" }),
      /超过|过大/,
    );

    const outside = join(root, "outside");
    await mkdir(outside);
    await writeFile(join(outside, "outside.jpg"), imageBytes("jpg"));
    try {
      await symlink(outside, join(avatar, "linked"), "junction");
    } catch (error: unknown) {
      const code =
        typeof error === "object" && error !== null && "code" in error
          ? error.code
          : undefined;
      if (code === "EPERM" || code === "EACCES") {
        context.skip("当前 Windows 权限不允许创建测试 junction。");
        return;
      }
      throw error;
    }
    await assert.rejects(
      readCompanionImage({
        sourceFilePath: source,
        relativePath: "avatar/linked/outside.jpg",
      }),
      /头像|路径/,
    );
  });
});

test("同级 image、emoji_sticker、video、audio 资源都能安全转换为 data URL", async () => {
  await withFixture(async ({ root, source }) => {
    await mkdir(join(root, "image"));
    await mkdir(join(root, "emoji_sticker"));
    await mkdir(join(root, "video"));
    await mkdir(join(root, "audio"));
    await writeFile(join(root, "image", "photo.png"), imageBytes("png"));
    await writeFile(
      join(root, "emoji_sticker", "sticker.gif"),
      imageBytes("gif"),
    );
    await writeFile(
      join(root, "video", "clip.mp4"),
      Uint8Array.from([
        0,
        0,
        0,
        0,
        0x66,
        0x74,
        0x79,
        0x70,
        0x69,
        0x73,
        0x6f,
        0x6d,
      ]),
    );
    await writeFile(
      join(root, "audio", "voice.amr"),
      new TextEncoder().encode("#!AMR\n"),
    );

    const requests = [
      ["image/photo.png", "image/png"],
      ["emoji_sticker/sticker.gif", "image/gif"],
      ["video/clip.mp4", "video/mp4"],
      ["audio/voice.amr", "audio/amr"],
    ] as const;
    for (const [relativePath, mime] of requests) {
      const result = await readCompanionMedia({
        sourceFilePath: source,
        relativePath,
      });
      assert.match(result, new RegExp(`^data:${mime};base64,`));
    }
  });
});

test("桌面桥接只暴露指定头像读取入口且主进程校验当前窗口", async () => {
  const preload = await readFile(
    new URL("../scripts/electron-preload.ts", import.meta.url),
    "utf8",
  );
  const main = await readFile(
    new URL("../scripts/electron-main.ts", import.meta.url),
    "utf8",
  );
  assert.match(preload, /webUtils\.getPathForFile/);
  assert.match(preload, /readCompanionImage/);
  assert.match(preload, /readCompanionMedia/);
  assert.doesNotMatch(preload, /readFile\s*:/);
  assert.match(main, /"read-companion-image"/);
  assert.match(main, /"read-companion-media"/);
  assert.match(main, /owner !== mainWindow/);
});
