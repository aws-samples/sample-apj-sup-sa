# Source this helper, then call omni_load_env <path> [parser-path].
# Non-empty .env assignments override the shell; empty assignments preserve an
# existing non-empty shell value, matching common/env_file.py.
omni_load_env() {
  local env_file="$1" env_dir parser_path assignments eval_status
  [ -f "$env_file" ] || return 0

  env_dir="${env_file%/*}"
  [ "$env_dir" = "$env_file" ] && env_dir=.
  parser_path="${2:-$env_dir/common/env_file.py}"
  if [ ! -f "$parser_path" ]; then
    echo "Environment parser not found: $parser_path" >&2
    return 1
  fi
  assignments="$(python3 "$parser_path" --shell "$env_file")" || {
    unset assignments
    return 1
  }
  eval "$assignments"
  eval_status=$?
  unset assignments
  return "$eval_status"
}
