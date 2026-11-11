"""contracts/ 스키마와 예시를 검증한다.

- 스키마 10개 자체가 JSON Schema Draft 2020-12로 유효한지
- examples/valid/*.json 은 통과해야 하고
- examples/invalid/*.json 은 (예시별로 지정된 스키마에서) 거부되어야 한다.

사용: python validate_examples.py   (어느 위치에서 실행해도 됨)
요구: jsonschema>=4.18, referencing
종료 코드: 0 = 모두 기대대로, 1 = 하나라도 어긋남
구현 단계(tasks T-00-8)에서 tests/ 의 pytest 계약 테스트로 옮긴다.
"""

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

HERE = Path(__file__).resolve().parent


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    schemas = {p.name: load(p) for p in sorted(HERE.glob("*.schema.json"))}
    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s, default_specification=DRAFT202012))
        for s in schemas.values()
    )
    for s in schemas.values():
        Draft202012Validator.check_schema(s)

    def validator(name: str) -> Draft202012Validator:
        return Draft202012Validator(schemas[name], registry=registry)

    # 정상 예시: 파일명이 `<스키마 이름>` 또는 `<스키마 이름>-<변형>` 형태
    base_names = sorted((n[: -len(".schema.json")] for n in schemas), key=len, reverse=True)

    def schema_for(stem: str) -> str:
        for base in base_names:
            if stem == base or stem.startswith(base + "-"):
                return base + ".schema.json"
        raise SystemExit(f"예시 {stem}.json 에 대응하는 스키마가 없다")

    failures = 0
    valid_files = sorted((HERE / "examples" / "valid").glob("*.json"))
    for p in valid_files:
        errors = list(validator(schema_for(p.stem)).iter_errors(load(p)))
        if errors:
            failures += 1
            print(f"FAIL  valid    {p.name}: {errors[0].message[:120]}")

    manifest = load(HERE / "examples" / "invalid" / "_manifest.json")
    for name, schema_name in manifest.items():
        errors = list(validator(schema_name).iter_errors(load(HERE / "examples" / "invalid" / name)))
        if not errors:
            failures += 1
            print(f"FAIL  invalid  {name}: 거부되어야 하는데 통과했다 ({schema_name})")

    # transcript는 배열이므로 별도 확인
    transcript = validator("transcript.schema.json")
    utterance = load(HERE / "examples" / "valid" / "utterance.json")
    if list(transcript.iter_errors([utterance])) or not list(transcript.iter_errors([])):
        failures += 1
        print("FAIL  transcript: 발언 1건은 통과, 빈 배열은 거부되어야 한다")

    print(
        f"스키마 {len(schemas)}개 | 정상 예시 {len(valid_files)}개 | 오류 예시 {len(manifest)}개 | "
        f"{'모두 기대대로' if not failures else f'어긋남 {failures}건'}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
