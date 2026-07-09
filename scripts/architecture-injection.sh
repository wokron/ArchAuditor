#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH_DIR="$ROOT_DIR/k8s-faults/patches"
NAMESPACE="${NAMESPACE:-default}"
NODE_NAME="${NODE_NAME:-}"
REPLICAS="${REPLICAS:-3}"

usage() {
  cat <<'EOF'
Usage:
  scripts/architecture-injection.sh <scenario>

Scenarios:
  k8s-missing-probes       Remove cart liveness/readiness probes.
  k8s-missing-requests     Remove frontend resource requests.
  k8s-missing-limits       Remove frontend resource limits.
  k8s-hostpath             Add hostPath mount to payment.
  config-drift             Add manual-change annotations to checkout.
  rollback-mark            Mark checkout as rollback-like change.
  isolation-same-node      Scale payment and pin replicas to one node.
  single-point-risk        Scale frontend to 1 replica and remove requests.
  reset-k8s-demo           Restore the known K8s demo faults used above.

Environment:
  NAMESPACE=default
  NODE_NAME=<k8s node name>   Required for isolation-same-node unless auto-detected.
  REPLICAS=3                  Used by isolation-same-node.
EOF
}

require_kubectl() {
  command -v kubectl >/dev/null 2>&1 || {
    echo "kubectl is required but was not found in PATH." >&2
    exit 1
  }
}

render_template() {
  local template="$1"
  local output="$2"
  local timestamp
  timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  sed \
    -e "s|\${NODE_NAME}|${NODE_NAME}|g" \
    -e "s|\${REPLICAS}|${REPLICAS}|g" \
    -e "s|\${DEMO_TIMESTAMP}|${timestamp}|g" \
    "$template" > "$output"
}

patch_deployment() {
  local deployment="$1"
  local patch_file="$2"
  kubectl patch deployment "$deployment" \
    -n "$NAMESPACE" \
    --type strategic \
    --patch-file "$patch_file"
}

patch_deployment_template() {
  local deployment="$1"
  local template_file="$2"
  local rendered
  rendered="$(mktemp)"
  render_template "$template_file" "$rendered"
  patch_deployment "$deployment" "$rendered"
  rm -f "$rendered"
}

auto_detect_node() {
  if [[ -n "$NODE_NAME" ]]; then
    return
  fi

  NODE_NAME="$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')"
  if [[ -z "$NODE_NAME" ]]; then
    echo "Cannot auto-detect a Kubernetes node. Set NODE_NAME explicitly." >&2
    exit 1
  fi
}

remove_demo_annotations() {
  local deployment="$1"
  kubectl annotate deployment "$deployment" -n "$NAMESPACE" \
    archauditor.io/manual-change- \
    kubernetes.io/change-cause- \
    arch-auditor/demo-co-deploy- \
    --overwrite >/dev/null 2>&1 || true
}

scenario="${1:-}"
if [[ -z "$scenario" || "$scenario" == "-h" || "$scenario" == "--help" ]]; then
  usage
  exit 0
fi

require_kubectl

case "$scenario" in
  k8s-missing-probes)
    patch_deployment cart "$PATCH_DIR/missing-probes-cart.yaml"
    ;;
  k8s-missing-requests)
    patch_deployment frontend "$PATCH_DIR/missing-requests-frontend.yaml"
    ;;
  k8s-missing-limits)
    patch_deployment frontend "$PATCH_DIR/missing-limits-frontend.yaml"
    ;;
  k8s-hostpath)
    patch_deployment payment "$PATCH_DIR/hostpath-payment.yaml"
    ;;
  config-drift)
    patch_deployment_template checkout "$PATCH_DIR/config-drift-checkout.yaml.tpl"
    ;;
  rollback-mark)
    patch_deployment_template checkout "$PATCH_DIR/rollback-mark-checkout.yaml.tpl"
    ;;
  isolation-same-node)
    auto_detect_node
    patch_deployment_template payment "$PATCH_DIR/isolation-payment-same-node.yaml.tpl"
    ;;
  single-point-risk)
    kubectl scale deployment/frontend -n "$NAMESPACE" --replicas=1
    patch_deployment frontend "$PATCH_DIR/missing-requests-frontend.yaml"
    ;;
  reset-k8s-demo)
    patch_deployment cart "$PATCH_DIR/baseline-cart-probes.yaml"
    patch_deployment frontend "$PATCH_DIR/baseline-frontend-resources.yaml"
    patch_deployment payment "$PATCH_DIR/baseline-payment.yaml"
    remove_demo_annotations checkout
    remove_demo_annotations frontend
    remove_demo_annotations cart
    remove_demo_annotations payment
    ;;
  *)
    echo "Unknown scenario: $scenario" >&2
    usage
    exit 1
    ;;
esac

echo "Scenario '$scenario' applied in namespace '$NAMESPACE'."
