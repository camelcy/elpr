from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from .store import MappingStore


ZOTERO_ITEM_KEY_PATTERN = re.compile(r"^[A-Z0-9]{8}$")
WINDOWS_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
PENDING_VALUE = "待填写"
MACHINE_FIELDS = ("title", "authors", "year", "citekey", "doi", "zotero_key", "zotero_link", "excalidraw")
DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "literature-card.md"
TEMPLATE_PLACEHOLDERS = (
    "title",
    "authors",
    "institutions",
    "year",
    "citekey",
    "doi",
    "zotero_key",
    "zotero_link",
    "excalidraw",
    "date_created",
    "date_modified",
    "zotero_link_url",
    "canvas_evidence",
)


class DuplicateLiteratureCardError(ValueError):
    pass


class InstitutionResolver(Protocol):
    def resolve(self, doi_value: str) -> list[str]: ...


def clean_windows_filename(value: str, fallback: str) -> str:
    cleaned = WINDOWS_UNSAFE_FILENAME.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned} - {fallback}"
    return cleaned[:180].rstrip(". ") or fallback


def yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def doi_url(value: Any) -> str:
    doi = re.sub(r"\s+", "", str(value or "")).strip()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.IGNORECASE)
    return f"https://doi.org/{doi}" if re.fullmatch(r"10\.\d{4,9}/\S+", doi, flags=re.IGNORECASE) else ""


def excalidraw_wikilink(canvas_path: str) -> str:
    normalized_path = canvas_path.replace("\\", "/")
    return f"[[{normalized_path}|Excalidraw 证据画布]]" if normalized_path else ""


def _frontmatter(text: str) -> tuple[int, int, str] | None:
    match = re.match(r"^\ufeff?---[ \t]*\r?\n", text)
    if not match:
        return None
    closing = re.search(r"(?m)^---[ \t]*(?:\r?\n|$)", text[match.end() :])
    if not closing:
        return None
    start = match.end()
    end = start + closing.start()
    return start, end, text[start:end]


def _field_spans(frontmatter: str) -> dict[str, tuple[int, int, str]]:
    lines = frontmatter.splitlines(keepends=True)
    starts: list[tuple[str, int]] = []
    offset = 0
    for line in lines:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):", line)
        if match:
            starts.append((match.group(1), offset))
        offset += len(line)

    spans: dict[str, tuple[int, int, str]] = {}
    for index, (key, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(frontmatter)
        spans[key] = (start, end, frontmatter[start:end])
    return spans


def _field_has_value(block: str) -> bool:
    first, *continuation = block.splitlines()
    value = first.split(":", 1)[1].strip()
    normalized_value = value.strip("'\"")
    if normalized_value == PENDING_VALUE:
        return False
    if value not in {"", "''", '""', "[]", "null", "Null", "NULL", "~"}:
        return True
    values = [line.strip() for line in continuation if line.strip() and not line.lstrip().startswith("#")]
    return any(not (line.startswith("-") and line[1:].strip().strip("'\"") == PENDING_VALUE) for line in values)


def _zotero_key_from_text(text: str) -> str:
    parsed = _frontmatter(text)
    if not parsed:
        return ""
    spans = _field_spans(parsed[2])
    block = spans.get("zotero_key")
    if not block:
        return ""
    value = block[2].splitlines()[0].split(":", 1)[1].strip()
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    value = value.strip("'\"").upper()
    return value if ZOTERO_ITEM_KEY_PATTERN.fullmatch(value) else ""


def _atomic_replace(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_create(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class LiteratureCardStore:
    def __init__(
        self,
        vault_path: Path,
        literature_folder: str,
        mappings: MappingStore,
        institution_resolver: InstitutionResolver | None = None,
        template_path: Path | None = None,
    ) -> None:
        self.vault_path = vault_path.resolve()
        normalized = literature_folder.replace("\\", "/").strip("/")
        if not normalized or re.match(r"^[A-Za-z]:", normalized):
            raise ValueError("literature folder must be a vault-relative path")
        parts = normalized.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("literature folder cannot contain relative path segments")
        self.literature_folder = "/".join(parts)
        self.root = self.vault_path.joinpath(*parts)
        if not self.root.resolve().is_relative_to(self.vault_path):
            raise ValueError("literature folder must stay inside the vault")
        self.mappings = mappings
        self.institution_resolver = institution_resolver
        self.template_path = template_path or DEFAULT_TEMPLATE_PATH
        self.lock = threading.RLock()

    def status(self, parent_item_key: str) -> dict[str, Any]:
        key = self._item_key(parent_item_key)
        with self.lock:
            matches = self._find(key)
            card = self._one_or_none(key, matches)
            return {
                "parentItemKey": key,
                "exists": card is not None,
                "cardPath": self._relative(card) if card else "",
            }

    def create_or_open(self, payload: dict[str, Any]) -> dict[str, Any]:
        metadata = self._metadata(payload)
        key = metadata["zotero_key"]
        with self.lock:
            matches = self._find(key)
            existing = self._one_or_none(key, matches)
            if existing:
                updated_fields = self._supplement_machine_fields(existing, metadata)
                return {
                    "parentItemKey": key,
                    "created": False,
                    "updatedFields": updated_fields,
                    "cardPath": self._relative(existing),
                }

            self.root.mkdir(parents=True, exist_ok=True)
            try:
                metadata["institutions"] = (
                    self.institution_resolver.resolve(metadata["doi"])
                    if self.institution_resolver is not None
                    else [PENDING_VALUE]
                )
            except Exception:
                metadata["institutions"] = [PENDING_VALUE]
            target = self._available_path(metadata["title"], key)
            _atomic_create(target, self._new_card(metadata))
            return {
                "parentItemKey": key,
                "created": True,
                "updatedFields": [],
                "cardPath": self._relative(target),
            }

    def _item_key(self, value: Any) -> str:
        key = str(value).strip().upper()
        if not ZOTERO_ITEM_KEY_PATTERN.fullmatch(key):
            raise ValueError("parent item key must be an 8-character Zotero item key")
        return key

    def _metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        authors_value = payload.get("authors", [])
        if not isinstance(authors_value, list):
            raise ValueError("authors must be a list")
        authors = []
        for author in authors_value:
            name = re.sub(r"\s+", " ", str(author)).strip()
            if name:
                authors.append(name[:300])
        key = self._item_key(payload.get("parentItemKey", ""))
        canvas_path = self.mappings.read().get(key, "")
        return {
            "title": re.sub(r"\s+", " ", str(payload.get("title", ""))).strip()[:500],
            "authors": authors,
            "year": re.sub(r"\s+", " ", str(payload.get("year", ""))).strip()[:20],
            "citekey": re.sub(r"\s+", " ", str(payload.get("citekey", ""))).strip()[:300],
            "doi": doi_url(payload.get("doi", ""))[:1000],
            "zotero_key": key,
            "zotero_link": f"zotero://select/library/items/{key}",
            "excalidraw": excalidraw_wikilink(canvas_path),
        }

    def _find(self, key: str) -> list[Path]:
        if not self.root.exists():
            return []
        matches = []
        for path in self.root.rglob("*.md"):
            try:
                if _zotero_key_from_text(path.read_text(encoding="utf-8-sig")) == key:
                    matches.append(path)
            except (OSError, UnicodeError):
                continue
        return sorted(matches, key=lambda path: path.as_posix().casefold())

    def _one_or_none(self, key: str, matches: list[Path]) -> Path | None:
        if len(matches) > 1:
            paths = "、".join(self._relative(path) for path in matches)
            raise DuplicateLiteratureCardError(
                f"同一 zotero_key {key} 对应多张文献卡片：{paths}；请手动处理后重试"
            )
        return matches[0] if matches else None

    def _available_path(self, title: str, key: str) -> Path:
        preferred = clean_windows_filename(title, f"Zotero {key}")
        candidates = [preferred, f"{preferred} - {key}"]
        for index in range(2, 1000):
            candidates.append(f"{preferred} - {key} ({index})")
        for candidate in candidates:
            path = self.root / f"{candidate}.md"
            if not path.exists():
                return path
        raise FileExistsError(f"could not allocate a safe literature card filename for {key}")

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.vault_path).as_posix()

    def _machine_blocks(self, metadata: dict[str, Any], *, placeholders: bool = False) -> dict[str, str]:
        authors = metadata["authors"]
        if authors:
            author_block = "authors:\n" + "".join(f"  - {yaml_string(author)}\n" for author in authors)
        elif placeholders:
            author_block = f"authors:\n  - {yaml_string(PENDING_VALUE)}\n"
        else:
            author_block = "authors: []\n"
        institutions = metadata.get("institutions", [])
        if institutions:
            institution_block = "institutions:\n" + "".join(
                f"  - {yaml_string(institution)}\n" for institution in institutions
            )
        elif placeholders:
            institution_block = f"institutions:\n  - {yaml_string(PENDING_VALUE)}\n"
        else:
            institution_block = "institutions: []\n"
        year = metadata["year"]
        fallback = PENDING_VALUE if placeholders else ""
        title_value = yaml_string(metadata["title"] or fallback) if metadata["title"] or fallback else ""
        year_value = year if re.fullmatch(r"\d{4}", year) else yaml_string(year or fallback) if year or fallback else ""
        citekey_value = yaml_string(metadata["citekey"] or fallback) if metadata["citekey"] or fallback else ""
        doi_value = yaml_string(metadata["doi"] or fallback) if metadata["doi"] or fallback else ""
        excalidraw_value = yaml_string(metadata["excalidraw"] or fallback) if metadata["excalidraw"] or fallback else ""
        return {
            "title": f"title:{f' {title_value}' if title_value else ''}\n",
            "authors": author_block,
            "institutions": institution_block,
            "year": f"year:{f' {year_value}' if year_value else ''}\n",
            "citekey": f"citekey:{f' {citekey_value}' if citekey_value else ''}\n",
            "doi": f"doi:{f' {doi_value}' if doi_value else ''}\n",
            "zotero_key": f"zotero_key: {yaml_string(metadata['zotero_key'])}\n",
            "zotero_link": f"zotero_link: {yaml_string(metadata['zotero_link'])}\n",
            "excalidraw": f"excalidraw:{f' {excalidraw_value}' if excalidraw_value else ''}\n",
        }

    def _supplement_machine_fields(self, path: Path, metadata: dict[str, Any]) -> list[str]:
        text = path.read_text(encoding="utf-8-sig")
        parsed = _frontmatter(text)
        if not parsed:
            raise ValueError(f"文献卡片缺少合法 frontmatter，未写入：{self._relative(path)}")
        start, end, frontmatter = parsed
        spans = _field_spans(frontmatter)
        blocks = self._machine_blocks(metadata)
        replacements: list[tuple[int, int, str, str]] = []
        additions: list[str] = []
        updated_fields: list[str] = []
        for field in MACHINE_FIELDS:
            span = spans.get(field)
            if span is None:
                additions.append(blocks[field])
                updated_fields.append(field)
            elif not _field_has_value(span[2]) and _field_has_value(blocks[field]):
                replacements.append((span[0], span[1], blocks[field], field))
                updated_fields.append(field)

        if not updated_fields:
            return []
        updated = frontmatter
        for field_start, field_end, block, _field in sorted(replacements, reverse=True):
            updated = updated[:field_start] + block + updated[field_end:]
        if additions:
            if updated and not updated.endswith(("\n", "\r")):
                updated += "\n"
            updated += "".join(additions)
        _atomic_replace(path, text[:start] + updated + text[end:])
        return updated_fields

    def _new_card(self, metadata: dict[str, Any]) -> str:
        fields = self._machine_blocks(metadata, placeholders=True)
        today = date.today().isoformat()
        values = {
            **fields,
            "date_created": today,
            "date_modified": today,
            "zotero_link_url": metadata["zotero_link"],
            "canvas_evidence": metadata["excalidraw"] or PENDING_VALUE,
        }
        try:
            template = self.template_path.read_text(encoding="utf-8-sig")
        except OSError as error:
            raise RuntimeError(f"无法读取文献卡片模板：{self.template_path}") from error

        missing = [name for name in TEMPLATE_PLACEHOLDERS if f"{{{{{name}}}}}" not in template]
        if missing:
            raise ValueError(f"文献卡片模板缺少占位符：{', '.join(missing)}")
        for name, value in values.items():
            template = template.replace(f"{{{{{name}}}}}", value)
        return template
