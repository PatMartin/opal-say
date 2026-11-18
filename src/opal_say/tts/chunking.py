"""Internal text chunking. Chunk boundaries are never exposed to API clients."""

from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?…;:])\s+|\n\s*\n|\n")
_CLAUSE_END = re.compile(r"(?<=[,;:—–])\s+")


def _split_long(piece: str, max_chars: int) -> list[str]:
    if len(piece) <= max_chars:
        return [piece]
    out: list[str] = []
    for clause in _CLAUSE_END.split(piece):
        if len(clause) <= max_chars:
            out.append(clause)
            continue
        current = ""
        for word in clause.split():
            while len(word) > max_chars:
                if current:
                    out.append(current)
                    current = ""
                out.append(word[:max_chars])
                word = word[max_chars:]
            candidate = f"{current} {word}" if current else word
            if len(candidate) > max_chars:
                out.append(current)
                current = word
            else:
                current = candidate
        if current:
            out.append(current)
    return out


def chunk_text(text: str, max_chars: int = 400) -> list[str]:
    """Split plain text into ordered chunks of at most `max_chars`, preferring sentence boundaries."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        sentence = " ".join(sentence.split())
        if sentence:
            pieces.extend(_split_long(sentence, max_chars))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}" if current else piece
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def split_stream_ready(text: str, max_chars: int = 400) -> tuple[list[str], str]:
    """Split the leading complete-sentence portion of `text` into chunks.

    Returns (chunks, remainder): chunks cover everything up to the last
    sentence boundary; the trailing partial sentence is handed back as
    remainder for the caller to keep buffering. Used by incremental input,
    where text arrives in arbitrary fragments and only complete sentences
    should be synthesized yet. A fragment larger than `max_chars` with no
    boundary is force-chunked so the pipeline is never starved by one long
    run-on fragment — its last chunk is retained as remainder.
    """
    boundary = 0
    for m in _SENTENCE_END.finditer(text):
        boundary = m.end()
    if boundary == 0:
        if len(text) > max_chars:
            chunks = chunk_text(text, max_chars)
            if len(chunks) > 1:
                return chunks[:-1], chunks[-1]
        return [], text
    return chunk_text(text[:boundary], max_chars), text[boundary:]
