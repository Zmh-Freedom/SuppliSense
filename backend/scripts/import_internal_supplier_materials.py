"""CLI entry point for internal supplier-material relationship imports."""

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.sourcing.internal_history_import_service import import_internal_supplier_material_file


def main() -> None:
    parser = argparse.ArgumentParser(description="导入内部零件—供应商关系清单")
    parser.add_argument("file", help="零件—供应商 Excel 文件")
    args = parser.parse_args()
    print(json.dumps(import_internal_supplier_material_file(args.file), ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
