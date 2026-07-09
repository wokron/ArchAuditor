#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_DIR="$ROOT_DIR/k8s-faults/runtime-flags"
FLAG_SCRIPT="$ROOT_DIR/scripts/flagd-feature.sh"

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
  BASE_URL=http://127.0.0.1:8080/feature
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

profile="${1:-}"
if [[ -z "$profile" || "$profile" == "-h" || "$profile" == "--help" ]]; then
  usage
  exit 0
fi

profile_file="$PROFILE_DIR/$profile.json"
if [[ ! -f "$profile_file" ]]; then
  echo "Unknown runtime profile: $profile" >&2
  usage
  exit 1
fi

"$(python_bin)" - "$profile_file" <<'PY' | while IFS=$'\t' read -r flag variant; do
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    profile = json.load(fh)

for flag, variant in profile.items():
    print(f"{flag}\t{variant}")
PY
  "$FLAG_SCRIPT" set "$flag" "$variant"
done

echo "Runtime fault profile '$profile' applied."
