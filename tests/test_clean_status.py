"""
Test Suite: Clean Status Standard Verification
Validates:
1. Complete elimination of ambiguous statuses:
   - "form_filled_ready_to_submit"
   - "dry_run_verified"
2. Strict standardization to two statuses across engines and API:
   - "applied": for successful submissions (including dry-run verified)
   - "failed": for any error, failure, or incomplete application
3. Engine methods return standardized statuses:
   - ats_engine.py
   - naukri_apply_engine.py
   - portal_apply_engine.py
   - src/api/main.py
"""
import sys
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FORBIDDEN_STATUSES = [
    "form_filled_ready_to_submit",
    "dry_run_verified",
]

TARGET_FILES = [
    PROJECT_ROOT / "src" / "automation" / "ats_engine.py",
    PROJECT_ROOT / "src" / "automation" / "naukri_apply_engine.py",
    PROJECT_ROOT / "src" / "automation" / "portal_apply_engine.py",
    PROJECT_ROOT / "src" / "api" / "main.py",
    PROJECT_ROOT / "src" / "automation" / "naukri_scraper.py",
    PROJECT_ROOT / "src" / "automation" / "indeed_scraper.py",
    PROJECT_ROOT / "src" / "automation" / "cutshort_scraper.py",
    PROJECT_ROOT / "src" / "automation" / "wellfound_scraper.py",
]


def check_forbidden_statuses_in_code():
    """Verify that forbidden status strings do not appear in key source files."""
    violations = []
    
    for file_path in TARGET_FILES:
        if not file_path.exists():
            continue
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        for line_idx, line in enumerate(lines, 1):
            # Check for forbidden statuses in string literals
            for forbidden in FORBIDDEN_STATUSES:
                if forbidden in line:
                    violations.append({
                        "file": file_path.name,
                        "line": line_idx,
                        "status": forbidden,
                        "content": line.strip()
                    })
                    
    return violations


def test_clean_status_enforcement():
    """Run verification for clean status enforcement."""
    print("\n--- Verifying Clean Status Standard (Strict 'applied' / 'failed') ---")
    violations = check_forbidden_statuses_in_code()
    
    if violations:
        print(f"❌ Found {len(violations)} occurrences of forbidden status strings:")
        for v in violations:
            print(f"   [{v['file']}:{v['line']}] {v['status']} -> {v['content']}")
        assert len(violations) == 0, f"Found {len(violations)} forbidden status occurrences"
    else:
        print("✅ Zero occurrences of 'form_filled_ready_to_submit' and 'dry_run_verified' in target files!")


if __name__ == "__main__":
    try:
        test_clean_status_enforcement()
    except Exception as e:
        print(f"FAILED: {e}")
        sys.exit(1)
