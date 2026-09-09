"""Check real public examples and require each invalid boundary call to fail."""
from pathlib import Path
import re
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-m", "mypy", "--follow-imports=silent",
               "--check-untyped-defs", "--disallow-untyped-defs",
               "--warn-unused-ignores", "--show-error-codes", "--no-incremental"]
    valid = ["botbowl/api.py", "examples/public_external.py", "examples/public_policy.py",
             "tests/typing/public_api_valid.py"]
    subprocess.run(command + valid, cwd=root, check=True, timeout=120)
    negative = "tests/typing/public_api_invalid.py"
    expected = {(str(line), code) for line, text in enumerate((root / negative).read_text().splitlines(), 1)
                for code in re.findall(r"# expect: ([a-z-]+)", text)}
    result = subprocess.run(command + [negative], cwd=root, text=True,
                            capture_output=True, timeout=120)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    observed = re.findall(r"public_api_invalid.py:(\d+): error: .* \[([a-z-]+)\]", result.stdout)
    assert result.returncode == 1 and expected and set(observed) == expected
    assert len(observed) == len(expected), "Unexpected duplicate diagnostics"
    assert result.stdout.count(": error:") == len(expected), "Unexpected errors outside negative calls"
    print(f"Public typing: valid usage accepted; {len(expected)} invalid calls rejected")


if __name__ == "__main__":
    main()
