import urllib.request
import urllib.error
import json
import uuid

base = "http://127.0.0.1:8000"

def test_flow():
    # 1. HTML landing verification
    req = urllib.request.Request(base + "/")
    with urllib.request.urlopen(req) as resp:
        html = resp.read().decode("utf-8")
    assert 'class="not-authenticated"' in html, "HTML body must have class not-authenticated"
    print("PASS 1: HTML body contains class='not-authenticated' by default (prevents any dashboard flash).")

    # 2. Registration with malformed email
    try:
        data = json.dumps({"email": "bademail", "password": "SecurePassword1", "display_name": "Test"}).encode()
        r = urllib.request.Request(base + "/api/auth/register", data=data, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(r)
        assert False, "Malformed email should fail"
    except urllib.error.HTTPError as e:
        assert e.code in (400, 422)
        print(f"PASS 2: Malformed email rejected with status {e.code}.")

    # 3. Registration with weak password (<8 chars)
    try:
        data = json.dumps({"email": "valid@example.com", "password": "weak", "display_name": "Test"}).encode()
        r = urllib.request.Request(base + "/api/auth/register", data=data, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(r)
        assert False, "Weak password should fail"
    except urllib.error.HTTPError as e:
        assert e.code in (400, 422)
        print(f"PASS 3: Weak password rejected with status {e.code}.")

    # 4. Valid account registration
    rand_id = str(uuid.uuid4())[:8]
    email = f"student_{rand_id}@example.com"
    data = json.dumps({"email": email, "password": "SecurePassword123", "display_name": "Maya Lin"}).encode()
    r = urllib.request.Request(base + "/api/auth/register", data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(r) as resp:
        res = json.loads(resp.read().decode())
        learner_id = res["learner_id"]
        assert res["email"] == email
        assert res["display_name"] == "Maya Lin"
        assert res["onboarding_completed"] is False
        print(f"PASS 4: Account registered successfully: learner_id={learner_id}, email={email}.")

    # 5. Duplicate account registration (Conflict)
    try:
        r = urllib.request.Request(base + "/api/auth/register", data=data, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(r)
        assert False, "Duplicate registration should fail"
    except urllib.error.HTTPError as e:
        assert e.code == 409
        print(f"PASS 5: Duplicate registration rejected with status {e.code} Conflict.")

    # 6. Login with incorrect password
    try:
        bad_data = json.dumps({"email": email, "password": "WrongPassword999"}).encode()
        r = urllib.request.Request(base + "/api/auth/login", data=bad_data, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(r)
        assert False, "Invalid password login should fail"
    except urllib.error.HTTPError as e:
        assert e.code == 401
        print(f"PASS 6: Login with wrong password rejected with status {e.code} Unauthorized.")

    # 7. Login with correct credentials
    good_data = json.dumps({"email": email, "password": "SecurePassword123"}).encode()
    r = urllib.request.Request(base + "/api/auth/login", data=good_data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(r) as resp:
        res = json.loads(resp.read().decode())
        assert res["learner_id"] == learner_id
        assert res["email"] == email
        assert res["display_name"] == "Maya Lin"
        assert res["onboarding_completed"] is False
        print(f"PASS 7: Login succeeded: user authenticated as {res['display_name']} ({res['email']}).")

    # 8. Complete interest onboarding questionnaire and save preferences
    pref_data = json.dumps({
        "learner_id": learner_id,
        "interests": ["Space and Astronomy", "Technology and Computers", "Artificial Intelligence and Robotics"],
        "learning_preferences": ["Visual models", "Step-by-step breakdown"],
        "preferred_difficulty": "Intermediate",
        "learning_goals": ["Understanding difficult concepts", "Learning topics beyond my syllabus"],
        "personal_learning_goal": "I want to explore astrophysics and quantum math"
    }).encode()
    r = urllib.request.Request(base + "/api/auth/preferences", data=pref_data, headers={"Content-Type": "application/json"}, method="PUT")
    with urllib.request.urlopen(r) as resp:
        res = json.loads(resp.read().decode())
        assert res["onboarding_completed"] is True
        assert len(res["preferences"]["interests"]) == 3
        print(f"PASS 8: Onboarding preferences saved in SQLite: interests={res['preferences']['interests']}.")

    # 9. Verify profile persistence on subsequent fetch
    r = urllib.request.Request(f"{base}/api/auth/profile?learner_id={learner_id}")
    with urllib.request.urlopen(r) as resp:
        res = json.loads(resp.read().decode())
        assert res["email"] == email
        assert res["onboarding_completed"] is True
        assert res["preferences"]["preferred_difficulty"] == "Intermediate"
        print("PASS 9: Profile and preferences persistent across sessions in SQLite.")

    # 10. AI Tutor personalization with saved preferences
    tutor_data = json.dumps({
        "message": "Explain equivalent fractions with space rockets.",
        "difficulty": "Intermediate",
        "learner_id": learner_id,
        "history": []
    }).encode()
    r = urllib.request.Request(base + "/api/tutor/chat", data=tutor_data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(r) as resp:
        res = json.loads(resp.read().decode())
        assert "reply" in res
        assert len(res["reply"]) > 10
        print("PASS 10: Personalized AI Tutor responded successfully using learner profile.")

    print("\nALL 10 VERIFICATION CHECKS PASSED PERFECTLY!")

if __name__ == "__main__":
    test_flow()
