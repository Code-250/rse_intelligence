from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from jose import jwt, JWTError
from datetime import datetime, timedelta
from bcrypt import hashpw, gensalt, checkpw
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
import logging

# Define the router
router = APIRouter(
    prefix="/api/v1/auth",
    tags=["auth"],
)

# Define the database connection
SQLALCHEMY_DATABASE_URL = os.environ.get('FDA_DATABASE_URL')
engine = create_engine(SQLALCHEMY_DATABASE_URL)
Base = declarative_base()

# Define the user model
class User(Base):
    __tablename__ = 'fda_users'
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True)
    hashed_password = Column(String)

# Create the database tables
Base.metadata.create_all(engine)

# Define the session maker
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Define the OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Define the token model
class Token(BaseModel):
    access_token: str
    token_type: str

# Define the token data model
class TokenData(BaseModel):
    email: str | None = None

# Define the user model
class User(BaseModel):
    email: str
    full_name: str | None = None
    disabled: bool | None = None

# Define the password context
password_context = CryptContext(schemes=["bcrypt"], default="bcrypt")

# Define the secret key
SECRET_KEY = os.environ.get('FDA_SECRET_KEY')
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# Define the register endpoint
@router.post("/register")
async def register(user: User):
    # Check if the user already exists
    db = SessionLocal()
    existing_user = db.query(User).filter(User.email == user.email).first()
    if existing_user:
        raise HTTPException(status_code=409, detail="Email already registered")
    # Hash the password
    hashed_password = hashpw(user.hashed_password.encode(), gensalt())
    # Create the user
    new_user = User(email=user.email, hashed_password=hashed_password)
    db.add(new_user)
    db.commit()
    db.close()
    # Generate the tokens
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(
        data={"sub": user.email}, expires_delta=timedelta(days=30)
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_id": new_user.id,
    }

# Define the login endpoint
@router.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # Check if the user exists
    db = SessionLocal()
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    # Check if the password is correct
    if not checkpw(form_data.password.encode(), user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    # Generate the tokens
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(
        data={"sub": user.email}, expires_delta=timedelta(days=30)
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
    }

# Define the refresh endpoint
@router.post("/refresh")
async def refresh(token: str = Depends(oauth2_scheme)):
    # Check if the token is valid
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    # Generate the new access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": payload['sub']}, expires_delta=access_token_expires
    )
    return {
        "access_token": access_token,
    }
