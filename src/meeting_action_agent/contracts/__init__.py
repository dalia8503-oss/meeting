"""contracts/ JSON Schema 검증 계층 (T-03-1).

- JSON Schema(specs/.../contracts/*.schema.json)가 정본이다. 규칙을 Python에 복제하지 않는다.
- validate()는 스키마 검증만 한다. 도메인/안전 가드(전이표·완료 가드)는 적용 지점의 책임이다.
- receive_agent_output()은 Agent(LLM) 출력 수신 지점: 완료 가드(도메인) → 스키마 순으로 평가한다.
"""
import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from meeting_action_agent.state import CompletionGuardError, Status

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "specs" / "001-meeting-action-agent" / "contracts"

_COMMON = "common"  # 공용 정의 모음: 단독 검증 대상이 아니다

SCHEMA_NAMES = frozenset(
    p.name[: -len(".schema.json")] for p in CONTRACTS_DIR.glob("*.schema.json")
) - {_COMMON}

# MVP에서 Agent(LLM)가 내놓을 수 있는 출력 (그 외 이름은 거부)
AGENT_OUTPUT_SCHEMAS = frozenset({"action-candidate", "meeting-analysis", "followup-result", "review-result"})


class SchemaValidationError(Exception):
    """계약(JSON Schema)의 필드·형식 위반. 도메인/안전 가드 예외와 별개다."""


@lru_cache(maxsize=1)
def _schemas_and_registry():
    schemas = {
        p.name[: -len(".schema.json")]: json.loads(p.read_text(encoding="utf-8"))
        for p in CONTRACTS_DIR.glob("*.schema.json")
    }
    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s, default_specification=DRAFT202012))
        for s in schemas.values()
    )
    return schemas, registry


@lru_cache(maxsize=None)
def _validator(schema_name: str) -> Draft202012Validator:
    schemas, registry = _schemas_and_registry()
    return Draft202012Validator(schemas[schema_name], registry=registry)


def validate(schema_name, data) -> None:
    """data가 schema_name 계약을 만족하는지 검증한다.

    위반 → SchemaValidationError, 알 수 없는 이름 → ValueError.
    """
    if schema_name not in SCHEMA_NAMES:
        raise ValueError(f"알 수 없는 schema name: {schema_name!r}")
    errors = sorted(_validator(schema_name).iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        first = errors[0]
        where = ".".join(str(p) for p in first.absolute_path) or "(root)"
        extra = f" 외 {len(errors) - 1}건" if len(errors) > 1 else ""
        raise SchemaValidationError(f"{schema_name}: {where}: {first.message[:200]}{extra}")


def _agent_proposed_statuses(schema_name, data):
    """Agent 출력 안에서 '상태 제안' 위치의 값들 (완료 가드 대상)."""
    if not isinstance(data, dict):
        return
    if schema_name == "action-candidate":
        yield data.get("status")
    elif schema_name == "followup-result":
        yield data.get("proposed_status")
    elif schema_name == "meeting-analysis":
        items = data.get("action_candidates")
        for item in items if isinstance(items, list) else []:
            candidate = item.get("candidate") if isinstance(item, dict) else None
            yield from _agent_proposed_statuses("action-candidate", candidate)


def receive_agent_output(schema_name, data) -> None:
    """Agent 출력 수신 지점. 허용 이름 외 → ValueError, '완료' 제안 → CompletionGuardError,
    그 외 계약 위반 → SchemaValidationError (도메인 가드가 스키마보다 우선)."""
    if schema_name not in AGENT_OUTPUT_SCHEMAS:
        raise ValueError(f"Agent 출력으로 허용되지 않는 schema name: {schema_name!r}")
    if any(s == Status.DONE.value for s in _agent_proposed_statuses(schema_name, data)):
        raise CompletionGuardError("Agent는 '완료'를 제안·설정할 수 없다 (사람 승인 필요)")
    validate(schema_name, data)
