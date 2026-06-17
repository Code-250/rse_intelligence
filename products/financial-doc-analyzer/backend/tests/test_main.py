import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_upload_document():
    # Test uploading a PDF document
    with open('test.pdf', 'rb') as file:
        response = client.post('/api/v1/documents/upload', files={'file': file})
    assert response.status_code == 202

def test_upload_non_pdf_document():
    # Test uploading a non-PDF document
    with open('test.txt', 'rb') as file:
        response = client.post('/api/v1/documents/upload', files={'file': file})
    assert response.status_code == 415

def test_upload_large_document():
    # Test uploading a large document
    with open('large_test.pdf', 'rb') as file:
        response = client.post('/api/v1/documents/upload', files={'file': file})
    assert response.status_code == 413
