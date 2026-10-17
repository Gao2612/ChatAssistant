"""Stable conversation bindings for realtime memory (T9).

A visible chat title or window title is never a stable identity. The UI resolves
the current ``(SourceKey, conversation_epoch)`` to a persistent :class:`ScopeKey`
through this registry; when the window process changes or the visible conversation
epoch moves on, the old binding stops resolving, so stale analysis or memory can
never be reused for another contact.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from .contracts import ScopeKey, SourceKey


@dataclass(frozen=True)
class Binding:
    """One revision of "this source and conversation belong to this scope"."""

    source: SourceKey
    conversation_epoch: int
    scope: ScopeKey
    revision: int


class ConversationBindings:
    """Thread-safe, revisioned source/epoch -> scope bindings."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._bindings: dict[SourceKey, Binding] = {}

    def bind(self, source: SourceKey, conversation_epoch: int, scope: ScopeKey) -> Binding:
        if not isinstance(source, SourceKey):
            raise TypeError("source 必须是 SourceKey。")
        if isinstance(conversation_epoch, bool) or not isinstance(conversation_epoch, int):
            raise TypeError("conversation_epoch 必须是整数。")
        if conversation_epoch < 0:
            raise ValueError("conversation_epoch 必须 >= 0。")
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey。")
        with self._lock:
            current = self._bindings.get(source)
            if (current is not None and current.conversation_epoch == conversation_epoch
                    and current.scope == scope):
                return current
            revision = 1 if current is None else current.revision + 1
            binding = Binding(source=source, conversation_epoch=conversation_epoch,
                              scope=scope, revision=revision)
            self._bindings[source] = binding
            return binding

    def resolve(self, source: SourceKey, conversation_epoch: int) -> Binding | None:
        """The binding only resolves for the exact source + conversation epoch."""
        if not isinstance(source, SourceKey):
            return None
        with self._lock:
            binding = self._bindings.get(source)
        if binding is None or binding.conversation_epoch != conversation_epoch:
            return None
        return binding

    def revoke(self, source: SourceKey, conversation_epoch: int | None = None) -> bool:
        """Remove a binding (optionally only when the epoch still matches)."""
        with self._lock:
            binding = self._bindings.get(source)
            if binding is None:
                return False
            if conversation_epoch is not None and binding.conversation_epoch != conversation_epoch:
                return False
            del self._bindings[source]
            return True

    def revoke_scope(self, scope: ScopeKey) -> int:
        """Invalidate every binding pointing at a deleted/changed profile."""
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey。")
        with self._lock:
            stale = [source for source, binding in self._bindings.items()
                     if binding.scope == scope]
            for source in stale:
                del self._bindings[source]
            return len(stale)

    def active(self) -> tuple[Binding, ...]:
        with self._lock:
            return tuple(self._bindings.values())

    def clear(self) -> None:
        with self._lock:
            self._bindings.clear()
