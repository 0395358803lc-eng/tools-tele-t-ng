import json
from typing import Any, Dict, List


class JsonExporter:
    def export(self, rows: List[Dict[str, Any]], output_path: str) -> None:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)
