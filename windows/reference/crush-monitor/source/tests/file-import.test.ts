import { test } from "node:test";
import assert from "node:assert/strict";
import {
  cleanHtmlText,
  FileImportError,
  MAX_FILE_CHARACTERS,
  readChatFile,
  reconstructPdfChatText,
  type PdfTextPage,
} from "../src/file-import";

const textEncoder = new TextEncoder();

function makeTextFile(fileName: string, text: string): File {
  return new File([text], fileName);
}

function escapePdfString(text: string): string {
  return text
    .replaceAll("\\", "\\\\")
    .replaceAll("(", "\\(")
    .replaceAll(")", "\\)");
}

function makePdfLines(lines: string[]): ArrayBuffer {
  const stream = lines.length
    ? `BT /F1 12 Tf 72 720 Td ${lines
        .map(
          (line, index) =>
            `${index ? "0 -18 Td " : ""}(${escapePdfString(line)}) Tj`,
        )
        .join(" ")} ET`
    : "";
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] " +
      "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    `<< /Length ${textEncoder.encode(stream).length} >>\n` +
      `stream\n${stream}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let document = "%PDF-1.4\n";
  const offsets: number[] = [0];
  for (let index = 0; index < objects.length; index++) {
    offsets.push(textEncoder.encode(document).length);
    document += `${index + 1} 0 obj\n${objects[index]}\nendobj\n`;
  }
  const xrefOffset = textEncoder.encode(document).length;
  document += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const offset of offsets.slice(1)) {
    document += `${offset.toString().padStart(10, "0")} 00000 n \n`;
  }
  document +=
    `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\n` +
    `startxref\n${xrefOffset}\n%%EOF\n`;
  const bytes = textEncoder.encode(document);
  const result = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(result).set(bytes);
  return result;
}

function makePdf(text: string): ArrayBuffer {
  return makePdfLines(text ? [text] : []);
}

function makePdfFile(fileName: string, text: string): File {
  return new File([makePdf(text)], fileName, { type: "application/pdf" });
}

test("txt、md、csv、html、json 和 pdf 六种扩展名都返回统一导入结果", async () => {
  const cases = [
    ["chat.txt", "txt", "plain text"],
    ["chat.md", "md", "markdown text"],
    ["chat.csv", "csv", "Alice,Hello"],
    ["chat.html", "html", "<p>html text</p>"],
    [
      "chat.json",
      "json",
      JSON.stringify([
        {
          StrTime: "2026-09-28 20:00:00",
          is_send: true,
          text: "json text",
        },
      ]),
    ],
    ["chat.pdf", "pdf", makePdf("pdf text")],
  ] as const;

  for (const [fileName, format, value] of cases) {
    const file =
      format === "pdf"
        ? new File([value], fileName, { type: "application/pdf" })
        : makeTextFile(fileName, value);
    const result = await readChatFile(file);
    assert.equal(result.fileName, fileName);
    assert.equal(result.format, format);
    assert.ok(result.text.length > 0);
  }
});

test("HTML 清洗提取可读正文、实体并移除脚本样式", async () => {
  const html =
    "<html><head><title>不要导入</title><style>hidden</style></head>" +
    "<body><h1>聊天</h1><p>Alice：你好&nbsp;&amp; 晚安</p>" +
    "<script>alert('不要导入')</script><div>第二行</div></body></html>";
  const result = await readChatFile(makeTextFile("chat.HTML", html));

  assert.equal(result.format, "html");
  assert.equal(result.text, "聊天\nAlice：你好 & 晚安\n第二行");
  assert.equal(
    cleanHtmlText("<p>A &amp; B</p><script>secret</script>"),
    "A & B",
  );
});

test("微信导出的 HTML 内嵌聊天数组保留 is_send 和最新时间顺序", async () => {
  const html = `<!doctype html><script>
const chatMessages = [
  {"StrTime":"2026-09-27 19:42:31","is_send":false,"text":"较早消息"},
  {"StrTime":"2026-09-28 20:15:44","is_send":true,"text":"最新消息"}
];
</script>`;
  const result = await readChatFile(makeTextFile("wechat.html", html));

  assert.equal(
    result.text,
    [
      "对方",
      "2026-09-27 19:42:31",
      "较早消息",
      "我",
      "2026-09-28 20:15:44",
      "最新消息",
    ].join("\n"),
  );
});

test("微信结构化导入按 is_send 保留方向、末次昵称与头像", async () => {
  const html = `<!doctype html><script>
const chatMessages = [
  {"StrTime":"2026-09-27 19:42:31","is_send":false,"display_name":"田子夜","avatar_src":"./avatar/ziye-old.jpg","text":"较早消息"},
  {"StrTime":"2026-09-28 20:15:44","is_send":true,"display_name":"我的旧名","avatar_src":"./avatar/me.jpg","text":"最新我的消息"},
  {"StrTime":"2026-09-28 20:16:44","is_send":false,"display_name":"田子夜的新名","avatar_src":"./avatar/ziye-new.png","text":"最新对方消息"},
  {"StrTime":"2026-09-28 20:17:44","is_send":true,"display_name":"我","text":"最后一条"}
];
</script>`;
  const avatars: Record<string, string> = {
    "avatar/me.jpg": "data:image/jpeg;base64,bWU=",
    "avatar/ziye-old.jpg": "data:image/jpeg;base64,b2xk",
    "avatar/ziye-new.png": "data:image/png;base64,bmV3",
  };
  const result = await readChatFile(
    makeTextFile("wechat.html", html),
    async (relativePath: string): Promise<string | null> =>
      avatars[relativePath] ?? null,
  );

  assert.deepEqual(result.structured?.records, [
    {
      sender: "other",
      text: "较早消息",
      timestamp: "2026-09-27 19:42:31",
      kind: "text",
    },
    {
      sender: "self",
      text: "最新我的消息",
      timestamp: "2026-09-28 20:15:44",
      kind: "text",
    },
    {
      sender: "other",
      text: "最新对方消息",
      timestamp: "2026-09-28 20:16:44",
      kind: "text",
    },
    {
      sender: "self",
      text: "最后一条",
      timestamp: "2026-09-28 20:17:44",
      kind: "text",
    },
  ]);
  assert.deepEqual(result.structured?.participants, {
    self: {
      displayName: "我",
      avatarDataUrl: "data:image/jpeg;base64,bWU=",
    },
    other: {
      displayName: "田子夜的新名",
      avatarDataUrl: "data:image/png;base64,bmV3",
    },
  });
  assert.deepEqual(result.structured?.warnings, []);
});

test("微信导入保留媒体元数据、读取同级资源且不把二进制放进分析文本", async () => {
  const html = `<!doctype html><script>
const chatMessages = [
  {"StrTime":"2026-09-28 20:00:00","is_send":false,"type":"3","path":"./image/day/photo","file_type":"png","file_size":1234},
  {"StrTime":"2026-09-28 20:01:00","is_send":true,"type":"3","path":"./image/day/animated.gif","file_type":"gif","file_name":"动图"},
  {"StrTime":"2026-09-28 20:02:00","is_send":false,"type":"43","path":"./video/day/video.mp4","duration":4200,"thumb_path":"./image/day/video-thumb"},
  {"StrTime":"2026-09-28 20:03:00","is_send":true,"type":"34","path":"./audio/day/voice.amr","duration":800},
  {"StrTime":"2026-09-28 20:04:00","is_send":false,"type":"47","path":"./emoji_sticker/sticker.gif","file_type":"png"}
];
</script>`;
  const dataUrls: Record<string, string> = {
    "image/day/photo.png": "data:image/png;base64,aW1hZ2U=",
    "image/day/animated.gif": "data:image/gif;base64,YW5pbWF0ZWQ=",
    "video/day/video.mp4": "data:video/mp4;base64,dmlkZW8=",
    "audio/day/voice.amr": "data:audio/amr;base64,YXVkaW8=",
    "emoji_sticker/sticker.gif": "data:image/gif;base64,ZW1vamk=",
  };
  const requested: string[] = [];
  const result = await readChatFile(
    makeTextFile("wechat.html", html),
    async (relativePath: string): Promise<string | null> => {
      requested.push(relativePath);
      return dataUrls[relativePath] ?? null;
    },
  );

  assert.deepEqual(requested, Object.keys(dataUrls));
  assert.deepEqual(
    result.structured?.records.map((record) => record.media),
    [
      {
        kind: "image",
        sourceType: "3",
        relativePath: "image/day/photo",
        resolvedPath: "image/day/photo.png",
        thumbnailPath: null,
        mimeType: "image/png",
        fileName: null,
        fileType: "png",
        fileSize: 1234,
        durationMs: null,
        dataUrl: dataUrls["image/day/photo.png"],
      },
      {
        kind: "animated_image",
        sourceType: "3",
        relativePath: "image/day/animated.gif",
        resolvedPath: "image/day/animated.gif",
        thumbnailPath: null,
        mimeType: "image/gif",
        fileName: "动图",
        fileType: "gif",
        fileSize: null,
        durationMs: null,
        dataUrl: dataUrls["image/day/animated.gif"],
      },
      {
        kind: "video",
        sourceType: "43",
        relativePath: "video/day/video.mp4",
        resolvedPath: "video/day/video.mp4",
        thumbnailPath: "image/day/video-thumb",
        mimeType: "video/mp4",
        fileName: null,
        fileType: null,
        fileSize: null,
        durationMs: 4200,
        dataUrl: dataUrls["video/day/video.mp4"],
      },
      {
        kind: "audio",
        sourceType: "34",
        relativePath: "audio/day/voice.amr",
        resolvedPath: "audio/day/voice.amr",
        thumbnailPath: null,
        mimeType: "audio/amr",
        fileName: null,
        fileType: null,
        fileSize: null,
        durationMs: 800,
        dataUrl: dataUrls["audio/day/voice.amr"],
      },
      {
        kind: "emoji",
        sourceType: "47",
        relativePath: "emoji_sticker/sticker.gif",
        resolvedPath: "emoji_sticker/sticker.gif",
        thumbnailPath: null,
        mimeType: "image/gif",
        fileName: null,
        fileType: "png",
        fileSize: null,
        durationMs: null,
        dataUrl: dataUrls["emoji_sticker/sticker.gif"],
      },
    ],
  );
  assert.equal(
    result.text,
    [
      "对方",
      "2026-09-28 20:00:00",
      "[图片]",
      "我",
      "2026-09-28 20:01:00",
      "[图片]",
      "对方",
      "2026-09-28 20:02:00",
      "[视频]",
      "我",
      "2026-09-28 20:03:00",
      "[语音]",
      "对方",
      "2026-09-28 20:04:00",
      "[动画表情]",
    ].join("\n"),
  );
  assert.doesNotMatch(result.text, /data:/);
});

test("没有媒体读取器时仍保留媒体路径和元数据而不产生二进制", async () => {
  const records = [
    {
      StrTime: "2026-09-28 20:15:44",
      is_send: false,
      type: "43",
      path: "./video/2026-09/demo.mp4",
      duration: 1000,
    },
  ];
  const result = await readChatFile(
    makeTextFile("wechat.json", JSON.stringify(records)),
  );

  assert.equal(
    result.structured?.records[0]?.media?.relativePath,
    "video/2026-09/demo.mp4",
  );
  assert.equal(result.structured?.records[0]?.media?.dataUrl, null);
  assert.equal(result.structured?.warnings.length, 0);
});

test("结构化导入的头像失败会向前回退，缺失 is_send 不会认首位为我", async () => {
  const records = [
    {
      StrTime: "2026-09-27 19:42:31",
      sender: "wxid-other",
      display_name: "同名",
      avatar_src: "./avatar/other-old.jpg",
      is_send: false,
      text: "第一条",
    },
    {
      StrTime: "2026-09-28 20:15:44",
      sender: "wxid-self",
      display_name: "同名",
      avatar_src: "./avatar/self.jpg",
      is_send: true,
      text: "第二条",
    },
    {
      StrTime: "2026-09-28 20:16:44",
      sender: "wxid-other",
      display_name: "",
      avatar_src: "./avatar/other-new.jpg",
      is_send: false,
      text: "第三条",
    },
    {
      StrTime: "2026-09-28 20:17:44",
      sender: "wxid-unknown",
      display_name: "未知",
      text: "缺少方向",
    },
  ];
  const result = await readChatFile(
    makeTextFile("wechat.json", JSON.stringify(records)),
    async (relativePath: string): Promise<string | null> => {
      if (relativePath === "avatar/other-old.jpg")
        return "data:image/jpeg;base64,b2xk";
      if (relativePath === "avatar/self.jpg")
        return "data:image/jpeg;base64,c2VsZg==";
      throw new Error("not found");
    },
  );

  assert.equal(result.structured?.records[3]?.sender, "other");
  assert.equal(result.structured?.participants.self.displayName, "同名");
  assert.equal(result.structured?.participants.other.displayName, "未知");
  assert.equal(
    result.structured?.participants.other.avatarDataUrl,
    "data:image/jpeg;base64,b2xk",
  );
  assert.match(result.structured?.warnings.join("\n") ?? "", /is_send/);
  assert.match(result.structured?.warnings.join("\n") ?? "", /other-new/);
});

test("没有头像读取器时结构化导入仍保留昵称并完成消息读取", async () => {
  const records = [
    {
      StrTime: "2026-09-28 20:15:44",
      is_send: false,
      display_name: "田子夜",
      avatar_src: "./avatar/ziye.jpg",
      text: "你好",
    },
  ];
  const result = await readChatFile(
    makeTextFile("wechat.json", JSON.stringify(records)),
  );

  assert.equal(result.structured?.records.length, 1);
  assert.equal(result.structured?.participants.other.displayName, "田子夜");
  assert.equal(result.structured?.participants.other.avatarDataUrl, null);
});

test("有文字层的 PDF 按页提取文字", async () => {
  const result = await readChatFile(makePdfFile("chat.pdf", "Alice: PDF text"));

  assert.equal(result.format, "pdf");
  assert.equal(result.text, "Alice: PDF text");
});

test("PDF 视觉文本行保留说话人、时间和正文边界", async () => {
  const file = new File(
    [makePdfLines(["Alice", "2026-02-27 10:00:00", "Hello"])],
    "layout.pdf",
    { type: "application/pdf" },
  );
  const result = await readChatFile(file);

  assert.equal(
    result.text,
    "Alice\n2026-02-27 10:00:00\nHello",
  );
});

test("PDF 导入返回页数、时间范围和无文字层页诊断", async () => {
  const file = new File(
    [makePdfLines(["Alice", "2026-02-27 10:00:00", "Hello"])],
    "layout.pdf",
    { type: "application/pdf" },
  );
  const result = await readChatFile(file);

  assert.deepEqual(result.pdf, {
    pages: 1,
    pagesWithoutText: 0,
    firstTimestamp: "2026-02-27 10:00:00",
    lastTimestamp: "2026-02-27 10:00:00",
  });
});

test("PDF 聊天截图按视觉顺序恢复双方气泡和时间边界", () => {
  const page: PdfTextPage = {
    width: 612,
    items: [
      { text: "2026-02-27 10:00:00", x: 265, y: 700, width: 83 },
      { text: "第一条右侧消息", x: 365, y: 670, width: 90 },
      { text: "2026-02-27 10:01:00", x: 265, y: 620, width: 83 },
      { text: "第一条左侧消息", x: 102, y: 590, width: 90 },
      { text: "第二条右侧消息", x: 365, y: 540, width: 90 },
      { text: "小程序", x: 366, y: 520, width: 35 },
    ],
  };

  const result = reconstructPdfChatText([page]);

  assert.equal(
    result,
    [
      "我",
      "2026-02-27 10:00:00",
      "第一条右侧消息",
      "对方",
      "2026-02-27 10:01:00",
      "第一条左侧消息",
      "我",
      "2026-02-27 10:01:00",
      "第二条右侧消息 小程序",
    ].join("\n"),
  );
});

test("无文字层的 PDF 抛出可理解的中文错误", async () => {
  await assert.rejects(
    readChatFile(makePdfFile("scan.pdf", "")),
    (error: unknown) =>
      error instanceof FileImportError &&
      /扫描|文字层|可提取/.test(error.message),
  );
});

test("未知扩展名被拒绝", async () => {
  await assert.rejects(
    readChatFile(makeTextFile("chat.docx", "not supported")),
    (error: unknown) =>
      error instanceof FileImportError && /不支持|扩展名/.test(error.message),
  );
});

test("超过 250000 个字符时拒绝导入且不截断", async () => {
  const tooLong = "a".repeat(MAX_FILE_CHARACTERS + 1);

  await assert.rejects(
    readChatFile(makeTextFile("too-long.txt", tooLong)),
    (error: unknown) =>
      error instanceof FileImportError && /250000/.test(error.message),
  );
});

test("恰好达到字符上限时保留完整文本", async () => {
  const text = "a".repeat(MAX_FILE_CHARACTERS);
  const result = await readChatFile(makeTextFile("limit.md", text));

  assert.equal(result.text, text);
});

test("文件读取失败时抛出中文错误", async () => {
  const unreadable = new File([""], "broken.txt");
  unreadable.text = async (): Promise<string> => {
    throw new Error("disk failure");
  };

  await assert.rejects(
    readChatFile(unreadable),
    (error: unknown) =>
      error instanceof FileImportError && /读取文件|失败/.test(error.message),
  );
});
