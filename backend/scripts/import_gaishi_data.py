"""CLI entry point for importing manually obtained Gasgoo supplier exports."""

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.sourcing.gaishi_import_service import import_gaishi_directory


def main() -> None:
    parser = argparse.ArgumentParser(description="导入盖世人工获取的供应商候选 Excel")
    parser.add_argument("directory", help="包含盖世 Excel 文件的目录")
    args = parser.parse_args()
    print(json.dumps(import_gaishi_directory(args.directory), ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
