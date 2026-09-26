"""Synthetic warm latency measurement; not Desktop/model or hardware qualification."""

import json
import statistics
import tempfile
import time
from pathlib import Path

from jev_context.common import uid
from jev_context.policy import Config
from jev_context.service import Service


def main():
    with tempfile.TemporaryDirectory(prefix="jev-measure-") as directory:
        root = Path(directory) / "project"
        root.mkdir()
        s = Service(Config(root, Path(directory) / "data", uid("project")))
        created = s.call(
            "work_open",
            dict(
                request_id="create",
                mutation_id="create",
                create=dict(
                    title="latency",
                    goal="synthetic",
                    scope=dict(mode="design", constraints=["설계만"]),
                    origin=dict(quote="설계만"),
                ),
            ),
        )
        wid = created["data"]["work_id"]
        s.call(
            "source_sync",
            dict(
                request_id="sync",
                mutation_id="sync",
                items=[
                    dict(
                        kind="excerpt",
                        text="권한 refreshToken() 승인 후 변경",
                        origin_label="synthetic",
                        external_key="synthetic",
                    )
                ],
            ),
        )
        measurements = {}
        for name, args in (
            ("work_open", dict(work_id=wid)),
            ("context_prepare", dict(work_id=wid, query="권한 refreshToken")),
        ):
            elapsed = []
            for index in range(100):
                start = time.perf_counter()
                result = s.call(name, dict(request_id=f"req-{index}", **args))
                assert result["outcome"] == "ok", result
                elapsed.append((time.perf_counter() - start) * 1000)
            measurements[name] = {
                "runs": 100,
                "median_ms": round(statistics.median(elapsed), 3),
                "p95_ms": round(sorted(elapsed)[94], 3),
            }
        s.close()
        print(
            json.dumps(
                {
                    "fixture": "one synthetic work, one Korean excerpt; same service process; no model",
                    "measurements": measurements,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
