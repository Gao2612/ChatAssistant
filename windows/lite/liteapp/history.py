"""Local, per-person memory for user-imported HTML and PDF conversations."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
import sqlite3
import uuid


class HistoryImportError(ValueError):
    pass


class _PlainHTML(HTMLParser):
    BLOCKS = {"address", "article", "blockquote", "br", "dd", "div", "dt", "h1", "h2", "h3",
              "h4", "h5", "h6", "li", "p", "section", "td", "th", "tr"}
    SKIP = {"script", "style", "noscript", "svg", "iframe", "template", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skipping = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping.append(tag)
        elif not self.skipping and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.skipping:
            if tag == self.skipping[-1]:
                self.skipping.pop()
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


def _sections_from_html(raw):
    text = None
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeError:
            continue
    if text is None:
        raise HistoryImportError("HTML 编码无法识别。")
    parser = _PlainHTML()
    parser.feed(text)
    return [" ".join(line.split()) for line in "".join(parser.parts).splitlines()
            if line.strip()]


def _sections_from_pdf(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise HistoryImportError("缺少 PDF 解析组件，请重新安装应用。") from None
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            raise HistoryImportError("PDF 有密码保护，请先导出无密码版本。")
        sections = []
        for page in reader.pages:
            text = page.extract_text() or ""
            sections.extend(" ".join(line.split()) for line in text.splitlines() if line.strip())
    except HistoryImportError:
        raise
    except Exception:
        raise HistoryImportError("PDF 损坏或无法读取。") from None
    if not sections:
        raise HistoryImportError("PDF 没有可提取文字；扫描件需要先做 OCR。")
    return sections


class HistoryStore:
    """Encrypted text records. Person association is explicit and never inferred by name."""

    def __init__(self, path=None, protect=None, unprotect=None):
        from . import settings
        self.path = Path(path) if path else settings.data_dir() / "memory.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.protect = protect or settings.crypt
        self.unprotect = unprotect or (lambda raw: settings.crypt(raw, True))
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS people (
                    id TEXT PRIMARY KEY, account_hash TEXT NOT NULL, display_name BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id),
                    digest TEXT NOT NULL, kind TEXT NOT NULL, name BLOB NOT NULL,
                    section_count INTEGER NOT NULL, char_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(person_id, digest));
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL, label BLOB NOT NULL, body BLOB NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_source ON messages(source_id, sequence);
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _account_hash(account_key):
        return hashlib.sha256(account_key.encode("utf-8")).hexdigest()

    def _seal(self, value):
        return self.protect(value.encode("utf-8"))

    def _open(self, value):
        return self.unprotect(value).decode("utf-8")

    def _person_exists(self, db, person_id):
        return db.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone() is not None

    def create_person(self, display_name, account_key="default"):
        display_name = display_name.strip()
        if not display_name or len(display_name) > 200:
            raise ValueError("请输入有效的联系人名称。")
        person_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("INSERT INTO people(id,account_hash,display_name) VALUES(?,?,?)",
                       (person_id, self._account_hash(account_key), self._seal(display_name)))
        return person_id

    def list_people(self, account_key="default"):
        with self._connect() as db:
            rows = db.execute("SELECT id,display_name FROM people WHERE account_hash=? ORDER BY rowid",
                              (self._account_hash(account_key),)).fetchall()
        return [{"id": item_id, "display_name": self._open(name)} for item_id, name in rows]

    def import_file(self, path, person_id):
        path = Path(path)
        kind = path.suffix.lower().lstrip(".")
        if kind not in ("html", "htm", "pdf"):
            raise HistoryImportError("只支持 HTML 或 PDF 文件。")
        try:
            size = path.stat().st_size
            if size > 100 * 1024 * 1024:
                raise HistoryImportError("文件超过 100 MB，请分批导入。")
            raw = path.read_bytes()
        except HistoryImportError:
            raise
        except OSError:
            raise HistoryImportError("文件无法读取。") from None
        digest = hashlib.sha256(raw).hexdigest()
        with self._connect() as db:
            if not self._person_exists(db, person_id):
                raise HistoryImportError("请先选择或创建联系人。")
            existing = db.execute("SELECT id,kind,section_count,char_count FROM sources WHERE person_id=? AND digest=?",
                                  (person_id, digest)).fetchone()
            if existing:
                return {"source_id": existing[0], "kind": existing[1], "section_count": existing[2],
                        "char_count": existing[3], "duplicate": True}
        sections = _sections_from_pdf(path) if kind == "pdf" else _sections_from_html(raw)
        if not sections:
            raise HistoryImportError("文件中没有可导入的聊天文字。")
        source_id = uuid.uuid4().hex
        char_count = sum(len(section) for section in sections)
        with self._connect() as db:
            db.execute("INSERT INTO sources(id,person_id,digest,kind,name,section_count,char_count) "
                       "VALUES(?,?,?,?,?,?,?)", (source_id, person_id, digest, kind,
                       self._seal(path.name), len(sections), char_count))
            db.executemany("INSERT INTO messages(source_id,sequence,label,body) VALUES(?,?,?,?)",
                           ((source_id, n, self._seal(f"片段 {n + 1}"), self._seal(section))
                            for n, section in enumerate(sections)))
        return {"source_id": source_id, "kind": kind, "section_count": len(sections),
                "char_count": char_count, "duplicate": False}

    def list_sources(self, person_id):
        with self._connect() as db:
            rows = db.execute("SELECT id,kind,name,section_count,char_count,created_at FROM sources "
                              "WHERE person_id=? ORDER BY created_at,id", (person_id,)).fetchall()
        return [{"id": row[0], "kind": row[1], "name": self._open(row[2]),
                 "section_count": row[3], "char_count": row[4], "created_at": row[5]} for row in rows]

    def list_messages(self, person_id, offset=0, limit=100):
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        with self._connect() as db:
            rows = db.execute("SELECT m.id,m.source_id,s.name,m.sequence,m.label,m.body "
                              "FROM messages m JOIN sources s ON s.id=m.source_id "
                              "WHERE s.person_id=? ORDER BY s.created_at,s.id,m.sequence LIMIT ? OFFSET ?",
                              (person_id, limit, offset)).fetchall()
        return [{"id": row[0], "source_id": row[1], "source_name": self._open(row[2]),
                 "sequence": row[3], "label": self._open(row[4]), "text": self._open(row[5])}
                for row in rows]

    def search(self, person_id, query, limit=50):
        query = query.strip().casefold()
        if not query:
            return []
        matches = []
        offset = 0
        while len(matches) < limit:
            page = self.list_messages(person_id, offset=offset, limit=500)
            if not page:
                break
            matches.extend(row for row in page if query in row["text"].casefold())
            offset += len(page)
        return matches[:limit]

    def stats(self, person_id):
        with self._connect() as db:
            source_count, section_count, char_count = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(section_count),0),COALESCE(SUM(char_count),0) "
                "FROM sources WHERE person_id=?", (person_id,)).fetchone()
        return {"source_count": source_count, "section_count": section_count,
                "char_count": char_count}

    def build_context(self, person_id, max_chars=8000):
        # Use most recent imported evidence; do not silently claim complete coverage.
        total = self.stats(person_id)["section_count"]
        rows = self.list_messages(person_id, offset=max(0, total - 500), limit=500)
        chosen = []
        remaining = max(0, int(max_chars))
        for row in reversed(rows):
            line = f"[{row['source_name']} / {row['label']}] {row['text']}"
            if len(line) > remaining:
                if not chosen and remaining:
                    chosen.append(line[-remaining:])
                break
            chosen.append(line)
            remaining -= len(line) + 1
        return "\n".join(reversed(chosen))

    def delete_source(self, source_id):
        with self._connect() as db:
            db.execute("DELETE FROM sources WHERE id=?", (source_id,))
