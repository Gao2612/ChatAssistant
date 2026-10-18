"""Fake PDF worker used only by tests: emits the T6 JSON-lines protocol.

Behavior: page_count=3; page 1 and page 3 succeed; page 2 simulates the real
worker watchdog firing (exit code 66 after a page_timeout event). No parsing and
no chat data is involved.
"""

import json
import sys


def main(argv):
    start_page = 1
    for index, value in enumerate(argv):
        if value == "--start-page" and index + 1 < len(argv):
            start_page = int(argv[index + 1])
    emit = lambda obj: print(json.dumps(obj), flush=True)
    emit({"type": "meta", "page_count": 3})
    for page in range(start_page, 4):
        emit({"type": "page_start", "page": page})
        if page == 2:
            emit({"type": "page_timeout", "page": 2})
            return 66
        emit({"type": "records", "page": page, "records": [{
            "text": f"合成PDF页{page}正文",
            "ordinal": page - 1,
            "record_type": "fragment",
            "locator": {"kind": "pdf", "page": page, "line": 1},
            "quality": {"source": "text"},
        }]})
        emit({"type": "page_done", "page": page})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
