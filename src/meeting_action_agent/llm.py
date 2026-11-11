"""T-00-14 최소 LLM adapter. runner(prompt: str) -> str 인터페이스만 제공한다.

역할은 prompt → Claude API → 응답 text 뿐이다. JSON 파싱·MeetingAnalysis 검증·Action 변환은
analyst/pipeline 계층이 담당한다 (여기서 구현하지 않는다).

환경변수: ANTHROPIC_API_KEY (SDK가 직접 읽음, 코드·로그에 남기지 않음), ANTHROPIC_MODEL (선택).
anthropic은 호출 시점에 import한다 (자동 테스트가 SDK/네트워크에 의존하지 않도록).
"""
import os

DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000


def claude_runner(prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수 사용
    response = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude refusal: {getattr(response, 'stop_details', None)}")
    return "".join(b.text for b in response.content if b.type == "text")
