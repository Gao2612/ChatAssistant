import { topEmotions } from "../shared/labels";
import { topIntents } from "../shared/intents";
import { replyRating } from "../shared/ratings";
import {
  ACTIONS,
  RELATIONS,
  STAGES,
  statusLabel,
  type LineResult,
  type Message,
  type Overview,
  type Relation,
} from "../shared/types";
import type {
  ParticipantProfile,
  ParticipantProfiles,
} from "../shared/participants";

export type AnalysisReportInput = {
  messages: readonly Message[];
  lines: Readonly<Record<string, LineResult>>;
  overview: Overview | null;
  participants: ParticipantProfiles;
  relation: Relation;
  generatedAt: string;
};

type ProbabilityItem = {
  label: string;
  percent: string;
};

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => {
    const entities: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[character];
  });
}

function formatValue(value: number | null | undefined): string {
  return value == null ? "—" : String(Math.round(value));
}

function formatConfidence(value: number | undefined): string {
  return value == null ? "" : ` · 置信度 ${Math.round(value * 100)}%`;
}

function safeAvatarDataUrl(value: string | null): string | null {
  if (!value) return null;
  const match = value.match(
    /^data:(image\/(?:jpeg|png|webp|gif));base64,([A-Za-z0-9+/]+={0,2})$/,
  );
  return match ? value : null;
}

function avatarInitial(profile: ParticipantProfile): string {
  return Array.from(profile.displayName.trim())[0] || "?";
}

function renderAvatar(profile: ParticipantProfile, className: string): string {
  const dataUrl = safeAvatarDataUrl(profile.avatarDataUrl);
  if (dataUrl)
    return `<img class="${className}" src="${escapeHtml(dataUrl)}" alt="" />`;
  return `<span class="${className} avatar-fallback" aria-label="${escapeHtml(profile.displayName)}头像">${escapeHtml(avatarInitial(profile))}</span>`;
}

function renderProbabilityItems(
  title: string,
  items: readonly ProbabilityItem[],
): string {
  const content = items.length
    ? items
        .map(
          (item) =>
            `<span class="probability-item">${escapeHtml(item.label)} ` +
            `<b>${escapeHtml(item.percent)}</b></span>`,
        )
        .join("")
    : '<span class="empty-analysis">未识别</span>';
  return `<div class="analysis-line"><b>${escapeHtml(title)}</b>${content}</div>`;
}

function renderLineAnalysis(message: Message, line: LineResult | undefined): string {
  if (!line)
    return '<p class="empty-analysis">尚未分析</p>';
  if (line.skipped)
    return `<p class="empty-analysis">${escapeHtml(line.skipped)}</p>`;

  const event = line.event
    ? `<p class="event">事件：${escapeHtml(line.event.kind)}${formatConfidence(line.event.confidence)}</p>`
    : "";
  if (message.sender === "other") {
    const emotions = topEmotions(line.emotions).map((item) => ({
      label: item.label,
      percent: item.percent,
    }));
    const intents = topIntents(line.intents).map((item) => ({
      label: item.label,
      percent: item.percent,
    }));
    return (
      event +
      renderProbabilityItems("情绪", emotions) +
      renderProbabilityItems("意图", intents)
    );
  }

  const rating = replyRating(line.score.value);
  const reply = rating
    ? `回复质量：${rating.label} · ${formatValue(line.score.value)}`
    : `回复质量：${statusLabel(line.score)}`;
  const replyType = line.replyType
    ? `<span class="probability-item">${escapeHtml(line.replyType)}` +
      `${formatConfidence(line.replyConfidence)}</span>`
    : "";
  return (
    event +
    `<div class="analysis-line"><b>回复</b>` +
    `<span class="probability-item">${escapeHtml(reply)}</span>` +
    replyType +
    "</div>"
  );
}

function renderOverview(overview: Overview | null): string {
  if (!overview)
    return '<p class="empty-analysis">总览尚未完成，逐句分析仍会保留。</p>';
  const stage = STAGES[overview.stage] ?? overview.stage;
  const action = ACTIONS[overview.action]?.label ?? overview.action;
  const boundary = overview.boundaryApplied ? "已考虑边界信息" : "";
  return `
    <div class="overview-grid">
      <div><span>好感度</span><b>${formatValue(overview.affinity.value)}</b></div>
      <div><span>判断</span><b>${escapeHtml(statusLabel(overview.affinity))}</b></div>
      <div><span>阶段</span><b>${escapeHtml(stage)}</b></div>
      <div><span>行动建议</span><b>${escapeHtml(action)}</b></div>
    </div>
    <p class="overview-note">${escapeHtml(boundary || "基于当前导入的聊天记录")}</p>`;
}

function renderMessage(
  message: Message,
  line: LineResult | undefined,
  participants: ParticipantProfiles,
): string {
  const profile =
    message.sender === "self" ? participants.self : participants.other;
  const kind = message.kind === "unreadable" ? " · 不可读附件" : "";
  return `
    <article class="message-record">
      <div class="message-meta">
        <div class="message-speaker">
          ${renderAvatar(profile, "message-avatar")}
          <b>${escapeHtml(profile.displayName)}${escapeHtml(kind)}</b>
        </div>
        <time>${escapeHtml(message.timestamp ?? "时间未知")}</time>
      </div>
      <div class="message-text">${escapeHtml(message.text)}</div>
      <div class="message-analysis">
        ${renderLineAnalysis(message, line)}
      </div>
    </article>`;
}

export function buildAnalysisReportHtml(input: AnalysisReportInput): string {
  const relation = RELATIONS[input.relation] ?? input.relation;
  const records = input.messages
    .map((message) =>
      renderMessage(message, input.lines[message.id], input.participants),
    )
    .join("");
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>聊天分析报告</title>
  <style>
    @page { size: A4; margin: 15mm 14mm; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: #242627;
      background: #fff;
      font-family: "Microsoft YaHei", "Noto Sans SC", "SimSun", sans-serif;
      font-size: 11pt;
      line-height: 1.65;
    }
    h1, h2, p { margin: 0; }
    h1 { font-size: 23pt; letter-spacing: .02em; }
    h2 {
      margin-bottom: 10px;
      padding-bottom: 5px;
      border-bottom: 1px solid #d8dfda;
      color: #35684d;
      font-size: 14pt;
    }
    .cover { margin-bottom: 22px; }
    .cover-participants { display: flex; gap: 16px; margin-top: 11px; }
    .cover-participant { display: flex; align-items: center; gap: 7px; color: #52635a; }
    .cover-avatar, .message-avatar {
      display: inline-block;
      width: 30px;
      height: 30px;
      border-radius: 50%;
      object-fit: cover;
      vertical-align: middle;
    }
    .avatar-fallback {
      background: #dbe7df;
      color: #35684d;
      text-align: center;
      line-height: 30px;
      font-weight: 600;
    }
    .subtitle { margin-top: 5px; color: #68736d; }
    .meta { margin-top: 12px; color: #68736d; font-size: 9.5pt; }
    section { margin-top: 20px; break-inside: avoid; }
    .overview-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 9px;
    }
    .overview-grid > div {
      padding: 10px;
      border: 1px solid #dce5df;
      border-radius: 6px;
      background: #f7faf8;
    }
    .overview-grid span, .overview-grid b { display: block; }
    .overview-grid span { color: #68736d; font-size: 9pt; }
    .overview-grid b { margin-top: 2px; font-size: 12pt; }
    .overview-note { margin-top: 8px; color: #68736d; font-size: 9.5pt; }
    .message-record {
      padding: 10px 0 13px;
      border-bottom: 1px solid #e5e9e6;
      break-inside: avoid;
    }
    .message-meta {
      display: flex;
      justify-content: space-between;
      gap: 15px;
      color: #52635a;
      font-size: 9.5pt;
    }
    .message-speaker { display: inline-flex; align-items: center; gap: 7px; }
    .message-meta time { color: #818b85; }
    .message-text {
      margin-top: 5px;
      padding: 8px 10px;
      border-radius: 5px;
      background: #f5f7f5;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .message-analysis {
      margin-top: 6px;
      color: #61738a;
      font-size: 9.5pt;
    }
    .analysis-line {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 5px 9px;
      margin-top: 2px;
    }
    .analysis-line > b { color: #52635a; }
    .probability-item { color: #61738a; }
    .probability-item b { color: #52635a; font-weight: 600; }
    .event { color: #8a6a4e; font-size: 9pt; }
    .empty-analysis { color: #929a95; }
    .footer {
      margin-top: 22px;
      padding-top: 8px;
      border-top: 1px solid #d8dfda;
      color: #929a95;
      font-size: 8.5pt;
    }
  </style>
</head>
<body>
  <header class="cover">
    <h1>聊天分析报告</h1>
    <p class="subtitle">${escapeHtml(input.participants.self.displayName)} 与 ${escapeHtml(input.participants.other.displayName)} · ${escapeHtml(relation)}</p>
    <div class="cover-participants">
      <div class="cover-participant">${renderAvatar(input.participants.self, "cover-avatar")}<span>${escapeHtml(input.participants.self.displayName)}</span></div>
      <div class="cover-participant">${renderAvatar(input.participants.other, "cover-avatar")}<span>${escapeHtml(input.participants.other.displayName)}</span></div>
    </div>
    <p class="meta">共 ${input.messages.length} 条记录 · 生成于 ${escapeHtml(input.generatedAt)}</p>
  </header>
  <section class="overview">
    <h2>分析总览</h2>
    ${renderOverview(input.overview)}
  </section>
  <section class="chat-records">
    <h2>原始聊天记录与逐句分析</h2>
    ${records || '<p class="empty-analysis">暂无聊天记录。</p>'}
  </section>
  <p class="footer">本报告包含导入的原始聊天文本，以及当前已完成的情绪、意图和回复质量分析。</p>
</body>
</html>`;
}
