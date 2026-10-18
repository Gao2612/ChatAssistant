# C13 offscreen WeChat recovery - task message 35

2026-10-18. Branch feat/windows-lite; starting HEAD f3f277d. No commit, push, release, real WeChat interaction or messages. Other worktree changes preserved.

## Cause and change

IsWindowVisible indicates a style bit, not intersection with a display. Both window_status/window_info treated it as visible, and restore_window only called ShowWindow.

MonitorFromRect with MONITOR_DEFAULTTONULL now detects rectangles outside every actual monitor. Non-minimized offscreen windows report hidden (snapshot visible=false/hidden=true). Default discovery excludes them; include_hidden retains recovery candidates. Partial intersections and secondary monitors remain visible. Minimized status retains precedence and existing discovery behavior.

Only restore_window(activate=True) moves offscreen windows to the primary work area BEFORE ShowWindow. SetWindowPos uses SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE. It checks again after SW_RESTORE because restoring a minimized window can reinstate a different normal rectangle. Move failure returns false; success requires visible, non-minimized and on a monitor. Default activate=False remains unchanged, with no position mutation. Existing identity validation and cloaked rejection remain.

## Verification

Python: G:/codex/wechat-lite-venv/Scripts/python.exe.

- Test-first: old implementation produced five failing assertions (including subtests): parked status incorrectly visible, restore did not move, and movement failure incorrectly succeeded. Final eight boundary unit tests pass.
- From repository root: -m unittest discover -s windows/lite/tests -p test_discovery*.py: 59 tests, 30 passed, 29 skipped. Log: _reports/c13_discovery_tests.log.
- From repository root: -m unittest discover -s windows/lite/tests -p test_window*.py: 102 tests, 89 passed, 13 skipped. Log: _reports/c13_window_tests.log.
- Five additional synthetic native desktop tests were added but skipped: CHATASSISTANT_NATIVE_DESKTOP_TESTS is not enabled. No claim of native desktop verification.
- Boundary tests isolate native APIs and exercise parked (-25600,-25600), hidden/minimized recovery, discovery snapshot/filtering, move-before-show ordering, flags, move failure, and nonactivating position preservation.
- Independent static review found no concrete correctness issue. Scoped git diff --check passed.
- Initial full-suite discovery omitted the project -t . option; it was interrupted and is not used as suite evidence.
- Correct project command from windows/lite: -m unittest discover -s tests -t . -q. 906 tests in 129.514s, 10 failures, 40 errors, 44 skipped; exit 1. Full suite is NOT green. Log: _reports/c13_full_package_tests.log.
- Compared failure/error test names with prior C1 run via _reports/c1_full_comparison.json after_failures: all 50 names match, zero added/removed. Full names and comparison: _reports/c13_full_comparison.json. This is a prior worktree run comparison, not a clean historical baseline.

## Unverified

Real WeChat taskbar/tray clicks, application launch button, real multi-monitor/DPI behavior and native desktop fixtures were not exercised. This delivery establishes code and unit handling of the reported state, not complete real-client compatibility.
