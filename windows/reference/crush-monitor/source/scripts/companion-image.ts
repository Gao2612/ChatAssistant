import { readdir, readFile, realpath, stat } from "node:fs/promises";
import {
  basename,
  dirname,
  extname,
  isAbsolute,
  relative,
  resolve,
  win32,
} from "node:path";

export type CompanionImageRequest = {
  sourceFilePath: string;
  relativePath: string;
};

export type CompanionMediaRequest = CompanionImageRequest;

const MAX_COMPANION_AVATAR_BYTES = 2 * 1024 * 1024;
const MAX_COMPANION_MEDIA_BYTES = 16 * 1024 * 1024;
const companionDirectories: ReadonlySet<string> = new Set([
  "audio",
  "avatar",
  "emoji",
  "emoji_sticker",
  "image",
  "video",
  "voice",
]);
const extensionlessResourceExtensions: readonly string[] = [
  ".jpg",
  ".jpeg",
  ".png",
  ".gif",
  ".webp",
  ".bmp",
  ".mp4",
  ".webm",
  ".mov",
  ".mkv",
  ".avi",
  ".3gp",
  ".amr",
  ".mp3",
  ".m4a",
  ".wav",
  ".ogg",
  ".opus",
  ".flac",
  ".aac",
  ".silk",
  ".wma",
];

type CompanionResource = {
  category: "audio" | "image" | "video";
  mimeType: string;
};

const companionResources: Record<string, CompanionResource> = {
  ".aac": { category: "audio", mimeType: "audio/aac" },
  ".amr": { category: "audio", mimeType: "audio/amr" },
  ".avi": { category: "video", mimeType: "video/x-msvideo" },
  ".bmp": { category: "image", mimeType: "image/bmp" },
  ".flac": { category: "audio", mimeType: "audio/flac" },
  ".gif": { category: "image", mimeType: "image/gif" },
  ".jpeg": { category: "image", mimeType: "image/jpeg" },
  ".jpg": { category: "image", mimeType: "image/jpeg" },
  ".m4a": { category: "audio", mimeType: "audio/mp4" },
  ".m4v": { category: "video", mimeType: "video/x-m4v" },
  ".mkv": { category: "video", mimeType: "video/x-matroska" },
  ".mov": { category: "video", mimeType: "video/quicktime" },
  ".mp3": { category: "audio", mimeType: "audio/mpeg" },
  ".mp4": { category: "video", mimeType: "video/mp4" },
  ".oga": { category: "audio", mimeType: "audio/ogg" },
  ".ogg": { category: "audio", mimeType: "audio/ogg" },
  ".opus": { category: "audio", mimeType: "audio/opus" },
  ".png": { category: "image", mimeType: "image/png" },
  ".silk": { category: "audio", mimeType: "audio/silk" },
  ".wav": { category: "audio", mimeType: "audio/wav" },
  ".webm": { category: "video", mimeType: "video/webm" },
  ".webp": { category: "image", mimeType: "image/webp" },
  ".wma": { category: "audio", mimeType: "audio/x-ms-wma" },
  ".3gp": { category: "video", mimeType: "video/3gpp" },
};

class CompanionMediaError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "CompanionMediaError";
  }
}

function safeError(message: string): CompanionMediaError {
  return new CompanionMediaError(message);
}

function isContainedPath(root: string, candidate: string): boolean {
  const rootPath = resolve(root).toLocaleLowerCase();
  const candidatePath = resolve(candidate).toLocaleLowerCase();
  const difference = relative(rootPath, candidatePath);
  return Boolean(difference) && !difference.startsWith("..") && !isAbsolute(difference);
}

function sourceFilePath(value: string): string {
  const source = value.trim();
  const extension = extname(source).toLocaleLowerCase();
  if (!source || (extension !== ".html" && extension !== ".json"))
    throw safeError(
      "头像或媒体来源必须是已导入的 HTML 或 JSON 聊天文件。",
    );
  if (!isAbsolute(source) && !win32.isAbsolute(source))
    throw safeError("头像或媒体来源路径无效。");
  return source;
}

function companionRelativePath(value: string): string {
  const input = value.trim();
  const normalized = input.replaceAll("\\", "/").replace(/^(?:\.\/)+/, "");
  if (
    !normalized ||
    normalized.includes("\u0000") ||
    normalized.startsWith("//") ||
    /^[a-z]:/i.test(normalized) ||
    /^[a-z][a-z\d+.-]*:/i.test(normalized) ||
    isAbsolute(normalized) ||
    win32.isAbsolute(normalized)
  )
    throw safeError("头像或媒体路径无效。");
  const segments = normalized.split("/");
  if (
    segments.length < 2 ||
    segments.some((segment) => !segment || segment === "." || segment === "..") ||
    !companionDirectories.has(segments[0].toLocaleLowerCase())
  )
    throw safeError(
      "头像或媒体路径必须位于同级 avatar、image、video、audio 或表情文件夹内。",
    );
  return segments.join("/");
}

function resourceForPath(path: string): CompanionResource {
  const resource = companionResources[extname(path).toLocaleLowerCase()];
  if (!resource) throw safeError("媒体扩展名不受支持。");
  return resource;
}

function hasPrefix(
  bytes: Buffer,
  values: readonly number[],
  offset = 0,
): boolean {
  return (
    bytes.length >= offset + values.length &&
    values.every((value, index) => bytes[offset + index] === value)
  );
}

function hasFourCc(bytes: Buffer, value: string, offset: number): boolean {
  return hasPrefix(
    bytes,
    Array.from(value, (character) => character.charCodeAt(0)),
    offset,
  );
}

function hasValidImageSignature(bytes: Buffer, mimeType: string): boolean {
  if (mimeType === "image/jpeg") return hasPrefix(bytes, [0xff, 0xd8, 0xff]);
  if (mimeType === "image/png")
    return hasPrefix(bytes, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  if (mimeType === "image/gif")
    return (
      hasPrefix(bytes, [0x47, 0x49, 0x46, 0x38, 0x37, 0x61]) ||
      hasPrefix(bytes, [0x47, 0x49, 0x46, 0x38, 0x39, 0x61])
    );
  if (mimeType === "image/bmp") return hasPrefix(bytes, [0x42, 0x4d]);
  return hasPrefix(bytes, [0x52, 0x49, 0x46, 0x46]) && hasFourCc(bytes, "WEBP", 8);
}

function hasValidVideoSignature(bytes: Buffer, mimeType: string): boolean {
  if (mimeType === "video/webm" || mimeType === "video/x-matroska")
    return hasPrefix(bytes, [0x1a, 0x45, 0xdf, 0xa3]);
  if (mimeType === "video/x-msvideo")
    return hasPrefix(bytes, [0x52, 0x49, 0x46, 0x46]) && hasFourCc(bytes, "AVI ", 8);
  return hasFourCc(bytes, "ftyp", 4);
}

function hasValidAudioSignature(bytes: Buffer, mimeType: string): boolean {
  if (mimeType === "audio/wav")
    return hasPrefix(bytes, [0x52, 0x49, 0x46, 0x46]) && hasFourCc(bytes, "WAVE", 8);
  if (mimeType === "audio/mpeg")
    return (
      hasPrefix(bytes, [0x49, 0x44, 0x33]) ||
      (bytes.length >= 2 && bytes[0] === 0xff && (bytes[1] & 0xe0) === 0xe0)
    );
  if (mimeType === "audio/mp4") return hasFourCc(bytes, "ftyp", 4);
  if (mimeType === "audio/ogg" || mimeType === "audio/opus")
    return hasPrefix(bytes, [0x4f, 0x67, 0x67, 0x53]);
  if (mimeType === "audio/flac") return hasPrefix(bytes, [0x66, 0x4c, 0x61, 0x43]);
  if (mimeType === "audio/amr") return hasPrefix(bytes, [0x23, 0x21, 0x41, 0x4d, 0x52]);
  if (mimeType === "audio/silk")
    return hasPrefix(bytes, [0x23, 0x21, 0x53, 0x49, 0x4c, 0x4b, 0x5f, 0x56, 0x33]);
  if (mimeType === "audio/aac")
    return bytes.length >= 2 && bytes[0] === 0xff && (bytes[1] & 0xf6) === 0xf0;
  if (mimeType === "audio/x-ms-wma")
    return hasPrefix(bytes, [
      0x30,
      0x26,
      0xb2,
      0x75,
      0x8e,
      0x66,
      0xcf,
      0x11,
      0xa6,
      0xd9,
      0x00,
      0xaa,
      0x00,
      0x62,
      0xce,
      0x6c,
    ]);
  return false;
}

function hasValidResourceSignature(
  bytes: Buffer,
  resource: CompanionResource,
): boolean {
  if (resource.category === "image")
    return hasValidImageSignature(bytes, resource.mimeType);
  if (resource.category === "video")
    return hasValidVideoSignature(bytes, resource.mimeType);
  return hasValidAudioSignature(bytes, resource.mimeType);
}

async function resolveSafeResourcePath(request: CompanionMediaRequest): Promise<{
  path: string;
  category: CompanionResource["category"];
  mimeType: string;
  maxBytes: number;
}> {
  const source = sourceFilePath(request.sourceFilePath);
  const relativePath = companionRelativePath(request.relativePath);
  const rootName = relativePath.slice(0, relativePath.indexOf("/"));
  let sourceRealPath: string;
  let resourceRootRealPath: string;
  let candidateRealPath: string;
  try {
    sourceRealPath = await realpath(source);
    const sourceInfo = await stat(sourceRealPath);
    if (!sourceInfo.isFile()) throw safeError("聊天来源不是有效文件。");
    const sourceDirectory = dirname(sourceRealPath);
    resourceRootRealPath = await realpath(resolve(sourceDirectory, rootName));
    const resourceRootInfo = await stat(resourceRootRealPath);
    if (!resourceRootInfo.isDirectory())
      throw safeError("同级媒体文件夹不是有效目录。");
    if (!isContainedPath(sourceDirectory, resourceRootRealPath))
      throw safeError("媒体文件夹必须位于聊天文件同级目录。");
    const requestedPath = resolve(sourceDirectory, relativePath);
    let candidatePath = requestedPath;
    try {
      await stat(candidatePath);
    } catch (_error: unknown) {
      if (extname(relativePath)) throw safeError("未找到可读取的同级媒体文件。");
      const directory = dirname(requestedPath);
      const requestedBase = basename(requestedPath).toLocaleLowerCase();
      const entries = await readdir(directory, { withFileTypes: true });
      const match = entries.find((entry) => {
        if (!entry.isFile()) return false;
        const extension = extname(entry.name).toLocaleLowerCase();
        if (!extensionlessResourceExtensions.includes(extension)) return false;
        return basename(entry.name, extension).toLocaleLowerCase() === requestedBase;
      });
      if (!match) throw safeError("未找到可读取的同级媒体文件。");
      candidatePath = resolve(directory, match.name);
    }
    candidateRealPath = await realpath(candidatePath);
    if (!isContainedPath(resourceRootRealPath, candidateRealPath))
      throw safeError("媒体路径超出允许的同级资源文件夹。");
    const candidateInfo = await stat(candidateRealPath);
    if (!candidateInfo.isFile()) throw safeError("媒体路径不是文件。");
  } catch (error: unknown) {
    if (error instanceof CompanionMediaError) throw error;
    throw safeError("未找到可读取的同级媒体文件。");
  }
  const resource = resourceForPath(candidateRealPath);
  return {
    path: candidateRealPath,
    category: resource.category,
    mimeType: resource.mimeType,
    maxBytes:
      rootName.toLocaleLowerCase() === "avatar"
        ? MAX_COMPANION_AVATAR_BYTES
        : MAX_COMPANION_MEDIA_BYTES,
  };
}

export async function readCompanionImage(
  request: CompanionImageRequest,
): Promise<string> {
  const resource = await resolveSafeResourcePath(request);
  let metadata: Awaited<ReturnType<typeof stat>>;
  try {
    metadata = await stat(resource.path);
  } catch (_error: unknown) {
    throw safeError("媒体文件无法读取。");
  }
  if (!metadata.isFile()) throw safeError("媒体路径不是文件。");
  if (metadata.size > resource.maxBytes)
    throw safeError(
      resource.maxBytes === MAX_COMPANION_AVATAR_BYTES
        ? "头像文件超过 2 MiB 限制。"
        : "媒体文件超过 16 MiB 限制。",
    );
  let bytes: Buffer;
  try {
    bytes = await readFile(resource.path);
  } catch (_error: unknown) {
    throw safeError("媒体文件无法读取。");
  }
  if (!hasValidResourceSignature(bytes, resource))
    throw safeError("媒体扩展名与文件签名不匹配。");
  return `data:${resource.mimeType};base64,${bytes.toString("base64")}`;
}

export async function readCompanionMedia(
  request: CompanionMediaRequest,
): Promise<string> {
  return readCompanionImage(request);
}
