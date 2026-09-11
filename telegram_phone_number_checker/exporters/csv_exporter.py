import csv
from typing import Any, Dict, List

RESULT_COLUMNS = [
    "id",
    "job_id",
    "original_phone",
    "normalized_phone",
    "status",
    "attempt_count",
    "telegram_user_id",
    "username",
    "first_name",
    "last_name",
    "user_was_online",
    "last_error_type",
    "last_error_message",
    "cleanup_error",
    "created_at",
    "completed_at",
]


class CsvExporter:
    def export(self, rows: List[Dict[str, Any]], output_path: str) -> None:
        if not rows:
            raise ValueError("No rows to export")
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
