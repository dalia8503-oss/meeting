"""최소 .env 로더. 외부 의존성 없이 KEY=VALUE 줄만 읽는다.

이미 설정된 환경변수는 덮어쓰지 않는다. 값은 로그·출력에 남기지 않는다.
"""
import os
from pathlib import Path

DEFAULT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def load_env(path: Path = DEFAULT_ENV_PATH) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value
