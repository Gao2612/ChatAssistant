import type { Message, Relation, LineResult, Overview } from "../shared/types";
import type { MemoryEvent } from "../shared/memory";
import {
  createParticipantProfiles,
  type ParticipantProfiles,
} from "../shared/participants";
export type Trend = { at: string; value: number | null; count: number };
type SavedConversationBase = {
  rubric: string;
  messages: Message[];
  relation: Relation;
  lines: Record<string, LineResult>;
  events: Record<string, MemoryEvent>;
  overview: Overview | null;
  trend: Trend[];
  analyzedCount: number;
  completed: boolean;
};
export type SavedConversationV1 = SavedConversationBase & {
  schema: 1;
  self: string;
  other: string;
};
export type SavedConversationV2 = SavedConversationBase & {
  schema: 2;
  participants: ParticipantProfiles;
};
export type SavedConversation = SavedConversationV2;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isProfile(value: unknown): boolean {
  if (!isRecord(value)) return false;
  return (
    typeof value.displayName === "string" &&
    (value.avatarDataUrl === null || typeof value.avatarDataUrl === "string")
  );
}

function assertSavedBase(value: Record<string, unknown>): void {
  if (
    typeof value.rubric !== "string" ||
    !Array.isArray(value.messages) ||
    typeof value.relation !== "string" ||
    !isRecord(value.lines) ||
    !isRecord(value.events) ||
    (value.overview !== null && !isRecord(value.overview)) ||
    !Array.isArray(value.trend) ||
    !Number.isInteger(value.analyzedCount) ||
    typeof value.completed !== "boolean"
  )
    throw new Error("本机聊天记录存储已损坏，已停止读取以避免覆盖。 ");
}

export function migrateSavedConversation(
  value: unknown,
): SavedConversation | undefined {
  if (value === undefined) return undefined;
  if (!isRecord(value))
    throw new Error("本机聊天记录存储格式无效，已停止读取。 ");
  assertSavedBase(value);
  if (value.schema === 1) {
    if (typeof value.self !== "string" || typeof value.other !== "string")
      throw new Error("本机聊天记录 schema 1 资料已损坏。 ");
    const legacy = value as SavedConversationV1;
    return {
      schema: 2,
      rubric: legacy.rubric,
      messages: legacy.messages,
      participants: createParticipantProfiles(legacy.self, legacy.other),
      relation: legacy.relation,
      lines: legacy.lines,
      events: legacy.events,
      overview: legacy.overview,
      trend: legacy.trend,
      analyzedCount: legacy.analyzedCount,
      completed: legacy.completed,
    };
  }
  if (value.schema === 2) {
    if (
      !isRecord(value.participants) ||
      !isProfile(value.participants.self) ||
      !isProfile(value.participants.other)
    )
      throw new Error("本机聊天记录 schema 2 资料已损坏。 ");
    return value as SavedConversation;
  }
  throw new Error("本机聊天记录 schema 不受支持，已停止读取。 ");
}

let connection: Promise<IDBDatabase> | undefined;
function db(): Promise<IDBDatabase> {
  return (connection ??= new Promise<IDBDatabase>((resolve, reject) => {
    const req = indexedDB.open("crush-monitor", 2);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains("workspace"))
        req.result.createObjectStore("workspace");
    };
    req.onsuccess = () => {
      resolve(req.result);
    };
    req.onerror = () => {
      connection = undefined;
      reject(req.error);
    };
  }));
}
export async function loadConversation(): Promise<
  SavedConversation | undefined
> {
  const database = await db();
  return new Promise((resolve, reject) => {
    const req = database
      .transaction("workspace")
      .objectStore("workspace")
      .get("current");
    req.onsuccess = () => {
      try {
        resolve(migrateSavedConversation(req.result));
      } catch (error: unknown) {
        reject(error);
      }
    };
    req.onerror = () => reject(req.error);
  });
}
// Serial transactions ensure clearing cannot be followed by an older queued save.
let queue: Promise<void> = Promise.resolve();
export function saveConversation(value: SavedConversation | null): Promise<void> {
  const operation = queue
    .catch(() => {})
    .then(async () => {
      const database = await db();
      await new Promise<void>((resolve, reject) => {
        const tx = database.transaction("workspace", "readwrite");
        if (value) tx.objectStore("workspace").put(value, "current");
        else tx.objectStore("workspace").delete("current");
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error ?? new Error("保存被中断"));
      });
    });
  queue = operation;
  return operation;
}
