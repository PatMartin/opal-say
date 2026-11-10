"""Flatten openapi/opal_say_v{ver}.yaml + shared $refs into one self-contained contract.

The repository keeps the decomposed spec (a service document plus shared
openapi/shared/*.yaml files) as the source of truth. The service serves a single
document at /openapi.json and the CLI derives its operation map from it, so the
packaged contract must be self-contained: every external file reference is
inlined into the bundle's components and rewritten to a local #/components/ ref.
Internal refs inside imported components are imported transitively.

Usage: python tools/bundle_contract.py [--check]
  --check   do not write; fail if the packaged contract differs from a fresh bundle
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# The authoritative source follows the package version so a contract rename cannot
# silently orphan this tool (importable directly from src/ without the venv).
sys.path.insert(0, str(ROOT / "src"))
from opal_say import __version__  # noqa: E402

SOURCE = ROOT / "openapi" / f"opal_say_v{__version__}.yaml"
TARGET = ROOT / "src" / "opal_say" / "contract" / "opal_say.yaml"

_docs: dict[Path, dict] = {}


def _load(path: Path) -> dict:
    if path not in _docs:
        _docs[path] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _docs[path]


def bundle(source: Path = SOURCE) -> dict:
    bundle_doc = copy.deepcopy(_load(source))
    bundle_doc.setdefault("components", {})
    seen: set[tuple[Path, str, str]] = set()

    def resolve(node, src_file: Path):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                file_part, _, frag = ref.partition("#")
                target_file = (src_file.parent / file_part).resolve() if file_part else src_file
                if file_part or target_file != source.resolve():
                    parts = frag.strip("/").split("/")
                    if len(parts) != 3 or parts[0] != "components":
                        # Non-components fragment (e.g. #/x-opal-event-envelope):
                        # inline the referenced node at this position.
                        target = _load(target_file)
                        for part in parts:
                            target = target[part]
                        merged = {k: resolve(v, src_file) for k, v in node.items() if k != "$ref"}
                        inlined = resolve(copy.deepcopy(target), target_file)
                        if isinstance(inlined, dict) and merged:
                            inlined = {**inlined, **merged}
                        elif merged:
                            raise ValueError(f"cannot merge siblings onto scalar $ref {ref!r}")
                        return inlined
                    kind, name = parts[1], parts[2]
                    if kind == "pathItems":
                        # Path items are inlined: the loader and op extraction read
                        # methods directly off paths entries.
                        target = _load(target_file)["components"]["pathItems"][name]
                        merged = {k: resolve(v, src_file) for k, v in node.items() if k != "$ref"}
                        return {**resolve(copy.deepcopy(target), target_file), **merged}
                    key = (target_file, kind, name)
                    # True when this node itself occupies the bundle's component
                    # slot (e.g. securitySchemes.BearerAuth aliasing the kernel):
                    # the resolved content must replace it, not a self-ref.
                    self_slot = bundle_doc["components"].get(kind, {}).get(name) is node
                    if key not in seen:
                        seen.add(key)
                        target = _load(target_file)["components"][kind][name]
                        resolved = resolve(copy.deepcopy(target), target_file)
                        comps = bundle_doc["components"].setdefault(kind, {})
                        if name in comps:
                            existing = comps[name]
                            alias = existing.get("$ref") if isinstance(existing, dict) else None
                            alias_target = ""
                            if isinstance(alias, str):
                                a_file, _, a_frag = alias.partition("#")
                                alias_target = f"{(src_file.parent / a_file).resolve() if a_file else src_file}#{a_frag}"
                            if alias_target != f"{target_file}#{frag}" and existing != resolved:
                                raise ValueError(f"component name collision: {kind}/{name}")
                        comps[name] = resolved
                    merged = {k: resolve(v, src_file) for k, v in node.items() if k != "$ref"}
                    if self_slot:
                        content = bundle_doc["components"][kind][name]
                        return {**content, **merged} if merged else content
                    return {"$ref": f"#/components/{kind}/{name}", **merged}
                return node  # internal ref of the root document: already bundle-local
            # Snapshot: resolving a child may import a new component into the very
            # dict being iterated (e.g. a kernel $ref inside components.schemas).
            return {k: resolve(v, src_file) for k, v in list(node.items())}
        if isinstance(node, list):
            return [resolve(v, src_file) for v in node]
        return node

    return resolve(bundle_doc, source.resolve())


def main() -> int:
    doc = bundle()
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120)
    if "--check" in sys.argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print(f"{TARGET} is stale; run tools/bundle_contract.py", file=sys.stderr)
            return 1
        print(f"{TARGET} is up to date")
        return 0
    TARGET.write_text(text, encoding="utf-8")
    ops = sum(1 for item in doc.get("paths", {}).values() for k in item if k in {"get", "put", "post", "patch", "delete"})
    print(f"wrote {TARGET} ({ops} operations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
