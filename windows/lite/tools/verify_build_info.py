"""Verify a packaged build manifest against the current source tree (T11)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from liteapp import build_info  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Verify ChatAssistant Lite build_info.json")
    parser.add_argument("--info", required=True, help="build_info.json inside the packaged app")
    parser.add_argument("--source-root", default=str(build_info.SOURCE_ROOT),
                        help="source root used to recompute the digest")
    parser.add_argument("--expect-version", default=None)
    args = parser.parse_args()
    result = build_info.verify(args.info, root=args.source_root,
                               expect_version=args.expect_version)
    print(json.dumps({"ok": result["ok"], "errors": result["errors"],
                      "warnings": result["warnings"]}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
