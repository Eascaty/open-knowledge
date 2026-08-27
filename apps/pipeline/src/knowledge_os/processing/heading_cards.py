"""Deterministic Markdown H2 splitting into independently classified cards."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple


SINGLE_VERSION = "single-v1"
MARKDOWN_H2_VERSION = "markdown-h2-v1"
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class DocumentCard:
    section_index: int
    title: str
    body: str
    heading_path: Tuple[str, ...]
    source_line_start: Optional[int]
    source_line_end: Optional[int]
    body_sha256: str
    splitter_version: str


@dataclass
class _Section:
    title: str
    lines: List[str]
    start: int
    end: int

    def text(self) -> str:
        return "\n".join(self.lines).strip()


def _single_card(title: str, body: str) -> List[DocumentCard]:
    normalized = body.strip()
    return [
        DocumentCard(
            section_index=0,
            title=title[:200],
            body=normalized,
            heading_path=(),
            source_line_start=1 if normalized else None,
            source_line_end=len(body.splitlines()) if normalized else None,
            body_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            splitter_version=SINGLE_VERSION,
        )
    ]


def _h2_headings(lines: List[str]) -> List[Tuple[int, str]]:
    headings: List[Tuple[int, str]] = []
    fence_character: Optional[str] = None
    in_frontmatter = bool(lines and lines[0].strip() == "---")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if in_frontmatter:
            if index > 0 and stripped in {"---", "..."}:
                in_frontmatter = False
            continue
        fence = _FENCE.match(line)
        if fence:
            character = fence.group(1)[0]
            if fence_character is None:
                fence_character = character
            elif fence_character == character:
                fence_character = None
            continue
        if fence_character is not None:
            continue
        match = _HEADING.match(line)
        if match and len(match.group(1)) == 2:
            heading_title = re.sub(r"\s+", " ", match.group(2)).strip()
            if heading_title:
                headings.append((index, heading_title[:200]))
    return headings


def _merge_small_sections(
    sections: List[_Section], minimum_characters: int
) -> List[_Section]:
    if len(sections) < 2 or minimum_characters <= 0:
        return sections
    merged: List[_Section] = []
    for section in sections:
        content_length = len(re.sub(r"\s+", "", section.text()))
        if content_length < minimum_characters and merged:
            previous = merged[-1]
            previous.lines.extend(section.lines)
            previous.end = section.end
        else:
            merged.append(section)
    if len(merged) > 1:
        first_length = len(re.sub(r"\s+", "", merged[0].text()))
        if first_length < minimum_characters:
            first = merged.pop(0)
            merged[0].lines = first.lines + merged[0].lines
            merged[0].start = first.start
            merged[0].title = "{} / {}".format(first.title, merged[0].title)[:200]
    return merged


def split_document(
    *,
    title: str,
    body: str,
    original_name: str,
    runtime: Mapping[str, Any],
) -> List[DocumentCard]:
    """Split a long Markdown document without changing its immutable raw file."""

    pipeline = runtime.get("pipeline", {})
    suffix = Path(original_name).suffix.casefold()
    enabled = bool(pipeline.get("split_markdown_headings", True))
    minimum_characters = max(0, int(pipeline.get("split_min_chars", 1200)))
    minimum_sections = max(2, int(pipeline.get("split_min_sections", 2)))
    minimum_section_characters = max(
        0, int(pipeline.get("split_min_section_chars", 120))
    )
    maximum_sections = max(2, min(int(pipeline.get("split_max_sections", 24)), 100))
    if (
        not enabled
        or suffix not in {".md", ".markdown"}
        or len(re.sub(r"\s+", "", body)) < minimum_characters
    ):
        return _single_card(title, body)

    lines = body.splitlines()
    headings = _h2_headings(lines)
    if len(headings) < minimum_sections:
        return _single_card(title, body)
    sections: List[_Section] = []
    for position, (start_index, heading_title) in enumerate(headings):
        segment_start = 0 if position == 0 else start_index
        segment_end = (
            headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        )
        sections.append(
            _Section(
                title=heading_title,
                lines=lines[segment_start:segment_end],
                start=segment_start + 1,
                end=segment_end,
            )
        )
    sections = _merge_small_sections(sections, minimum_section_characters)
    if len(sections) < minimum_sections:
        return _single_card(title, body)
    if len(sections) > maximum_sections:
        kept = sections[:maximum_sections]
        for overflow in sections[maximum_sections:]:
            kept[-1].lines.extend(overflow.lines)
            kept[-1].end = overflow.end
        sections = kept

    cards: List[DocumentCard] = []
    for index, section in enumerate(sections):
        text = section.text()
        cards.append(
            DocumentCard(
                section_index=index,
                title=section.title,
                body=text,
                heading_path=(title[:200], section.title),
                source_line_start=section.start,
                source_line_end=section.end,
                body_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                splitter_version=MARKDOWN_H2_VERSION,
            )
        )
    return cards


def document_id_for_card(
    source_id: str, source_sha256: str, card: DocumentCard
) -> str:
    if card.section_index == 0:
        return source_id
    identity = json.dumps(
        {
            "source_sha256": source_sha256,
            "splitter_version": card.splitter_version,
            "section_index": card.section_index,
            "heading_path": card.heading_path,
            "body_sha256": card.body_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return "card-{}".format(digest[:40])
