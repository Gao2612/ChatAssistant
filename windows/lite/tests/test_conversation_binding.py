import unittest

from liteapp.contracts import ScopeKey, SourceKey
from liteapp.conversation_binding import ConversationBindings


def source(pid=100, created=1000, hwnd=200, path="C:/Weixin/Weixin.exe"):
    return SourceKey(exe_path=path, pid=pid, process_started=created, hwnd=hwnd)


class ConversationBindingTests(unittest.TestCase):
    def test_binding_resolves_only_for_exact_source_and_epoch(self):
        bindings = ConversationBindings()
        src = source()
        scope = ScopeKey("acct-1", "person-1")
        binding = bindings.bind(src, 3, scope)
        self.assertEqual(binding.revision, 1)
        self.assertEqual(bindings.resolve(src, 3).scope, scope)
        self.assertIsNone(bindings.resolve(src, 2))
        self.assertIsNone(bindings.resolve(src, 4))

    def test_new_epoch_rebinds_with_incremented_revision(self):
        bindings = ConversationBindings()
        src = source()
        first = bindings.bind(src, 1, ScopeKey("acct", "alice"))
        second = bindings.bind(src, 2, ScopeKey("acct", "bob"))
        self.assertEqual(second.revision, first.revision + 1)
        self.assertIsNone(bindings.resolve(src, 1))
        self.assertEqual(bindings.resolve(src, 2).scope, second.scope)

    def test_rebinding_same_scope_and_epoch_is_idempotent(self):
        bindings = ConversationBindings()
        src = source()
        scope = ScopeKey("acct", "alice")
        first = bindings.bind(src, 5, scope)
        again = bindings.bind(src, 5, scope)
        self.assertEqual(first, again)
        self.assertEqual(again.revision, 1)

    def test_window_restart_is_a_different_source(self):
        bindings = ConversationBindings()
        old = source(pid=100, created=1000)
        new = source(pid=100, created=2000)
        bindings.bind(old, 1, ScopeKey("acct", "alice"))
        self.assertIsNone(bindings.resolve(new, 1))

    def test_revoke_matching_and_scope_wide(self):
        bindings = ConversationBindings()
        one, two = source(pid=1, created=1, hwnd=1), source(pid=2, created=2, hwnd=2)
        alice, bob = ScopeKey("acct", "alice"), ScopeKey("acct", "bob")
        bindings.bind(one, 1, alice)
        bindings.bind(two, 1, alice)
        self.assertFalse(bindings.revoke(one, conversation_epoch=9))
        self.assertTrue(bindings.revoke(one, conversation_epoch=1))
        self.assertIsNone(bindings.resolve(one, 1))
        self.assertEqual(bindings.revoke_scope(alice), 1)
        self.assertEqual(bindings.active(), ())

    def test_invalid_binding_inputs_fail_closed(self):
        bindings = ConversationBindings()
        with self.assertRaises(TypeError):
            bindings.bind("not-a-source", 1, ScopeKey("a", "b"))
        with self.assertRaises(ValueError):
            bindings.bind(source(), -1, ScopeKey("a", "b"))
        with self.assertRaises(TypeError):
            bindings.bind(source(), 1, "not-a-scope")


if __name__ == "__main__":
    unittest.main()
