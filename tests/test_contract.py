"""Contract tests: every operation is implemented, published and returns contract-conformant responses."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest
import yaml
from contract_check import METHODS, SPEC, _deref, check_response
from fastapi.routing import APIRoute

from conftest import ContractClient, make_profile, make_voice
from opal_say import __version__
from opal_say.api.routes import router
from opal_say.contract import CONTRACT_RESOURCE, contract_operations, load_contract

ROOT = Path(__file__).resolve().parents[1]

# The authoritative contract is the decomposed spec at `openapi/opal_say_v{version}.yaml`
# plus `openapi/shared/*.yaml`. The packaged copy that the service loads and serves at
# /openapi.json is a generated artifact: `tools/bundle_contract.py` flattens the shared
# $refs into one self-contained document. The packaged file must always equal a fresh
# bundle of the source, so editing one without regenerating the other fails CI.
ACTIVE_VERSION = __version__
SOURCE_CONTRACT = ROOT / "openapi" / f"opal_say_v{ACTIVE_VERSION}.yaml"
PACKAGED = CONTRACT_RESOURCE
TRACKED = "src/opal_say/contract/opal_say.yaml"


def _bundled() -> dict:
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        from bundle_contract import bundle
    finally:
        sys.path.remove(str(ROOT / "tools"))
    return bundle(SOURCE_CONTRACT)


def test_source_contract_parses_at_active_version():
    """The openapi/ source document declares the implemented version."""
    doc = yaml.safe_load(SOURCE_CONTRACT.read_text(encoding="utf-8"))
    assert doc["info"]["version"] == ACTIVE_VERSION


def test_packaged_contract_matches_bundled_source():
    """The packaged contract must equal a fresh bundle of the openapi/ source."""
    assert _bundled() == load_contract()


def test_packaged_contract_is_a_readable_regular_file():
    """The packaged contract must exist as a regular file, not a missing or dangling entry."""
    resource = files("opal_say").joinpath(PACKAGED)
    assert resource.is_file(), f"packaged contract {PACKAGED} is missing or not a regular file"
    assert resource.read_text(encoding="utf-8").startswith("openapi:")


def test_load_contract_returns_valid_v1_document():
    """load_contract() must yield a parseable OpenAPI document pinned to the implemented v1.0 API."""
    spec = load_contract()
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["version"] == ACTIVE_VERSION, "packaged contract must not silently differ from __version__"
    assert spec["paths"] and spec["components"]["schemas"]


def test_package_metadata_version_matches_contract_version():
    """pyproject metadata, `opal_say.__version__` and the packaged OpenAPI document
    must move together — a release bump that misses one is drift.

    pyproject is read from the file rather than importlib.metadata: an editable
    install snapshots the version at install time and stays stale after a bump.
    """
    import tomllib

    pyproject = tomllib.loads(ROOT.joinpath("pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__
    assert load_contract()["info"]["version"] == __version__


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys (PyYAML silently keeps the last)."""


def _reject_duplicate_keys(loader, node, deep=False):
    seen, mapping = set(), {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        assert key not in seen, f"duplicate YAML key {key!r} (line {key_node.start_mark.line + 1})"
        seen.add(key)
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_UniqueKeyLoader.add_constructor("tag:yaml.org,2002:map", _reject_duplicate_keys)


def test_contracts_have_no_duplicate_yaml_keys():
    """Duplicate keys (e.g. two `paths:` entries or a repeated path) must not parse silently."""
    for path in sorted((ROOT / "openapi").rglob("*.yaml")) + [ROOT / TRACKED]:
        yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)


def test_operation_ids_are_unique():
    ops = [op["operationId"] for item in SPEC["paths"].values() for m, op in item.items() if m in METHODS]
    dupes = {op_id for op_id in ops if ops.count(op_id) > 1}
    assert not dupes, f"duplicate operationIds: {sorted(dupes)}"


PATH_PARAM = re.compile(r"\{([^{}]+)\}")
PATH_ITEM_KEYS = frozenset((*METHODS, "parameters", "summary", "description", "servers"))


def test_paths_are_valid_templates_with_declared_params():
    """Every path starts with '/', has well-formed `{param}` placeholders and declares
    exactly the path parameters its template uses (path-item or operation level)."""
    for template, item in SPEC["paths"].items():
        assert template.startswith("/") and "//" not in template, template
        assert any(m in item for m in METHODS), f"{template} declares no operations"
        assert set(item) <= PATH_ITEM_KEYS, f"{template} has unexpected keys: {set(item) - PATH_ITEM_KEYS}"
        residual = PATH_PARAM.sub("", template)
        assert "{" not in residual and "}" not in residual, f"malformed placeholders in {template}"
        declared = {
            p["name"]
            for section in (item, *item.values())
            if isinstance(section, dict)
            for raw in section.get("parameters") or []
            if (p := _deref(raw)).get("in") == "path"
        }
        assert set(PATH_PARAM.findall(template)) == declared, (
            f"{template}: placeholders {set(PATH_PARAM.findall(template))} != declared path params {declared}"
        )


def test_git_records_contract_as_regular_file():
    """Regression guard: Git must store the packaged contract as mode 100644.

    It was once committed as a symlink (mode 120000) whose 'target' was the YAML
    text itself, which makes `git checkout` fail on Linux CI.
    """
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("git metadata not available")
    out = subprocess.run(
        [git, "ls-files", "-s", "--", TRACKED],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert out and out[0] == "100644", f"{TRACKED} has git mode {out[0] if out else '<untracked>'}, expected 100644"


def test_every_contract_operation_has_a_server_route():
    routes = {
        r.operation_id: (method, r.path)
        for r in router.routes
        if isinstance(r, APIRoute)
        for method in r.methods
    }
    for op_id, (method, path) in contract_operations().items():
        assert op_id in routes, f"operation {op_id} is not implemented"
        assert routes[op_id] == (method, path), f"{op_id} bound to {routes[op_id]}, contract says {(method, path)}"


def test_no_undocumented_api_routes():
    documented = set(contract_operations().values())
    for r in router.routes:
        if isinstance(r, APIRoute):
            for method in r.methods:
                assert (method, r.path) in documented, f"route {method} {r.path} is not in the contract"


def test_published_openapi_is_the_contract(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert resp.json() == SPEC


class RecordingClient(ContractClient):
    seen: set[str]

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        self._auto_if_match(method, url, kwargs)
        resp = super(ContractClient, self).request(method, url, *args, **kwargs)
        op = check_response(resp)
        if op:
            self.seen.add(op["operationId"])
        return resp


@pytest.fixture
def recording(app):
    with RecordingClient(app) as c:
        c.seen = set()
        yield c


def test_walkthrough_exercises_every_operation_with_conformant_responses(recording, config):
    c = recording
    assert c.get("/api/v1/info/").json()["name"] == "opal-say-svc"
    assert c.get("/api/v1/info/ping").json() == {"status": "ok"}
    assert c.get("/api/v1/health/").status_code == 200

    assert c.get("/api/v1/engine/").status_code == 200
    assert c.get("/api/v1/engine/fake").status_code == 200
    assert c.get("/api/v1/engine/nope").status_code == 404
    assert c.patch("/api/v1/engine/fake", json={"enabled": True}).status_code == 200
    assert c.patch("/api/v1/engine/fake", json={}).status_code == 422
    assert c.post("/api/v1/engine/fake/validate").json()["valid"] is True

    make_voice(c, "v1")
    make_voice(c, "v2", ref="tone-b")
    assert c.get("/api/v1/voice/", params={"engine_id": "fake", "enabled": True}).status_code == 200
    assert c.get("/api/v1/voice/v1").status_code == 200
    assert c.patch("/api/v1/voice/v1", json={"name": "Voice One"}).status_code == 200
    assert c.post("/api/v1/voice/v1/validate").json()["valid"] is True
    assert c.post("/api/v1/voice/v1/test").status_code == 200
    assert c.post("/api/v1/voice/v1/test", json={"output_format": "pcm"}).status_code == 200

    p1 = make_profile(c, "p1", "v1", is_active=True)
    p2 = make_profile(c, "p2", "v2")
    assert c.get("/api/v1/profiles/").status_code == 200
    assert c.get(f"/api/v1/profiles/{p1['id']}").status_code == 200
    assert c.patch(f"/api/v1/profiles/{p2['id']}", json={"speed": 1.5}).status_code == 200
    assert c.post(f"/api/v1/profiles/{p2['id']}/activate").status_code == 200
    assert c.delete(f"/api/v1/profiles/{p1['id']}").status_code == 204

    assert c.post("/api/v1/persona/", json={"persona_id": "amy", "name": "Amy", "voice_id": "v2"}).status_code == 201
    assert c.get("/api/v1/persona/").status_code == 200
    assert c.get("/api/v1/persona/amy").status_code == 200
    assert c.patch("/api/v1/persona/amy", json={"description": "warm"}).status_code == 200
    assert c.post("/api/v1/generate", json={"text": "Hello there.", "persona": "amy"}).status_code == 200
    assert c.delete("/api/v1/persona/amy").status_code == 204

    assert c.post("/api/v1/generate", json={"text": "Hello there."}).status_code == 200
    assert c.post("/api/v1/generate/stream", json={"text": "Hello there."}).status_code == 200

    assert c.put("/api/v1/settings/greeting", json={"value": "hi"}).status_code == 200
    assert c.get("/api/v1/settings/").json() == {"greeting": "hi"}
    assert c.get("/api/v1/settings/greeting").status_code == 200
    assert c.patch("/api/v1/settings/", json={"greeting": None, "other": "x"}).status_code == 200
    assert c.get("/api/v1/settings/").json() == {"other": "x"}
    assert c.delete("/api/v1/settings/other").status_code == 204

    assert c.get("/api/v1/health/live").json() == {"status": "ok", "ready": True}
    assert c.get("/api/v1/health/ready").json()["ready"] is True

    created = c.post("/api/v1/backup/create")
    assert created.status_code == 201
    filename = created.json()["filename"]
    assert created.headers["location"] == f"/api/v1/backup/download/{filename}"
    assert c.get("/api/v1/backup/list").status_code == 200
    assert c.get(f"/api/v1/backup/download/{filename}").status_code == 200
    assert c.post(f"/api/v1/backup/restore/{filename}").status_code == 200
    assert c.get("/api/v1/backup/export").status_code == 200
    assert c.get("/api/v1/backup/validate").status_code == 200
    payload = c.get("/api/v1/backup/export").json()
    assert c.post("/api/v1/backup/validate", json=payload).status_code == 200
    config.config_dir.mkdir(parents=True, exist_ok=True)
    (config.config_dir / "settings.yaml").write_text("version: 1\nsettings:\n  chunk_max_chars: 300\n")
    assert c.post("/api/v1/backup/import").json()["success"] is True
    assert c.delete(f"/api/v1/backup/{filename}").status_code == 204

    assert c.delete("/api/v1/voice/v1").status_code == 204

    missing = set(contract_operations()) - c.seen
    assert not missing, f"operations not exercised: {sorted(missing)}"
