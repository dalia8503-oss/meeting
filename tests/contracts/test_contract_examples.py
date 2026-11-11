"""T-03-1 (A): contracts/ 자체 검증 + 검증 모듈이 예시를 기대대로 판정하는지 (R-07, T-00-8 일부).

TEST 단계: 구현 전에 먼저 작성한다 (RED 확인 후 구현).

- 스키마 파일 자체의 유효성·예시 개수·기존 validate_examples.py와의 일치는 기존 자산 기준이라
  구현 전에도 통과한다 (회귀 방지 목적).
- meeting_action_agent.contracts.validate()가 valid 예시는 통과, invalid 예시는 SchemaValidationError로
  거부하는지는 구현 전이므로 FAIL(RED)이 정상이다.

가정한 인터페이스 (문서에 정의돼 있지 않아 이 테스트가 요구하는 최소 API)
  meeting_action_agent.contracts
    SchemaValidationError            # 계약 위반. CompletionGuardError/TransitionError와 별개
    SCHEMA_NAMES                     # 검증 가능한 스키마 이름 9개 (common 제외: 공용 정의 모음)
    validate(schema_name, data)      # 스키마 검증만 수행(도메인 가드는 없음). 위반 시 SchemaValidationError,
                                     # 모르는 이름은 ValueError
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from meeting_action_agent.contracts import SCHEMA_NAMES, SchemaValidationError, validate

CONTRACTS = Path(__file__).resolve().parents[2] / "specs" / "001-meeting-action-agent" / "contracts"
EXAMPLES = CONTRACTS / "examples"

EXPECTED_SCHEMAS = {
    "common", "utterance", "transcript", "meeting-metadata", "action", "action-candidate",
    "meeting-analysis", "followup-result", "review-result", "review-queue-item",
}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_name_for_example(stem: str) -> str:
    """validate_examples.py와 같은 규칙: 파일명이 '<스키마>' 또는 '<스키마>-<변형>'. 긴 이름 우선."""
    for base in sorted(EXPECTED_SCHEMAS - {"common"}, key=len, reverse=True):
        if stem == base or stem.startswith(base + "-"):
            return base
    raise AssertionError(f"대응하는 스키마가 없는 예시: {stem}")


VALID_FILES = sorted((EXAMPLES / "valid").glob("*.json"))
INVALID_MANIFEST = _load(EXAMPLES / "invalid" / "_manifest.json")


# ---------- 계약 자산 자체 (구현 전에도 통과) ----------

def test_schema_files_are_the_ten_expected_contracts():
    names = {p.name[: -len(".schema.json")] for p in CONTRACTS.glob("*.schema.json")}
    assert names == EXPECTED_SCHEMAS


@pytest.mark.parametrize("path", sorted(CONTRACTS.glob("*.schema.json")), ids=lambda p: p.name)
def test_every_schema_is_valid_draft_2020_12(path):
    Draft202012Validator.check_schema(_load(path))


def test_example_counts_match_the_contract_package():
    assert len(VALID_FILES) == 14
    assert len(INVALID_MANIFEST) == 21
    assert {p.name for p in (EXAMPLES / "invalid").glob("*.json")} - {"_manifest.json"} == set(INVALID_MANIFEST)


def test_validate_examples_script_agrees_with_these_counts():
    done = subprocess.run(  # -I는 환경변수를 무시하므로 -X utf8로 출력 인코딩을 고정한다
        [sys.executable, "-I", "-X", "utf8", str(CONTRACTS / "validate_examples.py")],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "정상 예시 14개" in done.stdout and "오류 예시 21개" in done.stdout


# ---------- 검증 모듈이 계약 예시를 기대대로 판정 (구현 전 RED) ----------

def test_module_exposes_all_validatable_schemas():
    """common은 공용 정의 모음이라 단독 검증 대상이 아니다. 나머지 9개를 이름으로 검증할 수 있어야 한다."""
    assert set(SCHEMA_NAMES) == EXPECTED_SCHEMAS - {"common"}


@pytest.mark.parametrize("path", VALID_FILES, ids=lambda p: p.name)
def test_valid_examples_pass(path):
    validate(_schema_name_for_example(path.stem), _load(path))  # 예외가 없어야 한다


@pytest.mark.parametrize("name,schema", sorted(INVALID_MANIFEST.items()), ids=lambda v: str(v))
def test_invalid_examples_are_rejected_with_schema_validation_error(name, schema):
    data = _load(EXAMPLES / "invalid" / name)
    with pytest.raises(SchemaValidationError):
        validate(schema[: -len(".schema.json")], data)


def test_transcript_schema_accepts_one_utterance_and_rejects_empty_array():
    utterance = _load(EXAMPLES / "valid" / "utterance.json")
    validate("transcript", [utterance])
    with pytest.raises(SchemaValidationError):
        validate("transcript", [])


def test_unknown_schema_name_is_a_programming_error():
    with pytest.raises(ValueError):
        validate("does-not-exist", {})
