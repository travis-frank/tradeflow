# Application Load Balancer in front of ECS api only (Step 05). Requires 01 + 04.


set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${SCRIPT_DIR}/.deployed-steps"
STEP="05"
PROJECT="tradeflow"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${REGION}"

ALB_NAME="${PROJECT}-alb"
TG_NAME="${PROJECT}-api-tg"
SG_ALB_NAME="${PROJECT}-alb-sg"
SG_ECS_NAME="${PROJECT}-ecs-tasks-sg"
PUBLIC_SUBNET_B_NAME="${PROJECT}-public-subnet-b"
PUBLIC_CIDR_B="10.0.3.0/24"
LOG_PREFIX="alb"
HEALTH_PATH="/health"
CONTAINER_PORT=8000
CLUSTER_NAME="${PROJECT}"
SERVICE_API="${PROJECT}-api"

# us-east-1 ELB account for classic S3 access-log PutObject grant
ELB_ACCOUNT_USEAST1="127311923021"

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

find_subnet_by_name() {
  local name="$1"
  aws_q ec2 describe-subnets \
    --filters "Name=tag:Name,Values=${name}" "Name=tag:Project,Values=${PROJECT}" \
    --query 'Subnets[0].SubnetId'
}

find_alb_arn() {
  aws_q elbv2 describe-load-balancers \
    --names "${ALB_NAME}" \
    --query 'LoadBalancers[0].LoadBalancerArn'
}

find_tg_arn() {
  aws_q elbv2 describe-target-groups \
    --names "${TG_NAME}" \
    --query 'TargetGroups[0].TargetGroupArn'
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

authorize_ingress_cidr() {
  local group_id="$1" port="$2" cidr="$3"
  local err
  if err="$(aws ec2 authorize-security-group-ingress \
    --group-id "${group_id}" \
    --protocol tcp \
    --port "${port}" \
    --cidr "${cidr}" 2>&1)"; then
    return 0
  fi
  if [[ "${err}" == *InvalidPermission.Duplicate* ]]; then
    return 0
  fi
  printf '%s\n' "${err}" >&2
  return 1
}

require_cmd aws
require_cmd python3

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
is_none "${ACCOUNT_ID}" && die "Unable to resolve AWS account. Check credentials / AWS_PROFILE."

[[ -f "${STATE_FILE}" ]] || die "Missing ${STATE_FILE}. Run steps 01 and 04 first."

VPC_ID="$(state_get 01 VPC_ID || true)"
PUBLIC_SUBNET_A="$(state_get 01 PUBLIC_SUBNET_ID || true)"
PUBLIC_RT_ID="$(state_get 01 PUBLIC_RT_ID || true)"
SG_ECS_ID="$(state_get 01 SG_ECS_ID || true)"
CLUSTER="$(state_get 04 CLUSTER || true)"
SERVICE_API_STATE="$(state_get 04 SERVICE_API || true)"
STEP01_REGION="$(state_get 01 REGION || true)"

is_none "${VPC_ID}" && die "STEP=01 missing VPC_ID"
is_none "${PUBLIC_SUBNET_A}" && die "STEP=01 missing PUBLIC_SUBNET_ID"
is_none "${PUBLIC_RT_ID}" && die "STEP=01 missing PUBLIC_RT_ID"
is_none "${SG_ECS_ID}" && die "STEP=01 missing SG_ECS_ID"
is_none "${CLUSTER}" && die "STEP=04 missing CLUSTER — run infra/04-ecs.sh first"
is_none "${SERVICE_API_STATE}" || SERVICE_API="${SERVICE_API_STATE}"
CLUSTER_NAME="${CLUSTER}"

if ! is_none "${STEP01_REGION}"; then
  REGION="${STEP01_REGION}"
  export AWS_DEFAULT_REGION="${REGION}"
fi

LOG_BUCKET="${PROJECT}-${ACCOUNT_ID}-alb-logs"

log "tradeflow Step ${STEP}: ALB in front of ${SERVICE_API}"
info "Account: ${ACCOUNT_ID}"
info "Region:  ${REGION}"
info "Profile: ${AWS_PROFILE:-<default>}"
info "VPC:     ${VPC_ID}"
echo

# ALB requires ≥2 subnets in different AZs — step 01 only created one public subnet
log "Public subnets for ALB"
AZ_A="$(aws_q ec2 describe-subnets --subnet-ids "${PUBLIC_SUBNET_A}" --query 'Subnets[0].AvailabilityZone')"
info "Public subnet A: ${PUBLIC_SUBNET_A} (${AZ_A})"

PUBLIC_SUBNET_B="$(find_subnet_by_name "${PUBLIC_SUBNET_B_NAME}")"
if is_none "${PUBLIC_SUBNET_B}"; then
  AZ_B="$(aws_q ec2 describe-availability-zones \
    --filters "Name=state,Values=available" \
    --query "AvailabilityZones[?ZoneName!='${AZ_A}'].ZoneName | [0]")"
  is_none "${AZ_B}" && die "Could not find a second AZ for the ALB"
  PUBLIC_SUBNET_B="$(aws ec2 create-subnet \
    --vpc-id "${VPC_ID}" \
    --cidr-block "${PUBLIC_CIDR_B}" \
    --availability-zone "${AZ_B}" \
    --tag-specifications "$(printf "${TAG_SPEC_EC2}" subnet "${PUBLIC_SUBNET_B_NAME}")" \
    --query 'Subnet.SubnetId' --output text)"
  aws ec2 modify-subnet-attribute --subnet-id "${PUBLIC_SUBNET_B}" --map-public-ip-on-launch
  aws ec2 associate-route-table --route-table-id "${PUBLIC_RT_ID}" --subnet-id "${PUBLIC_SUBNET_B}" >/dev/null
  info "Created public subnet B: ${PUBLIC_SUBNET_B} (${AZ_B}, ${PUBLIC_CIDR_B})"
else
  AZ_B="$(aws_q ec2 describe-subnets --subnet-ids "${PUBLIC_SUBNET_B}" --query 'Subnets[0].AvailabilityZone')"
  skip "Public subnet B already exists: ${PUBLIC_SUBNET_B} (${AZ_B})"
fi

log "Security groups"
SG_ALB_ID="$(find_sg_by_name "${SG_ALB_NAME}" "${VPC_ID}")"
if is_none "${SG_ALB_ID}"; then
  SG_ALB_ID="$(aws ec2 create-security-group \
    --group-name "${SG_ALB_NAME}" \
    --description "tradeflow ALB: inbound 80 from internet (443 later with ACM)" \
    --vpc-id "${VPC_ID}" \
    --tag-specifications "$(printf "${TAG_SPEC_EC2}" security-group "${SG_ALB_NAME}")" \
    --query 'GroupId' --output text)"
  info "Created ALB SG ${SG_ALB_ID}"
else
  skip "ALB SG already exists: ${SG_ALB_ID}"
fi

# HTTP only for now — HTTPS listener later reuses this SG (+ :443 rule)
HAS_80="$(aws_q ec2 describe-security-groups \
  --group-ids "${SG_ALB_ID}" \
  --query "SecurityGroups[0].IpPermissions[?FromPort==\`80\` && ToPort==\`80\`].IpRanges[].CidrIp | [?@=='0.0.0.0/0'] | [0]")"
if is_none "${HAS_80}"; then
  authorize_ingress_cidr "${SG_ALB_ID}" 80 "0.0.0.0/0"
  info "Authorized tcp/80 from 0.0.0.0/0 on ALB SG"
else
  skip "ALB SG already allows tcp/80 from 0.0.0.0/0"
fi

LIVE_ECS_SG="$(find_sg_by_name "${SG_ECS_NAME}" "${VPC_ID}")"
is_none "${LIVE_ECS_SG}" && die "ECS-tasks SG not found"
SG_ECS_ID="${LIVE_ECS_SG}"

# ECS api: inbound 8000 ONLY from ALB SG (not 0.0.0.0/0, not whole VPC)
HAS_8000="$(aws_q ec2 describe-security-groups \
  --group-ids "${SG_ECS_ID}" \
  --query "SecurityGroups[0].IpPermissions[?FromPort==\`${CONTAINER_PORT}\` && ToPort==\`${CONTAINER_PORT}\`].UserIdGroupPairs[].GroupId | [?@=='${SG_ALB_ID}'] | [0]")"
if is_none "${HAS_8000}"; then
  authorize_ingress_tcp "${SG_ECS_ID}" "${CONTAINER_PORT}" "${SG_ALB_ID}"
  info "Authorized tcp/${CONTAINER_PORT} on ECS SG from ALB SG only"
else
  skip "ECS SG already allows tcp/${CONTAINER_PORT} from ALB SG"
fi

log "S3 bucket for ALB access logs"
if aws s3api head-bucket --bucket "${LOG_BUCKET}" 2>/dev/null; then
  skip "Bucket exists: ${LOG_BUCKET}"
else
  if [[ "${REGION}" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "${LOG_BUCKET}" --region "${REGION}" >/dev/null
  else
    aws s3api create-bucket \
      --bucket "${LOG_BUCKET}" \
      --region "${REGION}" \
      --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
  fi
  info "Created s3://${LOG_BUCKET}"
fi

aws s3api put-public-access-block \
  --bucket "${LOG_BUCKET}" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" >/dev/null

aws s3api put-bucket-encryption \
  --bucket "${LOG_BUCKET}" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}' >/dev/null

# ELB regional account PutObject + GetBucketAcl for delivery
BUCKET_POLICY="$(python3 -c "
import json
bucket='${LOG_BUCKET}'
account='${ACCOUNT_ID}'
elb_account='${ELB_ACCOUNT_USEAST1}'
prefix='${LOG_PREFIX}'
print(json.dumps({
  'Version': '2012-10-17',
  'Statement': [
    {
      'Sid': 'AWSLogDeliveryWrite',
      'Effect': 'Allow',
      'Principal': {'AWS': f'arn:aws:iam::{elb_account}:root'},
      'Action': 's3:PutObject',
      'Resource': f'arn:aws:s3:::{bucket}/{prefix}/AWSLogs/{account}/*',
    },
    {
      'Sid': 'AWSLogDeliveryAclCheck',
      'Effect': 'Allow',
      'Principal': {'Service': 'logdelivery.elasticloadbalancing.amazonaws.com'},
      'Action': 's3:GetBucketAcl',
      'Resource': f'arn:aws:s3:::{bucket}',
    },
  ],
}))
")"
aws s3api put-bucket-policy --bucket "${LOG_BUCKET}" --policy "${BUCKET_POLICY}" >/dev/null
info "Bucket encryption + block-public-access + ALB log policy applied"

aws s3api put-bucket-tagging \
  --bucket "${LOG_BUCKET}" \
  --tagging "TagSet=[{Key=Project,Value=${PROJECT}},{Key=Step,Value=${STEP}},{Key=Name,Value=${LOG_BUCKET}}]" >/dev/null

log "Target group (health check ${HEALTH_PATH})"
TG_ARN="$(find_tg_arn)"
if is_none "${TG_ARN}"; then
  TG_ARN="$(aws elbv2 create-target-group \
    --name "${TG_NAME}" \
    --protocol HTTP \
    --port "${CONTAINER_PORT}" \
    --vpc-id "${VPC_ID}" \
    --target-type ip \
    --health-check-enabled \
    --health-check-protocol HTTP \
    --health-check-path "${HEALTH_PATH}" \
    --health-check-port traffic-port \
    --health-check-interval-seconds 30 \
    --health-check-timeout-seconds 5 \
    --healthy-threshold-count 2 \
    --unhealthy-threshold-count 3 \
    --matcher HttpCode=200 \
    --tags "Key=Project,Value=${PROJECT}" "Key=Step,Value=${STEP}" "Key=Name,Value=${TG_NAME}" \
    --query 'TargetGroups[0].TargetGroupArn' --output text)"
  info "Created target group ${TG_ARN}"
else
  skip "Target group already exists: ${TG_ARN}"
fi

log "Application Load Balancer"
ALB_ARN="$(find_alb_arn)"
if is_none "${ALB_ARN}"; then
  ALB_ARN="$(aws elbv2 create-load-balancer \
    --name "${ALB_NAME}" \
    --type application \
    --scheme internet-facing \
    --ip-address-type ipv4 \
    --subnets "${PUBLIC_SUBNET_A}" "${PUBLIC_SUBNET_B}" \
    --security-groups "${SG_ALB_ID}" \
    --tags "Key=Project,Value=${PROJECT}" "Key=Step,Value=${STEP}" "Key=Name,Value=${ALB_NAME}" \
    --query 'LoadBalancers[0].LoadBalancerArn' --output text)"
  info "Created ALB ${ALB_ARN}"
  info "Waiting for ALB to become active..."
  aws elbv2 wait load-balancer-available --load-balancer-arns "${ALB_ARN}"
else
  skip "ALB already exists: ${ALB_ARN}"
fi

ALB_DNS="$(aws_q elbv2 describe-load-balancers \
  --load-balancer-arns "${ALB_ARN}" \
  --query 'LoadBalancers[0].DNSName')"

aws elbv2 modify-load-balancer-attributes \
  --load-balancer-arn "${ALB_ARN}" \
  --attributes \
    "Key=access_logs.s3.enabled,Value=true" \
    "Key=access_logs.s3.bucket,Value=${LOG_BUCKET}" \
    "Key=access_logs.s3.prefix,Value=${LOG_PREFIX}" \
  >/dev/null
info "Access logs -> s3://${LOG_BUCKET}/${LOG_PREFIX}/"

# HTTP :80 listener — HTTPS :443 later: same TG + ACM CertificateArn
log "HTTP listener :80"
HTTP_LISTENER_ARN="$(aws_q elbv2 describe-listeners \
  --load-balancer-arn "${ALB_ARN}" \
  --query "Listeners[?Port==\`80\`].ListenerArn | [0]")"
if is_none "${HTTP_LISTENER_ARN}"; then
  HTTP_LISTENER_ARN="$(aws elbv2 create-listener \
    --load-balancer-arn "${ALB_ARN}" \
    --protocol HTTP \
    --port 80 \
    --default-actions "Type=forward,TargetGroupArn=${TG_ARN}" \
    --query 'Listeners[0].ListenerArn' --output text)"
  info "Created HTTP listener ${HTTP_LISTENER_ARN}"
else
  skip "HTTP :80 listener already exists: ${HTTP_LISTENER_ARN}"
fi

log "Attach ECS service ${SERVICE_API} to target group"
# Fargate container name must match task def (api)
EXISTING_LB="$(aws_q ecs describe-services \
  --cluster "${CLUSTER_NAME}" \
  --services "${SERVICE_API}" \
  --query "services[0].loadBalancers[?targetGroupArn=='${TG_ARN}'].targetGroupArn | [0]")"

if is_none "${EXISTING_LB}"; then
  if ! aws ecs update-service \
    --cluster "${CLUSTER_NAME}" \
    --service "${SERVICE_API}" \
    --load-balancers "targetGroupArn=${TG_ARN},containerName=api,containerPort=${CONTAINER_PORT}" \
    --health-check-grace-period-seconds 60 \
    --force-new-deployment >/dev/null; then
    die "Failed to attach load balancer to ${SERVICE_API}. Recreate the service with --load-balancers if your account rejects in-place LB updates."
  fi
  info "Attached ${SERVICE_API} -> ${TG_NAME} (forced new deployment)"
else
  skip "Service already attached to target group"
  aws ecs update-service \
    --cluster "${CLUSTER_NAME}" \
    --service "${SERVICE_API}" \
    --health-check-grace-period-seconds 60 \
    --force-new-deployment >/dev/null || true
fi

info "Waiting for ${SERVICE_API} to stabilize..."
aws ecs wait services-stable --cluster "${CLUSTER_NAME}" --services "${SERVICE_API}"

log "Summary"
echo
info "ALB ARN:       ${ALB_ARN}"
info "ALB DNS:       ${ALB_DNS}"
info "Target group:  ${TG_ARN}"
info "Health check:  ${HEALTH_PATH}"
info "ALB SG:        ${SG_ALB_ID} (inbound :80 from internet)"
info "ECS SG:        ${SG_ECS_ID} (inbound :${CONTAINER_PORT} from ALB SG only)"
info "Public A/B:    ${PUBLIC_SUBNET_A} / ${PUBLIC_SUBNET_B}"
info "Access logs:   s3://${LOG_BUCKET}/${LOG_PREFIX}/"
echo
log "Manual tests:"
info "curl -sS http://${ALB_DNS}${HEALTH_PATH}"
info "curl -sS http://${ALB_DNS}/api/prices/current/AAPL"
info "From Timescale via SSM: curl -sS --connect-timeout 3 http://<api-task-private-ip>:8000${HEALTH_PATH}  # should fail"
info "HTTPS later: aws elbv2 create-listener --load-balancer-arn ${ALB_ARN} --protocol HTTPS --port 443 --certificates CertificateArn=<acm> --default-actions Type=forward,TargetGroupArn=${TG_ARN}"

TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
STATE_LINE="STEP=${STEP} TIMESTAMP=${TIMESTAMP} VPC_ID=${VPC_ID} ALB_ARN=${ALB_ARN} ALB_DNS=${ALB_DNS} TG_ARN=${TG_ARN} HTTP_LISTENER_ARN=${HTTP_LISTENER_ARN} SG_ALB_ID=${SG_ALB_ID} SG_ECS_ID=${SG_ECS_ID} PUBLIC_SUBNET_A=${PUBLIC_SUBNET_A} PUBLIC_SUBNET_B=${PUBLIC_SUBNET_B} LOG_BUCKET=${LOG_BUCKET} LOG_PREFIX=${LOG_PREFIX} HEALTH_PATH=${HEALTH_PATH} CLUSTER=${CLUSTER_NAME} SERVICE_API=${SERVICE_API} REGION=${REGION}"

if [[ -f "${STATE_FILE}" ]] && grep -q '^STEP=05 ' "${STATE_FILE}" 2>/dev/null; then
  TMP="$(mktemp)"
  grep -v '^STEP=05 ' "${STATE_FILE}" > "${TMP}" || true
  echo "${STATE_LINE}" >> "${TMP}"
  mv "${TMP}" "${STATE_FILE}"
  info "Updated Step ${STEP} entry in ${STATE_FILE}"
else
  echo "${STATE_LINE}" >> "${STATE_FILE}"
  info "Appended Step ${STEP} entry to ${STATE_FILE}"
fi

log "Done."
