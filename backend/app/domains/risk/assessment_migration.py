"""Read-only migration inventory. No historical identities or hashes are fabricated."""

import json
from datetime import datetime, timezone

from app.db.mongo import get_db
from app.db.postgres import get_cursor


def migration_report() -> dict:
    with get_cursor() as (_, cur):
        cur.execute("""SELECT COUNT(*),COUNT(*) FILTER(WHERE assessment_status='legacy_unverified'),
            COUNT(*) FILTER(WHERE assessment_status='committed'),COUNT(*) FILTER(WHERE company_id IS NULL),
            COUNT(*) FILTER(WHERE policy_hash IS NULL),COUNT(*) FILTER(WHERE input_digest IS NULL)
            FROM assessment_history""")
        total, legacy, committed, no_company, no_policy, no_digest = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*) FROM (SELECT normalized_name FROM companies WHERE merged_into_id IS NULL GROUP BY normalized_name HAVING COUNT(*)>1) names"
        )
        ambiguous = cur.fetchone()[0]
    collection = get_db()["alert_snapshots"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "postgres": {
            "total": total,
            "legacy_unverified": legacy,
            "committed": committed,
            "missing_company_id": no_company,
            "missing_policy": no_policy,
            "missing_input_digest": no_digest,
            "ambiguous_company_name_groups": ambiguous,
        },
        "mongo": {
            "total": collection.count_documents({}),
            "without_assessment_id": collection.count_documents(
                {"assessment_id": None}
            ),
            "without_company_id": collection.count_documents({"company_id": None}),
        },
        "writes_performed": 0,
        "historical_uuid_preserved": True,
        "backfill_policy": "Only source-proven identities may be backfilled separately; unknown historical evidence remains legacy_unverified.",
    }


if __name__ == "__main__":
    print(json.dumps(migration_report(), ensure_ascii=False, indent=2))
