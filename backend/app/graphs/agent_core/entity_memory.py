"""Deterministic entity memory and turn-level target resolution.

The LLM may suggest names, but this module owns identity binding, focus
selection and pronoun resolution.  It never treats an unknown name as a
verified supplier.
"""

from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EntityIdentityStatus(str, Enum):
    VERIFIED = "verified"
    CANDIDATE = "candidate"
    PENDING_VERIFICATION = "pending_verification"
    AMBIGUOUS = "ambiguous"


class MentionMatchType(str, Enum):
    EXPLICIT_NAME = "explicit_name"
    ALIAS = "alias"
    SUPPLIER_CODE = "supplier_code"
    PLURAL_REFERENCE = "plural_reference"
    SINGULAR_REFERENCE = "singular_reference"
    ORDINAL_REFERENCE = "ordinal_reference"
    LLM_CANDIDATE = "llm_candidate"


class EntityMemoryEntry(BaseModel):
    """One canonical entity retained within a single session scope."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1, max_length=255)
    entity_type: str = Field(default="supplier", min_length=1, max_length=64)
    canonical_name: str = Field(min_length=1, max_length=255)
    aliases: list[str] = Field(default_factory=list)
    supplier_code: str | None = None
    identity_status: EntityIdentityStatus = EntityIdentityStatus.PENDING_VERIFICATION
    source_refs: list[str] = Field(default_factory=list)
    first_seen_turn_id: str = Field(min_length=1, max_length=255)
    last_seen_turn_id: str = Field(min_length=1, max_length=255)
    mention_count: int = Field(default=1, ge=1)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("aliases", "source_refs")
    @classmethod
    def dedupe_texts(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class EntityMention(BaseModel):
    """An auditable mention bound to an entity or left unresolved."""

    model_config = ConfigDict(extra="forbid")

    mention_id: str = Field(min_length=1, max_length=255)
    turn_id: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=255)
    entity_id: str | None = None
    match_type: MentionMatchType
    confidence: float = Field(ge=0.0, le=1.0)
    unresolved_reason: str | None = None


class FocusSet(BaseModel):
    """The only entity set that contextual references may reuse next turn."""

    model_config = ConfigDict(extra="forbid")

    entity_ids: list[str] = Field(default_factory=list, max_length=20)
    primary_entity_id: str | None = None
    source_turn_id: str = Field(min_length=1, max_length=255)
    reason: str = Field(default="none", min_length=1, max_length=64)

    @field_validator("entity_ids")
    @classmethod
    def dedupe_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item for item in value if item))


class EntityMemory(BaseModel):
    """Session-scoped entity memory; it must not cross session boundaries."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    session_id: str = ""
    entities: list[EntityMemoryEntry] = Field(default_factory=list)
    mentions: list[EntityMention] = Field(default_factory=list)
    focus_set: FocusSet | None = None


class TurnResolution(BaseModel):
    """Resolver output consumed by planning and graph adapters."""

    model_config = ConfigDict(extra="forbid")

    target_entity_ids: list[str] = Field(default_factory=list, max_length=20)
    target_supplier_names: list[str] = Field(default_factory=list, max_length=20)
    focus_set: FocusSet | None = None
    memory: EntityMemory
    confidence: float = Field(ge=0.0, le=1.0)
    needs_clarification: bool = False
    reason: str = Field(min_length=1, max_length=64)
    unresolved_mentions: list[str] = Field(default_factory=list, max_length=20)


_COMPANY_NAME_PATTERN = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9（）()·&\-]{2,80}?"
    r"(?:有限责任公司|股份有限公司|集团有限公司|有限公司|股份公司))"
)
_PLURAL_REFERENCE_TOKENS = (
    "这些企业", "上述企业", "这些供应商", "上述供应商", "推荐的供应商",
    "推荐企业", "它们", "全部企业", "所有企业", "这两家", "这些家",
    "那些家", "两家供应商", "两家公司", "这两家公司",
)
_SINGULAR_REFERENCE_TOKENS = ("这家", "该企业", "该供应商", "该公司", "它")
_ORDINAL_TARGETS = (
    ("排名第一", 1), ("第一家", 1), ("前两家", 2), ("前3家", 3),
    ("前三家", 3), ("前五家", 5),
)
_EXCLUSION_TOKENS = ("除了", "除去", "排除")
_SUPPLIER_CODE_PATTERN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9][A-Za-z0-9_\-/]{1,63})(?![A-Za-z0-9])")
_COMPANY_NAME_PREFIXES = (
    "请帮我分析一下", "帮我分析一下", "请复核一下", "复核一下", "请分析一下", "分析一下",
    "请评估一下", "评估一下", "请查询一下", "查询一下", "请查看一下", "查看一下",
    "请核查一下", "核查一下", "请查找一下", "查找一下", "请预测一下", "预测一下", "请确认一下", "确认一下",
    "请生成一下", "生成一下", "请生成", "生成",
    "请看一下", "看一下", "请对比一下", "对比一下", "比较一下",
    "请复核", "复核", "请分析", "分析", "请评估", "评估", "请查询", "查询",
    "请查看", "查看", "请核查", "核查", "请查找", "查找", "请预测", "预测", "请确认", "确认",
    "请生成", "生成",
    "请对比", "对比", "比较", "请对", "对", "将", "把", "比", "请", "帮我",
)


def resolve_turn(
    message: str,
    *,
    session_id: str = "",
    turn_id: str = "current-turn",
    previous_memory: EntityMemory | None = None,
    references: list[dict[str, Any]] | None = None,
    llm_candidates: list[str] | None = None,
) -> TurnResolution:
    """Resolve one turn using explicit current facts before session focus.

    ``llm_candidates`` are suggestions only.  Unknown suggestions are kept as
    pending entities and can never become verified through this function.
    """
    memory = _merge_references(
        (previous_memory or EntityMemory(session_id=session_id)).model_copy(deep=True),
        references or [],
        turn_id,
    )
    entries_by_alias = _aliases_to_entities(memory.entities)
    explicit_mentions = _explicit_company_names(message)
    explicit_ids: list[str] = []
    mentions: list[EntityMention] = []

    for name in explicit_mentions:
        entity, match_type = _bind_name(name, entries_by_alias, memory, turn_id)
        explicit_ids.append(entity.entity_id)
        mentions.append(_mention(turn_id, name, entity.entity_id, match_type, 1.0))

    for code in _matching_supplier_codes(message, memory.entities):
        entity = next(item for item in memory.entities if item.supplier_code == code)
        if entity.entity_id not in explicit_ids:
            explicit_ids.append(entity.entity_id)
            mentions.append(_mention(turn_id, code, entity.entity_id, MentionMatchType.SUPPLIER_CODE, 1.0))

    for entity in memory.entities:
        if entity.entity_id in explicit_ids:
            continue
        if any(alias and alias in message for alias in entity.aliases):
            explicit_ids.append(entity.entity_id)
            mentions.append(_mention(turn_id, next(alias for alias in entity.aliases if alias in message), entity.entity_id, MentionMatchType.ALIAS, 1.0))

    for candidate in llm_candidates or []:
        candidate = str(candidate).strip()
        if not candidate:
            continue
        known_entity = entries_by_alias.get(_normalize(candidate))
        entity, match_type = _bind_name(candidate, entries_by_alias, memory, turn_id)
        if entity.entity_id not in explicit_ids:
            explicit_ids.append(entity.entity_id)
            mentions.append(_mention(
                turn_id,
                candidate,
                entity.entity_id,
                match_type if known_entity else MentionMatchType.LLM_CANDIDATE,
                0.8,
            ))

    explicit_ids = _ordered_mention_ids(message, mentions)
    if explicit_ids:
        reason = "explicit_name_or_code"
        target_ids = _apply_exclusions(message, explicit_ids, entries_by_alias)
        confidence = 1.0
    else:
        target_ids, reason, confidence = _resolve_contextual_targets(message, memory)

    entities_by_id = {entity.entity_id: entity for entity in memory.entities}
    target_entities = [entities_by_id[entity_id] for entity_id in target_ids if entity_id in entities_by_id]
    for entity in target_entities:
        if not any(item.entity_id == entity.entity_id for item in mentions):
            mentions.append(_mention_for_reference(message, turn_id, entity, reason, confidence))
        entity.last_seen_turn_id = turn_id
        entity.mention_count += 1

    unresolved = [item.text for item in mentions if item.entity_id is None]
    needs_clarification = not target_ids and _has_contextual_reference(message)
    focus = FocusSet(
        entity_ids=target_ids,
        primary_entity_id=target_ids[0] if target_ids else None,
        source_turn_id=turn_id,
        reason=reason,
    ) if target_ids else memory.focus_set
    if focus is None and memory.entities:
        # A fresh supplier recommendation establishes the next-turn focus,
        # while leaving the current turn's analysis target empty.
        focus = FocusSet(
            entity_ids=[entity.entity_id for entity in memory.entities],
            primary_entity_id=memory.entities[0].entity_id,
            source_turn_id=turn_id,
            reason="reference_set",
        )
    updated_memory = memory.model_copy(update={"mentions": [*memory.mentions, *mentions], "focus_set": focus})
    return TurnResolution(
        target_entity_ids=target_ids,
        target_supplier_names=[entity.canonical_name for entity in target_entities],
        focus_set=focus,
        memory=updated_memory,
        confidence=confidence,
        needs_clarification=needs_clarification,
        reason=reason,
        unresolved_mentions=unresolved,
    )


def memory_from_state(raw_state: dict[str, Any] | None, *, session_id: str = "") -> EntityMemory:
    """Load memory from state without allowing malformed state to break chat."""
    raw = raw_state.get("entity_memory") if isinstance(raw_state, dict) else None
    if isinstance(raw, dict):
        try:
            return EntityMemory.model_validate(raw)
        except ValueError:
            pass
    return EntityMemory(session_id=session_id)


def _merge_references(memory: EntityMemory, references: list[dict[str, Any]], turn_id: str) -> EntityMemory:
    entities = list(memory.entities)
    by_key = {entity.entity_id: entity for entity in entities}
    aliases = _aliases_to_entities(entities)
    for reference in references:
        if not isinstance(reference, dict):
            continue
        name = str(reference.get("name") or reference.get("supplier_name") or "").strip()
        if not name:
            continue
        entity_id = _reference_entity_id(reference, name)
        existing = by_key.get(entity_id) or aliases.get(_normalize(name))
        if existing is None:
            existing = EntityMemoryEntry(
                entity_id=entity_id,
                entity_type="supplier",
                canonical_name=name,
                aliases=_reference_aliases(reference),
                supplier_code=_optional_text(reference.get("supplier_code")),
                identity_status=_reference_status(reference),
                source_refs=_reference_sources(reference),
                first_seen_turn_id=turn_id,
                last_seen_turn_id=turn_id,
                attributes=_reference_attributes(reference),
            )
            entities.append(existing)
            by_key[entity_id] = existing
        else:
            existing.aliases = list(dict.fromkeys([*existing.aliases, *_reference_aliases(reference)]))
            existing.source_refs = list(dict.fromkeys([*existing.source_refs, *_reference_sources(reference)]))
            if not existing.supplier_code:
                existing.supplier_code = _optional_text(reference.get("supplier_code"))
            if existing.identity_status == EntityIdentityStatus.PENDING_VERIFICATION:
                existing.identity_status = _reference_status(reference)
            existing.attributes = {**existing.attributes, **_reference_attributes(reference)}
        aliases = _aliases_to_entities(entities)
    return memory.model_copy(update={"session_id": memory.session_id, "entities": entities})


def _bind_name(
    name: str,
    aliases: dict[str, EntityMemoryEntry],
    memory: EntityMemory,
    turn_id: str,
) -> tuple[EntityMemoryEntry, MentionMatchType]:
    normalized = _normalize(name)
    entity = aliases.get(normalized)
    if entity:
        return entity, MentionMatchType.EXPLICIT_NAME if _normalize(entity.canonical_name) == normalized else MentionMatchType.ALIAS
    entity = EntityMemoryEntry(
        entity_id=_temp_entity_id(name),
        canonical_name=name,
        identity_status=EntityIdentityStatus.PENDING_VERIFICATION,
        first_seen_turn_id=turn_id,
        last_seen_turn_id=turn_id,
    )
    memory.entities.append(entity)
    aliases[normalized] = entity
    return entity, MentionMatchType.LLM_CANDIDATE if name not in _explicit_company_names(name) else MentionMatchType.EXPLICIT_NAME


def _resolve_contextual_targets(message: str, memory: EntityMemory) -> tuple[list[str], str, float]:
    entities = memory.entities
    focus_ids = memory.focus_set.entity_ids if memory.focus_set else []
    if not focus_ids:
        return [], "missing_context", 0.0
    if any(token in message for token in _EXCLUSION_TOKENS):
        excluded = {
            entity.entity_id
            for entity in entities
            if any(alias and alias in message for alias in [entity.canonical_name, *entity.aliases])
        }
        remaining = [entity.entity_id for entity in entities if entity.entity_id in focus_ids and entity.entity_id not in excluded]
        return remaining, "exclusion", 0.95
    for token, limit in _ORDINAL_TARGETS:
        if token in message:
            return focus_ids[:limit], "ordinal_reference", 0.95
    if any(token in message for token in _PLURAL_REFERENCE_TOKENS):
        return focus_ids, "plural_reference", 0.95
    if any(token in message for token in _SINGULAR_REFERENCE_TOKENS):
        return focus_ids[:1], "singular_reference", 0.9
    return [], "no_target", 0.0


def _apply_exclusions(message: str, target_ids: list[str], aliases: dict[str, EntityMemoryEntry]) -> list[str]:
    if not any(token in message for token in _EXCLUSION_TOKENS):
        return target_ids
    excluded = {
        entity.entity_id
        for entity in aliases.values()
        if entity.entity_id in target_ids and any(alias and alias in message for alias in [entity.canonical_name, *entity.aliases])
    }
    return [entity_id for entity_id in target_ids if entity_id not in excluded]


def _mention(turn_id: str, text: str, entity_id: str | None, match_type: MentionMatchType, confidence: float) -> EntityMention:
    return EntityMention(
        mention_id=hashlib.sha1(f"{turn_id}:{text}:{entity_id}".encode()).hexdigest(),
        turn_id=turn_id,
        text=text,
        entity_id=entity_id,
        match_type=match_type,
        confidence=confidence,
    )


def _mention_for_reference(message: str, turn_id: str, entity: EntityMemoryEntry, reason: str, confidence: float) -> EntityMention:
    match_type = {
        "plural_reference": MentionMatchType.PLURAL_REFERENCE,
        "singular_reference": MentionMatchType.SINGULAR_REFERENCE,
        "ordinal_reference": MentionMatchType.ORDINAL_REFERENCE,
    }.get(reason, MentionMatchType.EXPLICIT_NAME)
    return _mention(turn_id, message[:255], entity.entity_id, match_type, confidence)


def _ordered_mention_ids(message: str, mentions: list[EntityMention]) -> list[str]:
    ordered = sorted(
        (mention for mention in mentions if mention.entity_id),
        key=lambda mention: (
            message.find(mention.text) if message.find(mention.text) >= 0 else len(message),
            mention.mention_id,
        ),
    )
    return list(dict.fromkeys(mention.entity_id for mention in ordered if mention.entity_id))


def _explicit_company_names(message: str) -> list[str]:
    names: list[str] = []
    # Keep entity binding stable when copied text contains spaces inside a
    # legal name, such as before a parenthesized branch name.
    compact_message = re.sub(r"[\s　]+", "", str(message or ""))
    for match in _COMPANY_NAME_PATTERN.finditer(compact_message):
        name = match.group(1).strip()
        if match.start(1) > 0 and name.startswith("和"):
            name = name[1:].strip()
        previous = None
        while name and name != previous:
            previous = name
            for prefix in _COMPANY_NAME_PREFIXES:
                if name.startswith(prefix):
                    name = name[len(prefix):].strip()
                    break
        if name and name not in names:
            names.append(name)
    return names


def _matching_supplier_codes(message: str, entities: list[EntityMemoryEntry]) -> list[str]:
    codes = {entity.supplier_code for entity in entities if entity.supplier_code}
    return [code for code in _SUPPLIER_CODE_PATTERN.findall(message) if code in codes]


def _has_contextual_reference(message: str) -> bool:
    return any(token in message for token in (*_PLURAL_REFERENCE_TOKENS, *_SINGULAR_REFERENCE_TOKENS, *(item[0] for item in _ORDINAL_TARGETS)))


def _aliases_to_entities(entities: list[EntityMemoryEntry]) -> dict[str, EntityMemoryEntry]:
    result: dict[str, EntityMemoryEntry] = {}
    for entity in entities:
        for alias in [entity.canonical_name, *entity.aliases]:
            normalized = _normalize(alias)
            if normalized:
                result[normalized] = entity
    return result


def _reference_entity_id(reference: dict[str, Any], name: str) -> str:
    monitor_target_id = reference.get("monitor_target_id")
    if monitor_target_id:
        return f"monitor:{monitor_target_id}"
    stable_id = reference.get("supplier_id") or reference.get("company_id")
    if stable_id:
        return f"supplier:{stable_id}"
    candidate_id = reference.get("candidate_id")
    if candidate_id:
        return f"candidate:{candidate_id}"
    result_id = reference.get("result_id")
    if result_id:
        return f"result:{result_id}"
    return _temp_entity_id(name)


def _temp_entity_id(name: str) -> str:
    return f"temp:{hashlib.sha256(_normalize(name).encode()).hexdigest()[:32]}"


def _reference_status(reference: dict[str, Any]) -> EntityIdentityStatus:
    if reference.get("supplier_id") or reference.get("company_id") or reference.get("candidate_type") == "local":
        return EntityIdentityStatus.VERIFIED
    if reference.get("candidate_id") or reference.get("candidate_type") in {"external", "candidate"}:
        return EntityIdentityStatus.CANDIDATE
    return EntityIdentityStatus.PENDING_VERIFICATION


def _reference_aliases(reference: dict[str, Any]) -> list[str]:
    aliases = reference.get("aliases")
    return [str(alias).strip() for alias in aliases if str(alias).strip()] if isinstance(aliases, list) else []


def _reference_sources(reference: dict[str, Any]) -> list[str]:
    return [str(value).strip() for value in (reference.get("source"), reference.get("discovery_source")) if value]


def _reference_attributes(reference: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "monitor_target_id", "target_type", "candidate_id", "result_id", "candidate_type",
        "identity_status", "company_id", "supplier_id", "supplier_code", "website_url",
    )
    return {key: reference[key] for key in keys if reference.get(key)}


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _normalize(value: str) -> str:
    return re.sub(r"[\s　·,，。、“”\"'()（）\-_/]", "", value).casefold()


__all__ = [
    "EntityIdentityStatus", "EntityMemory", "EntityMemoryEntry", "EntityMention",
    "FocusSet", "MentionMatchType", "TurnResolution", "memory_from_state", "resolve_turn",
]
