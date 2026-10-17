"""Unit and API tests for Registration, Secure Login, Profile, and Interest Onboarding."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.service import hash_password, verify_password, validate_password_strength, validate_email_address, BadRequest


@pytest.fixture()
def client():
    with TestClient(create_app(":memory:", rate_limit_per_minute=0, model_path="")) as c:
        yield c


def test_password_hashing_and_verification():
    pw = "SuperSecure123"
    h1, s1 = hash_password(pw)
    h2, s2 = hash_password(pw)
    assert h1 != pw
    assert h1 != h2  # Different salts generate different hashes
    assert verify_password(pw, h1, s1) is True
    assert verify_password(pw, h2, s2) is True
    assert verify_password("WrongPassword123", h1, s1) is False


def test_password_strength_rules():
    # Valid
    validate_password_strength("ValidPass1")

    # Too short
    with pytest.raises(BadRequest, match="at least 8 characters"):
        validate_password_strength("Short1A")

    # Missing uppercase
    with pytest.raises(BadRequest, match="uppercase"):
        validate_password_strength("lowercase123")

    # Missing lowercase
    with pytest.raises(BadRequest, match="lowercase"):
        validate_password_strength("UPPERCASE123")

    # Missing number
    with pytest.raises(BadRequest, match="number"):
        validate_password_strength("NoNumbersHere")


def test_email_validation():
    assert validate_email_address("  Student@Example.Com  ") == "student@example.com"

    with pytest.raises(BadRequest, match="required"):
        validate_email_address("")

    with pytest.raises(BadRequest, match="valid email"):
        validate_email_address("notanemail")

    with pytest.raises(BadRequest, match="valid email"):
        validate_email_address("missingdomain@")

    with pytest.raises(BadRequest, match="valid email"):
        validate_email_address("@missinguser.com")


def test_registration_and_login_flow(client):
    # 1. Register successfully
    reg_res = client.post("/api/auth/register", json={
        "display_name": "Maya Lin",
        "email": "Maya.Lin@School.edu",
        "password": "Password123",
    })
    assert reg_res.status_code == 201
    data = reg_res.json()
    assert data["email"] == "maya.lin@school.edu"
    assert data["display_name"] == "Maya Lin"
    assert data["onboarding_completed"] is False
    learner_id = data["learner_id"]
    assert len(learner_id) == 36

    # 2. Reject duplicate email
    dup_res = client.post("/api/auth/register", json={
        "display_name": "Another Maya",
        "email": "maya.lin@school.edu",
        "password": "DifferentPassword999",
    })
    assert dup_res.status_code == 409
    assert "already exists" in dup_res.json()["detail"].lower()

    # 3. Reject weak password via API
    weak_res = client.post("/api/auth/register", json={
        "display_name": "Bob",
        "email": "bob@example.com",
        "password": "weak",
    })
    assert weak_res.status_code == 422 or weak_res.status_code == 400

    # 4. Login with correct credentials
    login_res = client.post("/api/auth/login", json={
        "email": "maya.lin@school.edu",
        "password": "Password123",
    })
    assert login_res.status_code == 200
    login_data = login_res.json()
    assert login_data["learner_id"] == learner_id
    assert login_data["display_name"] == "Maya Lin"
    assert login_data["onboarding_completed"] is False

    # 5. Login with invalid password
    bad_pw = client.post("/api/auth/login", json={
        "email": "maya.lin@school.edu",
        "password": "WrongPassword999",
    })
    assert bad_pw.status_code == 401

    # 6. Login with unknown email
    unknown = client.post("/api/auth/login", json={
        "email": "ghost@nowhere.com",
        "password": "Password123",
    })
    assert unknown.status_code == 401


def test_onboarding_preferences_and_tutor(client):
    # Register user
    reg = client.post("/api/auth/register", json={
        "display_name": "Sam Carter",
        "email": "sam@stargate.org",
        "password": "Astrophysics99",
    }).json()
    learner_id = reg["learner_id"]

    # Initial profile check
    prof_before = client.get(f"/api/auth/profile?learner_id={learner_id}").json()
    assert prof_before["onboarding_completed"] is False
    assert prof_before["preferences"]["interests"] == []

    # Save Onboarding Preferences
    update_res = client.put("/api/auth/preferences", json={
        "learner_id": learner_id,
        "interests": ["Space and Astronomy", "Artificial Intelligence and Robotics"],
        "learning_preferences": ["Real-world analogies", "Step-by-step breakdowns"],
        "preferred_difficulty": "Advanced",
        "learning_goals": ["Understanding difficult concepts", "Topics beyond syllabus"],
        "personal_learning_goal": "I want to understand rocket orbits and orbital mechanics.",
    })
    assert update_res.status_code == 200
    updated_prof = update_res.json()
    assert updated_prof["onboarding_completed"] is True
    assert "Space and Astronomy" in updated_prof["preferences"]["interests"]
    assert updated_prof["preferences"]["preferred_difficulty"] == "Advanced"
    assert updated_prof["preferences"]["personal_learning_goal"] == "I want to understand rocket orbits and orbital mechanics."

    # Login reflects onboarding completed
    login = client.post("/api/auth/login", json={
        "email": "sam@stargate.org",
        "password": "Astrophysics99",
    }).json()
    assert login["onboarding_completed"] is True
    assert login["preferences"]["interests"] == ["Space and Astronomy", "Artificial Intelligence and Robotics"]


def test_anonymous_learner_conversion(client):
    from app.questions import BANK
    q = BANK[0]

    # Create anonymous learner
    anon_id = client.post("/api/learners").json()["learner_id"]

    # Submit an attempt as anonymous
    att = client.post("/api/attempts", json={
        "learner_id": anon_id,
        "question_id": q.id,
        "answer": str(q.answer),
    })
    assert att.status_code == 200

    # Register providing the existing anonymous learner_id
    reg = client.post("/api/auth/register", json={
        "display_name": "Alex River",
        "email": "alex@river.com",
        "password": "SecurePassword1",
        "learner_id": anon_id,
    })
    assert reg.status_code == 201
    assert reg.json()["learner_id"] == anon_id

    # Verify history is preserved under this learner_id
    prog = client.get(f"/api/progress?learner_id={anon_id}").json()
    assert prog["totals"]["attempts"] >= 1
