import hashlib

from .contracts import AnalysisKey, ScopeKey, SourceKey, normalize_path

__all__ = ["ConversationState", "FrameFreshness", "choose_window", "normalize_path"]


def choose_window(items, selected):
    if selected:
        return next((item for item in items if item["id"] == selected), None)
    return items[0] if len(items) == 1 else None


class FrameFreshness:
    """Reject OCR results from frames superseded while recognition was running."""
    def __init__(self):
        self.latest = 0

    def observe(self, frame_seq):
        if frame_seq > self.latest:
            self.latest = frame_seq
            return True
        return False

    def accepts(self, frame_seq):
        return frame_seq > 0 and frame_seq == self.latest


class ConversationState:
    """Current visible snapshot only; never merge by a fuzzy conversation name."""
    def __init__(self):
        self.session = None
        self.title = ""
        self.text = ""
        self.revision = 0
        self.source_status = "disconnected"
        # Stable ScopeKey of the bound contact, never the visible title.
        self.scope = None

    @property
    def token(self):
        return self.session, self.revision

    @property
    def ready(self):
        return bool(self.session and self.title and self.text.strip()
                    and self.source_status in ("live_ready", "manual_ready"))

    def accepts(self, token):
        return self.ready and self.token == token

    def invalidate(self):
        self.revision += 1

    def bind_scope(self, scope):
        """Bind the stable ScopeKey of the visible contact.

        Passing a different (or cleared) scope invalidates in-flight results just
        like a conversation change, so a result produced for another contact can
        never be accepted after a rebinding. ``None`` means "not bound".
        """
        if scope is not None and not isinstance(scope, ScopeKey):
            raise TypeError("scope must be ScopeKey or None")
        if scope != self.scope:
            self.scope = scope
            self.invalidate()

    def snapshot_digest(self):
        """SHA-256 of the frozen visible snapshot (title + formatted text)."""
        payload = f"{self.title}\x00{self.text}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def analysis_key(self, source, *, conversation_epoch, binding_revision,
                     memory_version, inference_revision):
        """Freeze the AnalysisKey for an inference request over this snapshot."""
        if not isinstance(source, SourceKey):
            raise TypeError("source must be SourceKey")
        return AnalysisKey(source=source,
                           conversation_epoch=conversation_epoch,
                           snapshot_digest=self.snapshot_digest(),
                           binding_revision=binding_revision,
                           memory_version=memory_version,
                           inference_revision=inference_revision)

    def connect(self, session):
        self.session, self.title, self.text = session, "", ""
        self.source_status = "connecting" if session else "disconnected"
        self.invalidate()

    def mark_pending(self):
        if self.source_status != "live_pending":
            self.source_status = "live_pending"
            # Suspend using a result while the new frame is checked. Only a
            # confirmed change of title/text changes the semantic revision.
            # Sidebar/cursor animation must not permanently discard inference.

    def update(self, title, messages):
        text = "\n".join(f"{'我' if who == 'me' else (name or '对方')}：{body}" for who, name, body in messages) if title else ""
        if (title, text) != (self.title, self.text):
            self.title, self.text = title, text
            self.invalidate()
            changed = True
        else:
            changed = False
        self.source_status = "live_ready" if title and text.strip() else "unavailable"
        return changed

    def set_manual(self, title, text):
        if (title, text) != (self.title, self.text):
            self.title, self.text = title, text
            self.invalidate()
        self.source_status = "manual_ready" if title and text.strip() else "manual_edit"
