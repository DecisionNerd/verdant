"""The sole evaluated-model input seam: verified artifact content, never a manifest row.

Callers authorize and verify the exact sample artifact before calling this function.
Membership metadata, interpretation, truth, schema and review evidence are deliberately
not parameters. Do not serialize a manifest/member into ``content``.
"""

from pydantic import Field, field_validator

from verdant.contracts import Contract

MAX_MODEL_INPUT_BYTES = 65536


class ModelInput(Contract):
    text: str = Field(max_length=MAX_MODEL_INPUT_BYTES)

    @field_validator("text")
    @classmethod
    def bounded_utf8(cls, value):
        if len(value.encode("utf-8")) > MAX_MODEL_INPUT_BYTES or "\x00" in value:
            raise ValueError("Model text must be UTF-8 without NUL and at most 64 KiB")
        return value


def project_model_input(content: bytes, *, modality: str = "text") -> ModelInput:
    """Project only verified, plain UTF-8 text bytes; reject mappings and other modalities."""
    if type(content) is not bytes or modality != "text":
        raise ValueError("Model input requires verified text artifact bytes")
    if len(content) > MAX_MODEL_INPUT_BYTES:
        raise ValueError("Model input exceeds 64 KiB")
    try:
        return ModelInput(text=content.decode("utf-8", errors="strict"))
    except UnicodeError:
        raise ValueError("Model input is not valid UTF-8 text") from None
