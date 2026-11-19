"""Pydantic DTOs mirroring `components.schemas` in opal_say.yaml (the contract is authoritative)."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

AudioFormat = Literal["wav", "pcm"]
VOICE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
SampleRate = Annotated[int, Field(ge=8000, le=192000)]
Speed = Annotated[float, Field(ge=0.25, le=4.0)]
Volume = Annotated[float, Field(ge=0.0, le=2.0)]
EngineOptions = dict[str, Any]

_T = TypeVar("_T")


def _reject_null(value: Any) -> Any:
    if value is None:
        raise ValueError("may not be null")
    return value


# A PATCH body field: may be omitted entirely, but may not be sent as JSON null.
NonNull = Annotated[_T | None, BeforeValidator(_reject_null)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Patch(_Request):
    """PATCH bodies: `minProperties: 1`; non-nullable fields may be omitted but not null."""

    @model_validator(mode="after")
    def _non_empty(self):
        if not self.model_fields_set:
            raise ValueError("at least one property must be supplied")
        return self

    def changes(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.model_fields_set}


# -- engines -------------------------------------------------------------------


class EngineRead(BaseModel):
    engine_id: str
    name: str
    description: str | None = None
    enabled: bool
    available: bool
    options: EngineOptions
    metadata: dict[str, Any]


class EngineUpdate(_Patch):
    enabled: NonNull[bool] = None
    options: NonNull[EngineOptions] = None


# -- voices --------------------------------------------------------------------


class VoiceCreate(_Request):
    voice_id: str = Field(min_length=1, max_length=200, pattern=VOICE_ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    engine_id: str = Field(min_length=1)
    engine_voice_ref: str = Field(min_length=1, max_length=2048)
    language: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    engine_options: EngineOptions = Field(default_factory=dict)


class VoiceUpdate(_Patch):
    name: NonNull[str] = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    engine_id: NonNull[str] = Field(default=None, min_length=1)
    engine_voice_ref: NonNull[str] = Field(default=None, min_length=1, max_length=2048)
    language: str | None = Field(default=None, max_length=64)
    enabled: NonNull[bool] = None
    engine_options: NonNull[EngineOptions] = None


class VoiceRead(BaseModel):
    voice_id: str
    name: str
    description: str | None = None
    engine_id: str
    engine_voice_ref: str
    language: str | None = None
    enabled: bool
    native_sample_rate: int | None = None
    engine_options: EngineOptions
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


class VoiceTestRequest(_Request):
    text: str = Field(default="This is a test of the selected OPAL Say voice.", min_length=1)
    speed: Speed = 1.0
    volume: Volume = 1.0
    output_format: AudioFormat = "wav"
    sample_rate: SampleRate | None = None
    engine_options: EngineOptions = Field(default_factory=dict)


# -- personas ------------------------------------------------------------------


class PersonaCreate(_Request):
    persona_id: str = Field(min_length=1, max_length=200, pattern=VOICE_ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    voice_id: str | None = Field(default=None, min_length=1)
    enabled: bool = True


class PersonaUpdate(_Patch):
    name: NonNull[str] = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    voice_id: str | None = Field(default=None, min_length=1)
    enabled: NonNull[bool] = None


class PersonaRead(BaseModel):
    persona_id: str
    name: str
    description: str | None = None
    voice_id: str | None = None
    enabled: bool
    origin: Literal["local", "owner"] = "local"
    created_at: str
    updated_at: str


# -- profiles ------------------------------------------------------------------


def validate_profile_document(value: Any, schema: str) -> Any:
    from jsonschema import Draft202012Validator

    from opal_say.contract import load_contract

    validator = Draft202012Validator({"$ref": f"#/components/schemas/{schema}",
                                      "components": load_contract()["components"]})
    error = next(validator.iter_errors(value), None)
    if error is not None:
        raise ValueError(f"{'.'.join(map(str, error.absolute_path))}: {error.message}")
    return value


class ProfileCreate(_Request):
    id: str | None = None
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    is_active: bool = False
    shared: dict[str, Any] = Field(default_factory=dict)
    modules: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _namespace(cls, value):
        return validate_profile_document(value, "OpalProfileCreate")


class ProfileUpdate(_Patch):
    name: NonNull[str] = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    is_active: NonNull[bool] = None
    shared: NonNull[dict[str, Any]] = None
    modules: NonNull[dict[str, Any]] = None

    @model_validator(mode="before")
    @classmethod
    def _namespace(cls, value):
        return validate_profile_document(value, "OpalProfilePatch")


class ProfileRead(_Request):
    id: str
    name: str
    description: str | None = None
    is_active: bool
    shared: dict[str, Any]
    modules: dict[str, Any]
    created_at: str
    updated_at: str


# -- generation ----------------------------------------------------------------


class GenerationParams(_Request):
    """Voice/engine/tuning fields shared by one-shot and incremental generation."""

    profile_id: Annotated[str, Field(min_length=1)] | None = None
    persona_id: Annotated[str, Field(min_length=1, max_length=200, pattern=VOICE_ID_PATTERN)] | None = None
    persona: Annotated[str, Field(min_length=1, max_length=200, pattern=VOICE_ID_PATTERN)] | None = None

    @model_validator(mode="after")
    def _persona_alias(self):
        if self.persona_id is not None and self.persona is not None and self.persona_id != self.persona:
            raise ValueError("persona_id and persona must agree when both are supplied")
        self.persona = self.persona_id if self.persona_id is not None else self.persona
        return self
    voice_id: Annotated[str, Field(min_length=1)] | None = None
    engine_id: Annotated[str, Field(min_length=1)] | None = None
    speed: Speed | None = None
    volume: Volume | None = None
    sample_rate: SampleRate | None = None
    engine_options: EngineOptions = Field(default_factory=dict)


class GenerateStreamRequest(GenerationParams):
    text: str = Field(min_length=1)


class GenerateRequest(GenerateStreamRequest):
    output_format: AudioFormat | None = None


class IncrementalStreamParams(GenerationParams):
    """First NDJSON line of the incremental stream input: generation params without `text`."""


# -- service lifecycle ---------------------------------------------------------


class ServiceStartOptions(BaseModel):
    """`OpalServiceStartOptions`: open body; only `append` is portable."""

    model_config = ConfigDict(extra="allow")
    append: bool = False


class ServiceStatus(BaseModel):
    """`OpalServiceStatus` for service start/stop."""

    state: Literal["running", "stopped"]
    pid: int | None = None
    previous_pid: int | None = None
    log_file: str


class ServiceLogTail(BaseModel):
    """`OpalServiceLogTail` for service log."""

    log_file: str | None = None
    lines: list[str]


# -- settings / health / backup -------------------------------------------------


class SettingRead(BaseModel):
    id: int | None = None
    key: str
    value: Any
    configured_value: Any
    default: Any
    has_override: bool
    is_default: bool
    source: Literal["default", "persisted", "dotenv", "environment"]
    restart_required: bool
    pending_restart: bool
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: str | None = None


class SettingUpdate(_Request):
    value: Any


class ValidationMessage(BaseModel):
    code: str
    message: str
    field: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationResult(BaseModel):
    valid: bool
    errors: list[ValidationMessage]
    warnings: list[ValidationMessage]
    details: dict[str, Any]


class EngineHealthRead(BaseModel):
    engine_id: str
    enabled: bool
    available: bool
    message: str | None = None


class HealthRead(BaseModel):
    name: str
    api_version: str
    status: Literal["ok", "degraded", "error"]
    version: str
    live: bool
    ready: bool
    active_profile_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class BackupRead(BaseModel):
    filename: str
    created_at: str
    size_bytes: int | None = None


class BackupCreateResponse(BaseModel):
    filename: str
    message: str


class ConfigSaveRequest(_Request):
    destination: Literal["managed", "response"] = "managed"


class ConfigSnapshotRequest(_Request):
    snapshot: NonNull[dict[str, Any]] = None


class ConfigExport(BaseModel):
    format_version: str
    version: str | None = None  # legacy bundle field name; superseded by format_version
    exported_at: str
    engines: list[EngineRead]
    voices: list[VoiceRead]
    profiles: list[ProfileRead]
    settings: dict[str, Any]


class ConfigImportResult(BaseModel):
    success: bool
    validation: ValidationResult
    message: str | None = None


class OperationResult(BaseModel):
    success: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ServiceInfo(BaseModel):
    name: str
    version: str
    api_version: str
    description: str


class LivenessRead(BaseModel):
    status: Literal["ok"] = "ok"
