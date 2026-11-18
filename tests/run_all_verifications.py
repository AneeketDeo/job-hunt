"""
Master Test Verification Runner
Runs all verification suites:
1. Error Retry & Deduplication (test_error_retry.py)
2. Clean Status Standard (test_clean_status.py)
3. Persistent Resume System (test_persistent_resume.py)
4. Live Mode vs Dry Run (test_live_mode.py)
5. Portal SSO & Dashboard UI (test_portal_sso_ui.py)
"""
import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PYTHON_EXEC = sys.executable

SUITES = [
    ("Clean Status Standard", PROJECT_ROOT / "tests" / "test_clean_status.py"),
    ("Persistent Resume System", PROJECT_ROOT / "tests" / "test_persistent_resume.py"),
    ("Live Mode vs Dry Run", PROJECT_ROOT / "tests" / "test_live_mode.py"),
    ("Error Retry & Scrapers", PROJECT_ROOT / "tests" / "test_error_retry.py"),
    ("Portal SSO & UI Elements", PROJECT_ROOT / "tests" / "test_portal_sso_ui.py"),
]


def run_suite(name: str, path: Path):
    print(f"\n=======================================================")
    print(f"RUNNING SUITE: {name}")
    print(f"File: {path.name}")
    print(f"=======================================================")
    
    res = subprocess.run(
        [PYTHON_EXEC, str(path)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    
    print(res.stdout)
    if res.stderr:
        print("STDERR:")
        print(res.stderr)
        
    passed = (res.returncode == 0)
    print(f"RESULT: {'✅ PASSED' if passed else '❌ FAILED'} (exit code {res.returncode})")
    return passed, res.stdout, res.stderr


def main():
    results = {}
    for name, path in SUITES:
        passed, stdout, stderr = run_suite(name, path)
        results[name] = {
            "passed": passed,
            "stdout": stdout,
            "stderr": stderr
        }
        
    print("\n\n=======================================================")
    print("FINAL VERIFICATION SUMMARY REPORT")
    print("=======================================================")
    all_passed = True
    for name, r in results.items():
        status_str = "✅ PASS" if r["passed"] else "❌ FAIL"
        if not r["passed"]:
            all_passed = False
        print(f"- {name}: {status_str}")
        
    print(f"\nOVERALL: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
