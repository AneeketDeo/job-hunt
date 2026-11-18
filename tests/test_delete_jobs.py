import json
import urllib.request
from pathlib import Path

BASE_URL = "http://localhost:8000"
LEDGER_FILE = Path("data/applied_jobs.json")

def test_delete_functionality():
    # 1. Add 2 mock jobs to the ledger directly
    with open(LEDGER_FILE, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    test_id_1 = "test_del_001"
    test_id_2 = "test_del_002"
    test_id_3 = "test_del_003"

    ledger[test_id_1] = {
        "job_id": test_id_1,
        "title": "Mock Delete Test Role 1",
        "company": "Test Delete Co",
        "url": "https://example.com/job/test-1",
        "status": "applied",
        "applied_at": "2026-11-18T12:00:00"
    }
    ledger[test_id_2] = {
        "job_id": test_id_2,
        "title": "Mock Delete Test Role 2",
        "company": "Test Delete Co",
        "url": "https://example.com/job/test-2",
        "status": "applied",
        "applied_at": "2026-11-18T12:01:00"
    }
    ledger[test_id_3] = {
        "job_id": test_id_3,
        "title": "Mock Delete Test Role 3",
        "company": "Test Delete Co",
        "url": "https://example.com/job/test-3",
        "status": "applied",
        "applied_at": "2026-11-18T12:02:00"
    }

    with open(LEDGER_FILE, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2)

    print(f"Created 3 test jobs: {test_id_1}, {test_id_2}, {test_id_3}")

    # 2. Test DELETE single job via DELETE /api/jobs/applied/{test_id_1}
    req = urllib.request.Request(f"{BASE_URL}/api/jobs/applied/{test_id_1}", method="DELETE")
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        print("DELETE single response:", resp.status, res)
        assert resp.status == 200
        assert res["status"] == "success"
        assert res["deleted_count"] == 1
        assert test_id_1 in res["deleted_ids"]

    # 3. Verify test_id_1 is gone from disk
    with open(LEDGER_FILE, "r", encoding="utf-8") as f:
        updated_ledger = json.load(f)
    assert test_id_1 not in updated_ledger
    print(f"✅ {test_id_1} successfully removed from disk!")

    # 4. Test deleting test_id_1 again -> should return 404
    try:
        req_again = urllib.request.Request(f"{BASE_URL}/api/jobs/applied/{test_id_1}", method="DELETE")
        with urllib.request.urlopen(req_again) as resp:
            pass
        assert False, "Expected 404 for non-existent job"
    except urllib.error.HTTPError as e:
        print(f"✅ Duplicate delete returned expected HTTP {e.code}")
        assert e.code == 404

    # 5. Test bulk delete via POST /api/jobs/applied/delete for test_id_2 and test_id_3
    bulk_payload = {"job_ids": [test_id_2, test_id_3]}
    req_bulk = urllib.request.Request(
        f"{BASE_URL}/api/jobs/applied/delete",
        data=json.dumps(bulk_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req_bulk) as resp:
        bulk_res = json.loads(resp.read().decode("utf-8"))
        print("DELETE bulk response:", resp.status, bulk_res)
        assert resp.status == 200
        assert bulk_res["status"] == "success"
        assert bulk_res["deleted_count"] == 2
        assert test_id_2 in bulk_res["deleted_ids"]
        assert test_id_3 in bulk_res["deleted_ids"]

    # 6. Verify test_id_2 and test_id_3 are gone from disk
    with open(LEDGER_FILE, "r", encoding="utf-8") as f:
        final_ledger = json.load(f)
    assert test_id_2 not in final_ledger
    assert test_id_3 not in final_ledger
    print(f"✅ {test_id_2} and {test_id_3} successfully removed from disk via bulk delete!")

    print("\n🎉 ALL LEDGER DELETION API TESTS PASSED!")

if __name__ == "__main__":
    test_delete_functionality()
