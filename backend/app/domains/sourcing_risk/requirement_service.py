"""Validated boundary for turning sourcing text into procurement requirements."""

import json
import re
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from app.core.config import settings

REQUIRED_FIELDS = ("category", "specification")
MULTI_CATEGORY_SEPARATORS = ("、", ",", "，", "/")
_REGION_NAMES = ("华东", "华南", "华北", "西南", "西北", "东北")
_SOURCING_REQUEST_PATTERN = re.compile(
    r"(?:找|推荐|寻找|采购|需要)(?P<target>.+?)(?:供应商|厂家|厂商)"
)
_MATERIAL_NUMBER_PATTERN = re.compile(r"物料号(?:为|是|[:：])?\s*(?P<material>\d{4,})")
_HISTORICAL_SUPPLIER_PATTERN = re.compile(r"(?P<material>.+?)(?:有)?哪些历史合作供应商")


class SourcingRequirement(BaseModel):
    """The only fields an LLM may extract from a sourcing request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: StrictStr | None = Field(default=None, max_length=128)
    product: StrictStr | None = Field(default=None, max_length=256)
    material: StrictStr | None = Field(default=None, max_length=256)
    specification: StrictStr | None = Field(default=None, max_length=2000)
    region: StrictStr | None = Field(default=None, max_length=256)
    supply_region: StrictStr | None = Field(default=None, max_length=256)
    quantity: StrictInt | None = Field(default=None, ge=1)
    budget: StrictStr | None = Field(default=None, max_length=256)
    qualifications: StrictStr | None = Field(default=None, max_length=2000)
    delivery: StrictStr | None = Field(default=None, max_length=2000)
    risk_limit: StrictStr | None = Field(default=None, max_length=256)
    candidate_count: StrictInt | None = Field(default=None, ge=1, le=20)
    must_have: list[StrictStr] = Field(default_factory=list, max_length=20)
    optional_conditions: list[StrictStr] = Field(default_factory=list, max_length=20)


def extract_requirement(
    raw_text: str,
    provided: dict[str, Any] | None = None,
    validation_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ask the model for JSON limited to the requirement schema."""
    prompt = {
        "task": "Extract one procurement requirement from the user text.",
        "raw_text": raw_text,
        "provided": provided or {},
        "validation_errors": validation_errors or [],
        "allowed_fields": SourcingRequirement.model_json_schema(),
        "rules": [
            "Return a JSON object only.",
            "Do not infer supplier identity, scores, sanctions, or any decision.",
            "Do not write or request side effects.",
            "Use null when a field is absent.",
        ],
    }
    client = OpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_BASE_URL)
    response = client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是采购需求结构化解析器。只能提取用户明确提供的采购字段，"
                    "不得执行工具、写入数据或推断供应商身份、风险和决策。"
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        temperature=0,
        response_format={"type": "json_object"},
        max_tokens=600,
        timeout=30,
    )
    content = response.choices[0].message.content
    return json.loads(content or "{}")


def repair_requirement(
    raw_text: str,
    provided: dict[str, Any] | None,
    validation_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Perform the sole allowed correction attempt for malformed extraction."""
    return extract_requirement(raw_text, provided, validation_errors)


def missing_requirement_fields(requirement: dict[str, Any]) -> list[str]:
    """Return the required fields absent from a partially parsed requirement."""
    return [field for field in REQUIRED_FIELDS if not requirement.get(field)]


def parse_requirement(raw_text: str, provided: dict | None = None) -> dict:
    """Extract, validate once, and route incomplete requirements to clarification."""
    provided_values = provided or {}
    try:
        candidate = _extract_candidate(raw_text, provided_values)
    except (TypeError, ValueError) as exc:
        return _repair_or_clarify(raw_text, provided_values, _errors_from_exception(exc))

    if _contains_multiple_categories(candidate.get("category")):
        return _clarification_for_fields(["category"], candidate)

    try:
        requirement = SourcingRequirement.model_validate(candidate)
    except ValidationError as exc:
        return _repair_or_clarify(raw_text, provided_values, exc.errors())

    return _validated_result(requirement)


def resolve_harness_requirement(
    raw_text: str,
    provided: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a sourcing requirement for the read-only Harness path."""
    provided_values = dict(provided or {})
    if provided_values.get("category") and not _SOURCING_REQUEST_PATTERN.search(raw_text or ""):
        return _harness_ready(_normalise_harness_requirement(provided_values))

    # Preserve the material/category that appears before “寻找历史供应商”.
    # A generic LLM extractor can otherwise latch onto the word “历史” and
    # send an unusable category to the local material-supplier index.
    deterministic = _fallback_requirement_from_text(raw_text)
    if deterministic and re.search(r"为.+?(?:寻找|查找|搜索)(?:历史)?(?:合作)?供应商", raw_text or ""):
        return _harness_ready(deterministic, extraction_source="deterministic_fallback")

    if settings.LLM_API_KEY:
        try:
            parsed = parse_requirement(raw_text, provided_values)
        except Exception:
            parsed = {"status": "clarification_required", "missing": ["category"]}
        if parsed.get("status") == "ready":
            return _harness_ready(_normalise_harness_requirement(parsed["requirement"]))

    fallback = _fallback_requirement_from_text(raw_text)
    if fallback:
        return _harness_ready(fallback, extraction_source="deterministic_fallback")
    return {"status": "clarification_required", "missing": ["category"], "extraction_source": "unresolved"}


def _harness_ready(
    requirement: dict[str, Any],
    *,
    extraction_source: str = "llm_validated",
) -> dict[str, Any]:
    return {"status": "ready", "requirement": requirement, "extraction_source": extraction_source}


def _normalise_harness_requirement(raw: dict[str, Any]) -> dict[str, Any]:
    aliases = {
        "spec": "specification",
        "region_required": "region",
        "supply_area": "supply_region",
        "qualification": "qualifications",
    }
    payload = dict(raw)
    for source, target in aliases.items():
        if not payload.get(target) and payload.get(source) is not None:
            payload[target] = payload[source]
    allowed = set(SourcingRequirement.model_fields)
    validated = SourcingRequirement.model_validate(
        {key: value for key, value in payload.items() if key in allowed}
    )
    result = validated.model_dump(mode="json", exclude_none=True)
    if not result.get("specification"):
        result["specification"] = result.get("product") or result.get("material") or result.get("category")
    if raw.get("request_id"):
        result["request_id"] = str(raw["request_id"])
    return result


def _fallback_requirement_from_text(message: str) -> dict[str, Any] | None:
    material_number = _MATERIAL_NUMBER_PATTERN.search(message or "")
    if material_number:
        material = material_number.group("material")
        return {
            "category": "物料号寻源",
            "material": material,
            "product": material,
            "specification": material,
            "must_have": [],
            "optional_conditions": [],
        }
    historical = _HISTORICAL_SUPPLIER_PATTERN.search(message or "")
    if historical:
        material = historical.group("material").strip(" ，,、？?请帮我查询")
        if material:
            return {
                "category": material,
                "material": material,
                "product": material,
                "specification": material,
                "must_have": [],
                "optional_conditions": [],
            }
    by_material = re.search(r"为(?P<target>.+?)(?:寻找|查找|搜索)(?:历史)?(?:合作)?供应商", message or "")
    if by_material:
        material = by_material.group("target").strip(" ，,、的")
        if material:
            return {
                "category": material,
                "material": material,
                "product": material,
                "specification": material,
                "must_have": [],
                "optional_conditions": [],
            }
    match = _SOURCING_REQUEST_PATTERN.search(message or "")
    if not match:
        return None
    target = match.group("target").strip(" ，,、").rstrip("的").strip()
    if not target:
        return None
    risk_limit = None
    risk_filter = re.search(r"(?:领域)?风险(?P<level>最低|较低|可控|最小)$", target)
    if risk_filter:
        risk_limit = risk_filter.group("level")
        target = target[:risk_filter.start()].strip()
        if target.endswith("领域"):
            target = target[:-2].strip()
    if not target:
        return None
    region = next((item for item in _REGION_NAMES if target.startswith(item)), None)
    category = target[len(region):].strip() if region else target
    if not category:
        return None
    return {
        "category": category,
        "product": category,
        "specification": category,
        **({"risk_limit": risk_limit} if risk_limit else {}),
        **({"region": region, "supply_region": region} if region else {}),
        "must_have": [],
        "optional_conditions": [],
    }


def _repair_or_clarify(
    raw_text: str,
    provided: dict[str, Any],
    validation_errors: list[dict[str, Any]],
) -> dict:
    """Use the one permitted repair response, then report its actual violations."""
    try:
        candidate = _extract_candidate(
            raw_text,
            provided,
            validation_errors,
            repair=True,
        )
    except (TypeError, ValueError) as exc:
        return _clarification_for_errors(_errors_from_exception(exc), provided)

    if _contains_multiple_categories(candidate.get("category")):
        return _clarification_for_fields(["category"], candidate)

    try:
        requirement = SourcingRequirement.model_validate(candidate)
    except ValidationError as exc:
        return _clarification_for_errors(exc.errors(), candidate)

    return _validated_result(requirement)


def _extract_candidate(
    raw_text: str,
    provided: dict[str, Any],
    validation_errors: list[dict[str, Any]] | None = None,
    repair: bool = False,
) -> dict[str, Any]:
    extracted = (
        repair_requirement(raw_text, provided, validation_errors or [])
        if repair
        else extract_requirement(raw_text, provided)
    )
    if not isinstance(extracted, dict):
        raise TypeError("LLM requirement response must be a JSON object")
    return {**extracted, **provided}


def _validated_result(requirement: SourcingRequirement) -> dict:
    # Keep the established V2 parser response stable; the Harness resolver
    # exposes the extended sourcing fields through its own contract.
    data = requirement.model_dump(
        exclude={"product", "material", "supply_region", "must_have", "optional_conditions"}
    )
    missing = missing_requirement_fields(data)
    if missing:
        return {"status": "clarification_required", "missing": missing}
    return {"status": "ready", "requirement": data}


def _contains_multiple_categories(category: Any) -> bool:
    return isinstance(category, str) and any(
        separator in category for separator in MULTI_CATEGORY_SEPARATORS
    )


def _clarification_for_errors(
    validation_errors: list[dict[str, Any]], requirement: dict[str, Any]
) -> dict:
    fields = [
        str(error["loc"][0])
        for error in validation_errors
        if error.get("loc") and isinstance(error["loc"][0], str)
    ]
    return _clarification_for_fields(fields, requirement)


def _clarification_for_fields(fields: list[str], requirement: dict[str, Any]) -> dict:
    missing = list(dict.fromkeys(fields))
    for field in missing_requirement_fields(requirement):
        if field not in missing:
            missing.append(field)
    return {"status": "clarification_required", "missing": missing}


def _errors_from_exception(exc: Exception) -> list[dict[str, Any]]:
    """Represent unparseable adapter output without inventing a requirement field."""
    return [{"loc": (), "type": exc.__class__.__name__}]
