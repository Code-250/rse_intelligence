import pytest
from fastapi.testclient import TestClient
from main import app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Document

# Create a test client
client = TestClient(app)

db_url = 'sqlite:///example.db'
engine = create_engine(db_url)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

db = TestingSessionLocal()

# Define the test database
class Document(Base):
    __tablename__ = 'documents'
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True)
    status = Column(String, index=True)
    created_at = Column(DateTime, index=True)
    analysis = Column(String, index=True)

# Create the test database tables
Base.metadata.create_all(bind=engine)

def test_get_document():
    # Test getting a document
    response = client.get('/api/v1/documents/1')
    assert response.status_code == 200
    assert response.json()['id'] == 1
    assert response.json()['filename'] == 'example.pdf'
    assert response.json()['status'] == 'completed'
    assert response.json()['created_at'] == '2022-01-01T12:00:00'
    assert response.json()['analysis'] == 'This is an example analysis'

def test_get_documents():
    # Test getting a list of documents
    response = client.get('/api/v1/documents/')
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]['id'] == 1
    assert response.json()[0]['filename'] == 'example.pdf'
    assert response.json()[0]['status'] == 'completed'
    assert response.json()[0]['created_at'] == '2022-01-01T12:00:00'
    assert response.json()[0]['analysis'] == 'This is an example analysis'
    assert response.json()[1]['id'] == 2
    assert response.json()[1]['filename'] == 'example2.pdf'
    assert response.json()[1]['status'] == 'pending'
    assert response.json()[1]['created_at'] == '2022-01-02T12:00:00'
    assert response.json()[1]['analysis'] == None

def test_delete_document():
    # Test deleting a document
    response = client.delete('/api/v1/documents/1')
    assert response.status_code == 204
    # Check if the document is deleted
    response = client.get('/api/v1/documents/1')
    assert response.status_code == 404
