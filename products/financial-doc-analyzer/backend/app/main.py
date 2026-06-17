from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import os
import uuid
import logging
from logging.config import dictConfig
import asyncio
from . import ocr
from . import analysis
from . import db

class Document(BaseModel):
    id: str
    filename: str
    status: str

class UploadRequest(BaseModel):
    file: UploadFile

dictConfig({
    'version': 1,
    'formatters': {'default': {
        'format': '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
    }},
    'handlers': {'wsgi': {
        'class': 'logging.StreamHandler',
        'stream': 'ext://flask.logging.wsgi_errors_stream',
        'formatter': 'default'
    }},
    'root': {
        'level': 'DEBUG',
        'handlers': ['wsgi']
    }
})

app = FastAPI()

FDA_STORAGE_PATH = os.getenv('FDA_STORAGE_PATH')
FDA_MAX_FILE_SIZE_MB = int(os.getenv('FDA_MAX_FILE_SIZE_MB'))

@app.post('/api/v1/documents/upload')
async def upload_document(file: UploadFile = File(...)):
    if file.filename.split('.')[-1] != 'pdf':
        raise HTTPException(status_code=415, detail='Only PDF files are accepted.')
    if file.filename.split('.')[-1] == 'pdf' and file.spool_max_size > FDA_MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f'File size exceeds the maximum allowed size of {FDA_MAX_FILE_SIZE_MB} MB.')
    document_id = str(uuid.uuid4())
    user_id = 'current_user_id'
    filename = file.filename
    file_path = os.path.join(FDA_STORAGE_PATH, user_id, f'{document_id}.pdf')
    os.makedirs(os.path.join(FDA_STORAGE_PATH, user_id), exist_ok=True)
    with open(file_path, 'wb') as f:
        f.write(file.file.read())
    db.create_document(user_id, document_id, filename, 'processing')
    asyncio.create_task(ocr.process_document(document_id))
    return JSONResponse(status_code=202, content={'id': document_id, 'status': 'processing', 'filename': filename})
