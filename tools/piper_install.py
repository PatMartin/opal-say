"""Flatten and register Piper voices downloaded into resources/piper/.

The rhasspy/piper-voices Hugging Face snapshot nests models as
``<family>/<locale>/<speaker>/<quality>/<stem>.onnx`` (+ ``.onnx.json``),
but the Piper engine resolves ``voices_dir/<ref>.onnx`` flat. This tool:

1. moves complete ``<stem>.onnx`` + ``<stem>.onnx.json`` pairs to the top level;
2. registers each voice with the running service via ``opal say voices add``.

Idempotent: already-flattened files are left alone and existing voice IDs are
skipped, so it can be rerun while a catalog download is still in progress.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPER_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "resources" / "piper"
OPAL = ROOT / ".venv" / "Scripts" / "opal.exe"


def flatten() -> list[tuple[Path, dict]]:
    """Move nested model pairs to the top level; return (onnx, json-meta) pairs."""
    pairs: list[tuple[Path, dict]] = []
    for onnx in sorted(PIPER_DIR.glob("*/*/*/*/*.onnx")):
        meta_path = onnx.with_suffix(".onnx.json")
        if not meta_path.exists():
            continue  # pair still downloading
        dest = PIPER_DIR / onnx.name
        if not dest.exists():
            onnx.rename(dest)
            meta_path.rename(PIPER_DIR / meta_path.name)
        try:
            meta = json.loads((PIPER_DIR / meta_path.name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        pairs.append((dest, meta))
    for onnx in sorted(PIPER_DIR.glob("*.onnx")):
        meta_path = PIPER_DIR / (onnx.stem + ".onnx.json")
        if not meta_path.exists() or any(onnx == d for d, _ in pairs):
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        pairs.append((onnx, meta))
    return pairs


def voice_fields(stem: str, meta: dict) -> dict[str, str]:
    """Derive voice_id/name/language from `<locale>-<speaker>-<quality>` + json."""
    parts = stem.split("-")
    locale, quality, speaker = parts[0], parts[-1], "-".join(parts[1:-1])
    voice_id = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    voice_id = re.sub(r"[^A-Za-z0-9._-]+", "-", voice_id.lower().replace("_", "-")).strip("-")
    lang = (meta.get("language") or {})
    code = lang.get("code") or locale
    english = lang.get("name_english") or code
    name = f"{speaker} {quality} ({english} {code})"
    return {"voice_id": voice_id, "name": name, "language": code}


def register(fields: dict[str, str]) -> str:
    cmd = [
        str(OPAL), "say", "voices", "add", fields["voice_id"],
        "--name", fields["name"], "--engine", "piper",
        "--ref", fields["ref"], "--language", fields["language"],
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        return "added"
    detail = (res.stdout + res.stderr).lower()
    if "conflict" in detail or "409" in detail or "already" in detail:
        return "exists"
    return f"FAILED: {(res.stdout + res.stderr).strip()[:120]}"


def main() -> None:
    pairs = flatten()
    counts = {"added": 0, "exists": 0, "FAILED": 0}
    for onnx, meta in pairs:
        fields = voice_fields(onnx.stem, meta)
        fields["ref"] = onnx.stem
        result = register(fields)
        counts["FAILED" if result.startswith("FAILED") else result] += 1
        if result.startswith("FAILED"):
            print(f"  {fields['voice_id']}: {result}")
    print(f"{len(pairs)} model pairs: {counts['added']} added, "
          f"{counts['exists']} already registered, {counts['FAILED']} failed")


if __name__ == "__main__":
    if not PIPER_DIR.is_dir():
        sys.exit(f"missing {PIPER_DIR}")
    main()
