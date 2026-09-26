"""One sequential check entry point; never installs dependencies or changes a lock."""

import subprocess
import sys

for command in (
    [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"],
    [sys.executable, "-m", "ruff", "format", "--check", "src", "tests", "scripts"],
    [sys.executable, "-X", "utf8", "-m", "pytest", "-q"],
):
    result = subprocess.run(command, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)
