import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_register():
    response = client.post("/api/v1/auth/register", json={"email": "test@example.com", "password": "test"})
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["refresh_token"]
    assert response.json()["user_id"]


def test_login():
    response = client.post("/api/v1/auth/login", data={"grant_type": "password", "username": "test@example.com", "password": "test"})
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["refresh_token"]


def test_refresh():
    response = client.post("/api/v1/auth/login", data={"grant_type": "password", "username": "test@example.com", "password": "test"})
    refresh_token = response.json()["refresh_token"]
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_register_existing_user():
    client.post("/api/v1/auth/register", json={"email": "test@example.com", "password": "test"})
    response = client.post("/api/v1/auth/register", json={"email": "test@example.com", "password": "test"})
    assert response.status_code == 409


def test_login_wrong_password():
    client.post("/api/v1/auth/register", json={"email": "test@example.com", "password": "test"})
    response = client.post("/api/v1/auth/login", data={"grant_type": "password", "username": "test@example.com", "password": "wrong"})
    assert response.status_code == 401


def test_login_wrong_email():
    response = client.post("/api/v1/auth/login", data={"grant_type": "password", "username": "wrong@example.com", "password": "test"})
    assert response.status_code == 401


def test_refresh_expired_token():
    response = client.post("/api/v1/auth/login", data={"grant_type": "password", "username": "test@example.com", "password": "test"})
    refresh_token = response.json()["refresh_token"]
    # Simulate token expiration
    import datetime
    import jwt
    payload = jwt.decode(refresh_token, "secret", algorithms=["HS256"])
    payload["exp"] = datetime.datetime.utcnow() - datetime.timedelta(minutes=31)
    expired_token = jwt.encode(payload, "secret", algorithm="HS256")
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": expired_token})
    assert response.status_code == 401


def test_refresh_invalid_token():
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": "invalid"})
    assert response.status_code == 401


def test_refresh_missing_token():
    response = client.post("/api/v1/auth/refresh", json={})
    assert response.status_code == 401