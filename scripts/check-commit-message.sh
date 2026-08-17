#!/usr/bin/env bash
set -euo pipefail

VALID_TYPES='feat|fix|docs|test|refactor|chore|perf|ci|build|revert|style|security|ui|debug|improve|merge'
VALID_PATTERN="^(${VALID_TYPES})(\\([a-z0-9/_-]+\\))?: .+"

validate_message() {
  local commit_id="$1"
  local message="$2"

  if [[ "$message" =~ $VALID_PATTERN || "$message" == Merge\ * || "$message" == Revert\ * ]]; then
    return 0
  fi

  echo "提交 ${commit_id} 的提交信息不符合规范："
  echo "  ${message}"
  echo "要求：<type>(<scope>): <description>"
  echo "示例：feat(sourcing): 增加联网供应商发现"
  return 1
}

if [[ "${1:-}" == "--range" ]]; then
  range="${2:?用法：$0 --range <commit-range>}"
  failed=0
  while IFS= read -r commit_id; do
    message="$(git log -1 --format='%s' "$commit_id")"
    validate_message "$commit_id" "$message" || failed=1
  done < <(git rev-list --no-merges "$range")
  exit "$failed"
fi

message_file="${1:?用法：$0 <commit-msg-file>}"
validate_message "local commit" "$(sed -n '1p' "$message_file")"
