"""
Document Parser: Parse PDF, Word, Excel files and split into chunks.
"""

import hashlib
import io
import os
import uuid
from typing import Any, BinaryIO

import fitz  # PyMuPDF
import openpyxl
from docx import Document


# Chunk configuration
CHUNK_SIZE = 500  # characters per chunk
CHUNK_OVERLAP = 50  # overlap between chunks


def parse_file(file_content: bytes, filename: str) -> list[dict[str, Any]]:
    """
    Parse a file and return text chunks.

    Args:
        file_content: File content as bytes
        filename: Original filename (used to determine format)

    Returns:
        List of dicts with keys: content, metadata
    """
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".pdf":
        return parse_pdf(file_content, filename)
    elif ext == ".docx":
        return parse_docx(file_content, filename)
    elif ext == ".xlsx":
        return parse_excel(file_content, filename)
    elif ext == ".txt":
        return parse_text(file_content, filename)
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def parse_pdf(file_content: bytes, filename: str) -> list[dict[str, Any]]:
    """Parse PDF file using PyMuPDF."""
    doc = fitz.open(stream=file_content, filetype="pdf")
    chunks = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()

        # Split into chunks
        page_chunks = split_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)

        for i, chunk in enumerate(page_chunks):
            chunks.append({
                "content": chunk,
                "metadata": {
                    "source": filename,
                    "page": page_num + 1,
                    "chunk_index": i,
                    "type": "pdf",
                },
            })

    return chunks


def parse_docx(file_content: bytes, filename: str) -> list[dict[str, Any]]:
    """Parse Word document using python-docx."""
    doc = Document(io.BytesIO(file_content))
    full_text = []

    for para in doc.paragraphs:
        if para.text.strip():
            full_text.append(para.text)

    text = "\n".join(full_text)
    chunks = split_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)

    result = []
    for i, chunk in enumerate(chunks):
        result.append({
            "content": chunk,
            "metadata": {
                "source": filename,
                "chunk_index": i,
                "type": "docx",
            },
        })

    return result


def parse_excel(file_content: bytes, filename: str) -> list[dict[str, Any]]:
    """Parse Excel file using openpyxl."""
    wb = openpyxl.load_workbook(io.BytesIO(file_content), data_only=True)
    chunks = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = []

        for row in ws.iter_rows(values_only=True):
            # Convert row to string, skip empty rows
            row_str = " | ".join(str(cell) if cell is not None else "" for cell in row)
            if row_str.strip(" |"):
                rows.append(row_str)

        if rows:
            text = f"Sheet: {sheet_name}\n" + "\n".join(rows)
            sheet_chunks = split_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)

            for i, chunk in enumerate(sheet_chunks):
                chunks.append({
                    "content": chunk,
                    "metadata": {
                        "source": filename,
                        "sheet": sheet_name,
                        "chunk_index": i,
                        "type": "xlsx",
                    },
                })

    return chunks


def parse_text(file_content: bytes, filename: str) -> list[dict[str, Any]]:
    """Parse plain text file."""
    text = file_content.decode("utf-8", errors="ignore")
    chunks = split_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)

    result = []
    for i, chunk in enumerate(chunks):
        result.append({
            "content": chunk,
            "metadata": {
                "source": filename,
                "chunk_index": i,
                "type": "txt",
            },
        })

    return result


def split_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """
    Split text into overlapping chunks.

    Args:
        text: Text to split
        chunk_size: Size of each chunk in characters
        overlap: Overlap between chunks in characters

    Returns:
        List of text chunks
    """
    if not text.strip():
        return []

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]

        # Try to break at sentence or paragraph boundary
        if end < len(text):
            # Look for sentence ending
            for sep in ["。", ".", "\n", "！", "!", "？", "?"]:
                last_sep = chunk.rfind(sep)
                if last_sep > chunk_size // 2:
                    chunk = chunk[:last_sep + 1]
                    end = start + last_sep + 1
                    break

        chunks.append(chunk.strip())
        start = end - overlap

    return [c for c in chunks if c]  # Remove empty chunks


def generate_doc_id(filename: str, chunk_index: int) -> str:
    """Generate a unique ID for a document chunk."""
    # Use hash of filename + chunk index for deterministic IDs
    content = f"{filename}:{chunk_index}"
    return hashlib.md5(content.encode()).hexdigest()
