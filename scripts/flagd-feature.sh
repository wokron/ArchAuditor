#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-}"
NAMESPACE="${NAMESPACE:-default}"

usage() {
  cat <<'EOF'
Usage:
  scripts/flagd-feature.sh set <flag-name> <variant>

Examples:
  scripts/flagd-feature.sh set addCircularDependency on
  scripts/flagd-feature.sh set addCircularDependency off

Environment:
  BASE_URL=http://127.0.0.1:8080/feature
  NAMESPACE=default
EOF
}

python_bin() {
  if command -v python3 >/dev/null 2>&1; then
    echo python3
  elif command -v python >/dev/null 2>&1; then
    echo python
  else
    echo "python3 or python is required." >&2
    exit 1
  fi
}

resolve_base_url() {
  if [[ -n "$BASE_URL" ]]; then
    echo "${BASE_URL%/}"
    return
  fi

  for candidate in "http://127.0.0.1:8080/feature" "http://127.0.0.1:4000"; do
    if curl -fsS --max-time 5 "$candidate/api/read" >/dev/null 2>&1; then
      echo "$candidate"
      return
    fi
  done

  return 1
}

read_config_from_k8s() {
  local output="$1"

  command -v kubectl >/dev/null 2>&1 || {
    echo "kubectl is required for ConfigMap fallback but was not found in PATH." >&2
    exit 1
  }

  kubectl get configmap flagd-config \
    -n "$NAMESPACE" \
    -o jsonpath='{.data.demo\.flagd\.json}' > "$output"
}

write_config_to_k8s() {
  local config_path="$1"

  command -v kubectl >/dev/null 2>&1 || {
    echo "kubectl is required for ConfigMap fallback but was not found in PATH." >&2
    exit 1
  }

  kubectl create configmap flagd-config \
    -n "$NAMESPACE" \
    --from-file=demo.flagd.json="$config_path" \
    --dry-run=client \
    -o yaml | kubectl apply -f - >/dev/null

  kubectl rollout restart deployment/flagd -n "$NAMESPACE" >/dev/null
  kubectl rollout status deployment/flagd -n "$NAMESPACE" --timeout=180s >/dev/null
}

cmd="${1:-}"
if [[ "$cmd" == "-h" || "$cmd" == "--help" || -z "$cmd" ]]; then
  usage
  exit 0
fi

if [[ "$cmd" != "set" || $# -ne 3 ]]; then
  usage
  exit 1
fi

flag_name="$2"
variant="$3"
tmp_config="$(mktemp)"
tmp_body="$(mktemp)"
tmp_data="$(mktemp)"

mode="k8s"
base_url=""
if base_url="$(resolve_base_url)"; then
  if curl -fsS "$base_url/api/read" > "$tmp_config"; then
    mode="http"
  else
    read_config_from_k8s "$tmp_config"
  fi
else
  read_config_from_k8s "$tmp_config"
fi

"$(python_bin)" - "$tmp_config" "$tmp_body" "$tmp_data" "$flag_name" "$variant" <<'PY'
import json
import sys

config_path, body_path, data_path, flag_name, variant = sys.argv[1:6]

with open(config_path, "r", encoding="utf-8") as fh:
    config = json.load(fh)

flags = config.get("flags", {})
if flag_name not in flags:
    raise SystemExit(f"Flag '{flag_name}' does not exist.")

variants = flags[flag_name].get("variants", {})
if variant not in variants:
    raise SystemExit(
        f"Variant '{variant}' is invalid for '{flag_name}'. "
        f"Available variants: {', '.join(sorted(variants.keys()))}"
    )

flags[flag_name]["defaultVariant"] = variant

with open(data_path, "w", encoding="utf-8") as fh:
    json.dump(config, fh, indent=2)

with open(body_path, "w", encoding="utf-8") as fh:
    json.dump({"data": config}, fh)
PY

if [[ "$mode" == "http" ]]; then
  if curl -fsS \
    -X POST \
    -H "Content-Type: application/json" \
    --data-binary "@$tmp_body" \
    "$base_url/api/write" >/dev/null 2>/dev/null; then
    rm -f "$tmp_config" "$tmp_body" "$tmp_data"
    echo "Flag '$flag_name' set to variant '$variant' via $base_url."
    exit 0
  fi
fi

write_config_to_k8s "$tmp_data"

rm -f "$tmp_config" "$tmp_body" "$tmp_data"
echo "Flag '$flag_name' set to variant '$variant' via flagd-config ConfigMap in namespace '$NAMESPACE'."
