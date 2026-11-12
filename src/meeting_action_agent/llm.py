"""T-00-14 최소 LLM adapter. runner(prompt: str) -> str 인터페이스만 제공한다.

역할은 prompt → Claude API → 응답 text 뿐이다. JSON 파싱·MeetingAnalysis 검증·Action 변환은
analyst/pipeline 계층이 담당한다 (여기서 구현하지 않는다).

환경변수: ANTHROPIC_API_KEY (SDK가 직접 읽음, 코드·로그에 남기지 않음), ANTHROPIC_MODEL (선택).
anthropic은 호출 시점에 import한다 (자동 테스트가 SDK/네트워크에 의존하지 않도록).
"""
import os
import time

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


# 공식 문서(ai.google.dev/gemini-api/docs/models)가 신규 프로젝트용 최신으로 안내하고,
# models.list()로 이 키에서 generateContent 지원을 확인한 ID.
GEMINI_DEFAULT_MODEL = "gemini-3.8-flash"
GEMINI_TIMEOUT_SEC = 60  # 환경변수 GEMINI_TIMEOUT_SEC로 변경 가능. 무한 대기 방지


# Gemini Structured Output이 지원하지 않는 JSON Schema 키워드 (제거 대상).
# if/then/allOf 등 조건부 규칙은 Gemini가 강제할 수 없으므로 버리며, 최종 검증은 기존
# receive_agent_output / validate_analysis가 계속 담당한다.
_GEMINI_DROP_KEYS = {
    "$schema", "$id", "$comment", "$defs", "title", "allOf", "if", "then", "else", "not",
    "uniqueItems", "propertyNames", "contains",
}


def gemini_response_schema() -> dict:
    """contracts/meeting-analysis.schema.json → Gemini용 subset (원본 파일은 수정하지 않는다).

    파일 간 $ref는 인라인하고, oneOf→anyOf, const→enum으로 바꾸며 미지원 키워드는 제거한다.
    """
    import json

    from .contracts import CONTRACTS_DIR

    cache: dict = {}

    def load(name: str) -> dict:
        if name not in cache:
            cache[name] = json.loads((CONTRACTS_DIR / name).read_text(encoding="utf-8"))
        return cache[name]

    def resolve(ref: str, current: str):
        file, _, pointer = ref.partition("#")
        file = file or current
        node = load(file)
        for part in [x for x in pointer.split("/") if x]:
            node = node[part]
        return node, file

    def convert(node, current: str):
        if isinstance(node, list):
            return [convert(x, current) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target, file = resolve(node["$ref"], current)
            merged = {**convert(target, file), **{k: convert(v, current) for k, v in node.items() if k != "$ref"}}
            return merged
        out = {}
        for k, v in node.items():
            if k in _GEMINI_DROP_KEYS:
                continue
            if k == "oneOf":
                out["anyOf"] = convert(v, current)
            elif k == "const":
                out["enum"] = [v]
            elif k == "properties":
                out[k] = {pk: convert(pv, current) for pk, pv in v.items()}
            else:
                out[k] = convert(v, current)
        return out

    return convert(load("meeting-analysis.schema.json"), "meeting-analysis.schema.json")


def gemini_runner(prompt: str) -> str:
    """prompt → Gemini API(google-genai) → 응답 text. GEMINI_API_KEY(.env 또는 환경변수), GEMINI_MODEL(선택).

    JSON 파싱·검증·변환은 하지 않는다. 키는 코드·로그에 남기지 않는다.
    """
    from google import genai
    from google.genai import errors, types

    from .env import load_env

    load_env()
    timeout = float(os.environ.get("GEMINI_TIMEOUT_SEC", GEMINI_TIMEOUT_SEC))
    model = os.environ.get("GEMINI_MODEL", GEMINI_DEFAULT_MODEL)
    client = genai.Client(http_options=types.HttpOptions(timeout=int(timeout * 1000)))  # GEMINI_API_KEY 사용
    for attempt in range(3):  # 일시적 과부하(503 UNAVAILABLE)만 재시도
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                # Structured Output: 출력 형식만 제한 (파싱·검증은 analyst 계층 담당)
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=gemini_response_schema(),
                ),
            )
            break
        except errors.ServerError as e:
            if e.code == 504:
                raise RuntimeError(
                    f"Gemini API가 제한시간 내 응답하지 못했습니다 (504 DEADLINE_EXCEEDED): "
                    f"model={model}, timeout={timeout:g}초"
                ) from None
            if e.code != 503 or attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
        except Exception as e:
            if "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower():
                raise RuntimeError(
                    f"Gemini API가 제한시간 내 응답하지 못했습니다 (timeout): "
                    f"model={model}, timeout={timeout:g}초"
                ) from None
            raise
    if response.text is None:
        raise RuntimeError(f"Gemini 응답 text 없음: {getattr(response, 'prompt_feedback', None)}")
    return response.text
