"""Write the packaged build manifest (T11). Run from build.ps1 after PyInstaller."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from liteapp import build_info  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Write build_info.json for a ChatAssistant Lite build")
    parser.add_argument("--output", required=True, help="target build_info.json path")
    parser.add_argument("--source-root", default=str(build_info.SOURCE_ROOT),
                        help="source root (defaults to windows/lite)")
    parser.add_argument("--version", default=None, help="override app version")
    args = parser.parse_args()
    info = build_info.write_build_info(args.output, version=args.version, root=args.source_root)
    print(json.dumps({key: info[key] for key in
                      ("app_version", "source_digest", "source_file_count", "git")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
