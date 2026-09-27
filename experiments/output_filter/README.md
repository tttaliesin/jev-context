# 중단한 출력 축약 실험

설계 2.0 범위 밖의 PostToolUse 출력 교체 실험이다. 운영 hook·제품 wheel·기본 제품 테스트에 포함하지 않는다. 연구 근거와 재현 코드만 보존한다. [실험 계획과 중단 사유](../../docs/hook-benchmark-plan.md)를 참고한다.

저장소 루트에서 명시적으로 검사한다:

```powershell
.venv\Scripts\python.exe -m pytest experiments/output_filter -q
```

`scripts/hook_benchmark.py`는 이 디렉터리의 실험 도구를 절대 경로로 실행한다. 과거에 동결한 벤치마크 입력은 변경하지 않으며, 새 위치에서 만든 실험은 새 입력·해시로 구분한다.
