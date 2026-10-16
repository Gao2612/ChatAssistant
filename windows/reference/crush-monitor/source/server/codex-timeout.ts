export const CODEX_HANDSHAKE_TIMEOUT_MS: number = 60_000;
export const CODEX_BASE_TURN_TIMEOUT_MS: number = 120_000;
export const CODEX_MAX_TURN_TIMEOUT_MS: number = 180_000;
export const CODEX_PROMPT_SOFT_LIMIT_CHARS: number = 40_000;
export const CODEX_PROMPT_SPLIT_TARGET_CHARS: number = 24_000;
export const CODEX_RETRY_DELAY_MS: number = 150;
export const CODEX_MAX_ATTEMPTS: number = 2;

const retryableCodexErrorCodes: ReadonlySet<string> = new Set([
  "request-timeout",
  "turn-timeout",
  "server-overloaded",
  "network-failed",
]);

export function isRetryableCodexErrorCode(code: string): boolean {
  return retryableCodexErrorCodes.has(code);
}

export function turnTimeoutForPrompt(
  baseTimeoutMs: number,
  promptChars: number,
): number {
  if (
    !Number.isFinite(baseTimeoutMs) ||
    baseTimeoutMs <= 0 ||
    baseTimeoutMs > CODEX_MAX_TURN_TIMEOUT_MS
  )
    throw new RangeError("Codex 基础 turn 超时必须在有效范围内。");
  if (!Number.isFinite(promptChars) || promptChars < 0)
    throw new RangeError("Codex prompt 长度必须是非负有限数字。");
  const extraBlocks = Math.ceil(
    Math.max(0, promptChars - 16_000) / 8_000,
  );
  return Math.min(
    CODEX_MAX_TURN_TIMEOUT_MS,
    baseTimeoutMs + Math.min(60_000, extraBlocks * 15_000),
  );
}
