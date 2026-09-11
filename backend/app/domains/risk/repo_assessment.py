"""
Assessment history repository -- PostgreSQL.
"""

import json
import uuid
from typing import Any

from app.db.postgres import get_cursor


def clear_history() -> int:
    """Remove assessment history so old score semantics cannot mix with v3."""
    with get_cursor() as (conn, cur):
        cur.execute("DELETE FROM assessment_history")
        return int(cur.rowcount or 0)


def save_assessment(
    company_name: str,
    risk_score: int,
    risk_level: str,
    user_id: str | None = None,
    score_breakdown: dict[str, Any] | None = None,
    financial_data: dict[str, Any] | None = None,
    risk_detail: dict[str, Any] | None = None,
    scoring_version: str = "unknown",
) -> str:
    assess_id = str(uuid.uuid4())
    with get_cursor() as (conn, cur):
        cur.execute(
            """INSERT INTO assessment_history
               (id, user_id, company_name, risk_score, risk_level,
                score_breakdown, financial_data, risk_detail, scoring_version)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                assess_id, user_id, company_name, risk_score, risk_level,
                json.dumps(score_breakdown, ensure_ascii=False) if score_breakdown else None,
                json.dumps(financial_data, ensure_ascii=False) if financial_data else None,
                json.dumps(risk_detail, ensure_ascii=False) if risk_detail else None,
                scoring_version,
            ),
        )
    return assess_id


def get_history(
    company_name: str | None = None,
    user_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses = []
    params: list = []

    if company_name:
        clauses.append("company_name = %s")
        params.append(company_name)
    if user_id:
        clauses.append("user_id = %s")
        params.append(user_id)

    where = " AND ".join(clauses) if clauses else "TRUE"
    params.append(limit)

    with get_cursor() as (conn, cur):
        cur.execute(
            f"SELECT * FROM assessment_history WHERE {where} ORDER BY created_at DESC LIMIT %s",
            params,
        )
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        return [_row_to_dict(columns, row) for row in rows]


def get_trend(
    company_name: str,
    days: int = 90,
    scoring_version: str | None = None,
) -> list[dict[str, Any]]:
    """
    获取风险评分时间序列，按 scoring_version 过滤。

    如果不指定 scoring_version，返回最新版本的数据。
    """
    with get_cursor() as (conn, cur):
        if scoring_version:
            cur.execute(
                """SELECT risk_score, risk_level, created_at
                   FROM assessment_history
                   WHERE company_name = %s
                     AND scoring_version = %s
                     AND created_at >= NOW() - INTERVAL '%s days'
                   ORDER BY created_at ASC""",
                (company_name, scoring_version, days),
            )
        else:
            # 找到该公司最新的评分版本
            cur.execute(
                """SELECT scoring_version FROM assessment_history
                   WHERE company_name = %s
                   ORDER BY created_at DESC LIMIT 1""",
                (company_name,),
            )
            row = cur.fetchone()
            version = row[0] if row else "unknown"
            cur.execute(
                """SELECT risk_score, risk_level, created_at
                   FROM assessment_history
                   WHERE company_name = %s
                     AND scoring_version = %s
                     AND created_at >= NOW() - INTERVAL '%s days'
                   ORDER BY created_at ASC""",
                (company_name, version, days),
            )

        rows = cur.fetchall()
        return [
            {
                "date": row[2].strftime("%Y-%m-%d") if hasattr(row[2], "strftime") else str(row[2])[:10],
                "risk_score": row[0],
                "risk_level": row[1],
            }
            for row in rows
        ]


def _row_to_dict(columns, row) -> dict[str, Any]:
    result = dict(zip(columns, row))
    if "id" in result and hasattr(result["id"], "hex"):
        result["id"] = str(result["id"])
    if "user_id" in result and result["user_id"] and hasattr(result["user_id"], "hex"):
        result["user_id"] = str(result["user_id"])
    return result
