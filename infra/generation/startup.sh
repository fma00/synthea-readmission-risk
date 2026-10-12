#!/bin/bash
# Synthea generation VM startup script (v2 slice 5, PRD FR13). Passed per instance by scripts/launch_generation.py (NOT stored in
# the instance template). Reads every run parameter from the instance metadata, verifies every artifact's sha256, generates with
# the pinned jar/JDK and the pre-registered seeds, uploads the 10 tables + generation_summary.json + run_provenance.json to the raw
# prefix and writes _DONE LAST; on any failure writes _FAILED (the log tail). It never shuts the VM down: a guest shutdown would
# STOP (and keep billing the disk) rather than delete; the launcher deletes the VM, or max_run_duration does.
# Design: notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md (Interfaces section 11, "VM startup").
mkdir -p /work
# Both to a file (the _FAILED object is its tail) and to the guest agent, which forwards the startup script's output to Cloud
# Logging (hence the logging.logWriter grant), so a run's full log outlives the VM.
exec > >(tee -a /work/startup.log) 2>&1
set -Eeuo pipefail

FAILED_SENT=""
MD=http://metadata.google.internal/computeMetadata/v1

# On failure it names the key on stderr: inside $(...) only stdout is captured, so the message still reaches the log.
meta() { curl -sf -H "Metadata-Flavor: Google" "$MD/instance/attributes/$1" || { echo "metadata key $1 unreadable" >&2; return 1; }; }

# Messages go to stderr: with set -E the trap also fires inside $(...), whose stdout would swallow them.
on_error() {
  # From the ERR trap: no arguments, so BASH_COMMAND/BASH_LINENO name the failure. From fail: the caller's reason and line.
  local failed_command="${1:-$BASH_COMMAND}" failed_line="${2:-${BASH_LINENO[0]}}"
  trap - ERR
  if [ -n "$FAILED_SENT" ]; then sleep infinity; fi
  FAILED_SENT=1
  echo "startup.sh FAILED at $(date -u +%FT%TZ): line $failed_line: $failed_command" >&2
  local prefix
  prefix=$(meta raw_prefix || true)
  if [ -z "$prefix" ]; then
    echo "raw_prefix unreadable: nothing uploaded; the VM idles until max_run_duration deletes it (the launcher reports it vanished)" >&2
    sleep infinity
  fi
  sleep 2  # let tee flush the last lines into the file
  tail -n 200 /work/startup.log >/work/_FAILED || true
  if command -v gcloud >/dev/null 2>&1; then
    gcloud storage cp /work/_FAILED "$prefix/_FAILED" --if-generation-match=0 || true
  else
    # no gcloud on the image: the JSON API with the VM's own token
    local token bucket object
    token=$(curl -sf -H "Metadata-Flavor: Google" "$MD/instance/service-accounts/default/token" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])' || true)
    bucket=${prefix#gs://}; bucket=${bucket%%/*}
    object=${prefix#gs://"$bucket"/}/_FAILED
    curl -sf -X POST -H "Authorization: Bearer $token" -H "Content-Type: text/plain" --data-binary @/work/_FAILED \
      "https://storage.googleapis.com/upload/storage/v1/b/$bucket/o?uploadType=media&ifGenerationMatch=0&name=$object" || true
  fi
  sleep infinity
}
trap on_error ERR

fail() { echo "FAIL: $*" >&2; on_error "fail: $*" "${BASH_LINENO[0]}"; }

for tool in gcloud curl sha256sum tar python3; do
  command -v "$tool" >/dev/null 2>&1 || fail "missing tool $tool on the image"
done
export CLOUDSDK_STORAGE_PARALLEL_COMPOSITE_UPLOAD_ENABLED=False  # create-only service account: no component deletes

VM_STARTED=$(date -u +%FT%TZ)
RUN=$(meta run)
RAW_PREFIX=$(meta raw_prefix)
cd /work

fetch() {  # fetch <metadata key prefix> <local file>
  gcloud storage cp "$(meta "$1_uri")" "$2"
  echo "$(meta "$1_sha256")  $2" | sha256sum -c -
}
fetch jar synthea.jar
fetch jdk jdk.tar.gz
fetch zip job.zip
fetch gen_script generate_synthea_data.py

mkdir -p /opt/jdk
tar -xzf jdk.tar.gz -C /opt/jdk --strip-components=1
JDK_VERSION=$(/opt/jdk/bin/java -version 2>&1 | head -n 1)
case "$JDK_VERSION" in
  *'"21.0.12.1"'*) ;;
  *) fail "unexpected JDK: $JDK_VERSION" ;;
esac

GEN_STARTED=$(date -u +%FT%TZ)
START_S=$(date +%s)
env PATH="/opt/jdk/bin:$PATH" PYTHONPATH=/work/job.zip python3 generate_synthea_data.py \
  --population "$(meta population)" --seed "$(meta seed)" --clinician-seed "$(meta clinician_seed)" \
  --reference-date "$(meta reference_date)" --end-date "$(meta end_date)" --jvm-timezone "$(meta jvm_timezone)" \
  --require-alive-population --output /work/out --jar-path /work/synthea.jar --state "$(meta state)" \
  --years-of-history "$(meta years_of_history)" --included-files "$(meta included_files | tr ';' ',')" \
  --java-max-heap "$(meta java_max_heap)" \
  --provenance "run=$RUN" --provenance "prereg_commit=$(meta prereg_commit)" \
  --provenance "prereg_sha256=$(meta prereg_sha256)" --provenance "head_commit=$(meta head_commit)" \
  --provenance "zip_sha256=$(meta zip_sha256)" --provenance "synthea_jar_sha256=$(meta jar_sha256)"
GEN_SECONDS=$(( $(date +%s) - START_S ))
GEN_FINISHED=$(date -u +%FT%TZ)
RAW_BYTES=$(du -sb /work/out/csv | cut -f1)

IFS=';' read -r -a TABLES <<<"$(meta included_files)"
for table in "${TABLES[@]}"; do
  gcloud storage cp "/work/out/csv/$table" "$RAW_PREFIX/csv/$table" --if-generation-match=0
done
UPLOAD_FINISHED=$(date -u +%FT%TZ)

KEYS=$(curl -sf -H "Metadata-Flavor: Google" "$MD/instance/attributes/")
python3 - "$RUN" "$VM_STARTED" "$GEN_STARTED" "$GEN_FINISHED" "$GEN_SECONDS" "$UPLOAD_FINISHED" "$RAW_BYTES" "$KEYS" <<'PY' >/work/run_provenance.json
import json, subprocess, sys
run, vm_started, gen_started, gen_finished, gen_seconds, upload_finished, raw_bytes, keys = sys.argv[1:9]
md = "http://metadata.google.internal/computeMetadata/v1/instance/"
def get(path):
    out = subprocess.run(["curl", "-sf", "-H", "Metadata-Flavor: Google", md + path], capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else None
skip = {"startup-script", "enable-oslogin", "block-project-ssh-keys"}
metadata = {k: get("attributes/" + k) for k in keys.split() if k not in skip}
print(json.dumps({
    "run": run,
    "metadata": metadata,
    "machine_type": (get("machine-type") or "").rsplit("/", 1)[-1] or None,
    "zone": (get("zone") or "").rsplit("/", 1)[-1] or None,
    "image": get("image"),
    "vm_started_utc": vm_started,
    "generation_started_utc": gen_started,
    "generation_finished_utc": gen_finished,
    "generation_wall_seconds": int(gen_seconds),
    "upload_finished_utc": upload_finished,
    "raw_bytes": int(raw_bytes),
}, indent=2, sort_keys=True))
PY
gcloud storage cp /work/out/generation_summary.json "$RAW_PREFIX/generation_summary.json" --if-generation-match=0
gcloud storage cp /work/run_provenance.json "$RAW_PREFIX/run_provenance.json" --if-generation-match=0
: >/work/_DONE
gcloud storage cp /work/_DONE "$RAW_PREFIX/_DONE" --if-generation-match=0
echo "startup.sh done at $(date -u +%FT%TZ)"
sleep infinity
