# ElastiCache Redis for tradeflow
#
# Conventions for all infra/0N-*.sh:
#   Tag Project=tradeflow, Step=NN at create time
#   Idempotent: skip if resource exists by tag/name
#   Append one line to infra/.deployed-steps on success

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${SCRIPT_DIR}/.deployed-steps"
STEP="02"
PROJECT="tradeflow"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${REGION}"

REPLICATION_GROUP_ID="${PROJECT}-redis"
SUBNET_GROUP_NAME="${PROJECT}-redis-subnet-group"
SG_REDIS_NAME="${PROJECT}-redis-sg"
SG_ECS_NAME="${PROJECT}-ecs-tasks-sg"
SECRET_NAME="${PROJECT}/elasticache/credentials"
NODE_TYPE="${NODE_TYPE:-cache.t3.micro}"

TAG_SPEC_EC2="ResourceType=%s,Tags=[{Key=Project,Value=${PROJECT}},{Key=Step,Value=${STEP}},{Key=Name,Value=%s}]"

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

# Parse KEY=value
state_get() {
  local step="$1" key="$2"
  local line
  line="$(grep "^STEP=${step} " "${STATE_FILE}" 2>/dev/null || true)"
  [[ -n "${line}" ]] || return 1
  printf '%s\n' "${line}" | tr ' ' '\n' | grep "^${key}=" | head -1 | cut -d= -f2-
}

find_sg_by_name() {
  local name="$1" vpc_id="$2"
  aws_q ec2 describe-security-groups \
    --filters "Name=group-name,Values=${name}" "Name=vpc-id,Values=${vpc_id}" \
    --query 'SecurityGroups[0].GroupId'
}

secret_exists() {
  aws secretsmanager describe-secret --secret-id "${SECRET_NAME}" >/dev/null 2>&1
}

subnet_group_exists() {
  aws elasticache describe-cache-subnet-groups \
    --cache-subnet-group-name "${SUBNET_GROUP_NAME}" >/dev/null 2>&1
}

replication_group_exists() {
  local status
  status="$(aws_q elasticache describe-replication-groups \
    --replication-group-id "${REPLICATION_GROUP_ID}" \
    --query 'ReplicationGroups[0].Status')"
  [[ -n "${status}" && "${status}" != "None" && "${status}" != "deleted" && "${status}" != "deleting" ]]
}

# Ignore InvalidPermission.Duplicate so re-runs stay idempotent under set -e
authorize_ingress_tcp() {
  local group_id="$1" port="$2" source_group="$3"
  local err
  if err="$(aws ec2 authorize-security-group-ingress \
    --group-id "${group_id}" \
    --protocol tcp \
    --port "${port}" \
    --source-group "${source_group}" 2>&1)"; then
    return 0
  fi
  if [[ "${err}" == *InvalidPermission.Duplicate* ]]; then
    return 0
  fi
  printf '%s\n' "${err}" >&2
  return 1
}

# ElastiCache auth token: 16–128 printable ASCII, no / " @
generate_auth_token() {
  python3 -c '
import secrets, string
alphabet = string.ascii_letters + string.digits + "!&#$^<>-"
print("".join(secrets.choice(alphabet) for _ in range(64)))
'
}

require_cmd aws
require_cmd python3

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
is_none "${ACCOUNT_ID}" && die "Unable to resolve AWS account. Check credentials / AWS_PROFILE."

[[ -f "${STATE_FILE}" ]] || die "Missing ${STATE_FILE}. Run infra/01-timescaledb-ec2.sh first."

VPC_ID="$(state_get 01 VPC_ID || true)"
PRIVATE_SUBNET_ID="$(state_get 01 PRIVATE_SUBNET_ID || true)"
SG_ECS_ID="$(state_get 01 SG_ECS_ID || true)"
STEP01_REGION="$(state_get 01 REGION || true)"

is_none "${VPC_ID}" && die "STEP=01 missing VPC_ID in ${STATE_FILE}"
is_none "${PRIVATE_SUBNET_ID}" && die "STEP=01 missing PRIVATE_SUBNET_ID in ${STATE_FILE}"
is_none "${SG_ECS_ID}" && die "STEP=01 missing SG_ECS_ID in ${STATE_FILE}"

if ! is_none "${STEP01_REGION}"; then
  REGION="${STEP01_REGION}"
  export AWS_DEFAULT_REGION="${REGION}"
fi

# Confirm ECS SG still exists
LIVE_ECS_SG="$(find_sg_by_name "${SG_ECS_NAME}" "${VPC_ID}")"
if is_none "${LIVE_ECS_SG}"; then
  die "ECS-tasks SG ${SG_ECS_ID} not found in ${VPC_ID}. Re-run step 01."
fi
SG_ECS_ID="${LIVE_ECS_SG}"

log "tradeflow Step ${STEP}: ElastiCache Redis (single-node)"
info "Account: ${ACCOUNT_ID}"
info "Region:  ${REGION}"
info "Profile: ${AWS_PROFILE:-<default>}"
info "VPC:     ${VPC_ID}"
info "Subnet:  ${PRIVATE_SUBNET_ID} (private, from step 01)"
info "ECS SG:  ${SG_ECS_ID}"
echo

log "Security group"
SG_REDIS_ID="$(find_sg_by_name "${SG_REDIS_NAME}" "${VPC_ID}")"
if is_none "${SG_REDIS_ID}"; then
  SG_REDIS_ID="$(aws ec2 create-security-group \
    --group-name "${SG_REDIS_NAME}" \
    --description "ElastiCache Redis: inbound 6379 from ECS tasks SG only" \
    --vpc-id "${VPC_ID}" \
    --tag-specifications "$(printf "${TAG_SPEC_EC2}" security-group "${SG_REDIS_NAME}")" \
    --query 'GroupId' --output text)"
  info "Created Redis SG ${SG_REDIS_ID}"
else
  skip "Redis SG already exists: ${SG_REDIS_ID}"
fi

# Flatten UserIdGroupPairs[] before filtering, nested projection misses existing rules
HAS_6379="$(aws_q ec2 describe-security-groups \
  --group-ids "${SG_REDIS_ID}" \
  --query "SecurityGroups[0].IpPermissions[?FromPort==\`6379\` && ToPort==\`6379\`].UserIdGroupPairs[].GroupId | [?@=='${SG_ECS_ID}'] | [0]")"
if is_none "${HAS_6379}"; then
  authorize_ingress_tcp "${SG_REDIS_ID}" 6379 "${SG_ECS_ID}"
  info "Authorized tcp/6379 from ${SG_ECS_NAME} only"
else
  skip "Ingress tcp/6379 from ECS SG already present"
fi

log "Cache subnet group"
if subnet_group_exists; then
  skip "Subnet group already exists: ${SUBNET_GROUP_NAME}"
else
  aws elasticache create-cache-subnet-group \
    --cache-subnet-group-name "${SUBNET_GROUP_NAME}" \
    --cache-subnet-group-description "tradeflow Redis private subnets (Step ${STEP})" \
    --subnet-ids "${PRIVATE_SUBNET_ID}" \
    --tags "Key=Project,Value=${PROJECT}" "Key=Step,Value=${STEP}" "Key=Name,Value=${SUBNET_GROUP_NAME}" \
    >/dev/null
  info "Created subnet group ${SUBNET_GROUP_NAME} -> ${PRIVATE_SUBNET_ID}"
fi

# Auth token in Secrets Manager before cluster create, never printed
log "Secrets Manager (auth token)"
SECRET_ARN=""
AUTH_TOKEN=""
if secret_exists; then
  SECRET_ARN="$(aws secretsmanager describe-secret --secret-id "${SECRET_NAME}" --query ARN --output text)"
  AUTH_TOKEN="$(aws secretsmanager get-secret-value \
    --secret-id "${SECRET_NAME}" \
    --query SecretString --output text | python3 -c 'import json,sys; print(json.load(sys.stdin)["auth_token"])')"
  skip "Secret already exists: ${SECRET_ARN}"
else
  AUTH_TOKEN="$(generate_auth_token)"
  SECRET_STRING="$(AUTH_TOKEN="${AUTH_TOKEN}" python3 -c 'import json,os; print(json.dumps({
    "auth_token": os.environ["AUTH_TOKEN"],
    "port": 6379,
    "engine": "redis",
    "transit_encryption": True,
    "at_rest_encryption": True
  }))')"
  SECRET_ARN="$(aws secretsmanager create-secret \
    --name "${SECRET_NAME}" \
    --description "tradeflow ElastiCache Redis auth token (Step ${STEP})" \
    --secret-string "${SECRET_STRING}" \
    --tags "Key=Project,Value=${PROJECT}" "Key=Step,Value=${STEP}" "Key=Name,Value=${SECRET_NAME}" \
    --query ARN --output text)"
  unset SECRET_STRING
  info "Created secret ${SECRET_ARN}"
  info "Auth token stored in Secrets Manager only (not printed)."
fi

# Resolve a current Redis engine version (avoid pinning a stale ID)
ENGINE_VERSION="$(aws_q elasticache describe-cache-engine-versions \
  --engine redis \
  --query 'reverse(sort_by(CacheEngineVersions,&EngineVersion))[0].EngineVersion')"
is_none "${ENGINE_VERSION}" && die "Could not resolve a Redis engine version"
info "Redis engine version: ${ENGINE_VERSION}"

log "ElastiCache replication group (${NODE_TYPE}, single node, no cluster mode)"
if replication_group_exists; then
  skip "Replication group already exists: ${REPLICATION_GROUP_ID}"
else
  aws elasticache create-replication-group \
    --replication-group-id "${REPLICATION_GROUP_ID}" \
    --replication-group-description "tradeflow Redis (Step ${STEP})" \
    --engine redis \
    --engine-version "${ENGINE_VERSION}" \
    --cache-node-type "${NODE_TYPE}" \
    --num-cache-clusters 1 \
    --cache-subnet-group-name "${SUBNET_GROUP_NAME}" \
    --security-group-ids "${SG_REDIS_ID}" \
    --at-rest-encryption-enabled \
    --transit-encryption-enabled \
    --auth-token "${AUTH_TOKEN}" \
    --tags "Key=Project,Value=${PROJECT}" "Key=Step,Value=${STEP}" "Key=Name,Value=${REPLICATION_GROUP_ID}" \
    >/dev/null
  info "Creating replication group ${REPLICATION_GROUP_ID} (encryption at rest + in transit, AUTH required)..."
fi

# Drop token from shell as soon as create has used it
unset AUTH_TOKEN

info "Waiting for replication group to become available..."
aws elasticache wait replication-group-available --replication-group-id "${REPLICATION_GROUP_ID}"

REDIS_ENDPOINT="$(aws_q elasticache describe-replication-groups \
  --replication-group-id "${REPLICATION_GROUP_ID}" \
  --query 'ReplicationGroups[0].NodeGroups[0].PrimaryEndpoint.Address')"
REDIS_PORT="$(aws_q elasticache describe-replication-groups \
  --replication-group-id "${REPLICATION_GROUP_ID}" \
  --query 'ReplicationGroups[0].NodeGroups[0].PrimaryEndpoint.Port')"

is_none "${REDIS_ENDPOINT}" && die "Replication group available but primary endpoint missing"
is_none "${REDIS_PORT}" && REDIS_PORT="6379"

# Persist endpoint into the secret for later steps; never echo the token
CURRENT_SECRET="$(aws secretsmanager get-secret-value \
  --secret-id "${SECRET_NAME}" \
  --query SecretString --output text)"
UPDATED_SECRET="$(ENDPOINT="${REDIS_ENDPOINT}" PORT="${REDIS_PORT}" \
  python3 -c 'import json,os,sys; d=json.load(sys.stdin); d["endpoint"]=os.environ["ENDPOINT"]; d["port"]=int(os.environ["PORT"]); print(json.dumps(d))' \
  <<<"${CURRENT_SECRET}")"
unset CURRENT_SECRET
aws secretsmanager put-secret-value \
  --secret-id "${SECRET_NAME}" \
  --secret-string "${UPDATED_SECRET}" >/dev/null
unset UPDATED_SECRET
info "Updated secret with endpoint (auth token unchanged, not printed)"

log "Summary"
echo
info "Replication group: ${REPLICATION_GROUP_ID}"
info "Node type:         ${NODE_TYPE}"
info "Engine version:    ${ENGINE_VERSION}"
info "Endpoint:          ${REDIS_ENDPOINT}"
info "Port:              ${REDIS_PORT}"
info "TLS / AUTH:        required (token in Secrets Manager only)"
info "Secret ARN:        ${SECRET_ARN}"
info "Subnet group:      ${SUBNET_GROUP_NAME}"
info "Redis SG:          ${SG_REDIS_ID}"
info "ECS tasks SG:      ${SG_ECS_ID}"
echo
log "Manual test (from Timescale via SSM — token from Secrets Manager, not here):"
info "TOKEN=\$(aws secretsmanager get-secret-value --secret-id ${SECRET_NAME} --query SecretString --output text | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"auth_token\"])')"
info "redis-cli -h ${REDIS_ENDPOINT} -p ${REDIS_PORT} --tls -a \"\$TOKEN\" ping"
info "Unauthenticated / non-TLS attempts should be rejected."

TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
STATE_LINE="STEP=${STEP} TIMESTAMP=${TIMESTAMP} VPC_ID=${VPC_ID} PRIVATE_SUBNET_ID=${PRIVATE_SUBNET_ID} REPLICATION_GROUP_ID=${REPLICATION_GROUP_ID} REDIS_ENDPOINT=${REDIS_ENDPOINT} REDIS_PORT=${REDIS_PORT} SUBNET_GROUP=${SUBNET_GROUP_NAME} SG_REDIS_ID=${SG_REDIS_ID} SG_ECS_ID=${SG_ECS_ID} SECRET_ARN=${SECRET_ARN} SECRET_NAME=${SECRET_NAME} ENGINE_VERSION=${ENGINE_VERSION} NODE_TYPE=${NODE_TYPE} REGION=${REGION}"

if [[ -f "${STATE_FILE}" ]] && grep -q '^STEP=02 ' "${STATE_FILE}" 2>/dev/null; then
  TMP="$(mktemp)"
  grep -v '^STEP=02 ' "${STATE_FILE}" > "${TMP}" || true
  echo "${STATE_LINE}" >> "${TMP}"
  mv "${TMP}" "${STATE_FILE}"
  info "Updated Step ${STEP} entry in ${STATE_FILE}"
else
  echo "${STATE_LINE}" >> "${STATE_FILE}"
  info "Appended Step ${STEP} entry to ${STATE_FILE}"
fi

log "Done."
