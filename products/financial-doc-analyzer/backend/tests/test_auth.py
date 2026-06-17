from fastapi.testclient import TestClient
from main import app
from . import models
import pytest
from datetime import datetime, timedelta
import jwt

client = TestClient(app)

def test_register():
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "test@example.com", "password": "testpassword"}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["token_type"] == "bearer"

def test_login():
    client.post(
        "/api/v1/auth/register",
        json={"email": "test@example.com", "password": "testpassword"}
    )
    response = client.post(
        "/api/v1/auth/login",
        data={"grant_type": "password", "username": "test@example.com", "password": "testpassword"}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["token_type"] == "bearer"

def test_refresh():
    client.post(
        "/api/v1/auth/register",
        json={"email": "test@example.com", "password": "testpassword"}
    )
    response = client.post(
        "/api/v1/auth/login",
        data={"grant_type": "password", "username": "test@example.com", "password": "testpassword"}
    )
    access_token = response.json()["access_token"]
    response = client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["token_type"] == "bearer"

def test_register_duplicate_email():
    client.post(
        "/api/v1/auth/register",
        json={"email": "test@example.com", "password": "testpassword"}
    )
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "test@example.com", "password": "testpassword"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Email already registered"

def test_login_wrong_password():
    client.post(
        "/api/v1/auth/register",
        json={"email": "test@example.com", "password": "testpassword"}
    )
    response = client.post(
        "/api/v1/auth/login",
        data={"grant_type": "password", "username": "test@example.com", "password": "wrongpassword"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect username or password"

def test_refresh_expired_token():
    client.post(
        "/api/v1/auth/register",
        json={"email": "test@example.com", "password": "testpassword"}
    )
    response = client.post(
        "/api/v1/auth/login",
        data={"grant_type": "password", "username": "test@example.com", "password": "testpassword"}
    )
    access_token = response.json()["access_token"]
    # Simulate expired token
    payload = jwt.decode(access_token, "FDA_SECRET_KEY", algorithms=["HS256"])
    payload["exp"] = datetime.utcnow() - timedelta(minutes=61)
    expired_token = jwt.encode(payload, "FDA_SECRET_KEY", algorithm="HS256")
    response = client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"

def test_refresh_missing_token():
    response = client.post(
        "/api/v1/auth/refresh"
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"

def test_refresh_malformed_token():
    response = client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": "Bearer malformedtoken"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"