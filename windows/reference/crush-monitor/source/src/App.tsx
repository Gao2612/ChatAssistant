import { useVirtualizer } from "@tanstack/react-virtual";
import {
  loadConversation,
  saveConversation,
  type SavedConversation,
} from "./storage";
import { INTENTS, topIntents } from "../shared/intents";
import { REPLY_RATINGS, replyRating } from "../shared/ratings";
import { EMOTIONS, topEmotions } from "../shared/labels";
import {
  useEffect,
  useRef,
  useState,
  useCallback,
  type ChangeEvent,
  type ReactNode,
} from "react";
import {
  Heart,
  MoreHorizontal,
  X,
  ArrowUpRight,
  RotateCcw,
  MessageCircle,
  Settings2,
  Plus,
  ArrowRight,
  Send,
  Check,
  FileDown,
} from "lucide-react";
import { parseChat, toMessages, mergeMessages } from "../shared/parser";
import {
  RUBRIC,
  ACTIONS,
  RELATIONS,
  statusLabel,
  meanQuality,
  type ChatMedia,
  type Message,
  type Relation,
} from "../shared/types";
import { exampleText } from "../shared/fixtures";
import {
  analysisProgressPercent,
  useAnalysis,
  type AnalysisProgress,
  type AnalysisStatus,
} from "./useAnalysis";
import { currentAction } from "../shared/action";
import {
  readChatFile,
  type PdfImportSummary,
  type StructuredChatImport,
} from "./file-import";
import { buildAnalysisReportHtml } from "./export-report";
import {
  createParticipantProfiles,
  mergeParticipantProfiles,
  swapParticipantProfiles,
  type ParticipantProfile,
  type ParticipantProfiles,
} from "../shared/participants";

type UpstreamStatus = {
  available: boolean;
  hasUpdate: boolean;
  latestMessage: string | null;
  compareUrl: string | null;
  changedFiles: { path: string; status: string }[];
};

type ApiProvider =
  | "typesafe"
  | "vercel"
  | "openrouter"
  | "deepseek"
  | "codex";

type RuntimeConfig = {
  configured: boolean;
  provider?: ApiProvider;
  model?: string;
  hasApiKey: boolean;
  requiresApiKey: boolean;
};

type ApiHealth = {
  configured: boolean;
  provider?: ApiProvider;
  model?: string;
  error?: string;
};

type CodexRateLimit = {
  usedPercent: number | null;
  resetsAt: number | null;
  windowDurationMins: number | null;
};

type CodexStatus = {
  available: boolean;
  loggedIn: boolean;
  authMode: "chatgpt" | null;
  planType: string | null;
  rateLimits: CodexRateLimit[];
  error?: string;
};

type CodexModel = {
  id: string;
  displayName: string;
  description: string;
  isDefault: boolean;
};

const API_PROVIDERS: ReadonlyArray<{
  value: ApiProvider;
  label: string;
}> = [
  { value: "typesafe", label: "TypeSafe" },
  { value: "vercel", label: "Vercel AI Gateway" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "codex", label: "ChatGPT Plus（Codex）" },
];

const SELF_LABELS = new Set(["我", "本人", "me", "i"]);

function defaultParticipants(): ParticipantProfiles {
  return createParticipantProfiles("我", "Crush");
}

type ParticipantAvatarProps = {
  profile: ParticipantProfile;
  className: string;
};

function avatarFallback(profile: ParticipantProfile): string {
  const name = profile.displayName.trim();
  return name && name !== "__self_absent__" ? Array.from(name)[0] || "我" : "我";
}

function ParticipantAvatar({
  profile,
  className,
}: ParticipantAvatarProps): ReactNode {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [profile.avatarDataUrl]);
  const src = failed ? null : profile.avatarDataUrl;
  return (
    <div className={className} aria-label={`${profile.displayName || "参与者"}头像`}>
      {src ? (
        <img
          className="participant-avatar-image"
          src={src}
          alt=""
          onError={() => setFailed(true)}
        />
      ) : (
        <span aria-hidden="true">{avatarFallback(profile)}</span>
      )}
    </div>
  );
}

function isLikelySelfSpeaker(name: string): boolean {
  return SELF_LABELS.has(name.trim().toLowerCase());
}

type SelfSelection = {
  speaker: string;
  notice: string;
};

function selectSelfSpeaker(
  names: string[],
  existingSelf: string,
): SelfSelection {
  if (existingSelf) return { speaker: existingSelf, notice: "" };
  const explicitSelf = names.find(isLikelySelfSpeaker);
  if (explicitSelf) return { speaker: explicitSelf, notice: "" };
  if (names.length === 2)
    return {
      speaker: names[0],
      notice: "已自动按首个说话人分析，可在设置中交换身份",
    };
  if (names.length === 1)
    return {
      speaker: "__self_absent__",
      notice: "只识别到一位说话人，已按对方处理，可在设置中交换身份",
    };
  if (names.length > 2)
    return {
      speaker: names[0],
      notice: "识别到多位说话人，已按首个说话人分析，可在设置中交换身份",
    };
  return {
    speaker: "__self_absent__",
    notice: "没有识别到说话人，请检查聊天格式后重试",
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error("本地服务返回了无法识别的配置格式。");
  return value as Record<string, unknown>;
}

function asProvider(value: unknown): ApiProvider | undefined {
  if (value === undefined || value === null) return undefined;
  if (
    value === "typesafe" ||
    value === "vercel" ||
    value === "openrouter" ||
    value === "deepseek" ||
    value === "codex"
  )
    return value;
  throw new Error("本地服务返回了未知的 API 平台。");
}

function optionalText(
  record: Record<string, unknown>,
  key: string,
): string | undefined {
  const value = record[key];
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "string")
    throw new Error(`本地服务返回的 ${key} 不是文本。`);
  return value;
}

function parseRuntimeConfig(value: unknown): RuntimeConfig {
  const record = asRecord(value);
  if (typeof record.configured !== "boolean")
    throw new Error("本地服务返回的配置状态无效。");
  if (typeof record.hasApiKey !== "boolean")
    throw new Error("本地服务没有返回 API Key 状态。");
  if (typeof record.requiresApiKey !== "boolean")
    throw new Error("本地服务没有返回平台认证方式。");
  return {
    configured: record.configured,
    provider: asProvider(record.provider),
    model: optionalText(record, "model"),
    hasApiKey: record.hasApiKey,
    requiresApiKey: record.requiresApiKey,
  };
}

function parseApiHealth(value: unknown): ApiHealth {
  const record = asRecord(value);
  if (typeof record.configured !== "boolean")
    throw new Error("本地服务返回的健康状态无效。");
  return {
    configured: record.configured,
    provider: asProvider(record.provider),
    model: optionalText(record, "model"),
    error: optionalText(record, "error"),
  };
}

function nullableNumber(value: unknown, field: string): number | null {
  if (value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value))
    throw new Error(`本地服务返回的 ${field} 不是有效数字。`);
  return value;
}

function parseCodexStatus(value: unknown): CodexStatus {
  const record = asRecord(value);
  if (
    typeof record.available !== "boolean" ||
    typeof record.loggedIn !== "boolean"
  )
    throw new Error("本地服务返回的 Codex 状态无效。");
  if (record.authMode !== null && record.authMode !== "chatgpt")
    throw new Error("本地服务返回了未知的 Codex 登录方式。");
  if (record.planType !== null && typeof record.planType !== "string")
    throw new Error("本地服务返回了未知的 Codex 套餐。");
  if (!Array.isArray(record.rateLimits))
    throw new Error("本地服务没有返回 Codex 额度状态。");
  const rateLimits = record.rateLimits.map((value) => {
    const limit = asRecord(value);
    return {
      usedPercent: nullableNumber(limit.usedPercent, "usedPercent"),
      resetsAt: nullableNumber(limit.resetsAt, "resetsAt"),
      windowDurationMins: nullableNumber(
        limit.windowDurationMins,
        "windowDurationMins",
      ),
    };
  });
  return {
    available: record.available,
    loggedIn: record.loggedIn,
    authMode: record.authMode,
    planType: record.planType,
    rateLimits,
    error: optionalText(record, "error"),
  };
}

function parseCodexModels(value: unknown): CodexModel[] {
  const record = asRecord(value);
  if (!Array.isArray(record.models))
    throw new Error("本地服务没有返回 Codex 模型目录。");
  return record.models.map((value) => {
    const model = asRecord(value);
    if (
      typeof model.id !== "string" ||
      !model.id ||
      typeof model.displayName !== "string" ||
      !model.displayName ||
      typeof model.description !== "string" ||
      typeof model.isDefault !== "boolean"
    )
      throw new Error("本地服务返回的 Codex 模型信息无效。");
    return {
      id: model.id,
      displayName: model.displayName,
      description: model.description,
      isDefault: model.isDefault,
    };
  });
}

function codexPlanLabel(status: CodexStatus): string {
  if (status.authMode !== "chatgpt" || !status.loggedIn) return "登录未知";
  if (status.planType?.toLowerCase() === "plus") return "ChatGPT · Plus";
  return `ChatGPT · ${status.planType || "套餐未知"}`;
}

function rateLimitLabel(limit: CodexRateLimit): string {
  const used =
    limit.usedPercent === null
      ? "已用未知"
      : `已用 ${Math.round(limit.usedPercent)}%`;
  const window =
    limit.windowDurationMins === null
      ? "窗口未知"
      : limit.windowDurationMins < 1_440
        ? `${Math.round(limit.windowDurationMins / 60)} 小时窗口`
        : `${Math.round(limit.windowDurationMins / 1_440)} 天窗口`;
  const reset =
    limit.resetsAt === null
      ? "重置时间未知"
      : `重置 ${new Date(limit.resetsAt * 1_000).toLocaleString("zh-CN")}`;
  return `${used} · ${window} · ${reset}`;
}

function responseError(value: unknown): string | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return undefined;
  const error = (value as Record<string, unknown>).error;
  return typeof error === "string" && error.trim() ? error : undefined;
}

async function requestJson(
  url: string,
  init: RequestInit,
  fallback: string,
): Promise<unknown> {
  const response = await fetch(url, init);
  const text = await response.text();
  let body: unknown;
  if (text.trim()) {
    try {
      body = JSON.parse(text) as unknown;
    } catch (error: unknown) {
      if (!(error instanceof SyntaxError)) throw error;
    }
  }
  if (!response.ok) {
    const detail = responseError(body);
    throw new Error(`${detail || fallback}（HTTP ${response.status}）`);
  }
  if (body === undefined)
    throw new Error(`${fallback}：本地服务返回为空，请重试。`);
  return body;
}

function operationError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : fallback;
}

function analysisPhaseLabel(
  progress: AnalysisProgress,
  status: AnalysisStatus,
): string {
  switch (progress.phase) {
    case "preparing":
      return "准备分析任务";
    case "overview":
      return "分析整体概览";
    case "batches":
      return "分析消息批次";
    case "finalizing":
      return "汇总最终建议";
    case "complete":
      return "分析完成";
    case "error":
      return "分析未完成";
    case "cancelled":
      return "分析已停止";
    case "idle":
      if (status === "cancelled") return "分析已停止";
      if (status === "error") return "分析未完成";
      if (status === "complete") return "分析完成";
      return "分析已暂停";
  }
}

function analysisProgressSummary(progress: AnalysisProgress): string {
  const percent = analysisProgressPercent(progress);
  if (percent === null) return "正在计算真实任务总数…";
  const done = Math.min(progress.total, Math.max(0, progress.done));
  return `已完成 ${done} 批，剩余 ${progress.total - done} 批 · ${percent}%`;
}

function AnalysisProgressBar({
  progress,
}: {
  progress: AnalysisProgress;
}): ReactNode {
  const percent = analysisProgressPercent(progress);
  const done =
    percent === null
      ? undefined
      : Math.min(progress.total, Math.max(0, progress.done));
  const indeterminate = percent === null;
  return (
    <div
      className={`analysis-progress-bar${indeterminate ? " indeterminate" : ""}`}
      role="progressbar"
      aria-label="聊天分析进度"
      aria-valuemin={0}
      aria-valuemax={indeterminate ? undefined : progress.total}
      aria-valuenow={done}
      aria-valuetext={
        indeterminate
          ? "正在计算真实任务总数"
          : `${done}/${progress.total} 批，${percent}%`
      }
    >
      <span
        style={indeterminate ? undefined : { width: `${percent}%` }}
      />
    </div>
  );
}

type AnalysisProgressDetailsProps = {
  progress: AnalysisProgress;
  status: AnalysisStatus;
  error: string;
  onCancel: () => void;
  onRetry: () => void;
};

function AnalysisProgressDetails({
  progress,
  status,
  error,
  onCancel,
  onRetry,
}: AnalysisProgressDetailsProps): ReactNode {
  const showBar = status === "loading" || progress.total > 0;
  const showRetry = status === "error" || status === "cancelled";
  return (
    <section className="analysis-progress-details" aria-live="polite">
      <div className="analysis-progress-heading">
        <strong>{analysisPhaseLabel(progress, status)}</strong>
        {progress.failed > 0 && (
          <span className="analysis-progress-failed">
            失败 {progress.failed} 批
          </span>
        )}
      </div>
      {showBar && <AnalysisProgressBar progress={progress} />}
      <p className="analysis-progress-summary">
        {analysisProgressSummary(progress)}
      </p>
      {error && (
        <p className="analysis-progress-error" role="alert">
          当前错误：{error}
        </p>
      )}
      <div className="analysis-progress-actions">
        {status === "loading" && (
          <button type="button" onClick={onCancel}>
            停止
          </button>
        )}
        {showRetry && (
          <button type="button" onClick={onRetry}>
            {status === "cancelled" ? "继续分析" : "重试"}
          </button>
        )}
      </div>
    </section>
  );
}

function mediaLabel(media: ChatMedia): string {
  if (media.kind === "image") return "图片";
  if (media.kind === "animated_image") return "动画表情";
  if (media.kind === "video") return "视频";
  if (media.kind === "audio") return "语音";
  return "表情";
}

function MediaPreview({ media }: { media: ChatMedia }): ReactNode {
  if (!media.dataUrl)
    return (
      <div className="message-media message-media-missing" role="img">
        <span>{mediaLabel(media)}</span>
        <small>同目录媒体文件暂时无法读取</small>
      </div>
    );
  if (media.kind === "video")
    return (
      <video
        className="message-media message-video"
        controls
        preload="metadata"
        src={media.dataUrl}
        aria-label={media.fileName || "视频"}
      />
    );
  if (media.kind === "audio")
    return (
      <audio
        className="message-audio"
        controls
        preload="metadata"
        src={media.dataUrl}
        aria-label={media.fileName || "语音"}
      />
    );
  return (
    <img
      className="message-media message-image"
      src={media.dataUrl}
      alt={media.fileName || mediaLabel(media)}
      loading="lazy"
    />
  );
}

function Modal({
  title,
  children,
  close,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const old = document.activeElement as HTMLElement;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.key === "Tab") {
        const nodes = Array.from(
          ref.current?.querySelectorAll<HTMLElement>(
            "button:not(:disabled),select,textarea,input",
          ) || [],
        );
        if (e.shiftKey && document.activeElement === nodes[0]) {
          e.preventDefault();
          nodes.at(-1)?.focus();
        } else if (!e.shiftKey && document.activeElement === nodes.at(-1)) {
          e.preventDefault();
          nodes[0]?.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      old?.focus();
    };
  }, []);
  return (
    <div
      className="overlay"
      onMouseDown={(e) => e.target === e.currentTarget && close()}
    >
      <div
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="modal"
      >
        <header>
          <h2>{title}</h2>
          <button className="icon" aria-label="关闭" onClick={close}>
            <X size={20} />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
export default function App() {
  const a = useAnalysis();
  const [messages, setMessages] = useState<Message[]>([]),
    [input, setInput] = useState(""),
    [relation, setRelation] = useState<Relation>("crush");
  const [participants, setParticipants] =
    useState<ParticipantProfiles>(defaultParticipants);
  const self = participants.self.displayName;
  const other = participants.other.displayName;
  const [importingFile, setImportingFile] = useState(false),
    [importedFileName, setImportedFileName] = useState(""),
    [settings, setSettings] = useState(false),
    [detail, setDetail] = useState<string | null>(null),
    [notice, setNotice] = useState("");
  const [overlap, setOverlap] = useState<Message[] | null>(null);
  const [ready, setReady] = useState(false),
    [storageError, setStorageError] = useState("");
  const [upstream, setUpstream] = useState<UpstreamStatus | null>(null);
  const [apiProvider, setApiProvider] =
    useState<ApiProvider>("typesafe");
  const [apiKey, setApiKey] = useState("");
  const [deepseekModel, setDeepseekModel] = useState("");
  const [apiConfig, setApiConfig] = useState<RuntimeConfig | null>(null);
  const [apiHealth, setApiHealth] = useState<ApiHealth | null>(null);
  const [codexStatus, setCodexStatus] = useState<CodexStatus | null>(null);
  const [codexStatusLoading, setCodexStatusLoading] = useState(false);
  const [codexModel, setCodexModel] = useState("");
  const [codexModels, setCodexModels] = useState<CodexModel[]>([]);
  const [codexModelsLoading, setCodexModelsLoading] = useState(false);
  const [codexModelsError, setCodexModelsError] = useState("");
  const [configLoading, setConfigLoading] = useState(false);
  const [configSaving, setConfigSaving] = useState(false);
  const [configError, setConfigError] = useState("");
  const [configNotice, setConfigNotice] = useState("");
  const [exporting, setExporting] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const virtual = useVirtualizer({
    count: messages.length,
    getScrollElement: () => scroller.current,
    estimateSize: () => 150,
    getItemKey: useCallback((i: number) => messages[i].id, [messages]),
    overscan: 8,
    anchorTo: "end",
    followOnAppend: true,
    scrollEndThreshold: 100,
  });
  useEffect(() => {
    let live = true;
    loadConversation()
      .then((saved) => {
        if (!live) return;
        if (saved) {
          setMessages(saved.messages);
          setParticipants(saved.participants);
          setRelation(saved.relation);
          a.restore(saved);
        }
        setReady(true);
      })
      .catch(() => {
        if (live) {
          setStorageError(
            "本机记录读取失败，请检查浏览器存储权限。为避免覆盖旧记录，暂不自动保存。",
          );
          setReady(true);
        }
      });
    return () => {
      live = false;
    };
  }, []);
  useEffect(() => {
    let live = true;
    fetch("/api/upstream", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("upstream check failed");
        return (await response.json()) as UpstreamStatus;
      })
      .then((status) => {
        if (live) setUpstream(status);
      })
      .catch(() => {
        if (live) setUpstream(null);
      });
    return () => {
      live = false;
    };
  }, []);
  const loadRuntimeConfig = useCallback(async (): Promise<void> => {
    setConfigLoading(true);
    setConfigError("");
    try {
      const body = await requestJson(
        "/api/config",
        { method: "GET", cache: "no-store" },
        "读取内置配置失败",
      );
      const config = parseRuntimeConfig(body);
      setApiConfig(config);
      setApiKey("");
      if (config.provider) setApiProvider(config.provider);
      setDeepseekModel(
        config.provider === "deepseek" ? config.model || "" : "",
      );
      setCodexModel(
        config.provider === "codex" && config.model !== "codex-default"
          ? config.model || ""
          : "",
      );
    } catch (error: unknown) {
      setApiConfig(null);
      setConfigError(operationError(error, "读取内置配置失败，请重试。"));
    } finally {
      setConfigLoading(false);
    }
  }, []);
  const refreshHealth = useCallback(async (): Promise<boolean> => {
    try {
      const body = await requestJson(
        "/api/health",
        { method: "GET", cache: "no-store" },
        "刷新健康状态失败",
      );
      setApiHealth(parseApiHealth(body));
      return true;
    } catch (error: unknown) {
      setApiHealth(null);
      setConfigError(operationError(error, "刷新健康状态失败，请重试。"));
      return false;
    }
  }, []);
  const loadCodexStatus = useCallback(async (): Promise<boolean> => {
    setCodexStatusLoading(true);
    try {
      const body = await requestJson(
        "/api/codex/status",
        { method: "GET", cache: "no-store" },
        "读取 ChatGPT Plus 状态失败",
      );
      setCodexStatus(parseCodexStatus(body));
      return true;
    } catch (error: unknown) {
      setCodexStatus(null);
      setConfigError(
        operationError(error, "读取 ChatGPT Plus 状态失败，请重试。"),
      );
      return false;
    } finally {
      setCodexStatusLoading(false);
    }
  }, []);
  const loadCodexModels = useCallback(async (): Promise<boolean> => {
    setCodexModelsLoading(true);
    setCodexModelsError("");
    try {
      const body = await requestJson(
        "/api/codex/models",
        { method: "GET", cache: "no-store" },
        "读取 Codex 模型目录失败",
      );
      setCodexModels(parseCodexModels(body));
      return true;
    } catch (error: unknown) {
      setCodexModelsError(
        operationError(error, "读取 Codex 模型目录失败，请重试。"),
      );
      return false;
    } finally {
      setCodexModelsLoading(false);
    }
  }, []);
  useEffect(() => {
    if (!settings) return;
    setConfigNotice("");
    void loadRuntimeConfig();
  }, [settings, loadRuntimeConfig]);
  useEffect(() => {
    if (!settings || apiProvider !== "codex") {
      setCodexStatus(null);
      setCodexStatusLoading(false);
      setCodexModels([]);
      setCodexModelsError("");
      setCodexModelsLoading(false);
      return;
    }
    void loadCodexStatus();
    void loadCodexModels();
  }, [settings, apiProvider, loadCodexStatus, loadCodexModels]);
  const pendingSave = useRef<SavedConversation | null>(null),
    saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null),
    lastSavedMessages = useRef<Message[] | null>(null);
  const flushSave = () => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = null;
    void saveConversation(pendingSave.current).catch(() =>
      setStorageError(
        "本机保存失败，可能存储空间不足。当前页面仍可使用，请勿刷新以免丢失未保存记录。",
      ),
    );
  };
  useEffect(() => {
    if (!ready || storageError) return;
    pendingSave.current = messages.length
      ? {
          schema: 2,
          rubric: RUBRIC,
          messages,
          participants,
          relation,
          lines: a.lines,
          events: a.events,
          overview: a.overview,
          trend: a.trend,
          analyzedCount: a.analyzedCount,
          completed: a.status === "complete",
        }
      : null;
    if (lastSavedMessages.current !== messages || a.status !== "loading") {
      lastSavedMessages.current = messages;
      flushSave();
    } else if (!saveTimer.current)
      saveTimer.current = setTimeout(flushSave, 750);
  }, [
    ready,
    messages,
    participants,
    relation,
    a.lines,
    a.events,
    a.overview,
    a.trend,
    a.analyzedCount,
    a.status,
  ]);
  useEffect(() => {
    const flush = () => {
      if (saveTimer.current) flushSave();
    };
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", flush);
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, []);
  const stay = useRef(true);
  useEffect(() => {
    if (messages.length && stay.current)
      virtual.scrollToIndex(messages.length - 1, { align: "end" });
  }, [messages.length]);
  const busy = a.status === "loading",
    ov = a.overview,
    value = ov?.affinity.value,
    quality = meanQuality(messages, a.lines);
  const actionOverview = currentAction(ov, messages, a.overviewFresh);
  const actionLabel = actionOverview
    ? ACTIONS[actionOverview.action]?.label
    : busy
      ? "正在更新…"
      : messages.length
        ? "建议待更新"
        : "等你导入聊天";
  const last = a.trend.at(-1),
    previous = a.trend.at(-2);
  const delta =
    a.status === "complete" && last?.value != null && previous?.value != null
      ? last.value - previous.value
      : null;
  function add(
    ms: Message[],
    mode: "auto" | "append" | "skip" = "auto",
  ): Message[] | undefined {
    const m = mergeMessages(messages, ms, mode);
    if (m.ambiguous) {
      setOverlap(ms);
      return undefined;
    }
    if (!m.added) {
      setNotice("没有新增消息，这段已经分析过了。");
      setInput("");
      return undefined;
    }
    setNotice("");
    setMessages(m.messages);
    setInput("");
    return m.messages;
  }
  function analyzeChat(): void {
    if (!input.trim()) {
      if (messages.length) void a.run(messages, relation);
      return;
    }
    const prepared = prepare(input, undefined);
    if (prepared) void a.run(prepared, relation);
  }
  function prepare(
    text: string,
    pdf: PdfImportSummary | undefined,
  ): Message[] | undefined {
    if (!text.trim()) return undefined;
    if (text.length > 250000) {
      setNotice("这次粘贴超过25万字符，请分几次追加；历史记录不会被截断。");
      return undefined;
    }
    const p = parseChat(text);
    const names = [...new Set(p.messages.map((x) => x.speaker))];
    if (!p.messages.length) {
      setNotice(p.warnings[0] || "没有可以分析的聊天内容，请检查文件后重试");
      return undefined;
    }
    const selection = selectSelfSpeaker(names, messages.length ? self : "");
    const selectedSelf = selection.speaker;
    const selectedOther =
      selectedSelf === "__self_absent__"
        ? names[0] || "Crush"
        : names.find((name) => name !== selectedSelf) || "Crush";
    setParticipants((current) =>
      mergeParticipantProfiles(
        current,
        createParticipantProfiles(
          selectedSelf === "__self_absent__" ? "我" : selectedSelf,
          selectedOther,
        ),
      ),
    );
    const added = add(toMessages(p.messages, selectedSelf));
    if (!added) return undefined;
    const pdfNotice = pdf
      ? [
          `PDF 已读取 ${pdf.pages} 页，识别到的最后时间：${pdf.lastTimestamp || "未识别"}`,
          "PDF 本身不包含该时间之后的消息；如要最新内容，请重新导出 PDF，或导入同目录 HTML/JSON",
          pdf.pagesWithoutText > 0
            ? `${pdf.pagesWithoutText} 页没有文字层，可能遗漏图片型页面`
            : "",
        ]
          .filter(Boolean)
          .join("；")
      : "";
    setNotice(
      [selection.notice, p.warnings[0] || "", pdfNotice]
        .filter(Boolean)
        .join("；"),
    );
    return added;
  }

  function importStructuredChat(
    structured: StructuredChatImport,
  ): void {
    if (!structured.records.length) {
      setNotice("文件中没有可以分析的聊天内容，请检查文件后重试。");
      return;
    }
    setParticipants((current) =>
      mergeParticipantProfiles(current, structured.participants),
    );
    const added = add(
      structured.records.map((record) => ({
        id: crypto.randomUUID(),
        sender: record.sender,
        text: record.text,
        timestamp: record.timestamp,
        kind: record.kind,
        ...(record.media ? { media: record.media } : {}),
      })),
    );
    if (!added) return;
    const lastTimestamp = structured.records.at(-1)?.timestamp || "未识别";
    setNotice(
      [
        `已读取 ${structured.records.length} 条微信记录，最后时间：${lastTimestamp}`,
        ...structured.warnings,
      ]
        .filter(Boolean)
        .join("；"),
    );
  }

  async function importSelectedFile(file: File): Promise<void> {
    setImportingFile(true);
    setNotice("");
    try {
      const desktop = window.crushDesktop;
      const reader = desktop
        ? (relativePath: string): Promise<string | null> =>
            desktop.readCompanionImage(file, relativePath)
        : undefined;
      const result = await readChatFile(file, reader);
      setImportedFileName(result.fileName);
      if (result.structured) {
        importStructuredChat(result.structured);
      } else {
        prepare(result.text, result.pdf);
      }
      if (!result.structured && (result.format === "html" || result.format === "json")) {
        const parsed = parseChat(result.text);
        setNotice(
          `已读取 ${parsed.messages.length} 条微信记录，最后时间：${parsed.messages.at(-1)?.timestamp || "未识别"}`,
        );
      }
    } catch (error: unknown) {
      setImportedFileName("");
      setNotice(operationError(error, "文件读取失败，请检查文件格式后重试。"));
    } finally {
      setImportingFile(false);
    }
  }
  async function exportAnalysisPdf(): Promise<void> {
    if (!messages.length) {
      setNotice("暂无聊天记录，导入或粘贴聊天后再导出。");
      return;
    }
    setExporting(true);
    setNotice("");
    try {
      const html = buildAnalysisReportHtml({
        messages,
        lines: a.lines,
        overview: a.overview,
        participants,
        relation,
        generatedAt: new Date().toLocaleString("zh-CN"),
      });
      const fileName = `crush-analysis-${new Date()
        .toISOString()
        .slice(0, 10)}.pdf`;
      const desktop = window.crushDesktop;
      if (!desktop) {
        const popup = window.open("", "_blank");
        if (!popup)
          throw new Error("打印窗口被浏览器拦截，请允许弹出窗口后重试。");
        popup.document.write(html);
        popup.document.close();
        popup.focus();
        popup.print();
        setNotice("已打开打印窗口，请选择“另存为 PDF”。");
        return;
      }
      const result = await desktop.exportPdf({ html, fileName });
      setNotice(
        result.saved
          ? `PDF 已导出：${result.path || fileName}`
          : "已取消导出。",
      );
    } catch (error: unknown) {
      setNotice(operationError(error, "PDF 导出失败，请重试。"));
    } finally {
      setExporting(false);
    }
  }
  function handleFileChange(event: ChangeEvent<HTMLInputElement>): void {
    const file = event.currentTarget.files?.[0];
    const inputElement = event.currentTarget;
    if (!file) return;
    void importSelectedFile(file).finally(() => {
      inputElement.value = "";
    });
  }
  function clear() {
    a.reset();
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = null;
    pendingSave.current = null;
    void saveConversation(null)
      .then(() => setStorageError(""))
      .catch(() => setStorageError("本机记录删除失败，请重试清空。"));
    setMessages([]);
    setInput("");
    setParticipants(defaultParticipants());
    setNotice("");
    setImportedFileName("");
    setSettings(false);
    setDetail(null);
  }
  async function saveRuntimeConfiguration(): Promise<void> {
    if (apiProvider !== "codex" && !apiKey.trim()) {
      setConfigError(
        "请输入新的 API Key；已配置的 Key 不会回显，修改配置时请重新输入。",
      );
      return;
    }
    setConfigSaving(true);
    setConfigError("");
    setConfigNotice("");
    const payload:
      | { provider: "codex"; codexModel?: string }
      | {
          provider: Exclude<ApiProvider, "codex">;
          apiKey: string;
          deepseekModel?: string;
        } =
      apiProvider === "codex"
        ? {
            provider: "codex",
            ...(codexModel.trim()
              ? { codexModel: codexModel.trim() }
              : {}),
          }
        : { provider: apiProvider, apiKey: apiKey.trim() };
    if (
      payload.provider === "deepseek" &&
      deepseekModel.trim()
    )
      payload.deepseekModel = deepseekModel.trim();
    try {
      await requestJson(
        "/api/config",
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
        "保存内置配置失败",
      );
      setApiKey("");
      a.clearError();
      await loadRuntimeConfig();
      const healthRefreshed = await refreshHealth();
      const codexRefreshed =
        payload.provider !== "codex" ||
        ((await loadCodexStatus()) && (await loadCodexModels()));
      setConfigNotice(
        healthRefreshed && codexRefreshed
          ? "配置已保存，健康状态已刷新。"
          : "配置已保存，但健康状态刷新失败，请重试。",
      );
    } catch (error: unknown) {
      setConfigError(operationError(error, "保存内置配置失败，请重试。"));
    } finally {
      setConfigSaving(false);
    }
  }
  const chosen = messages.find((m) => m.id === detail),
    result = detail ? a.lines[detail] : undefined;
  return (
    <main className="app">
      {upstream?.hasUpdate && (
        <aside className="upstream-notice" role="status">
          <div>
            <strong>原仓库有更新</strong>
            <span>
              {upstream.latestMessage || "上游 main 分支已有新提交"}
              {upstream.changedFiles.length > 0
                ? ` · ${upstream.changedFiles.length} 个文件变化`
                : ""}
            </span>
          </div>
          {upstream.compareUrl && (
            <a
              href={upstream.compareUrl}
              target="_blank"
              rel="noreferrer"
            >
              查看变更 <ArrowUpRight size={15} />
            </a>
          )}
        </aside>
      )}
      <div className="workspace">
        <section className="wechat" aria-label="微信聊天">
          <nav className="chat-rail" aria-label="聊天工具">
            <ParticipantAvatar
              profile={participants.self}
              className="rail-avatar"
            />
            <button
              className="rail-active"
              aria-label="滚动到最新聊天"
              onClick={() => {
                stay.current = true;
                if (messages.length)
                  virtual.scrollToIndex(messages.length - 1, { align: "end" });
              }}
            >
              <MessageCircle size={23} />
            </button>
            <button
              className="rail-settings"
              aria-label="聊天设置"
              onClick={() => setSettings(true)}
            >
              <Settings2 size={22} />
            </button>
          </nav>
          <header className="chat-head">
            <div className="contact-title">
              <h2>{messages.length ? other : "微信聊天"}</h2>
              <span>{RELATIONS[relation]}</span>
            </div>
            <button
              className="header-affinity"
              onClick={() => setDetail("overview")}
              aria-label="查看好感度详情"
            >
              <span>好感度</span>
              <strong key={value} className="affinity-number">
                {value ?? "—"}
              </strong>
              {value != null && (
                <span className="affinity-hearts" aria-hidden="true">
                  <Heart className="affinity-heart heart-one" size={12} />
                  <Heart className="affinity-heart heart-two" size={9} />
                  <Heart className="affinity-heart heart-three" size={7} />
                </span>
              )}
              {delta != null && delta !== 0 && (
                <small>
                  {delta > 0 ? "+" : ""}
                  {delta}
                </small>
              )}
            </button>
            <div className="header-tools">
              <button
                className="icon"
                aria-label="新聊天"
                title="新聊天"
                onClick={() => setDetail("clear")}
              >
                <Plus size={20} />
              </button>
              <button
                className="icon"
                aria-label="导出分析 PDF"
                title="导出分析 PDF"
                disabled={!messages.length || exporting}
                onClick={() => void exportAnalysisPdf()}
              >
                <FileDown size={20} />
              </button>
              <button
                className="icon"
                aria-label="更多聊天设置"
                onClick={() => setSettings(true)}
              >
                <MoreHorizontal size={24} />
              </button>
            </div>
          </header>
          <div
            ref={scroller}
            className="chat-scroll"
            onScroll={(e) => {
              const el = e.currentTarget;
              stay.current =
                el.scrollHeight - el.scrollTop - el.clientHeight < 100;
            }}
          >
            {!messages.length ? (
              <div className="empty">
                <h2>粘贴聊天记录</h2>
                <p>支持微信、QQ 复制记录及 WhatsApp 文本导出</p>
                <button
                  className="text-button"
                  onClick={() => prepare(exampleText(0), undefined)}
                >
                  用一段示例试试 <ArrowUpRight size={16} />
                </button>
              </div>
            ) : (
              <div
                style={{
                  height: virtual.getTotalSize(),
                  position: "relative",
                  width: "100%",
                }}
              >
                {virtual.getVirtualItems().map((row) => {
                  const i = row.index,
                    m = messages[i];
                  const r = a.lines[m.id];
                  const profile =
                    m.sender === "self"
                      ? participants.self
                      : participants.other;

                  return (
                    <div
                      key={m.id}
                      data-index={row.index}
                      ref={virtual.measureElement}
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "100%",
                        transform: `translateY(${row.start}px)`,
                      }}
                      id={`message-${m.id}`}
                      className={`message ${m.sender}`}
                    >
                      {(i === 0 || m.timestamp !== messages[i - 1].timestamp) &&
                        m.timestamp && (
                          <div className="timestamp">
                            {m.timestamp.replace(/^\d{4}年/, "")}
                          </div>
                        )}
                      <div className="message-row">
                        <ParticipantAvatar
                          className={`avatar ${m.sender === "self" ? "mine" : ""}`}
                          profile={profile}
                        />
                        <div className="message-content">
                          <div className={`bubble${m.media ? " bubble-media" : ""}`}>
                            {m.media && <MediaPreview media={m.media} />}
                            {(!m.media || !m.media.dataUrl) && <span>{m.text}</span>}
                          </div>
                          {m.kind === "text" && (
                            <div className={`message-tags ${m.sender}`}>
                              {r?.skipped ? (
                                <span className="pending-tag">{r.skipped}</span>
                              ) : m.sender === "other" ? (
                                <>
                                  <div className="analysis-row emotion-row">
                                    <span className="analysis-row-label">
                                      情绪
                                    </span>
                                    {r?.emotions ? (
                                      topEmotions(r.emotions).map((emotion) => (
                                        <button
                                          key={emotion.key}
                                          className={`emotion-tag emotion-${emotion.key}`}
                                          onClick={() => setDetail(m.id)}
                                          aria-label={`${emotion.label} ${emotion.percent}，查看情绪分析：${m.text}`}
                                        >
                                          <span>{emotion.label}</span>
                                          <b>{emotion.percent}</b>
                                        </button>
                                      ))
                                    ) : (
                                      <button
                                        className="pending-tag"
                                        disabled={busy}
                                        onClick={() =>
                                          a.run(messages, relation)
                                        }
                                      >
                                        {busy ? "分析中" : "分析情绪"}
                                      </button>
                                    )}
                                  </div>
                                  <div className="analysis-row intent-row">
                                    <span className="analysis-row-label">
                                      意图
                                    </span>
                                    {r?.intents ? (
                                      topIntents(r.intents).map((intent) => (
                                        <button
                                          key={intent.key}
                                          className="intent-tag"
                                          onClick={() => setDetail(m.id)}
                                          aria-label={`${intent.label} ${intent.percent}，查看意图分析：${m.text}`}
                                        >
                                          <span>{intent.label}</span>
                                          <b>{intent.percent}</b>
                                        </button>
                                      ))
                                    ) : (
                                      <button
                                        className="pending-tag"
                                        disabled={busy}
                                        onClick={() =>
                                          a.run(messages, relation)
                                        }
                                      >
                                        {busy ? "分析中" : "分析意图"}
                                      </button>
                                    )}
                                  </div>
                                </>
                              ) : r ? (
                                <button
                                  className="reply-tag"
                                  onClick={() => setDetail(m.id)}
                                  aria-label={`查看回复评价：${m.text}`}
                                >
                                  <span>回复评级：</span>
                                  <b>
                                    {replyRating(r.score.value)?.label ??
                                      "待判断"}
                                  </b>
                                </button>
                              ) : (
                                <button
                                  className="pending-tag"
                                  disabled={busy}
                                  onClick={() => a.run(messages, relation)}
                                >
                                  {busy ? "分析中" : "评价回复"}
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
          <div className="chat-insights">
            <button
              className="reply-summary"
              onClick={() => setDetail("performance")}
            >
              <span>我的发挥</span>
              <strong>{replyRating(quality)?.label ?? "—"}</strong>
              {quality != null && <span>{quality}分</span>}
            </button>
            <span className="insight-divider" />
            <button
              className="action-summary"
              onClick={() => setDetail("action")}
            >
              <span>下一步</span>
              <strong>{actionLabel}</strong>
              <ArrowRight size={14} />
            </button>
          </div>
          <div className="composer">
            <textarea
              aria-label="粘贴聊天记录"
              disabled={!ready}
              placeholder={
                messages.length
                  ? "粘贴新的聊天，自动合并重复记录"
                  : "在这里粘贴聊天记录…"
              }
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onPaste={(e) => {
                const t = e.clipboardData.getData("text");
                if (t.trim()) {
                  e.preventDefault();
                  setInput(t);
                  prepare(t, undefined);
                }
              }}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter")
                  analyzeChat();
              }}
            />
            <div className="composer-bottom">
              <div className="composer-feedback">
                <span role="status">{storageError || notice}</span>{" "}
                <div className="analysis-status" aria-live="polite">
                  {busy ? (
                    <div className="analysis-progress">
                      <span className="working" />
                      <span>
                        {analysisPhaseLabel(a.progress, a.status)}
                      </span>
                      <AnalysisProgressBar progress={a.progress} />
                      <span>{analysisProgressSummary(a.progress)}</span>
                      <button type="button" onClick={a.cancel}>
                        停止
                      </button>
                    </div>
                  ) : a.status === "error" ? (
                    <>
                      <span>
                        分析未完成
                        {a.progress.failed > 0
                          ? `（失败 ${a.progress.failed} 批）`
                          : ""}
                      </span>
                      <button
                        type="button"
                        onClick={() => void a.run(messages, relation)}
                      >
                        <RotateCcw size={14} />
                        重试
                      </button>
                    </>
                  ) : a.status === "complete" ? (
                    <span className="completed">
                      <Check size={14} />
                      分析完成
                      <button onClick={() => setDetail("overview")}>
                        娱乐参考
                      </button>
                    </span>
                  ) : a.status === "cancelled" ? (
                    <>
                      <span>
                        分析已停止
                        {a.progress.total > 0
                          ? ` · ${analysisProgressSummary(a.progress)}`
                          : ""}
                      </span>
                      <button
                        type="button"
                        onClick={() => void a.run(messages, relation)}
                      >
                        继续分析
                      </button>
                    </>
                  ) : messages.length ? (
                    <>
                      <span>分析已暂停</span>
                      <button
                        type="button"
                        onClick={() => void a.run(messages, relation)}
                      >
                        继续分析
                      </button>
                    </>
                  ) : null}
                </div>
                {a.error && <span className="error">{a.error}</span>}
              </div>
              <div className="composer-actions">
                <input
                  ref={fileInput}
                  className="file-input"
                  type="file"
                  accept=".txt,.md,.csv,.html,.json,.pdf"
                  onChange={handleFileChange}
                />
                {importedFileName && (
                  <span
                    className="import-file-name"
                    role="status"
                    title={importedFileName}
                  >
                    已导入：{importedFileName}
                  </span>
                )}
                <button
                  className="file-import"
                  disabled={!ready || importingFile}
                  onClick={() => fileInput.current?.click()}
                >
                  {importingFile ? "读取中…" : "导入文件"}
                </button>
                <button
                  className="send"
                  disabled={!ready || (!input.trim() && !messages.length)}
                  onClick={() => void analyzeChat()}
                >
                  <Send size={15} />
                  分析聊天
                </button>
              </div>
            </div>
          </div>
        </section>
      </div>
      {settings && (
        <Modal title="聊天设置" close={() => setSettings(false)}>
          <section
            className="settings-section"
            aria-labelledby="api-settings-title"
          >
            <h3 id="api-settings-title">内置 API 配置</h3>
            <label className="field">
              API 平台
              <select
                name="api-provider"
                aria-label="API 平台"
                value={apiProvider}
                disabled={configLoading || configSaving}
                onChange={(e) => setApiProvider(e.target.value as ApiProvider)}
              >
                {API_PROVIDERS.map((provider) => (
                  <option value={provider.value} key={provider.value}>
                    {provider.label}
                  </option>
                ))}
              </select>
            </label>
            {apiProvider !== "codex" && (
              <label className="field">
                API Key（不会回显）
                <input
                  name="api-key"
                  type="password"
                  autoComplete="new-password"
                  value={apiKey}
                  disabled={configLoading || configSaving}
                  placeholder={
                    apiConfig?.hasApiKey
                      ? "已配置，输入新 Key 可替换"
                      : "输入 API Key"
                  }
                  onChange={(e) => setApiKey(e.target.value)}
                />
                <small className="field-hint">
                  当前状态：{apiConfig?.hasApiKey ? "已配置" : "未配置"}
                </small>
              </label>
            )}
            {apiProvider === "codex" && (
              <div className="codex-status-card" role="status">
                <p className="field-hint">
                  复用本机 Codex 的 ChatGPT Plus 登录，不需要单独填写 API
                  Key。
                </p>
                {codexStatusLoading ? (
                  <p>正在读取 ChatGPT Plus 登录和额度…</p>
                ) : codexStatus ? (
                  <>
                    <strong>{codexPlanLabel(codexStatus)}</strong>
                    {codexStatus.error ? (
                      <p className="error">{codexStatus.error}</p>
                    ) : codexStatus.rateLimits.length ? (
                      <ul>
                        {codexStatus.rateLimits.map((limit, index) => (
                          <li key={`${limit.windowDurationMins}-${index}`}>
                            额度 {index + 1}：{rateLimitLabel(limit)}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p>额度：未知 · 重置时间未知</p>
                    )}
                  </>
                ) : (
                  <p>登录、套餐和额度：未知</p>
                )}
                <div className="codex-model-field">
                  <label className="field">
                    Codex 模型
                    <select
                      name="codex-model"
                      aria-label="Codex 模型"
                      value={codexModel}
                      disabled={
                        configLoading ||
                        configSaving ||
                        codexModelsLoading ||
                        (!codexModels.length && !codexModelsError)
                      }
                      onChange={(e) => setCodexModel(e.target.value)}
                    >
                      <option value="">
                        跟随 Codex 默认模型
                        {codexModels.find((model) => model.isDefault)
                          ? `（${codexModels.find((model) => model.isDefault)?.displayName}）`
                          : ""}
                      </option>
                      {codexModels.map((model) => (
                        <option value={model.id} key={model.id}>
                          {model.displayName}
                        </option>
                      ))}
                    </select>
                    <small className="field-hint">
                      {codexModel
                        ? `待保存的模型：${codexModels.find((model) => model.id === codexModel)?.displayName || codexModel}`
                        : "当前设置：跟随 Codex 默认模型"}
                    </small>
                  </label>
                  {codexModelsLoading && <p>正在读取可用模型…</p>}
                  {codexModelsError && (
                    <>
                      <p className="error">{codexModelsError}</p>
                      <button
                        type="button"
                        className="secondary codex-status-retry"
                        disabled={configLoading || configSaving || codexModelsLoading}
                        onClick={() => void loadCodexModels()}
                      >
                        重试读取模型列表
                      </button>
                    </>
                  )}
                </div>
                {(!codexStatus || codexStatus.error) && (
                  <button
                    type="button"
                    className="secondary codex-status-retry"
                    disabled={configLoading || configSaving || codexStatusLoading}
                    onClick={() => {
                      setConfigError("");
                      setConfigNotice("");
                      void loadCodexStatus();
                    }}
                  >
                    重试读取 Codex 状态
                  </button>
                )}
                  </div>
                )}
            {apiProvider === "deepseek" && (
              <label className="field">
                DeepSeek 模型
                <input
                  name="deepseek-model"
                  type="text"
                  value={deepseekModel}
                  disabled={configLoading || configSaving}
                  placeholder="deepseek-flash"
                  onChange={(e) => setDeepseekModel(e.target.value)}
                />
              </label>
            )}
            {configError && (
              <p className="error" role="alert">
                {configError}
              </p>
            )}
            {configNotice && (
              <p className="config-success" role="status">
                {configNotice}
              </p>
            )}
            {apiHealth && (
              <p className="config-health" role="status">
                健康状态：
                {apiHealth.configured
                  ? `已配置 · ${apiHealth.provider || "未知平台"}`
                  : `未配置${apiHealth.error ? ` · ${apiHealth.error}` : ""}`}
              </p>
            )}
            <button
              className="primary"
              disabled={configLoading || configSaving}
              onClick={() => void saveRuntimeConfiguration()}
            >
              {configSaving ? "保存中…" : "保存 API 配置"}
            </button>
          </section>
          <label className="field">
            你们的关系
            <select
              value={relation}
              onChange={(e) => {
                const r = e.target.value as Relation;
                setRelation(r);
                if (messages.length) a.run(messages, r);
              }}
            >
              {Object.entries(RELATIONS).map(([k, v]) => (
                <option value={k} key={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <button
            className="secondary"
            disabled={!messages.length}
            onClick={() => {
              const ms = messages.map((m) => ({
                ...m,
                sender:
                  m.sender === "self" ? ("other" as const) : ("self" as const),
              }));
              setParticipants((current) => swapParticipantProfiles(current));
              setMessages(ms);
              a.reset();
              a.run(ms, relation);
              setSettings(false);
            }}
          >
            交换双方身份
          </button>
          <button
            className="secondary"
            disabled={!messages.length || exporting}
            onClick={() => void exportAnalysisPdf()}
          >
            {exporting ? "导出中…" : "导出完整分析 PDF"}
          </button>
          <button className="secondary danger" onClick={clear}>
            清空聊天，重新开始
          </button>
          <p>
            已保存 {messages.length.toLocaleString()}{" "}
            条聊天。记录保存在本机浏览器，刷新后可继续；分析时只发送所需片段给模型服务。清空会删除本机记录。
          </p>
        </Modal>
      )}
      {detail === "clear" && (
        <Modal title="开始新的聊天？" close={() => setDetail(null)}>
          <p>当前聊天、分析和本机保存的记录都会删除。</p>
          <button className="primary" onClick={clear}>
            开始新聊天
          </button>
          <button className="secondary" onClick={() => setDetail(null)}>
            保留当前聊天
          </button>
        </Modal>
      )}
      {detail && detail !== "clear" && (
        <Modal
          title={
            detail === "overview"
              ? "好感度"
              : detail === "action"
                ? "下一步"
                : detail === "performance"
                  ? "我的发挥"
                  : chosen?.sender === "other"
                    ? "情绪与意图"
                    : "回复评价"
          }
          close={() => setDetail(null)}
        >
          {detail === "overview" ? (
            <>
              <p>
                0—100 是模型对这段聊天的好感信号评分，不是「对方喜欢你的概率」。
              </p>
              <p>
                根据近期对话和相关历史原话评分，旧分数不参与计算。证据少时仍保留分数供娱乐参考。
              </p>
              {!!ov?.memoryEvidenceIds?.length && (
                <details>
                  <summary>参考的历史原话</summary>
                  {[...new Set(ov.memoryEvidenceIds)].map((id) => {
                    const m = messages.find((m) => m.id === id);
                    return m ? (
                      <blockquote key={id}>
                        {m.sender === "self" ? self : other}：{m.text}
                      </blockquote>
                    ) : null;
                  })}
                </details>
              )}
              {ov?.affinityDimensions && (
                <div className="affinity-breakdown">
                  {ov.affinityDimensions.map((d) => (
                    <div key={d.key}>
                      <span>{d.label}</span>
                      <meter
                        min="0"
                        max="100"
                        value={d.judgment.value ?? 0}
                        aria-label={`${d.label} ${d.judgment.value} 分`}
                      />
                      <strong>{d.judgment.value}</strong>
                      <small>
                        占 {d.weight}% · {statusLabel(d.judgment)}
                      </small>
                    </div>
                  ))}
                </div>
              )}
              {ov?.boundaryApplied && (
                <p>
                  对方表达了明确且仍有效的拒绝边界。综合原分{" "}
                  {ov.affinityRawValue}，最终好感度最多显示 25 分。
                </p>
              )}
              {ov && (
                <p>
                  本轮判断：{statusLabel(ov.affinity)}。综合确定度{" "}
                  {Math.round(ov.affinity.confidence * 100)}%。
                </p>
              )}
            </>
          ) : detail === "action" ? (
            <>
              <h3>{actionLabel}</h3>
              <p>
                {actionOverview
                  ? ACTIONS[actionOverview.action]?.detail
                  : busy
                    ? "正在根据最新聊天更新建议。"
                    : messages.length
                      ? "最新聊天的建议尚未更新，请重新分析。"
                      : "导入后生成建议。"}
              </p>
              {(busy ||
                a.progress.total > 0 ||
                a.status === "error" ||
                a.status === "cancelled") && (
                <AnalysisProgressDetails
                  progress={a.progress}
                  status={a.status}
                  error={a.error}
                  onCancel={a.cancel}
                  onRetry={() => void a.run(messages, relation)}
                />
              )}
              {actionOverview?.actionEvidenceId && (
                <blockquote>
                  {
                    messages.find(
                      (m) => m.id === actionOverview.actionEvidenceId,
                    )?.text
                  }
                </blockquote>
              )}
              {!actionOverview &&
                !busy &&
                a.status !== "error" &&
                a.status !== "cancelled" &&
                !!messages.length && (
                <button
                  type="button"
                  onClick={() => void a.run(messages, relation)}
                >
                  重新分析
                </button>
              )}
            </>
          ) : detail === "performance" ? (
            <>
              <div className="detail-score">
                {quality ?? "—"}
                <span>/100</span>
              </div>
              <p>
                已完成分析的我方回复平均分。模型根据发出时的前文评价表达质量，
                再按固定分数区间显示评级。
              </p>
              <div className="reply-guide">
                {REPLY_RATINGS.map((v) => (
                  <p key={v.label}>
                    <strong>
                      {v.label} · {v.range} 分
                    </strong>
                    ：{v.description}
                  </p>
                ))}
              </div>
            </>
          ) : (
            <>
              <blockquote>{chosen?.text}</blockquote>
              {chosen?.sender === "other" ? (
                <>
                  <h3>情绪</h3>
                  <div className="emotion-distribution">
                    {Object.entries(result?.emotions || {})
                      .sort((a, b) => b[1] - a[1])
                      .map(([key, p]) => (
                        <div key={key}>
                          <span>
                            {EMOTIONS[key as keyof typeof EMOTIONS]?.label ||
                              key}
                          </span>
                          <div className="probability-track">
                            <i style={{ width: `${p * 100}%` }} />
                          </div>
                          <b>
                            {p > 0 && p < 0.005
                              ? "<1%"
                              : `${Math.round(p * 100)}%`}
                          </b>
                        </div>
                      ))}
                  </div>
                  <h3 className="intent-detail-heading">意图</h3>
                  <div className="intent-distribution">
                    {Object.entries(result?.intents || {})
                      .filter(([key, p]) => key in INTENTS && p > 0)
                      .sort((a, b) => b[1] - a[1])
                      .map(([key, p]) => (
                        <div key={key} className="intent-detail-item">
                          <div>
                            <strong>
                              {INTENTS[key as keyof typeof INTENTS].label}
                            </strong>
                            <b>
                              {p < 0.005 ? "<1%" : `${Math.round(p * 100)}%`}
                            </b>
                          </div>
                          <p>{INTENTS[key as keyof typeof INTENTS].criteria}</p>
                        </div>
                      ))}
                    {!result?.intents && <p>意图尚未分析。</p>}
                  </div>
                  <p>
                    两行分别展示主要情绪与主要沟通意图的候选解读，不代表测量真实内心。每行最多显示前三项，保留原始概率，不重新凑成
                    100%。
                  </p>
                </>
              ) : (
                <>
                  <h3 className="reply-verdict">
                    回复评级：
                    {replyRating(result?.score.value)?.label ?? "待判断"}
                  </h3>
                  <p>
                    {replyRating(result?.score.value)?.description ??
                      "当前语境不足以判断表达质量"}
                  </p>
                  <p>
                    回复评分 {result?.score.value ?? "—"} / 100 ·{" "}
                    {result && statusLabel(result.score)}
                  </p>
                </>
              )}
              <p>结合当前已导入的上下文判断，不代表对方真实想法。</p>
            </>
          )}
        </Modal>
      )}
      {overlap && (
        <Modal title="这段可能重复了" close={() => setOverlap(null)}>
          <p>相同内容也可能是新消息，请选择如何合并。</p>
          <button
            className="primary"
            onClick={() => {
              add(overlap, "skip");
              setOverlap(null);
            }}
          >
            跳过重合部分
          </button>
          <button
            className="secondary"
            onClick={() => {
              add(overlap, "append");
              setOverlap(null);
            }}
          >
            作为新消息追加
          </button>
        </Modal>
      )}
    </main>
  );
}
