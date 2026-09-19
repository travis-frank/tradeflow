# ECS Fargate: api, worker, beat 
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${SCRIPT_DIR}/.deployed-steps"
STEP="04"
PROJECT="tradeflow"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${REGION}"

CLUSTER_NAME="${PROJECT}"
EXEC_ROLE_NAME="${PROJECT}-ecs-execution-role"
TASK_ROLE_NAME="${PROJECT}-ecs-task-role"
CONN_SECRET_NAME="${PROJECT}/app/connection-urls"
SECRET_KEY_NAME="${PROJECT}/app/secret-key"
SG_ECS_NAME="${PROJECT}-ecs-tasks-sg"

CPU="${CPU:-256}"
MEMORY="${MEMORY:-512}"
DESIRED_COUNT="${DESIRED_COUNT:-1}"

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

secret_arn() {
  aws secretsmanager describe-secret --secret-id "$1" --query ARN --output text 2>/dev/null || true
}

role_exists() {
  aws iam get-role --role-name "$1" >/dev/null 2>&1
}

log_group_exists() {
  aws logs describe-log-groups --log-group-name-prefix "$1" \
    --query "logGroups[?logGroupName=='$1'].logGroupName | [0]" \
    --output text 2>/dev/null | grep -qx "$1"
}

cluster_active() {
  local status
  status="$(aws_q ecs describe-clusters --clusters "${CLUSTER_NAME}" --query 'clusters[0].status')"
  [[ "${status}" == "ACTIVE" ]]
}

service_exists() {
  local name="$1"
  local status
  status="$(aws_q ecs describe-services --cluster "${CLUSTER_NAME}" --services "${name}" \
    --query 'services[0].status')"
  [[ "${status}" == "ACTIVE" ]]
}

require_cmd aws
require_cmd python3
require_cmd openssl

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
is_none "${ACCOUNT_ID}" && die "Unable to resolve AWS account. Check credentials / AWS_PROFILE."

[[ -f "${STATE_FILE}" ]] || die "Missing ${STATE_FILE}. Run steps 01–03 first."

VPC_ID="$(state_get 01 VPC_ID || true)"
PRIVATE_SUBNET_ID="$(state_get 01 PRIVATE_SUBNET_ID || true)"
SG_ECS_ID="$(state_get 01 SG_ECS_ID || true)"
DB_HOST="$(state_get 01 PRIVATE_IP || true)"
DB_SECRET_NAME="$(state_get 01 SECRET_NAME || true)"
REDIS_ENDPOINT="$(state_get 02 REDIS_ENDPOINT || true)"
REDIS_PORT="$(state_get 02 REDIS_PORT || true)"
REDIS_SECRET_NAME="$(state_get 02 SECRET_NAME || true)"
IMAGE_URI="$(state_get 03 IMAGE_URI || true)"
STEP01_REGION="$(state_get 01 REGION || true)"

is_none "${VPC_ID}" && die "STEP=01 missing VPC_ID"
is_none "${PRIVATE_SUBNET_ID}" && die "STEP=01 missing PRIVATE_SUBNET_ID"
is_none "${SG_ECS_ID}" && die "STEP=01 missing SG_ECS_ID"
is_none "${DB_HOST}" && die "STEP=01 missing PRIVATE_IP"
is_none "${DB_SECRET_NAME}" && die "STEP=01 missing SECRET_NAME"
is_none "${REDIS_ENDPOINT}" && die "STEP=02 missing REDIS_ENDPOINT"
is_none "${REDIS_SECRET_NAME}" && die "STEP=02 missing SECRET_NAME"
is_none "${IMAGE_URI}" && die "STEP=03 missing IMAGE_URI"
is_none "${REDIS_PORT}" && REDIS_PORT="6379"

if ! is_none "${STEP01_REGION}"; then
  REGION="${STEP01_REGION}"
  export AWS_DEFAULT_REGION="${REGION}"
fi

LIVE_ECS_SG="$(aws_q ec2 describe-security-groups \
  --filters "Name=group-name,Values=${SG_ECS_NAME}" "Name=vpc-id,Values=${VPC_ID}" \
  --query 'SecurityGroups[0].GroupId')"
is_none "${LIVE_ECS_SG}" && die "ECS-tasks SG not found in ${VPC_ID}. Re-run step 01."
SG_ECS_ID="${LIVE_ECS_SG}"

log "tradeflow Step ${STEP}: ECS Fargate (api, worker, beat)"
info "Account: ${ACCOUNT_ID}"
info "Region:  ${REGION}"
info "Profile: ${AWS_PROFILE:-<default>}"
info "Image:   ${IMAGE_URI}"
info "Subnet:  ${PRIVATE_SUBNET_ID} (private)"
info "ECS SG:  ${SG_ECS_ID}"
echo

# Manual JWT secret must exist before we wire the task definition
log "Manual secrets check"
SECRET_KEY_ARN="$(secret_arn "${SECRET_KEY_NAME}")"
if is_none "${SECRET_KEY_ARN}"; then
  die "Missing secret ${SECRET_KEY_NAME}. Create it first (see script header)."
fi
info "SECRET_KEY: ${SECRET_KEY_ARN}"

# Build connection URLs from steps 01–02; store in SM (never in task def plaintext)
log "Connection URL secrets"
DB_JSON="$(aws secretsmanager get-secret-value --secret-id "${DB_SECRET_NAME}" --query SecretString --output text)"
REDIS_JSON="$(aws secretsmanager get-secret-value --secret-id "${REDIS_SECRET_NAME}" --query SecretString --output text)"

CONN_JSON="$(
  DB_JSON="${DB_JSON}" REDIS_JSON="${REDIS_JSON}" \
  DB_HOST="${DB_HOST}" REDIS_ENDPOINT="${REDIS_ENDPOINT}" REDIS_PORT="${REDIS_PORT}" \
  python3 -c '
import json, os
from urllib.parse import quote

db = json.loads(os.environ["DB_JSON"])
rd = json.loads(os.environ["REDIS_JSON"])
user = quote(db["username"], safe="")
password = quote(db["password"], safe="")
dbname = db["dbname"]
db_port = db.get("port", 5432)
host = os.environ["DB_HOST"]
token = quote(rd["auth_token"], safe="")
rhost = os.environ["REDIS_ENDPOINT"]
rport = os.environ["REDIS_PORT"]
# rediss:// = TLS; redis-py URL flag is "required"/"none"/"optional" (not CERT_REQUIRED)
ssl_q = "ssl_cert_reqs=required"
urls = {
    "DATABASE_URL": f"postgresql+asyncpg://{user}:{password}@{host}:{db_port}/{dbname}",
    "REDIS_URL": f"rediss://:{token}@{rhost}:{rport}/0?{ssl_q}",
    "CELERY_BROKER_URL": f"rediss://:{token}@{rhost}:{rport}/1?{ssl_q}",
    "CELERY_RESULT_BACKEND": f"rediss://:{token}@{rhost}:{rport}/2?{ssl_q}",
}
print(json.dumps(urls))
'
)"
unset DB_JSON REDIS_JSON

CONN_SECRET_ARN="$(secret_arn "${CONN_SECRET_NAME}")"
if is_none "${CONN_SECRET_ARN}"; then
  CONN_SECRET_ARN="$(aws secretsmanager create-secret \
    --name "${CONN_SECRET_NAME}" \
    --description "tradeflow ECS connection URLs (Step ${STEP}) — values only, not in task defs" \
    --secret-string "${CONN_JSON}" \
    --tags "Key=Project,Value=${PROJECT}" "Key=Step,Value=${STEP}" "Key=Name,Value=${CONN_SECRET_NAME}" \
    --query ARN --output text)"
  info "Created ${CONN_SECRET_ARN}"
else
  aws secretsmanager put-secret-value \
    --secret-id "${CONN_SECRET_NAME}" \
    --secret-string "${CONN_JSON}" >/dev/null
  skip "Updated existing connection-urls secret"
fi
unset CONN_JSON
# Re-read ARN in case create returned incomplete
CONN_SECRET_ARN="$(secret_arn "${CONN_SECRET_NAME}")"

log "CloudWatch log groups"
for svc in api worker beat; do
  LG="/ecs/${PROJECT}-${svc}"
  if log_group_exists "${LG}"; then
    skip "Log group exists: ${LG}"
  else
    aws logs create-log-group --log-group-name "${LG}" \
      --tags "Project=${PROJECT},Step=${STEP},Name=${LG}" >/dev/null
    aws logs put-retention-policy --log-group-name "${LG}" --retention-in-days 14 >/dev/null
    info "Created ${LG}"
  fi
done

log "IAM roles"
TRUST='{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}'

if role_exists "${EXEC_ROLE_NAME}"; then
  skip "Execution role exists: ${EXEC_ROLE_NAME}"
else
  aws iam create-role \
    --role-name "${EXEC_ROLE_NAME}" \
    --assume-role-policy-document "${TRUST}" \
    --tags "Key=Project,Value=${PROJECT}" "Key=Step,Value=${STEP}" "Key=Name,Value=${EXEC_ROLE_NAME}" \
    >/dev/null
  info "Created ${EXEC_ROLE_NAME}"
fi

if role_exists "${TASK_ROLE_NAME}"; then
  skip "Task role exists: ${TASK_ROLE_NAME}"
else
  aws iam create-role \
    --role-name "${TASK_ROLE_NAME}" \
    --assume-role-policy-document "${TRUST}" \
    --tags "Key=Project,Value=${PROJECT}" "Key=Step,Value=${STEP}" "Key=Name,Value=${TASK_ROLE_NAME}" \
    >/dev/null
  info "Created ${TASK_ROLE_NAME} (no extra runtime permissions)"
fi

EXEC_POLICY="$(python3 -c "
import json
account='${ACCOUNT_ID}'
region='${REGION}'
project='${PROJECT}'
secrets = [
  '${CONN_SECRET_ARN}',
  '${SECRET_KEY_ARN}',
]
log_groups = [
  f'arn:aws:logs:{region}:{account}:log-group:/ecs/{project}-api',
  f'arn:aws:logs:{region}:{account}:log-group:/ecs/{project}-api:log-stream:*',
  f'arn:aws:logs:{region}:{account}:log-group:/ecs/{project}-worker',
  f'arn:aws:logs:{region}:{account}:log-group:/ecs/{project}-worker:log-stream:*',
  f'arn:aws:logs:{region}:{account}:log-group:/ecs/{project}-beat',
  f'arn:aws:logs:{region}:{account}:log-group:/ecs/{project}-beat:log-stream:*',
]
doc = {
  'Version': '2012-10-17',
  'Statement': [
    {
      'Sid': 'ECRAuth',
      'Effect': 'Allow',
      'Action': ['ecr:GetAuthorizationToken'],
      'Resource': '*',
    },
    {
      'Sid': 'ECRPullTradeflowApiOnly',
      'Effect': 'Allow',
      'Action': [
        'ecr:BatchCheckLayerAvailability',
        'ecr:GetDownloadUrlForLayer',
        'ecr:BatchGetImage',
      ],
      'Resource': f'arn:aws:ecr:{region}:{account}:repository/{project}-api',
    },
    {
      'Sid': 'ReadExactSecrets',
      'Effect': 'Allow',
      'Action': ['secretsmanager:GetSecretValue'],
      'Resource': secrets,
    },
    {
      'Sid': 'CloudWatchLogsExactGroups',
      'Effect': 'Allow',
      'Action': ['logs:CreateLogStream', 'logs:PutLogEvents'],
      'Resource': log_groups,
    },
  ],
}
print(json.dumps(doc))
")"

aws iam put-role-policy \
  --role-name "${EXEC_ROLE_NAME}" \
  --policy-name "${PROJECT}-ecs-execution" \
  --policy-document "${EXEC_POLICY}" >/dev/null
info "Execution role policy: ECR ${PROJECT}-api + 2 secrets + 3 log groups"

EXEC_ROLE_ARN="$(aws iam get-role --role-name "${EXEC_ROLE_NAME}" --query 'Role.Arn' --output text)"
TASK_ROLE_ARN="$(aws iam get-role --role-name "${TASK_ROLE_NAME}" --query 'Role.Arn' --output text)"

# IAM eventual consistency before register-task-definition / run-task
info "Waiting briefly for IAM role propagation..."
sleep 10

log "ECS cluster (Container Insights)"
if cluster_active; then
  skip "Cluster exists: ${CLUSTER_NAME}"
  aws ecs update-cluster-settings \
    --cluster "${CLUSTER_NAME}" \
    --settings name=containerInsights,value=enabled >/dev/null || true
else
  aws ecs create-cluster \
    --cluster-name "${CLUSTER_NAME}" \
    --settings name=containerInsights,value=enabled \
    --tags "key=Project,value=${PROJECT}" "key=Step,value=${STEP}" "key=Name,value=${CLUSTER_NAME}" \
    >/dev/null
  info "Created cluster ${CLUSTER_NAME} with Container Insights"
fi

log "Task definitions"
register_task() {
  local family="$1" container_name="$2" command_json="$3" log_group="$4" port_mappings_json="$5"

  local td
  td="$(
    python3 -c "
import json
family = '''${family}'''
container = '''${container_name}'''
image = '''${IMAGE_URI}'''
exec_role = '''${EXEC_ROLE_ARN}'''
task_role = '''${TASK_ROLE_ARN}'''
conn_arn = '''${CONN_SECRET_ARN}'''
secret_key_arn = '''${SECRET_KEY_ARN}'''
log_group = '''${log_group}'''
region = '''${REGION}'''
cpu = '''${CPU}'''
memory = '''${MEMORY}'''
command = json.loads('''${command_json}''')
port_mappings = json.loads('''${port_mappings_json}''')

# Secrets only — no plaintext credentials in the task definition
secrets = [
  {'name': 'DATABASE_URL', 'valueFrom': f'{conn_arn}:DATABASE_URL::'},
  {'name': 'REDIS_URL', 'valueFrom': f'{conn_arn}:REDIS_URL::'},
  {'name': 'CELERY_BROKER_URL', 'valueFrom': f'{conn_arn}:CELERY_BROKER_URL::'},
  {'name': 'CELERY_RESULT_BACKEND', 'valueFrom': f'{conn_arn}:CELERY_RESULT_BACKEND::'},
  {'name': 'SECRET_KEY', 'valueFrom': secret_key_arn},
]

container_def = {
  'name': container,
  'image': image,
  'essential': True,
  'command': command,
  'environment': [
    {'name': 'APP_ENV', 'value': 'production'},
  ],
  'secrets': secrets,
  'logConfiguration': {
    'logDriver': 'awslogs',
    'options': {
      'awslogs-group': log_group,
      'awslogs-region': region,
      'awslogs-stream-prefix': 'ecs',
    },
  },
}
if port_mappings:
  container_def['portMappings'] = port_mappings

td = {
  'family': family,
  'networkMode': 'awsvpc',
  'requiresCompatibilities': ['FARGATE'],
  'cpu': cpu,
  'memory': memory,
  'executionRoleArn': exec_role,
  'taskRoleArn': task_role,
  'containerDefinitions': [container_def],
}
print(json.dumps(td))
"
  )"

  local td_file
  td_file="$(mktemp)"
  printf '%s' "${td}" > "${td_file}"
  aws ecs register-task-definition --cli-input-json "file://${td_file}" \
    --query 'taskDefinition.taskDefinitionArn' --output text
  rm -f "${td_file}"
}

API_TD_ARN="$(register_task \
  "${PROJECT}-api" \
  "api" \
  '["uvicorn","api.main:app","--host","0.0.0.0","--port","8000"]' \
  "/ecs/${PROJECT}-api" \
  '[{"containerPort":8000,"protocol":"tcp"}]')"
info "Registered api: ${API_TD_ARN}"

WORKER_TD_ARN="$(register_task \
  "${PROJECT}-worker" \
  "worker" \
  '["celery","-A","workers.celery_app","worker","--loglevel=info"]' \
  "/ecs/${PROJECT}-worker" \
  '[]')"
info "Registered worker: ${WORKER_TD_ARN}"

BEAT_TD_ARN="$(register_task \
  "${PROJECT}-beat" \
  "beat" \
  '["celery","-A","workers.celery_app","beat","--loglevel=info"]' \
  "/ecs/${PROJECT}-beat" \
  '[]')"
info "Registered beat: ${BEAT_TD_ARN}"

NETWORK_CONFIG="awsvpcConfiguration={subnets=[${PRIVATE_SUBNET_ID}],securityGroups=[${SG_ECS_ID}],assignPublicIp=DISABLED}"

# One-off migration via same api task def + command override (credentials stay in SM)
log "Alembic migration (one-off ECS task)"
MIGRATE_TASK_ARN="$(aws ecs run-task \
  --cluster "${CLUSTER_NAME}" \
  --task-definition "${API_TD_ARN}" \
  --launch-type FARGATE \
  --network-configuration "${NETWORK_CONFIG}" \
  --overrides '{"containerOverrides":[{"name":"api","command":["alembic","upgrade","head"]}]}' \
  --query 'tasks[0].taskArn' --output text)"

is_none "${MIGRATE_TASK_ARN}" && die "Failed to start migration task"
info "Migration task: ${MIGRATE_TASK_ARN}"
info "Waiting for migration task to stop..."
aws ecs wait tasks-stopped --cluster "${CLUSTER_NAME}" --tasks "${MIGRATE_TASK_ARN}"

MIGRATE_EXIT="$(aws_q ecs describe-tasks \
  --cluster "${CLUSTER_NAME}" \
  --tasks "${MIGRATE_TASK_ARN}" \
  --query 'tasks[0].containers[0].exitCode')"
MIGRATE_REASON="$(aws_q ecs describe-tasks \
  --cluster "${CLUSTER_NAME}" \
  --tasks "${MIGRATE_TASK_ARN}" \
  --query 'tasks[0].stoppedReason')"

if [[ "${MIGRATE_EXIT}" != "0" ]]; then
  die "Migration failed (exit=${MIGRATE_EXIT}). Reason: ${MIGRATE_REASON}. Check /ecs/${PROJECT}-api logs."
fi
info "Migration succeeded (exit 0)"

ensure_service() {
  local name="$1" td_arn="$2"
  if service_exists "${name}"; then
    aws ecs update-service \
      --cluster "${CLUSTER_NAME}" \
      --service "${name}" \
      --task-definition "${td_arn}" \
      --desired-count "${DESIRED_COUNT}" \
      --force-new-deployment \
      >/dev/null
    info "Updated service ${name}"
  else
    aws ecs create-service \
      --cluster "${CLUSTER_NAME}" \
      --service-name "${name}" \
      --task-definition "${td_arn}" \
      --desired-count "${DESIRED_COUNT}" \
      --launch-type FARGATE \
      --network-configuration "${NETWORK_CONFIG}" \
      --scheduling-strategy REPLICA \
      --deployment-configuration "maximumPercent=200,minimumHealthyPercent=100" \
      --tags "key=Project,value=${PROJECT}" "key=Step,value=${STEP}" "key=Name,value=${name}" \
      >/dev/null
    info "Created service ${name}"
  fi
}

log "ECS services"
ensure_service "${PROJECT}-api" "${API_TD_ARN}"
ensure_service "${PROJECT}-worker" "${WORKER_TD_ARN}"
ensure_service "${PROJECT}-beat" "${BEAT_TD_ARN}"

info "Waiting for services to stabilize (may take several minutes)..."
aws ecs wait services-stable \
  --cluster "${CLUSTER_NAME}" \
  --services "${PROJECT}-api" "${PROJECT}-worker" "${PROJECT}-beat"

log "Summary"
echo
info "Cluster:          ${CLUSTER_NAME} (Container Insights on)"
info "Image:            ${IMAGE_URI}"
info "Execution role:   ${EXEC_ROLE_ARN}"
info "Task role:        ${TASK_ROLE_ARN}"
info "Conn secret:      ${CONN_SECRET_ARN}"
info "API task def:     ${API_TD_ARN}"
info "Worker task def:  ${WORKER_TD_ARN}"
info "Beat task def:    ${BEAT_TD_ARN}"
info "Migration task:   ${MIGRATE_TASK_ARN}"
info "Network:          private ${PRIVATE_SUBNET_ID}, assignPublicIp=DISABLED, SG ${SG_ECS_ID}"
echo
log "Manual checks:"
info "aws ecs describe-services --cluster ${CLUSTER_NAME} --services ${PROJECT}-api ${PROJECT}-worker ${PROJECT}-beat"
info "aws ecs describe-task-definition --task-definition ${PROJECT}-api --query taskDefinition.containerDefinitions[0].secrets"
info "Confirm secrets are ARN refs only — no plaintext URLs/passwords."
info "Logs: /ecs/${PROJECT}-api  /ecs/${PROJECT}-worker  /ecs/${PROJECT}-beat"

TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
STATE_LINE="STEP=${STEP} TIMESTAMP=${TIMESTAMP} CLUSTER=${CLUSTER_NAME} VPC_ID=${VPC_ID} PRIVATE_SUBNET_ID=${PRIVATE_SUBNET_ID} SG_ECS_ID=${SG_ECS_ID} IMAGE_URI=${IMAGE_URI} EXEC_ROLE_ARN=${EXEC_ROLE_ARN} TASK_ROLE_ARN=${TASK_ROLE_ARN} CONN_SECRET_ARN=${CONN_SECRET_ARN} SECRET_KEY_ARN=${SECRET_KEY_ARN} API_TD_ARN=${API_TD_ARN} WORKER_TD_ARN=${WORKER_TD_ARN} BEAT_TD_ARN=${BEAT_TD_ARN} SERVICE_API=${PROJECT}-api SERVICE_WORKER=${PROJECT}-worker SERVICE_BEAT=${PROJECT}-beat REGION=${REGION}"

if [[ -f "${STATE_FILE}" ]] && grep -q '^STEP=04 ' "${STATE_FILE}" 2>/dev/null; then
  TMP="$(mktemp)"
  grep -v '^STEP=04 ' "${STATE_FILE}" > "${TMP}" || true
  echo "${STATE_LINE}" >> "${TMP}"
  mv "${TMP}" "${STATE_FILE}"
  info "Updated Step ${STEP} entry in ${STATE_FILE}"
else
  echo "${STATE_LINE}" >> "${STATE_FILE}"
  info "Appended Step ${STEP} entry to ${STATE_FILE}"
fi

log "Done."
