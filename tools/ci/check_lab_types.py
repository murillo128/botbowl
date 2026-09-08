"""Bounded structural protocol checks with exact negative diagnostics."""
from pathlib import Path
import re
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-m", "mypy", "--follow-imports=silent", "--strict",
               "--show-error-codes", "--no-incremental"]
    positive = ["botbowl/lab/protocols.py", "botbowl/lab/adapters.py", "botbowl/lab/__init__.py",
                "examples/lab_protocols.py", "tests/typing/lab_protocols_valid.py"]
    subprocess.run(command + positive, cwd=root, check=True, timeout=120)
    negative = "tests/typing/lab_protocols_invalid.py"
    expected = {(str(line), code) for line, text in enumerate((root / negative).read_text().splitlines(), 1)
                for code in re.findall(r"# expect: ([a-z-]+)", text)}
    result = subprocess.run(command + [negative], cwd=root, text=True,
                            capture_output=True, timeout=120)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    observed = re.findall(r"lab_protocols_invalid.py:(\d+): error: .* \[([a-z-]+)\]", result.stdout)
    assert result.returncode == 1 and expected and set(observed) == expected
    assert len(observed) == len(expected), "Unexpected duplicate diagnostics"
    assert result.stdout.count(": error:") == len(expected), "Unexpected errors outside negative controls"
    print(f"Lab typing: structural examples accepted; {len(expected)} incorrect signatures rejected")


if __name__ == "__main__":
    main()
