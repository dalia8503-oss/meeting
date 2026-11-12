"""실행 모드. 서버 환경변수 MAA_MODE로만 결정하며 요청으로 바꿀 수 없다."""
import os
from pathlib import Path

DEFAULT_MODE = "demo"
VALID_MODES = ("live", "demo")


def get_mode() -> str:
    """잘못된 값은 안전한 demo로 처리한다."""
    value = os.environ.get("MAA_MODE", DEFAULT_MODE).strip().lower()
    return value if value in VALID_MODES else DEFAULT_MODE


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def get_data_root():
    """실행 모드별 저장 루트: <MAA_RUNTIME_DIR 또는 <프로젝트>/runtime>/<mode>. demo와 live는 서로 섞이지 않는다."""
    base = Path(os.environ.get("MAA_RUNTIME_DIR") or PROJECT_ROOT / "runtime")
    return base / get_mode()
