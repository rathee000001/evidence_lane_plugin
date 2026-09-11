"""Power BI closed inputs, offline model derivatives and exact export contracts."""

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .registry import Contract

DIGEST = r"^[0-9a-f]{64}$"


class PowerBiSelection(Contract):
    lane_id: Literal["power_bi"] = "power_bi"


class PowerBiIndex(PowerBiSelection):
    filename: str = Field(min_length=1, max_length=1000)
    companion_files: list[str] = Field(default_factory=list, max_length=127)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)
    max_file_bytes: int = Field(default=8_388_608, ge=1, le=16_777_216)
    inspect_models: bool = True
    max_rows_per_table: int = Field(default=20, ge=0, le=1000)

    @model_validator(mode="after")
    def distinct_files(self):
        if self.max_file_bytes > 8_388_608 and (self.companion_files or not self.filename.lower().endswith('.zip')):
            raise ValueError('Only a whole ZIP package permits an explicit file bound above 8 MiB; companions keep their 8 MiB bound.')
        files = [self.filename, *self.companion_files]
        if len({path.replace("\\", "/").casefold() for path in files}) != len(files):
            raise ValueError("Each admitted Power BI file must be distinct.")
        return self


class PowerBiSnapshot(PowerBiSelection):
    snapshot_id: str = Field(pattern=DIGEST)


class PowerBiSchemaRead(PowerBiSelection):
    schema_uri: str | None = Field(default=None, min_length=1, max_length=1000)
    max_bytes: int = Field(default=262_144, ge=2048, le=1_048_576)


class PowerBiQuery(PowerBiSnapshot):
    match_mode: Literal['all', 'any'] = 'all'
    collection: Literal[
        "metadata",
        "text",
        "project",
        "report",
        "page",
        "visual",
        "model",
        "table",
        "column",
        "measure",
        "relationship",
        "partition",
        "expression",
        "role",
        "hierarchy",
        "data_source",
        "perspective",
        "culture",
        "annotation",
        "bookmark",
        "filter",
        "theme",
        "model_metadata",
        "model_row",
        "reference",
        "package_member",
        "opaque",
    ] = "text"
    query: str | None = Field(default=None, min_length=1, max_length=500)
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=20, ge=1, le=100)
    max_bytes: int = Field(default=65_536, ge=2048, le=262_144)


class PowerBiRead(PowerBiSnapshot):
    representation: Literal["power_bi", "original_source", "member", "original_member"] = "power_bi"
    member: str | None = Field(default=None, min_length=1, max_length=1000)
    offset: int = Field(default=0, ge=0, le=33_554_432)
    max_bytes: int = Field(default=65_536, ge=1024, le=131_072)

    @model_validator(mode="after")
    def selected_member(self):
        if (self.representation in {"member", "original_member"}) != (self.member is not None):
            raise ValueError("Only a package member read requires its exact member name.")
        return self


class PowerBiExport(PowerBiSnapshot):
    filename: str = Field(min_length=1, max_length=1000)
    expected_sha256: str | None = Field(default=None, pattern=DIGEST)


class PowerBiGenerate(PowerBiSelection):
    logical_name: str = Field(pattern=r"^[^/\\:]+\.(bim|zip)$", max_length=180)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)
    database: dict[str, JsonValue] | None = None
    files: dict[str, str] | None = Field(default=None, min_length=1, max_length=128)
    resources_base64: dict[str, str] = Field(default_factory=dict, max_length=127)
    entrypoint: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def complete_generation(self):
        if self.logical_name.endswith(".bim"):
            if (
                self.database is None
                or self.files is not None
                or self.entrypoint is not None
                or self.resources_base64
            ):
                raise ValueError("BIM generation requires one database document only.")
        elif self.database is not None or self.files is None or self.entrypoint not in self.files:
            raise ValueError(
                "Project/report ZIP generation requires explicit UTF-8 files and their entrypoint."
            )
        elif len(self.files) + len(self.resources_base64) > 128:
            raise ValueError("A generated Power BI package admits at most 128 explicit files.")
        return self


class PowerBiPartReplacement(Contract):
    part: str = Field(min_length=1, max_length=1000)
    expected_sha256: str | None = Field(default=None, pattern=DIGEST)
    content_utf8: str | None = Field(default=None, min_length=1, max_length=1_048_576)
    content_base64: str | None = Field(default=None, min_length=1, max_length=11_184_812)

    @model_validator(mode="after")
    def exact_part_change(self):
        if self.content_utf8 is not None and self.content_base64 is not None:
            raise ValueError("Select one exact UTF-8 or binary replacement representation.")
        if (
            self.content_utf8 is None
            and self.content_base64 is None
            and self.expected_sha256 is None
        ):
            raise ValueError("Deleting a part requires its exact current hash.")
        return self


class PowerBiEdit(PowerBiSnapshot):
    expected_sha256: str = Field(pattern=DIGEST)
    replacements: list[PowerBiPartReplacement] = Field(min_length=1, max_length=64)
