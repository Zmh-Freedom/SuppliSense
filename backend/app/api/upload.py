"""
File upload and multimodal input API.
"""

import os
import uuid
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.services.document_parser import parse_file, generate_doc_id
from app.services.knowledge_base import add_documents

router = APIRouter()

# Upload directory
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


class UploadResponse(BaseModel):
    file_id: str
    filename: str
    file_type: str
    chunks_extracted: int
    metadata: dict[str, Any]


class FileListResponse(BaseModel):
    files: list[dict[str, Any]]


@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """
    Upload a file for analysis.
    Supports: PDF, DOCX, XLSX, TXT, CSV
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Check file extension
    allowed_extensions = {".pdf", ".docx", ".xlsx", ".txt", ".csv"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Allowed: {', '.join(allowed_extensions)}"
        )

    # Generate unique file ID
    file_id = str(uuid.uuid4())

    # Save file to disk
    file_path = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")
    try:
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

    # Parse file content
    try:
        chunks = parse_file(content, file.filename)
    except Exception as e:
        # Clean up saved file
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {str(e)}")

    if not chunks:
        # Clean up saved file
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=400, detail="No content extracted from file")

    # Add to knowledge base
    try:
        doc_ids = []
        documents = []
        metadatas = []

        for i, chunk in enumerate(chunks):
            doc_id = generate_doc_id(file.filename, i)
            doc_ids.append(doc_id)
            documents.append(chunk["content"])
            metadatas.append({
                **chunk["metadata"],
                "file_id": file_id,
            })

        add_documents(documents, metadatas, doc_ids)
    except Exception as e:
        # Clean up saved file
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"Failed to add to knowledge base: {str(e)}")

    return UploadResponse(
        file_id=file_id,
        filename=file.filename,
        file_type=ext[1:],  # Remove the dot
        chunks_extracted=len(chunks),
        metadata={
            "file_path": file_path,
            "file_size": len(content),
        }
    )


@router.get("/files", response_model=FileListResponse)
async def list_files():
    """List all uploaded files."""
    files = []
    if os.path.exists(UPLOAD_DIR):
        for filename in os.listdir(UPLOAD_DIR):
            file_path = os.path.join(UPLOAD_DIR, filename)
            if os.path.isfile(file_path):
                stat = os.stat(file_path)
                files.append({
                    "filename": filename,
                    "file_path": file_path,
                    "file_size": stat.st_size,
                    "created_at": stat.st_ctime,
                })

    return FileListResponse(files=files)


@router.delete("/files/{file_id}")
async def delete_file(file_id: str):
    """Delete an uploaded file."""
    # Find and delete file
    if os.path.exists(UPLOAD_DIR):
        for filename in os.listdir(UPLOAD_DIR):
            if filename.startswith(file_id):
                file_path = os.path.join(UPLOAD_DIR, filename)
                try:
                    os.remove(file_path)
                    return {"message": f"File {filename} deleted"}
                except Exception as e:
                    raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")

    raise HTTPException(status_code=404, detail="File not found")
