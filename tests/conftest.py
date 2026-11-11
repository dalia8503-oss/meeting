"""공용 fixture. 기준 데이터는 contracts/examples (가상 회의)를 사용한다."""
import copy
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "specs" / "001-meeting-action-agent" / "contracts"


def _load(name: str) -> dict:
    return json.loads((CONTRACTS / "examples" / "valid" / name).read_text(encoding="utf-8"))


@pytest.fixture
def new_action() -> dict:
    """status=신규, approved_by_human=null 인 유효한 Action (contracts valid 예시)."""
    return copy.deepcopy(_load("action.json"))


@pytest.fixture
def approval() -> dict:
    """유효한 approved_by_human 값 (contracts valid 예시)."""
    return copy.deepcopy(_load("action-completed.json")["approved_by_human"])


@pytest.fixture
def followup_evidence() -> list:
    """2차 회의의 완료형 발언 근거 (SPEC 8장 시나리오)."""
    return [
        {
            "meeting_id": "20261117-01",
            "timestamp": "00:08:31",
            "speaker": "김대리",
            "text": "생산성 자료 수정해서 공유했습니다.",
        }
    ]


@pytest.fixture
def store_path(tmp_path) -> Path:
    return tmp_path / "actions" / "actions.json"
