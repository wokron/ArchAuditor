#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-default}"
FLAG_FILE="${FLAG_FILE:-$ROOT_DIR/opentelemetry-demo/src/flagd/demo.flagd.json}"

usage() {
  cat <<'EOF'
Usage:
  scripts/sync-flagd-config.sh

Updates the Kubernetes flagd-config ConfigMap from the local demo.flagd.json file
and restarts the flagd deployment so new runtime fault switches are available.

Environment:
  NAMESPACE=default
  FLAG_FILE=opentelemetry-demo/src/flagd/demo.flagd.json
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

command -v kubectl >/dev/null 2>&1 || {
  echo "kubectl is required but was not found in PATH." >&2
  exit 1
}

if [[ ! -f "$FLAG_FILE" ]]; then
  echo "Flag file not found: $FLAG_FILE" >&2
  exit 1
fi

kubectl create configmap flagd-config \
  -n "$NAMESPACE" \
  --from-file=demo.flagd.json="$FLAG_FILE" \
  --dry-run=client \
  -o yaml | kubectl apply -f -

kubectl rollout restart deployment/flagd -n "$NAMESPACE"
kubectl rollout status deployment/flagd -n "$NAMESPACE" --timeout=180s

echo "flagd-config synced from '$FLAG_FILE' in namespace '$NAMESPACE'."
