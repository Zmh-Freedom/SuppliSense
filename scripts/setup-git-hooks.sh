#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(git rev-parse --show-toplevel)"
git -C "$ROOT_DIR" config core.hooksPath .githooks
echo "已启用项目 Git Hook：.githooks"
