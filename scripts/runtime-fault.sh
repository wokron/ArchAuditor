#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_DIR="$ROOT_DIR/k8s-faults/runtime-flags"
NAMESPACE="${NAMESPACE:-default}"
FLAGD_DEPLOYMENT="${FLAGD_DEPLOYMENT:-flagd}"
RESTART_WORKLOADS="${RESTART_WORKLOADS:-true}"
RESET_BEFORE_PROFILE="${RESET_BEFORE_PROFILE:-true}"

usage() {
  cat <<'EOF'
Usage:
  scripts/runtime-fault.sh <profile>

Profiles:
  circular-dependency   Turn on archCircular.
  dependency-latency    Turn on archLatency.
  dependency-failure    Turn on paymentUnreachable and archCrash.
  monolithic-service    Turn on frontend aggregation and CPU busy work.
  single-point-runtime  Break the critical checkout payment path.
  long-chain            Increase checkout chain length and async queue fan-out.
  resource-stress       Turn on adHighCpu, emailMemoryLeak, archSpike.
  over-decomposition    Turn on archNPlusOne and kafkaQueueProblems.
  reset-runtime         Turn off the known runtime architecture fault flags.

Environment:
  NAMESPACE=default
  FLAGD_DEPLOYMENT=flagd
  RESTART_WORKLOADS=true
  RESET_BEFORE_PROFILE=true     Reset known runtime fault flags before applying a profile.
EOF
}

python_bin() {
  if command -v python3 >/dev/null 2>&1 && python3 -c "import sys" >/dev/null 2>&1; then
    echo python3
  elif command -v python >/dev/null 2>&1 && python -c "import sys" >/dev/null 2>&1; then
    echo python
  else
    echo "python3 or python is required." >&2
    exit 1
  fi
}

profile="${1:-}"
if [[ -z "$profile" || "$profile" == "-h" || "$profile" == "--help" ]]; then
  usage
  exit 0
fi

profile_file="$PROFILE_DIR/$profile.json"
reset_file="$PROFILE_DIR/reset-runtime.json"
if [[ ! -f "$profile_file" ]]; then
  echo "Unknown runtime profile: $profile" >&2
  usage
  exit 1
fi

command -v kubectl >/dev/null 2>&1 || {
  echo "kubectl is required but was not found in PATH." >&2
  exit 1
}

tmp_config="$(mktemp)"
tmp_data="$(mktemp)"
trap 'rm -f "$tmp_config" "$tmp_data"' EXIT

kubectl get configmap flagd-config \
  -n "$NAMESPACE" \
  -o jsonpath='{.data.demo\.flagd\.json}' > "$tmp_config"

"$(python_bin)" - "$tmp_config" "$profile_file" "$tmp_data" "$reset_file" "$profile" "$RESET_BEFORE_PROFILE" <<'PY'
import json
import sys

config_path, profile_path, output_path, reset_path, profile_name, reset_before = sys.argv[1:7]

with open(config_path, "r", encoding="utf-8") as fh:
    config = json.load(fh)

with open(profile_path, "r", encoding="utf-8") as fh:
    profile = json.load(fh)

base_profile = {}
if reset_before.lower() == "true" and profile_name != "reset-runtime":
    with open(reset_path, "r", encoding="utf-8") as fh:
        base_profile = json.load(fh)

flags = config.get("flags", {})

for flag_name, variant in {**base_profile, **profile}.items():
    if flag_name not in flags:
        raise SystemExit(f"Flag '{flag_name}' does not exist.")

    variants = flags[flag_name].get("variants", {})
    if variant not in variants:
        raise SystemExit(
            f"Variant '{variant}' is invalid for '{flag_name}'. "
            f"Available variants: {', '.join(sorted(variants.keys()))}"
        )

    flags[flag_name]["defaultVariant"] = variant

with open(output_path, "w", encoding="utf-8") as fh:
    json.dump(config, fh, indent=2)
PY

kubectl create configmap flagd-config \
  -n "$NAMESPACE" \
  --from-file=demo.flagd.json="$tmp_data" \
  --dry-run=client \
  -o yaml | kubectl apply -f - >/dev/null

kubectl rollout restart "deployment/$FLAGD_DEPLOYMENT" -n "$NAMESPACE" >/dev/null
kubectl rollout status "deployment/$FLAGD_DEPLOYMENT" -n "$NAMESPACE" --timeout=180s >/dev/null

workloads_for_profile() {
  case "$profile" in
    dependency-latency)
      echo "product-catalog"
      ;;
    dependency-failure)
      echo "checkout recommendation"
      ;;
    circular-dependency)
      echo "recommendation"
      ;;
    monolithic-service)
      echo "frontend"
      ;;
    single-point-runtime)
      echo "checkout"
      ;;
    long-chain)
      echo "checkout load-generator"
      ;;
    over-decomposition)
      echo "frontend checkout fraud-detection"
      ;;
    resource-stress)
      echo "frontend ad email load-generator"
      ;;
    reset-runtime)
      echo "product-catalog checkout recommendation frontend ad email fraud-detection load-generator"
      ;;
    *)
      echo ""
      ;;
  esac
}

restart_workloads() {
  [[ "$RESTART_WORKLOADS" == "true" ]] || return 0

  local workloads
  workloads="$(workloads_for_profile)"
  [[ -n "$workloads" ]] || return 0

  for workload in $workloads; do
    if ! kubectl get "deployment/$workload" -n "$NAMESPACE" >/dev/null 2>&1; then
      continue
    fi
    kubectl rollout restart "deployment/$workload" -n "$NAMESPACE" >/dev/null
  done

  for workload in $workloads; do
    if ! kubectl get "deployment/$workload" -n "$NAMESPACE" >/dev/null 2>&1; then
      continue
    fi
    kubectl rollout status "deployment/$workload" -n "$NAMESPACE" --timeout=180s >/dev/null
  done
}

restart_workloads

echo "Runtime fault profile '$profile' applied via flagd-config ConfigMap in namespace '$NAMESPACE'."
