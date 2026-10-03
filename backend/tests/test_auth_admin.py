import pytest
from starlette.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_login_success_and_me(client):
    # 1. Login with valid admin credentials
    login_res = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "PyroLens2026!"},
    )
    assert login_res.status_code == 200
    token_data = login_res.json()
    assert "access_token" in token_data
    token = token_data["access_token"]
    assert token_data["user"]["role"] == "admin"

    # 2. Get current profile via /auth/me
    headers = {"Authorization": f"Bearer {token}"}
    me_res = client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["username"] == "admin"
    assert me_data["role"] == "admin"


def test_login_invalid_credentials(client):
    res = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "WrongPassword123"},
    )
    assert res.status_code == 401


def test_role_enforcement_admin_endpoints(client):
    # 1. Login as viewer
    viewer_login = client.post(
        "/api/v1/auth/login",
        json={"username": "viewer", "password": "PyroLens2026!"},
    )
    assert viewer_login.status_code == 200
    viewer_token = viewer_login.json()["access_token"]
    viewer_headers = {"Authorization": f"Bearer {viewer_token}"}

    # 2. Viewer attempts to access admin endpoint -> 403 Forbidden
    forbidden_res = client.get("/api/v1/admin/users", headers=viewer_headers)
    assert forbidden_res.status_code == 403

    # 3. Viewer attempts to create user -> 403 Forbidden
    forbidden_post = client.post(
        "/api/v1/admin/users",
        json={"username": "unauthorized", "password": "pass", "role": "analyst"},
        headers=viewer_headers,
    )
    assert forbidden_post.status_code == 403


def test_admin_user_crud_lifecycle(client):
    # 1. Login as admin
    admin_login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "PyroLens2026!"},
    )
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 2. List users
    users_res = client.get("/api/v1/admin/users", headers=admin_headers)
    assert users_res.status_code == 200
    users = users_res.json()
    assert len(users) >= 3
    usernames = [u["username"] for u in users]
    assert "admin" in usernames
    assert "analyst" in usernames
    assert "viewer" in usernames

    # 3. Create a new user
    new_user_payload = {
        "username": "patrol_lead",
        "email": "lead@pyrolens.org",
        "password": "TemporaryPassword123!",
        "role": "analyst",
        "is_active": True,
    }
    create_res = client.post(
        "/api/v1/admin/users",
        json=new_user_payload,
        headers=admin_headers,
    )
    # Could be 200 or 201
    assert create_res.status_code in (200, 201)
    created_user = create_res.json()
    assert created_user["username"] == "patrol_lead"
    user_id = created_user["id"]

    # 4. Update the user
    update_res = client.patch(
        f"/api/v1/admin/users/{user_id}",
        json={"role": "viewer"},
        headers=admin_headers,
    )
    assert update_res.status_code == 200
    updated_user = update_res.json()
    assert updated_user["role"] == "viewer"

    # 5. Delete the test user
    delete_res = client.delete(f"/api/v1/admin/users/{user_id}", headers=admin_headers)
    assert delete_res.status_code in (200, 204)

    # Verify user no longer returned
    get_res = client.get(f"/api/v1/admin/users/{user_id}", headers=admin_headers)
    assert get_res.status_code == 404
