import {
  createParticipantProfiles,
  type ParticipantProfiles,
} from "../shared/participants";
import type { ChatMedia, ChatMediaKind } from "../shared/types";

export const MAX_FILE_CHARACTERS = 250000;

export type ChatFileFormat =
  | "txt"
  | "md"
  | "csv"
  | "html"
  | "json"
  | "pdf";

export type PdfImportSummary = {
  pages: number;
  pagesWithoutText: number;
  firstTimestamp: string | null;
  lastTimestamp: string | null;
};

export type FileImportResult = {
  text: string;
  fileName: string;
  format: ChatFileFormat;
  pdf?: PdfImportSummary;
  structured?: StructuredChatImport;
};

export type ImportedChatRecord = {
  sender: "self" | "other";
  text: string;
  timestamp: string | null;
  kind: "text" | "unreadable";
  media?: ChatMedia;
};

export type StructuredChatImport = {
  records: readonly ImportedChatRecord[];
  participants: ParticipantProfiles;
  warnings: readonly string[];
};

export type CompanionAvatarReader = (
  relativePath: string,
) => Promise<string | null>;

export type CompanionMediaReader = CompanionAvatarReader;

export class FileImportError extends Error {
  public readonly cause: unknown;

  public constructor(message: string, cause?: unknown) {
    super(message);
    this.name = "FileImportError";
    this.cause = cause;
  }
}

const htmlEntities: Record<string, string> = {
  amp: "&",
  apos: "'",
  gt: ">",
  lt: "<",
  nbsp: " ",
  quot: '"',
};

const ignoredHtmlTags =
  /<(head|script|style|noscript|template)\b[^>]*>[\s\S]*?<\/\s*\1\s*>/gi;
const htmlComment = /<!--[\s\S]*?-->/g;
const htmlBreak = /<\s*br\s*\/?>/gi;
const htmlBlockEndTags: readonly string[] = [
  "address",
  "article",
  "aside",
  "blockquote",
  "div",
  "dl",
  "dt",
  "dd",
  "fieldset",
  "figcaption",
  "figure",
  "footer",
  "form",
  "h[1-6]",
  "header",
  "hr",
  "li",
  "main",
  "nav",
  "ol",
  "p",
  "pre",
  "section",
  "table",
  "tbody",
  "td",
  "tfoot",
  "th",
  "thead",
  "tr",
  "ul",
];
const htmlBlockEnd = new RegExp(
  `<\\s*\\/\\s*(?:${htmlBlockEndTags.join("|")})\\s*>`,
  "gi",
);
const htmlTag = /<[^>]*>/g;

function decodeHtmlEntity(entity: string): string {
  const value = entity.slice(1, -1);
  if (value in htmlEntities) return htmlEntities[value];
  const hexadecimal = value.match(/^#x([\da-f]+)$/i);
  const decimal = value.match(/^#(\d+)$/);
  const codePoint = hexadecimal
    ? Number.parseInt(hexadecimal[1], 16)
    : decimal
      ? Number.parseInt(decimal[1], 10)
      : Number.NaN;
  if (!Number.isInteger(codePoint) || codePoint < 0 || codePoint > 0x10ffff)
    return entity;
  return String.fromCodePoint(codePoint);
}

function decodeHtmlEntities(text: string): string {
  return text.replace(
    /&(?:#x[\da-f]+|#\d+|[a-z][a-z\d]+);/gi,
    decodeHtmlEntity,
  );
}

export function cleanHtmlText(html: string): string {
  const withBreaks = html
    .replace(ignoredHtmlTags, "")
    .replace(htmlComment, "")
    .replace(htmlBreak, "\n")
    .replace(htmlBlockEnd, "\n");
  const withoutTags = withBreaks.replace(htmlTag, "");
  return decodeHtmlEntities(withoutTags)
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function getFileFormat(fileName: string): ChatFileFormat {
  const dot = fileName.lastIndexOf(".");
  const extension = dot >= 0 ? fileName.slice(dot + 1).toLowerCase() : "";
  if (
    extension === "txt" ||
    extension === "md" ||
    extension === "csv" ||
    extension === "html" ||
    extension === "json" ||
    extension === "pdf"
  )
    return extension;
  throw new FileImportError(
    `不支持的文件类型“${fileName}”，仅支持 .txt、.md、.csv、.html、.json 和 .pdf。`,
  );
}

function ensureWithinLimit(text: string, fileName: string): void {
  if (Array.from(text).length > MAX_FILE_CHARACTERS)
    throw new FileImportError(
      `文件“${fileName}”超过 250000 个字符上限，请拆分文件后重试。`,
    );
}

function readHtmlText(html: string): string {
  if (typeof DOMParser === "undefined") return cleanHtmlText(html);
  const document = new DOMParser().parseFromString(html, "text/html");
  return cleanHtmlText(document.body?.innerHTML ?? html);
}

type WeChatExportRecord = {
  StrTime?: unknown;
  avatar_src?: unknown;
  display_name?: unknown;
  duration?: unknown;
  file_name?: unknown;
  file_size?: unknown;
  file_type?: unknown;
  is_send?: unknown;
  path?: unknown;
  sender?: unknown;
  text?: unknown;
  thumb_path?: unknown;
  desc?: unknown;
  type?: unknown;
};

function extractJsonArray(source: string, start: number): string | null {
  let depth = 0;
  let quoted = false;
  let escaped = false;
  for (let index = start; index < source.length; index++) {
    const character = source[index];
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === '"') quoted = false;
      continue;
    }
    if (character === '"') {
      quoted = true;
      continue;
    }
    if (character === "[") depth++;
    if (character === "]") {
      depth--;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  return null;
}

function isWeChatExportRecord(value: unknown): value is WeChatExportRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseWeChatExportRecords(
  source: string,
  fileName: string,
): WeChatExportRecord[] | null {
  const lowerName = fileName.toLowerCase();
  const json = lowerName.endsWith(".json")
    ? source.trim()
    : (() => {
        const marker = source.indexOf("const chatMessages");
        if (marker < 0) return null;
        const start = source.indexOf("[", marker);
        return start < 0 ? null : extractJsonArray(source, start);
      })();
  if (!json) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(json) as unknown;
  } catch (error: unknown) {
    throw new FileImportError(
      `微信导出文件“${fileName}”中的聊天数据不是合法 JSON。`,
      error,
    );
  }
  if (!Array.isArray(parsed))
    throw new FileImportError(`微信导出文件“${fileName}”没有聊天数组。`);
  const records = parsed.filter(isWeChatExportRecord);
  if (records.length !== parsed.length)
    throw new FileImportError(`微信导出文件“${fileName}”包含无法识别的消息记录。`);
  return records;
}

function optionalRecordText(
  record: WeChatExportRecord,
  key:
    | "StrTime"
    | "avatar_src"
    | "display_name"
    | "file_name"
    | "file_type"
    | "path"
    | "sender"
    | "text"
    | "thumb_path"
    | "desc"
    | "type",
): string {
  const value = record[key];
  return typeof value === "string" ? value.trim() : "";
}

function attachmentText(type: string): string {
  const labels: Record<string, string> = {
    "3": "[图片]",
    "34": "[语音]",
    "43": "[视频]",
    "47": "[动画表情]",
    "49": "[文件]",
  };
  return labels[type] || "[附件]";
}

function senderByExplicitDirection(
  records: readonly WeChatExportRecord[],
): Map<string, "self" | "other"> {
  const directions = new Map<string, "self" | "other">();
  for (const record of records) {
    const sender = optionalRecordText(record, "sender");
    if (!sender || typeof record.is_send !== "boolean") continue;
    directions.set(sender, record.is_send ? "self" : "other");
  }
  return directions;
}

function resolveRecordSender(
  record: WeChatExportRecord,
  directions: ReadonlyMap<string, "self" | "other">,
  warnings: string[],
): "self" | "other" {
  if (typeof record.is_send === "boolean")
    return record.is_send ? "self" : "other";
  const sender = optionalRecordText(record, "sender");
  const knownDirection = sender ? directions.get(sender) : undefined;
  warnings.push(
    sender
      ? `记录“${sender}”缺少 is_send，已按已知发送者或对方兼容处理。`
      : "记录缺少 is_send，已按对方兼容处理。",
  );
  return knownDirection ?? "other";
}

const mediaKinds: Readonly<Record<string, ChatMediaKind>> = {
  "34": "audio",
  "43": "video",
  "47": "emoji",
};

const mediaMimeTypes: Readonly<Record<string, string>> = {
  aac: "audio/aac",
  amr: "audio/amr",
  flac: "audio/flac",
  gif: "image/gif",
  jpeg: "image/jpeg",
  jpg: "image/jpeg",
  m4a: "audio/mp4",
  m4v: "video/x-m4v",
  mov: "video/quicktime",
  mp3: "audio/mpeg",
  mp4: "video/mp4",
  oga: "audio/ogg",
  ogg: "audio/ogg",
  opus: "audio/opus",
  png: "image/png",
  silk: "audio/silk",
  wav: "audio/wav",
  webm: "video/webm",
  webp: "image/webp",
};

function normalizedCompanionPath(value: string): string | null {
  const normalized = value.replaceAll("\\", "/").replace(/^(?:\.\/)+/, "");
  const segments = normalized.split("/");
  if (
    !normalized ||
    normalized.startsWith("/") ||
    normalized.startsWith("//") ||
    /^[a-z]:/i.test(normalized) ||
    segments.some((segment) => !segment || segment === "." || segment === "..")
  )
    return null;
  return normalized;
}

function optionalRecordNumber(
  record: WeChatExportRecord,
  key: "duration" | "file_size",
): number | null {
  const value = record[key];
  if (typeof value === "number" && Number.isFinite(value) && value >= 0)
    return value;
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function mediaMimeType(
  kind: ChatMediaKind,
  path: string,
  fileType: string,
): string {
  const extension = path.split(".").at(-1)?.toLowerCase() || "";
  if (extension && mediaMimeTypes[extension]) return mediaMimeTypes[extension];
  const normalizedType = fileType.toLowerCase().replace(/^\./, "");
  if (normalizedType && mediaMimeTypes[normalizedType])
    return mediaMimeTypes[normalizedType];
  if (kind === "video") return "video/*";
  if (kind === "audio") return "audio/*";
  return "image/*";
}

function mediaFileName(explicitName: string): string | null {
  return explicitName || null;
}

type MediaReadCache = Map<string, Promise<string | null>>;

function mediaCandidatePaths(
  relativePath: string,
  kind: ChatMediaKind,
  fileType: string,
): readonly string[] {
  if (relativePath.includes(".")) return [relativePath];
  const normalizedType = fileType.toLowerCase().replace(/^\./, "");
  const extensions =
    kind === "video"
      ? ["mp4", "webm", "mov", "mkv", "avi", "3gp"]
      : kind === "audio"
        ? ["amr", "mp3", "m4a", "wav", "ogg", "opus", "aac", "silk"]
        : ["png", "jpg", "jpeg", "gif", "webp", "bmp"];
  const candidates = normalizedType
    ? [normalizedType, ...extensions]
    : extensions;
  return [...new Set(candidates)].map((extension) =>
    `${relativePath}.${extension}`,
  );
}

async function readMediaDataUrl(
  paths: readonly string[],
  reader: CompanionMediaReader | undefined,
  cache: MediaReadCache,
  warnings: string[],
): Promise<{ dataUrl: string | null; resolvedPath: string | null }> {
  if (!reader) return { dataUrl: null, resolvedPath: null };
  for (const path of paths) {
    let pending = cache.get(path);
    if (!pending) {
      pending = reader(path);
      cache.set(path, pending);
    }
    try {
      const dataUrl = await pending;
      if (dataUrl) return { dataUrl, resolvedPath: path };
    } catch (_error: unknown) {
      // 逐个尝试导出器可能使用的实际扩展名。
    }
  }
  warnings.push(`媒体“${paths[0]}”无法读取，已保留附件占位。`);
  return { dataUrl: null, resolvedPath: null };
}

async function recordMedia(
  record: WeChatExportRecord,
  reader: CompanionMediaReader | undefined,
  cache: MediaReadCache,
  warnings: string[],
): Promise<ChatMedia | null> {
  const sourceType = optionalRecordText(record, "type");
  const kind = mediaKinds[sourceType];
  const relativePath = normalizedCompanionPath(
    optionalRecordText(record, "path"),
  );
  if (!relativePath) return null;
  const fileType = optionalRecordText(record, "file_type") || null;
  const resolvedKind =
    sourceType === "3" &&
    (relativePath.toLowerCase().endsWith(".gif") || fileType === "gif")
      ? "animated_image"
      : sourceType === "3"
        ? "image"
        : kind;
  if (!resolvedKind) return null;
  const thumbnailPath =
    normalizedCompanionPath(optionalRecordText(record, "thumb_path")) ?? null;
  const paths = [
    ...mediaCandidatePaths(relativePath, resolvedKind, fileType || ""),
    ...(thumbnailPath && resolvedKind !== "video"
      ? mediaCandidatePaths(thumbnailPath, resolvedKind, fileType || "")
      : []),
  ];
  const loaded = await readMediaDataUrl(
    paths,
    reader,
    cache,
    warnings,
  );
  return {
    kind: resolvedKind,
    sourceType,
    relativePath,
    resolvedPath: loaded.resolvedPath,
    thumbnailPath,
    mimeType: mediaMimeType(
      resolvedKind,
      loaded.resolvedPath ?? relativePath,
      fileType || "",
    ),
    fileName: mediaFileName(optionalRecordText(record, "file_name")),
    fileType,
    fileSize: optionalRecordNumber(record, "file_size"),
    durationMs: optionalRecordNumber(record, "duration"),
    dataUrl: loaded.dataUrl,
  };
}

async function recordBody(
  record: WeChatExportRecord,
  reader: CompanionMediaReader | undefined,
  cache: MediaReadCache,
  warnings: string[],
): Promise<{
  text: string;
  kind: "text" | "unreadable";
  media?: ChatMedia;
} | null> {
  const text =
    optionalRecordText(record, "text") || optionalRecordText(record, "desc");
  const type = optionalRecordText(record, "type");
  const media = await recordMedia(record, reader, cache, warnings);
  if (text)
    return {
      text,
      kind: "text",
      ...(media ? { media } : {}),
    };
  if (type)
    return {
      text: attachmentText(type),
      kind: "unreadable",
      ...(media ? { media } : {}),
    };
  return null;
}

function normalizedAvatarPath(record: WeChatExportRecord): string | null {
  const raw = optionalRecordText(record, "avatar_src");
  if (!raw) return null;
  return raw.replaceAll("\\", "/").replace(/^\.\//, "") || null;
}

async function readLatestAvatar(
  candidates: readonly string[],
  reader: CompanionAvatarReader | undefined,
  warnings: string[],
): Promise<string | null> {
  if (!reader) return null;
  for (let index = candidates.length - 1; index >= 0; index--) {
    const path = candidates[index];
    try {
      const dataUrl = await reader(path);
      if (dataUrl) return dataUrl;
      warnings.push(`头像“${path}”无法读取，已尝试更早的导出头像。`);
    } catch (_error: unknown) {
      warnings.push(`头像“${path}”无法读取，已尝试更早的导出头像。`);
    }
  }
  return null;
}

async function createStructuredWeChatImport(
  records: readonly WeChatExportRecord[],
  reader: CompanionAvatarReader | undefined,
): Promise<StructuredChatImport> {
  const warnings: string[] = [];
  const directions = senderByExplicitDirection(records);
  const parsed: ImportedChatRecord[] = [];
  const mediaCache: MediaReadCache = new Map();
  const names: Record<"self" | "other", string> = { self: "", other: "" };
  const avatars: Record<"self" | "other", string[]> = { self: [], other: [] };
  for (const record of records) {
    const sender = resolveRecordSender(record, directions, warnings);
    const displayName = optionalRecordText(record, "display_name");
    if (displayName) names[sender] = displayName;
    const avatar = normalizedAvatarPath(record);
    if (avatar) avatars[sender].push(avatar);
    const body = await recordBody(record, reader, mediaCache, warnings);
    if (!body) continue;
    parsed.push({
      sender,
      text: body.text,
      timestamp: optionalRecordText(record, "StrTime") || null,
      kind: body.kind,
      ...(body.media ? { media: body.media } : {}),
    });
  }
  const baseProfiles = createParticipantProfiles(names.self, names.other);
  const selfAvatar = await readLatestAvatar(
    avatars.self,
    reader,
    warnings,
  );
  const otherAvatar = await readLatestAvatar(
    avatars.other,
    reader,
    warnings,
  );
  return {
    records: parsed,
    participants: {
      self: { ...baseProfiles.self, avatarDataUrl: selfAvatar },
      other: { ...baseProfiles.other, avatarDataUrl: otherAvatar },
    },
    warnings: [...new Set(warnings)],
  };
}

function renderStructuredWeChatText(records: readonly ImportedChatRecord[]): string {
  const lines: string[] = [];
  for (const record of records) {
    if (!record.timestamp || !record.text) continue;
    lines.push(record.sender === "self" ? "我" : "对方", record.timestamp, record.text);
  }
  return lines.join("\n").trim();
}

async function readWeChatExport(
  source: string,
  fileName: string,
  reader: CompanionAvatarReader | undefined,
): Promise<{ text: string; structured: StructuredChatImport } | null> {
  const records = parseWeChatExportRecords(source, fileName);
  if (!records) return null;
  const structured = await createStructuredWeChatImport(records, reader);
  return { text: renderStructuredWeChatText(structured.records), structured };
}

export type PdfTextItem = {
  text: string;
  x: number | null;
  y: number | null;
  width: number;
};

export type PdfTextPage = {
  width: number;
  items: readonly PdfTextItem[];
};

type PdfContentItem = PdfTextItem & {
  hasEOL: boolean;
};

type PdfVisualLine = {
  text: string;
  x: number | null;
  width: number;
  y: number | null;
};

type PdfChatSide = "left" | "right" | "center";

type PdfChatMessage = {
  side: Exclude<PdfChatSide, "center">;
  timestamp: string;
  text: string;
  lastText: string;
  lastY: number;
};

const pdfDateTime =
  /^\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?$/;
const pdfMediaDuration = /^\d{1,2}:\d{2}\s*\/\s*\d{1,2}:\d{2}$/;
const pdfCardMeta = /^(?:小程序|微信红包|红包|转账|聊天转账|已收款|已领取)$/;

function normalizePdfText(text: string): string {
  return text
    .normalize("NFKC")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+/g, " ")
    .trim();
}

function getPdfTextItem(item: unknown): PdfContentItem | null {
  if (
    typeof item === "object" &&
    item !== null &&
    "str" in item &&
    typeof item.str === "string"
  ) {
    const transform =
      "transform" in item && Array.isArray(item.transform)
        ? item.transform
        : [];
    return {
      text: item.str,
      x: typeof transform[4] === "number" ? transform[4] : null,
      y: typeof transform[5] === "number" ? transform[5] : null,
      width:
        "width" in item && typeof item.width === "number" ? item.width : 0,
      hasEOL: "hasEOL" in item && item.hasEOL === true,
    };
  }
  return null;
}

function buildPdfVisualLines(
  items: readonly PdfTextItem[],
): PdfVisualLine[] {
  const ordered = items
    .filter((item) => Boolean(item.text.trim()) && item.y !== null)
    .map((item) => ({ ...item }))
    .sort((left, right) => {
      const yDifference = (right.y ?? 0) - (left.y ?? 0);
      if (yDifference !== 0) return yDifference;
      return (left.x ?? 0) - (right.x ?? 0);
    });
  const lines: PdfVisualLine[] = [];
  for (const item of ordered) {
    const text = normalizePdfText(item.text);
    if (!text) continue;
    const right = item.x === null ? null : item.x + item.width;
    const previous = lines.at(-1);
    if (
      previous &&
      previous.y !== null &&
      item.y !== null &&
      Math.abs(previous.y - item.y) <= 2
    ) {
      const leftEdge =
        previous.x === null || item.x === null
          ? null
          : Math.min(previous.x, item.x);
      const rightEdge =
        previous.x === null || right === null
          ? null
          : Math.max(previous.x + previous.width, right);
      lines[lines.length - 1] = {
        text: normalizePdfText(`${previous.text}${text}`),
        x: leftEdge,
        width:
          leftEdge === null || rightEdge === null
            ? 0
            : Math.max(0, rightEdge - leftEdge),
        y: previous.y,
      };
      continue;
    }
    lines.push({ text, x: item.x, width: item.width, y: item.y });
  }
  return lines;
}

function readPdfPageText(items: readonly PdfTextItem[]): string {
  return buildPdfVisualLines(items)
    .map((line) => line.text)
    .filter(Boolean)
    .join("\n")
    .trim();
}

function getPdfLineCenter(line: PdfVisualLine): number | null {
  return line.x === null ? null : line.x + line.width / 2;
}

function getPdfChatSide(line: PdfVisualLine, pageWidth: number): PdfChatSide {
  const center = getPdfLineCenter(line);
  if (center === null || Math.abs(center - pageWidth / 2) <= 60)
    return "center";
  return center > pageWidth / 2 ? "right" : "left";
}

function isPdfCardMeta(text: string): boolean {
  return pdfCardMeta.test(text);
}

function canAppendPdfLine(
  current: PdfChatMessage,
  line: PdfVisualLine,
  side: Exclude<PdfChatSide, "center">,
): boolean {
  if (current.side !== side || line.y === null) return false;
  const gap = current.lastY - line.y;
  if (gap < 0 || gap > 240) return false;
  return gap <= 24 || isPdfCardMeta(line.text);
}

function appendPdfLine(current: PdfChatMessage, line: PdfVisualLine): PdfChatMessage {
  const separator =
    isPdfCardMeta(line.text) || isPdfCardMeta(current.lastText) ? " " : "";
  return {
    ...current,
    text: normalizePdfText(`${current.text}${separator}${line.text}`),
    lastText: line.text,
    lastY: line.y ?? current.lastY,
  };
}

function looksLikePdfChat(pages: readonly PdfTextPage[]): boolean {
  let dateCount = 0;
  let hasLeft = false;
  let hasRight = false;
  for (const page of pages) {
    for (const line of buildPdfVisualLines(page.items)) {
      if (pdfDateTime.test(line.text)) dateCount++;
      const side = getPdfChatSide(line, page.width);
      if (side === "left") hasLeft = true;
      if (side === "right") hasRight = true;
    }
  }
  return dateCount >= 2 && hasLeft && hasRight;
}

export function reconstructPdfChatText(pages: readonly PdfTextPage[]): string {
  const messages: PdfChatMessage[] = [];
  let current: PdfChatMessage | null = null;
  let timestamp: string | null = null;
  const flush = (): void => {
    if (current?.text.trim()) messages.push(current);
    current = null;
  };

  pages.forEach((page, pageIndex) => {
    const lines = buildPdfVisualLines(page.items);
    lines.forEach((line) => {
      if (!line.text) return;
      if (pdfDateTime.test(line.text)) {
        flush();
        timestamp = line.text;
        return;
      }
      const side = getPdfChatSide(line, page.width);
      const isPageTitle =
        pageIndex === 0 &&
        timestamp === null &&
        side === "center" &&
        (line.y ?? 0) > 700;
      if (isPageTitle || pdfMediaDuration.test(line.text)) return;
      if (timestamp === null) return;
      if (side === "center") {
        flush();
        messages.push({
          side: "left",
          timestamp,
          text: line.text,
          lastText: line.text,
          lastY: line.y ?? 0,
        });
        return;
      }
      if (current && canAppendPdfLine(current, line, side)) {
        current = appendPdfLine(current, line);
        return;
      }
      flush();
      current = {
        side,
        timestamp,
        text: line.text,
        lastText: line.text,
        lastY: line.y ?? 0,
      };
    });
  });
  flush();
  return messages
    .map((message) =>
      [message.side === "right" ? "我" : "对方", message.timestamp, message.text].join(
        "\n",
      ),
    )
    .join("\n")
    .trim();
}

type PdfJsModule = typeof import("pdfjs-dist");

async function loadPdfJs(): Promise<PdfJsModule> {
  if (typeof window === "undefined")
    return import("pdfjs-dist/legacy/build/pdf.mjs") as Promise<PdfJsModule>;
  const pdfjs = await import("pdfjs-dist");
  const workerModule = await import("pdfjs-dist/build/pdf.worker.min.mjs?url");
  // Worker 文件由 Vite 从依赖中打包，浏览器不会访问远程地址。
  pdfjs.GlobalWorkerOptions.workerSrc = workerModule.default;
  return pdfjs;
}

async function readPdfText(
  file: File,
): Promise<{ text: string; summary: PdfImportSummary }> {
  try {
    const data = new Uint8Array(await file.arrayBuffer());
    const pdfjs = await loadPdfJs();
    const loadingTask = pdfjs.getDocument({
      data,
      useSystemFonts: true,
      verbosity: 0,
    });
    const pdfDocument = await loadingTask.promise;
    const pages: PdfTextPage[] = [];
    const fallbackPages: string[] = [];
    try {
      for (
        let pageNumber = 1;
        pageNumber <= pdfDocument.numPages;
        pageNumber++
      ) {
        const page = await pdfDocument.getPage(pageNumber);
        const content = await page.getTextContent();
        const items = content.items
          .map(getPdfTextItem)
          .filter((item): item is PdfContentItem => item !== null);
        const viewport = page.getViewport({ scale: 1 });
        pages.push({ width: viewport.width, items });
        fallbackPages.push(readPdfPageText(items));
      }
    } finally {
      await pdfDocument.destroy();
    }
    const timestamps = pages.flatMap((page) =>
      buildPdfVisualLines(page.items)
        .map((line) => line.text)
        .filter((line) => pdfDateTime.test(line)),
    );
    const summary: PdfImportSummary = {
      pages: pages.length,
      pagesWithoutText: pages.filter(
        (page) => buildPdfVisualLines(page.items).length === 0,
      ).length,
      firstTimestamp: timestamps[0] ?? null,
      lastTimestamp: timestamps.at(-1) ?? null,
    };
    const text = looksLikePdfChat(pages)
      ? reconstructPdfChatText(pages)
      : fallbackPages.join("\n").trim();
    if (!text)
      throw new FileImportError(
        `PDF 文件“${file.name}”没有可提取的文字层，可能是扫描型 PDF，请先进行 OCR 后重试。`,
      );
    return { text, summary };
  } catch (error: unknown) {
    if (error instanceof FileImportError) throw error;
    throw new FileImportError(
      `PDF 文件“${file.name}”解析失败，请确认文件未损坏后重试。`,
      error,
    );
  }
}

export async function readChatFile(
  file: File,
  reader?: CompanionAvatarReader,
): Promise<FileImportResult> {
  const format = getFileFormat(file.name);
  try {
    let text: string;
    let pdf: PdfImportSummary | undefined;
    if (format === "pdf") {
      const result = await readPdfText(file);
      text = result.text;
      pdf = result.summary;
    } else {
      const source = await file.text();
      const weChat =
        format === "html" || format === "json"
          ? await readWeChatExport(source, file.name, reader)
          : null;
      if (format === "json" && weChat === null)
        throw new FileImportError(
          `JSON 文件“${file.name}”不是支持的微信聊天导出格式。`,
        );
      text =
        weChat?.text ?? (format === "html" ? readHtmlText(source) : source);
      if (weChat) {
        ensureWithinLimit(text, file.name);
        return {
          text,
          fileName: file.name,
          format,
          structured: weChat.structured,
        };
      }
    }
    ensureWithinLimit(text, file.name);
    return { text, fileName: file.name, format, ...(pdf ? { pdf } : {}) };
  } catch (error: unknown) {
    if (error instanceof FileImportError) throw error;
    throw new FileImportError(
      `读取文件“${file.name}”失败，请确认文件未损坏且仍可访问后重试。`,
      error,
    );
  }
}
