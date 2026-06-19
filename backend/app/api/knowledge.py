"""
Knowledge Base API: Document upload, search, and management.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.services.document_parser import generate_doc_id, parse_file
from app.services.knowledge_base import (
    add_documents,
    clear_all,
    delete_documents,
    get_stats,
    search,
)

router = APIRouter(dependencies=[Depends(get_current_user)])


class SearchRequest(BaseModel):
    query: str
    n_results: int = 5
    filters: dict[str, Any] | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[dict[str, Any]]
    count: int


class UploadResponse(BaseModel):
    filename: str
    chunks_added: int
    doc_ids: list[str]


class StatsResponse(BaseModel):
    total_documents: int
    collection_name: str
    embedding_model: str


@router.post(
    "/upload",
    response_model=UploadResponse,
    summary="上传文档到知识库",
    description="上传文档（支持 PDF、DOCX、XLSX、TXT），解析后存入向量知识库以供 RAG 检索。",
    responses={
        400: {"description": "文件格式不支持或解析失败"},
        500: {"description": "服务器内部错误"},
    },
)
async def upload_document(file: UploadFile = File(...)):
    """
    Upload a document to the knowledge base.
    Supported formats: PDF, DOCX, XLSX, TXT
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Check file extension
    allowed_extensions = {".pdf", ".docx", ".xlsx", ".txt"}
    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format: {ext}. Allowed: {allowed_extensions}",
        )

    # Read file content
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    # Parse document
    try:
        chunks = parse_file(content, file.filename)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {str(e)}")

    if not chunks:
        raise HTTPException(status_code=400, detail="No content extracted from file")

    # Generate IDs and prepare data
    doc_ids = []
    documents = []
    metadatas = []

    for i, chunk in enumerate(chunks):
        doc_id = generate_doc_id(file.filename, i)
        doc_ids.append(doc_id)
        documents.append(chunk["content"])
        metadatas.append(chunk["metadata"])

    # Add to knowledge base
    try:
        add_documents(documents, metadatas, doc_ids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add to knowledge base: {str(e)}")

    return UploadResponse(
        filename=file.filename,
        chunks_added=len(chunks),
        doc_ids=doc_ids,
    )


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="搜索知识库",
    description="在知识库中执行语义搜索，返回与查询最相关的文档片段。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def search_documents(req: SearchRequest):
    """Search the knowledge base for relevant documents."""
    try:
        results = search(req.query, n_results=req.n_results, filters=req.filters)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

    return SearchResponse(
        query=req.query,
        results=results,
        count=len(results),
    )


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="获取知识库统计信息",
    description="返回知识库的文档总数、集合名称和嵌入模型信息。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def get_knowledge_stats():
    """Get knowledge base statistics."""
    try:
        stats = get_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")

    return StatsResponse(**stats)


@router.delete(
    "/clear",
    summary="清空知识库",
    description="删除知识库中的所有文档，操作不可恢复。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def clear_knowledge_base():
    """Clear all documents from the knowledge base."""
    try:
        clear_all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear: {str(e)}")

    return {"message": "Knowledge base cleared"}


@router.delete(
    "/documents",
    summary="删除指定文档",
    description="根据文档 ID 列表删除知识库中的指定文档。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def delete_knowledge_documents(ids: list[str]):
    """Delete specific documents from the knowledge base."""
    try:
        delete_documents(ids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {str(e)}")

    return {"message": f"Deleted {len(ids)} documents", "ids": ids}
