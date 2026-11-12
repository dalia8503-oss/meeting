"""Step A: 기한 원문 보존. LLM이 '까지'를 떼고 돌려준 due_date를 근거 발언에서 안전하게 복원한다.

복원은 정규화가 아니다. Transcript에 실제 존재하는 '까지'만 되살리고, 새 표현은 만들지 않는다.
"""
import pytest

from meeting_action_agent.rules.due_date import restore_due_raw


@pytest.mark.parametrize("llm, text, expected", [
    ("금요일", "금요일까지 도면 수정해서 공유해 주세요.", "금요일까지"),
    ("다음주 화요일", "다음주 화요일까지 검토 의견을 주세요.", "다음주 화요일까지"),
    ("11월 20일", "11월 20일까지 완료해 주세요.", "11월 20일까지"),
    ("목요일 오전", "목요일 오전까지 부탁드립니다.", "목요일 오전까지"),
])
def test_restores_missing_ggaji_from_transcript(llm, text, expected):
    assert restore_due_raw(llm, text) == expected


def test_already_complete_or_regression_unchanged():
    assert restore_due_raw("금요일까지", "금요일까지 공유해 주세요.") == "금요일까지"


@pytest.mark.parametrize("llm, text", [
    ("다음주 화요일", "가능하면 다음주쯤 해 주세요."),       # 발언에 없는 표현: 생성 금지
    ("이번 달", "이번 달 안에 끝내 주세요."),                 # '까지' 아님: suffix 생성 금지
    ("금요일", "금요일에 공유해 주세요."),                    # 다른 조사
    ("금요일", "금요일까지 하고 다음 금요일에 또 봅시다."),   # 애매(여러 번 등장)하면 확장 안 함
    ("미정", "미정까지 해 주세요."),
    ("확인 필요", "확인 필요까지"),
])
def test_never_invents_or_extends_when_ambiguous(llm, text):
    assert restore_due_raw(llm, text) == llm
