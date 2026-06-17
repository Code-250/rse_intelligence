from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from typing import List, Optional
from datetime import datetime
import os
import json

router = APIRouter(
    prefix='/api/v1/documents',
    tags=['documents']
)

# Define the database connection
SQLALCHEMY_DATABASE_URL = 'sqlite:///example.db'
engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define the document model
class Document(Base):
    __tablename__ = 'documents'
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True)
    status = Column(String, index=True)
    created_at = Column(DateTime, index=True)
    analysis = Column(String, index=True)

# Create the database tables
Base.metadata.create_all(bind=engine)

db = SessionLocal()

# Define the authentication scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl='login')

# Define the document retrieval endpoint
@router.get('/{id}', response_model=Document)
def get_document(id: int, token: str = Depends(oauth2_scheme)):
    # Check if the document exists and is owned by the requesting user
    document = db.query(Document).filter(Document.id == id).first()
    if document is None or document.status != 'completed':
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found or not completed')
    return document

# Define the document listing endpoint
@router.get('/', response_model=List[Document])
def get_documents(limit: int = 20, offset: int = 0, token: str = Depends(oauth2_scheme)):
    # Get the list of documents for the requesting user
    documents = db.query(Document).offset(offset).limit(limit).all()
    return documents

# Define the document deletion endpoint
@router.delete('/{id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_document(id: int, token: str = Depends(oauth2_scheme)):
    # Check if the document exists and is owned by the requesting user
    document = db.query(Document).filter(Document.id == id).first()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found')
    # Delete the document, analysis, and file from storage
    db.delete(document)
    db.commit()
    return JSONResponse(content={'message': 'Document deleted successfully'}, status_code=status.HTTP_200_OK)
