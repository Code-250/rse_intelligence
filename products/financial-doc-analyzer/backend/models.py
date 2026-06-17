from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
Base = declarative_base()
class User(Base):
    __tablename__ = 'fda_users'
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True)
    hashed_password = Column(String)
    plan = Column(String)
    created_at = Column(DateTime)
class Document(Base):
    __tablename__ = 'fda_documents'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('fda_users.id'))
    filename = Column(String)
    storage_path = Column(String)
    status = Column(String)
    created_at = Column(DateTime)
    user = relationship('User', backref='documents')
class Analysis(Base):
    __tablename__ = 'fda_analyses'
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey('fda_documents.id'))
    raw_ocr = Column(Text)
    structured_data = Column(Text)
    ai_summary = Column(Text)
    model_used = Column(String)
    processing_ms = Column(Integer)
    created_at = Column(DateTime)
    document = relationship('Document', backref='analyses')
class Usage(Base):
    __tablename__ = 'fda_usage'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('fda_users.id'))
    month = Column(String)
    document_count = Column(Integer)
    user = relationship('User', backref='usage')