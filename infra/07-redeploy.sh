# Redeploy backend after teardown. Recreates Redis/ECS/ALB; rebuilds frontend for new ALB DNS.
#
# Order: start EC2 → 02 ElastiCache → 04 ECS (refreshes connection-urls secret) →
#        05 ALB → 06 frontend (required every time — VITE_API_URL is baked into the SPA).


set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${SCRIPT_DIR}/.deployed-steps"
PROJECT="tradeflow"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${REGION}"

CLUSTER_NAME="${PROJECT}"
TG_NAME="${PROJECT}-api-tg"
HEALTH_PATH="/health"

log()  { printf '==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

aws_q() {
  aws "$@" --output text 2>/dev/null || true
}

is_none() {
  [[ -z "${1:-}" || "${1}" == "None" || "${1}" == "null" ]]
}

state_get() {
  local step="$1" key="$2"
  local line
  line="$(grep "^STEP=${step} " "${STATE_FILE}" 2>/dev/null || true)"
  [[ -n "${line}" ]] || return 1
  printf '%s\n' "${line}" | tr ' ' '\n' | grep "^${key}=" | head -1 | cut -d= -f2-
}

run_step() {
  local script="$1"
  [[ -x "${script}" ]] || die "Missing executable ${script}"
  log "Running $(basename "${script}")"
  # shellcheck disable=SC2093
  "${script}"
}

require_cmd aws
require_cmd curl

[[ -f "${STATE_FILE}" ]] || die "Missing ${STATE_FILE}"

REGION="$(state_get 01 REGION || echo "${REGION}")"
export AWS_DEFAULT_REGION="${REGION}"
INSTANCE_ID="$(state_get 01 INSTANCE_ID || true)"
is_none "${INSTANCE_ID}" && die "STEP=01 missing INSTANCE_ID — cannot start Timescale EC2"

log "tradeflow redeploy"
info "Region:  ${REGION}"
info "Profile: ${AWS_PROFILE:-<default>}"
info "EC2:     ${INSTANCE_ID}"
echo

# --- 1. Start Timescale EC2; wait for SSM ---
log "Start Timescale EC2"
INST_STATE="$(aws_q ec2 describe-instances --instance-ids "${INSTANCE_ID}" --query 'Reservations[0].Instances[0].State.Name')"
case "${INST_STATE}" in
  running)
    info "Already running"
    ;;
  stopped)
    aws ec2 start-instances --instance-ids "${INSTANCE_ID}" >/dev/null
    info "Starting ${INSTANCE_ID}..."
    aws ec2 wait instance-running --instance-ids "${INSTANCE_ID}"
    ;;
  stopping)
    info "Waiting for stop to finish, then start..."
    aws ec2 wait instance-stopped --instance-ids "${INSTANCE_ID}"
    aws ec2 start-instances --instance-ids "${INSTANCE_ID}" >/dev/null
    aws ec2 wait instance-running --instance-ids "${INSTANCE_ID}"
    ;;
  terminated|shutting-down)
    die "Instance ${INSTANCE_ID} is ${INST_STATE}. Re-run infra/01-timescaledb-ec2.sh to recreate."
    ;;
  *)
    die "Unexpected instance state: ${INST_STATE}"
    ;;
esac

# Refresh private IP in case it changed (usually stable across stop/start)
NEW_IP="$(aws_q ec2 describe-instances --instance-ids "${INSTANCE_ID}" --query 'Reservations[0].Instances[0].PrivateIpAddress')"
OLD_IP="$(state_get 01 PRIVATE_IP || true)"
if ! is_none "${NEW_IP}" && [[ "${NEW_IP}" != "${OLD_IP}" ]]; then
  info "Private IP changed ${OLD_IP} → ${NEW_IP}; updating STEP=01 state line"
  LINE="$(grep '^STEP=01 ' "${STATE_FILE}")"
  LINE="$(printf '%s\n' "${LINE}" | sed "s/PRIVATE_IP=[^ ]*/PRIVATE_IP=${NEW_IP}/")"
  TMP="$(mktemp)"
  grep -v '^STEP=01 ' "${STATE_FILE}" > "${TMP}" || true
  echo "${LINE}" >> "${TMP}"
  mv "${TMP}" "${STATE_FILE}"
fi

info "Waiting for SSM Managed Instance Online..."
for _ in $(seq 1 60); do
  PING="$(aws_q ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=${INSTANCE_ID}" \
    --query 'InstanceInformationList[0].PingStatus')"
  if [[ "${PING}" == "Online" ]]; then
    info "SSM Online"
    break
  fi
  sleep 10
done
PING="$(aws_q ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=${INSTANCE_ID}" \
  --query 'InstanceInformationList[0].PingStatus')"
[[ "${PING}" == "Online" ]] || die "SSM never came Online for ${INSTANCE_ID}"

# --- 2. ElastiCache (always recreated; new endpoint) ---
run_step "${SCRIPT_DIR}/02-elasticache.sh"

# --- 3. ECS (rebuilds connection-urls secret with new Redis endpoint + DB host) ---
run_step "${SCRIPT_DIR}/04-ecs.sh"

# --- 4. ALB (new DNS each recreate) ---
run_step "${SCRIPT_DIR}/05-alb.sh"

ALB_DNS="$(state_get 05 ALB_DNS || true)"
TG_ARN="$(state_get 05 TG_ARN || true)"
is_none "${ALB_DNS}" && die "STEP=05 missing ALB_DNS after 05-alb.sh"

# --- 5. Frontend rebuild+upload (required every redeploy — ALB DNS baked into JS) ---
log "Frontend must be rebuilt: static SPA embeds VITE_API_URL at build time"
run_step "${SCRIPT_DIR}/06-frontend.sh"

WEBSITE_URL="$(state_get 06 WEBSITE_URL || true)"
VITE_API_URL="$(state_get 06 VITE_API_URL || true)"

# --- Confirm ECS running + ALB health ---
log "Verify ECS services RUNNING"
for svc in tradeflow-api tradeflow-worker tradeflow-beat; do
  RUNNING="$(aws_q ecs describe-services --cluster "${CLUSTER_NAME}" --services "${svc}" --query 'services[0].runningCount')"
  DESIRED="$(aws_q ecs describe-services --cluster "${CLUSTER_NAME}" --services "${svc}" --query 'services[0].desiredCount')"
  info "${svc}: running=${RUNNING} desired=${DESIRED}"
  [[ "${RUNNING}" == "${DESIRED}" && "${RUNNING}" != "0" ]] || die "${svc} not fully running"
done

log "Verify ALB target health (${HEALTH_PATH})"
HTTP_CODE="000"
for _ in $(seq 1 36); do
  HTTP_CODE="$(curl -sS -o /tmp/tradeflow-health.json -w '%{http_code}' "http://${ALB_DNS}${HEALTH_PATH}" 2>/dev/null || echo '000')"
  STATES="$(aws elbv2 describe-target-health --target-group-arn "${TG_ARN}" \
    --query 'TargetHealthDescriptions[].TargetHealth.State' --output text 2>/dev/null || true)"
  info "HTTP ${HTTP_CODE}; TG states: ${STATES:-none}"
  if [[ "${HTTP_CODE}" == "200" ]] && printf '%s' "${STATES}" | grep -q 'healthy'; then
    break
  fi
  sleep 10
done
[[ "${HTTP_CODE}" == "200" ]] || die "ALB health check HTTP ${HTTP_CODE} (expected 200)"
info "ALB /health OK"

log "Redeploy complete — ready to demo"
echo
info "ALB DNS:       ${ALB_DNS}"
info "Health:        http://${ALB_DNS}${HEALTH_PATH}"
info "Sample API:    http://${ALB_DNS}/api/prices/current/AAPL"
info "Frontend URL:  ${WEBSITE_URL}"
info "Frontend API:  ${VITE_API_URL}  (rebuilt + re-synced to S3 this run)"
echo
info "Confirm frontend bake-in: VITE_API_URL should equal http://${ALB_DNS}"
if [[ "${VITE_API_URL}" == "http://${ALB_DNS}" ]]; then
  info "OK — frontend bundle points at this ALB DNS"
else
  die "Frontend VITE_API_URL (${VITE_API_URL}) does not match ALB DNS http://${ALB_DNS}"
fi
echo
log "Open ${WEBSITE_URL} in a browser for the end-to-end demo."
log "Done."
