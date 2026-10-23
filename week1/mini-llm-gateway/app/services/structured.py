from __future__ import annotations

import json
from typing import Any

import jsonschema

from app.core.errors import GatewayError
from app.schemas import ResponseFormat


class StructuredOutputError(GatewayError):
    """Raised when a response cannot be parsed or validated against the schema."""

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(
            message,
            status_code=422,
            error_type="invalid_request_error",
            code="structured_output_invalid",
            details=details,
        )
        self.repair_instruction = (
            f"The previous response failed validation: {message}. "
            "Reply with ONLY a valid JSON object that satisfies the requested schema, "
            "without markdown fences or extra prose."
        )


def _extract_json(text: str) -> Any:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`").strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise StructuredOutputError("The response was not valid JSON", details=text[:1000])


def validate_structured(text: str, response_format: ResponseFormat) -> Any:
    """Parse and validate text against the requested response_format."""
    if response_format.type == "text":
        return None

    parsed = _extract_json(text)
    if response_format.type == "json_object":
        if not isinstance(parsed, dict):
            raise StructuredOutputError("The response was not a JSON object")
        return parsed

    spec = response_format.json_schema
    schema = spec.schema_ if spec is not None else None
    if not schema:
        raise StructuredOutputError("response_format.json_schema.schema is missing")
    try:
        jsonschema.validate(parsed, schema)
    except jsonschema.ValidationError as exc:
        raise StructuredOutputError(f"failed JSON Schema validation: {exc.message}") from exc
    return parsed
