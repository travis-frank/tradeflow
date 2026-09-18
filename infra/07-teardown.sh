# Tear down backend compute. Keeps frontend, ECR, secrets, IAM/SGs.
#
# Default: STOP Timescale EC2 (preserves EBS). Set TERMINATE_EC2=1 to terminate instead.
# Confirm with y/n unless TEARDOWN_YES=1.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${SCRIPT_DIR}/.deployed-steps"
PROJECT="tradeflow"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${REGION}"

CLUSTER_NAME="${PROJECT}"
REPLICATION_GROUP_ID="${PROJECT}-redis"
ALB_NAME="${PROJECT}-alb"
TG_NAME="${PROJECT}-api-tg"
TERMINATE_EC2="${TERMINATE_EC2:-0}"
TEARDOWN_YES="${TEARDOWN_YES:-0}"

log()  { printf '==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
skip() { printf '    [skip] %s\n' "$*"; }
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

clear_step_lines() {
  local step="$1"
  [[ -f "${STATE_FILE}" ]] || return 0
  if grep -q "^STEP=${step} " "${STATE_FILE}" 2>/dev/null; then
    TMP="$(mktemp)"
    grep -v "^STEP=${step} " "${STATE_FILE}" > "${TMP}" || true
    mv "${TMP}" "${STATE_FILE}"
  fi
}

require_cmd aws

[[ -f "${STATE_FILE}" ]] || die "Missing ${STATE_FILE}"

REGION="$(state_get 01 REGION || echo "${REGION}")"
export AWS_DEFAULT_REGION="${REGION}"

INSTANCE_ID="$(state_get 01 INSTANCE_ID || true)"
NAT_ID="$(state_get 01 NAT_GATEWAY_ID || true)"
ALB_ARN="$(state_get 05 ALB_ARN || true)"
TG_ARN="$(state_get 05 TG_ARN || true)"
FRONTEND_BUCKET="$(state_get 06 FRONTEND_BUCKET || true)"
WEBSITE_URL="$(state_get 06 WEBSITE_URL || true)"
ECR_REPO="$(state_get 03 ECR_REPO || echo "${PROJECT}-api")"

# Resolve live ARNs if state is stale
if is_none "${ALB_ARN}"; then
  ALB_ARN="$(aws_q elbv2 describe-load-balancers --names "${ALB_NAME}" --query 'LoadBalancers[0].LoadBalancerArn')"
fi
if is_none "${TG_ARN}"; then
  TG_ARN="$(aws_q elbv2 describe-target-groups --names "${TG_NAME}" --query 'TargetGroups[0].TargetGroupArn')"
fi

EC2_ACTION="STOP (EBS preserved)"
if [[ "${TERMINATE_EC2}" == "1" ]]; then
  EC2_ACTION="TERMINATE (EBS deleted with instance if DeleteOnTermination=true)"
fi

log "tradeflow teardown — planned actions"
echo
info "WILL tear down / stop:"
info "  • ECS services tradeflow-api/worker/beat → scale 0, then delete"
info "  • ECS cluster ${CLUSTER_NAME}"
info "  • ALB ${ALB_NAME} (+ listeners)"
info "  • Target group ${TG_NAME}"
info "  • ElastiCache replication group ${REPLICATION_GROUP_ID}"
info "  • Timescale EC2 ${INSTANCE_ID:-<unknown>}: ${EC2_ACTION}"
echo
info "WILL keep (still present after teardown):"
info "  • ECR repo/images (${ECR_REPO})"
info "  • Secrets Manager secrets"
info "  • Frontend S3 website (${FRONTEND_BUCKET:-<none>} → ${WEBSITE_URL:-n/a})"
info "  • IAM roles, security groups, VPC/subnets"
info "  • NAT Gateway ${NAT_ID:-<unknown>}  << still ~\$0.045/hr while it exists"
info "  • ALB access-log S3 bucket (if created)"
echo
info "Set TERMINATE_EC2=1 to terminate the DB instance instead of stop."
echo

if [[ "${TEARDOWN_YES}" != "1" ]]; then
  printf 'Proceed with teardown? [y/N] '
  read -r ans || true
  case "${ans}" in
    y|Y|yes|YES) ;;
    *) log "Aborted."; exit 0 ;;
  esac
fi

# --- ECS services first (releases target group), then ALB/TG, then cluster ---
log "ECS services → scale 0, delete"
for svc in tradeflow-api tradeflow-worker tradeflow-beat; do
  STATUS="$(aws_q ecs describe-services --cluster "${CLUSTER_NAME}" --services "${svc}" --query 'services[0].status')"
  if [[ "${STATUS}" == "ACTIVE" ]]; then
    aws ecs update-service --cluster "${CLUSTER_NAME}" --service "${svc}" --desired-count 0 >/dev/null || true
    aws ecs delete-service --cluster "${CLUSTER_NAME}" --service "${svc}" --force >/dev/null
    info "Deleted service ${svc}"
  else
    skip "Service ${svc} not active (${STATUS:-missing})"
  fi
done

log "ALB + target group"
if ! is_none "${ALB_ARN}"; then
  aws elbv2 delete-load-balancer --load-balancer-arn "${ALB_ARN}" >/dev/null
  info "Deleted ALB ${ALB_ARN}"
  info "Waiting for ALB to finish deleting..."
  # wait until describe fails / not found
  for _ in $(seq 1 60); do
    ST="$(aws_q elbv2 describe-load-balancers --load-balancer-arns "${ALB_ARN}" --query 'LoadBalancers[0].State.Code')"
    [[ -z "${ST}" || "${ST}" == "None" ]] && break
    sleep 10
  done
else
  skip "No ALB ARN found"
fi

if ! is_none "${TG_ARN}"; then
  # Target group may take a moment to detach
  for _ in $(seq 1 30); do
    if aws elbv2 delete-target-group --target-group-arn "${TG_ARN}" >/dev/null 2>&1; then
      info "Deleted target group ${TG_ARN}"
      break
    fi
    sleep 5
  done
else
  skip "No target group ARN found"
fi

log "ECS cluster"
CL_STATUS="$(aws_q ecs describe-clusters --clusters "${CLUSTER_NAME}" --query 'clusters[0].status')"
if [[ "${CL_STATUS}" == "ACTIVE" ]]; then
  aws ecs delete-cluster --cluster "${CLUSTER_NAME}" >/dev/null
  info "Deleted cluster ${CLUSTER_NAME}"
else
  skip "Cluster not active (${CL_STATUS:-missing})"
fi

log "ElastiCache replication group"
RG_STATUS="$(aws_q elasticache describe-replication-groups --replication-group-id "${REPLICATION_GROUP_ID}" --query 'ReplicationGroups[0].Status')"
if ! is_none "${RG_STATUS}" && [[ "${RG_STATUS}" != "deleting" ]]; then
  aws elasticache delete-replication-group \
    --replication-group-id "${REPLICATION_GROUP_ID}" \
    --no-retain-primary-cluster >/dev/null
  info "Deleting ${REPLICATION_GROUP_ID} (wait until gone — several minutes)..."
  for _ in $(seq 1 60); do
    ST="$(aws_q elasticache describe-replication-groups --replication-group-id "${REPLICATION_GROUP_ID}" --query 'ReplicationGroups[0].Status')"
    is_none "${ST}" && break
    sleep 15
  done
  info "ElastiCache deleted (or delete in progress)"
else
  skip "Replication group not present (${RG_STATUS:-missing})"
fi

log "Timescale EC2"
if is_none "${INSTANCE_ID}"; then
  skip "No INSTANCE_ID in state"
else
  INST_STATE="$(aws_q ec2 describe-instances --instance-ids "${INSTANCE_ID}" --query 'Reservations[0].Instances[0].State.Name')"
  if [[ "${TERMINATE_EC2}" == "1" ]]; then
    if [[ "${INST_STATE}" != "terminated" && "${INST_STATE}" != "shutting-down" ]]; then
      aws ec2 terminate-instances --instance-ids "${INSTANCE_ID}" >/dev/null
      info "Terminated ${INSTANCE_ID}"
    else
      skip "Instance already ${INST_STATE}"
    fi
  else
    if [[ "${INST_STATE}" == "running" || "${INST_STATE}" == "pending" ]]; then
      aws ec2 stop-instances --instance-ids "${INSTANCE_ID}" >/dev/null
      info "Stopping ${INSTANCE_ID} (EBS kept)..."
      aws ec2 wait instance-stopped --instance-ids "${INSTANCE_ID}"
      info "Instance stopped"
    else
      skip "Instance already ${INST_STATE}"
    fi
  fi
fi

# Drop stale step lines that will be recreated on redeploy
clear_step_lines 02
clear_step_lines 04
clear_step_lines 05
# Keep 01 (EC2 IDs), 03 (ECR), 06 (frontend)

log "Teardown summary"
echo
info "Torn down / stopped:"
info "  ECS services + cluster, ALB + TG, ElastiCache, Timescale EC2 (${EC2_ACTION})"
echo
info "Still present (may still bill):"
info "  NAT Gateway ${NAT_ID:-?} — hourly until deleted manually"
info "  Stopped EC2 EBS volume (if stopped, not terminated)"
info "  Frontend S3: ${WEBSITE_URL:-n/a}"
info "  ECR ${ECR_REPO}, Secrets Manager, IAM roles, security groups, VPC"
echo
info "Redeploy when needed: AWS_PROFILE=${AWS_PROFILE:-tfrank-deploy} ./infra/07-redeploy.sh"
log "Done."
