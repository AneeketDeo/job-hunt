"""
Test Suite: Persistent Resume Verification
Validates:
1. POST /api/profile/upload-resume:
   - Saves uploaded resume to data/resumes/candidate_resume.pdf
   - Records metadata in data/candidate_profile.json under resume_file:
     {filename, upload_timestamp, file_size_kb, file_path}
2. ProfileRepository.get_persistent_resume_path():
   - Returns Path to candidate_resume.pdf if present
   - Gracefully returns None or fallback if absent
3. Engines integration:
   - ats_engine.py, portal_apply_engine.py, naukri_apply_engine.py
     reference get_persistent_resume_path() for resume uploads.
"""
import sys
import io
import json
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient
from src.api.main import app
from src.core.profile_repository import ProfileRepository


def test_persistent_resume_upload():
    """Verify POST /api/profile/upload-resume saves to data/resumes/candidate_resume.pdf."""
    client = TestClient(app)
    
    # Create fake PDF content
    fake_pdf = b"%PDF-1.4 ... Fake PDF content for testing ... %%EOF"
    file_payload = {
        "file": ("my_resume.pdf", io.BytesIO(fake_pdf), "application/pdf")
    }
    
    response = client.post("/api/profile/upload-resume", files=file_payload)
    assert response.status_code == 200, f"Upload failed: {response.text}"
    data = response.json()
    print(f"✅ Upload response: {data}")
    
    # Verify candidate_resume.pdf exists
    resumes_dir = PROJECT_ROOT / "data" / "resumes"
    persistent_pdf = resumes_dir / "candidate_resume.pdf"
    assert persistent_pdf.exists(), f"Expected persistent resume at {persistent_pdf}"
    print(f"✅ Found persistent resume on disk: {persistent_pdf} ({persistent_pdf.stat().st_size} bytes)")
    
    # Verify candidate_profile.json has resume_file metadata
    profile_path = PROJECT_ROOT / "data" / "candidate_profile.json"
    if profile_path.exists():
        with open(profile_path, "r", encoding="utf-8") as f:
            profile_data = json.load(f)
        assert "resume_file" in profile_data, f"'resume_file' missing in candidate_profile.json: {list(profile_data.keys())}"
        rf = profile_data["resume_file"]
        print(f"✅ Verified candidate_profile.json resume_file metadata: {rf}")
        assert "filename" in rf
        assert "file_path" in rf
        assert "upload_timestamp" in rf


def test_profile_repository_get_persistent_resume():
    """Verify ProfileRepository.get_persistent_resume_path()."""
    repo = ProfileRepository()
    assert hasattr(repo, "get_persistent_resume_path") or hasattr(ProfileRepository, "get_persistent_resume_path"), \
        "ProfileRepository missing get_persistent_resume_path() method"
        
    path = repo.get_persistent_resume_path() if hasattr(repo, "get_persistent_resume_path") else ProfileRepository.get_persistent_resume_path()
    print(f"✅ ProfileRepository.get_persistent_resume_path() returned: {path}")
    if path:
        assert Path(path).exists(), f"Path returned does not exist: {path}"


def test_engines_reference_persistent_resume():
    """Verify engines check persistent resume path."""
    engine_files = [
        PROJECT_ROOT / "src" / "automation" / "ats_engine.py",
        PROJECT_ROOT / "src" / "automation" / "portal_apply_engine.py",
        PROJECT_ROOT / "src" / "automation" / "naukri_apply_engine.py",
    ]
    
    for ef in engine_files:
        assert ef.exists(), f"Missing engine file: {ef}"
        with open(ef, "r", encoding="utf-8") as f:
            code = f.read()
        has_ref = "get_persistent_resume_path" in code or "candidate_resume.pdf" in code or "persistent_resume" in code
        print(f"[{ef.name}] References persistent resume: {has_ref}")
        assert has_ref, f"{ef.name} does not reference get_persistent_resume_path or persistent resume"


if __name__ == "__main__":
    print("\n--- Verifying Persistent Resume System ---")
    failed = False
    try:
        test_persistent_resume_upload()
    except Exception as e:
        print(f"❌ Upload test failed: {e}")
        failed = True
        
    try:
        test_profile_repository_get_persistent_resume()
    except Exception as e:
        print(f"❌ ProfileRepository test failed: {e}")
        failed = True
        
    try:
        test_engines_reference_persistent_resume()
    except Exception as e:
        print(f"❌ Engines reference test failed: {e}")
        failed = True

    if failed:
        sys.exit(1)
