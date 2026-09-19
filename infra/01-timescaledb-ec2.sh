# Private TimescaleDB EC2 for tradeflow (Step 01). No ECS/ElastiCache.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${SCRIPT_DIR}/.deployed-steps"
STEP="01"
PROJECT="tradeflow"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${REGION}"

VPC_NAME="${PROJECT}-vpc"
PUBLIC_SUBNET_NAME="${PROJECT}-public-subnet"
PRIVATE_SUBNET_NAME="${PROJECT}-private-subnet"
IGW_NAME="${PROJECT}-igw"
NAT_NAME="${PROJECT}-nat"
EIP_NAME="${PROJECT}-nat-eip"
PUBLIC_RT_NAME="${PROJECT}-public-rt"
PRIVATE_RT_NAME="${PROJECT}-private-rt"
SG_ECS_NAME="${PROJECT}-ecs-tasks-sg"
SG_DB_NAME="${PROJECT}-timescaledb-sg"
IAM_ROLE_NAME="${PROJECT}-timescaledb-ec2-role"
IAM_PROFILE_NAME="${PROJECT}-timescaledb-ec2-profile"
INSTANCE_NAME="${PROJECT}-timescaledb"
SECRET_NAME="${PROJECT}/timescaledb/credentials"

VPC_CIDR="10.0.0.0/16"
PUBLIC_CIDR="10.0.1.0/24"
PRIVATE_CIDR="10.0.2.0/24"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.medium}"
EBS_SIZE_GB=20

TAG_SPEC_BASE="ResourceType=%s,Tags=[{Key=Project,Value=${PROJECT}},{Key=Step,Value=${STEP}},{Key=Name,Value=%s}]"

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

find_vpc() {
  aws_q ec2 describe-vpcs \
    --filters "Name=tag:Name,Values=${VPC_NAME}" "Name=tag:Project,Values=${PROJECT}" \
    --query 'Vpcs[0].VpcId'
}

find_subnet_by_name() {
  local name="$1"
  aws_q ec2 describe-subnets \
    --filters "Name=tag:Name,Values=${name}" "Name=tag:Project,Values=${PROJECT}" \
    --query 'Subnets[0].SubnetId'
}

find_igw() {
  aws_q ec2 describe-internet-gateways \
    --filters "Name=tag:Name,Values=${IGW_NAME}" "Name=tag:Project,Values=${PROJECT}" \
    --query 'InternetGateways[0].InternetGatewayId'
}

find_nat() {
  aws_q ec2 describe-nat-gateways \
    --filter "Name=tag:Name,Values=${NAT_NAME}" "Name=tag:Project,Values=${PROJECT}" \
              "Name=state,Values=pending,available" \
    --query 'NatGateways[0].NatGatewayId'
}

find_eip_alloc() {
  aws_q ec2 describe-addresses \
    --filters "Name=tag:Name,Values=${EIP_NAME}" "Name=tag:Project,Values=${PROJECT}" \
    --query 'Addresses[0].AllocationId'
}

find_rt_by_name() {
  local name="$1"
  aws_q ec2 describe-route-tables \
    --filters "Name=tag:Name,Values=${name}" "Name=tag:Project,Values=${PROJECT}" \
    --query 'RouteTables[0].RouteTableId'
}

find_sg_by_name() {
  local name="$1"
  local vpc_id="$2"
  aws_q ec2 describe-security-groups \
    --filters "Name=group-name,Values=${name}" "Name=vpc-id,Values=${vpc_id}" \
    --query 'SecurityGroups[0].GroupId'
}

find_instance() {
  aws_q ec2 describe-instances \
    --filters "Name=tag:Name,Values=${INSTANCE_NAME}" \
              "Name=tag:Project,Values=${PROJECT}" \
              "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[0].Instances[0].InstanceId'
}

secret_exists() {
  aws secretsmanager describe-secret --secret-id "${SECRET_NAME}" >/dev/null 2>&1
}

role_exists() {
  aws iam get-role --role-name "${IAM_ROLE_NAME}" >/dev/null 2>&1
}

profile_exists() {
  aws iam get-instance-profile --instance-profile-name "${IAM_PROFILE_NAME}" >/dev/null 2>&1
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

require_cmd aws
require_cmd openssl
require_cmd python3

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
is_none "${ACCOUNT_ID}" && die "Unable to resolve AWS account. Check credentials / AWS_PROFILE."

log "tradeflow Step ${STEP}: TimescaleDB on private EC2"
info "Account: ${ACCOUNT_ID}"
info "Region:  ${REGION}"
info "Profile: ${AWS_PROFILE:-<default>}"
echo
log "COST WARNING: A NAT Gateway will be created (~\$0.045/hr + data processing in ${REGION})."
info "Charges accrue while the NAT Gateway exists, even if the EC2 instance is stopped."
info "Delete the NAT Gateway (and EIP) when tearing down to stop that cost."
echo

log "Networking (VPC, subnets, NAT)"

VPC_ID="$(find_vpc)"
if is_none "${VPC_ID}"; then
  VPC_ID="$(aws ec2 create-vpc \
    --cidr-block "${VPC_CIDR}" \
    --tag-specifications "$(printf "${TAG_SPEC_BASE}" vpc "${VPC_NAME}")" \
    --query 'Vpc.VpcId' --output text)"
  aws ec2 modify-vpc-attribute --vpc-id "${VPC_ID}" --enable-dns-support '{"Value":true}'
  aws ec2 modify-vpc-attribute --vpc-id "${VPC_ID}" --enable-dns-hostnames '{"Value":true}'
  info "Created VPC ${VPC_ID} (${VPC_CIDR})"
else
  skip "VPC already exists: ${VPC_ID}"
fi

AZ="$(aws ec2 describe-availability-zones \
  --filters "Name=state,Values=available" \
  --query 'AvailabilityZones[0].ZoneName' --output text)"
info "Using AZ: ${AZ}"

PUBLIC_SUBNET_ID="$(find_subnet_by_name "${PUBLIC_SUBNET_NAME}")"
if is_none "${PUBLIC_SUBNET_ID}"; then
  PUBLIC_SUBNET_ID="$(aws ec2 create-subnet \
    --vpc-id "${VPC_ID}" \
    --cidr-block "${PUBLIC_CIDR}" \
    --availability-zone "${AZ}" \
    --tag-specifications "$(printf "${TAG_SPEC_BASE}" subnet "${PUBLIC_SUBNET_NAME}")" \
    --query 'Subnet.SubnetId' --output text)"
  aws ec2 modify-subnet-attribute --subnet-id "${PUBLIC_SUBNET_ID}" --map-public-ip-on-launch
  info "Created public subnet ${PUBLIC_SUBNET_ID} (${PUBLIC_CIDR})"
else
  skip "Public subnet already exists: ${PUBLIC_SUBNET_ID}"
fi

PRIVATE_SUBNET_ID="$(find_subnet_by_name "${PRIVATE_SUBNET_NAME}")"
if is_none "${PRIVATE_SUBNET_ID}"; then
  PRIVATE_SUBNET_ID="$(aws ec2 create-subnet \
    --vpc-id "${VPC_ID}" \
    --cidr-block "${PRIVATE_CIDR}" \
    --availability-zone "${AZ}" \
    --tag-specifications "$(printf "${TAG_SPEC_BASE}" subnet "${PRIVATE_SUBNET_NAME}")" \
    --query 'Subnet.SubnetId' --output text)"
  info "Created private subnet ${PRIVATE_SUBNET_ID} (${PRIVATE_CIDR})"
else
  skip "Private subnet already exists: ${PRIVATE_SUBNET_ID}"
fi

IGW_ID="$(find_igw)"
if is_none "${IGW_ID}"; then
  IGW_ID="$(aws ec2 create-internet-gateway \
    --tag-specifications "$(printf "${TAG_SPEC_BASE}" internet-gateway "${IGW_NAME}")" \
    --query 'InternetGateway.InternetGatewayId' --output text)"
  aws ec2 attach-internet-gateway --internet-gateway-id "${IGW_ID}" --vpc-id "${VPC_ID}"
  info "Created and attached IGW ${IGW_ID}"
else
  skip "Internet Gateway already exists: ${IGW_ID}"
  ATTACHED="$(aws_q ec2 describe-internet-gateways \
    --internet-gateway-ids "${IGW_ID}" \
    --query "InternetGateways[0].Attachments[?VpcId=='${VPC_ID}'].State | [0]")"
  if is_none "${ATTACHED}"; then
    aws ec2 attach-internet-gateway --internet-gateway-id "${IGW_ID}" --vpc-id "${VPC_ID}"
    info "Attached existing IGW ${IGW_ID} to ${VPC_ID}"
  fi
fi

PUBLIC_RT_ID="$(find_rt_by_name "${PUBLIC_RT_NAME}")"
if is_none "${PUBLIC_RT_ID}"; then
  PUBLIC_RT_ID="$(aws ec2 create-route-table \
    --vpc-id "${VPC_ID}" \
    --tag-specifications "$(printf "${TAG_SPEC_BASE}" route-table "${PUBLIC_RT_NAME}")" \
    --query 'RouteTable.RouteTableId' --output text)"
  info "Created public route table ${PUBLIC_RT_ID}"
else
  skip "Public route table already exists: ${PUBLIC_RT_ID}"
fi

EXISTING_DEFAULT="$(aws_q ec2 describe-route-tables \
  --route-table-ids "${PUBLIC_RT_ID}" \
  --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].GatewayId | [0]")"
if is_none "${EXISTING_DEFAULT}"; then
  aws ec2 create-route \
    --route-table-id "${PUBLIC_RT_ID}" \
    --destination-cidr-block "0.0.0.0/0" \
    --gateway-id "${IGW_ID}" >/dev/null
  info "Added 0.0.0.0/0 -> IGW on public route table"
else
  skip "Public default route already present (${EXISTING_DEFAULT})"
fi

ASSOC="$(aws_q ec2 describe-route-tables \
  --route-table-ids "${PUBLIC_RT_ID}" \
  --query "RouteTables[0].Associations[?SubnetId=='${PUBLIC_SUBNET_ID}'].RouteTableAssociationId | [0]")"
if is_none "${ASSOC}"; then
  aws ec2 associate-route-table --route-table-id "${PUBLIC_RT_ID}" --subnet-id "${PUBLIC_SUBNET_ID}" >/dev/null
  info "Associated public subnet with public route table"
else
  skip "Public subnet already associated with public route table"
fi

EIP_ALLOC_ID="$(find_eip_alloc)"
if is_none "${EIP_ALLOC_ID}"; then
  EIP_ALLOC_ID="$(aws ec2 allocate-address \
    --domain vpc \
    --tag-specifications "$(printf "${TAG_SPEC_BASE}" elastic-ip "${EIP_NAME}")" \
    --query 'AllocationId' --output text)"
  info "Allocated Elastic IP ${EIP_ALLOC_ID} for NAT Gateway"
else
  skip "NAT Elastic IP already allocated: ${EIP_ALLOC_ID}"
fi

NAT_ID="$(find_nat)"
if is_none "${NAT_ID}"; then
  log "Creating NAT Gateway..."
  NAT_ID="$(aws ec2 create-nat-gateway \
    --subnet-id "${PUBLIC_SUBNET_ID}" \
    --allocation-id "${EIP_ALLOC_ID}" \
    --tag-specifications "$(printf "${TAG_SPEC_BASE}" natgateway "${NAT_NAME}")" \
    --query 'NatGateway.NatGatewayId' --output text)"
  info "Waiting for NAT Gateway ${NAT_ID} to become available..."
  aws ec2 wait nat-gateway-available --nat-gateway-ids "${NAT_ID}"
  info "NAT Gateway ${NAT_ID} is available (hourly cost now accruing)"
else
  skip "NAT Gateway already exists: ${NAT_ID}"
  STATE="$(aws_q ec2 describe-nat-gateways --nat-gateway-ids "${NAT_ID}" --query 'NatGateways[0].State')"
  if [[ "${STATE}" == "pending" ]]; then
    info "Waiting for existing NAT Gateway to become available..."
    aws ec2 wait nat-gateway-available --nat-gateway-ids "${NAT_ID}"
  fi
fi

PRIVATE_RT_ID="$(find_rt_by_name "${PRIVATE_RT_NAME}")"
if is_none "${PRIVATE_RT_ID}"; then
  PRIVATE_RT_ID="$(aws ec2 create-route-table \
    --vpc-id "${VPC_ID}" \
    --tag-specifications "$(printf "${TAG_SPEC_BASE}" route-table "${PRIVATE_RT_NAME}")" \
    --query 'RouteTable.RouteTableId' --output text)"
  info "Created private route table ${PRIVATE_RT_ID}"
else
  skip "Private route table already exists: ${PRIVATE_RT_ID}"
fi

EXISTING_PRIV_DEFAULT="$(aws_q ec2 describe-route-tables \
  --route-table-ids "${PRIVATE_RT_ID}" \
  --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].NatGatewayId | [0]")"
if is_none "${EXISTING_PRIV_DEFAULT}"; then
  aws ec2 create-route \
    --route-table-id "${PRIVATE_RT_ID}" \
    --destination-cidr-block "0.0.0.0/0" \
    --nat-gateway-id "${NAT_ID}" >/dev/null
  info "Added 0.0.0.0/0 -> NAT on private route table"
else
  skip "Private default route already present (${EXISTING_PRIV_DEFAULT})"
fi

ASSOC_PRIV="$(aws_q ec2 describe-route-tables \
  --route-table-ids "${PRIVATE_RT_ID}" \
  --query "RouteTables[0].Associations[?SubnetId=='${PRIVATE_SUBNET_ID}'].RouteTableAssociationId | [0]")"
if is_none "${ASSOC_PRIV}"; then
  aws ec2 associate-route-table --route-table-id "${PRIVATE_RT_ID}" --subnet-id "${PRIVATE_SUBNET_ID}" >/dev/null
  info "Associated private subnet with private route table"
else
  skip "Private subnet already associated with private route table"
fi

# Password must exist in Secrets Manager before EC2 user-data runs
log "Secrets Manager"
SECRET_ARN=""
if secret_exists; then
  SECRET_ARN="$(aws secretsmanager describe-secret --secret-id "${SECRET_NAME}" --query ARN --output text)"
  skip "Secret already exists: ${SECRET_ARN}"
else
  DB_PASSWORD="$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)"
  SECRET_STRING="$(python3 -c "import json,sys; print(json.dumps({
    'username': 'tradeflow',
    'password': sys.argv[1],
    'dbname': 'tradeflow',
    'port': 5432,
    'engine': 'postgres'
  }))" "${DB_PASSWORD}")"
  unset DB_PASSWORD

  SECRET_ARN="$(aws secretsmanager create-secret \
    --name "${SECRET_NAME}" \
    --description "tradeflow TimescaleDB credentials (Step ${STEP})" \
    --secret-string "${SECRET_STRING}" \
    --tags "Key=Project,Value=${PROJECT}" "Key=Step,Value=${STEP}" "Key=Name,Value=${SECRET_NAME}" \
    --query ARN --output text)"
  unset SECRET_STRING
  info "Created secret ${SECRET_ARN}"
  info "Password is ONLY in Secrets Manager — not in user-data, env files, or this repo."
fi

# IAM: SSM Session Manager + read this one secret only
log "IAM instance role"
TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}'

SECRET_POLICY="$(python3 -c "import json,sys; print(json.dumps({
  'Version': '2012-10-17',
  'Statement': [{
    'Sid': 'ReadTimescaleSecretOnly',
    'Effect': 'Allow',
    'Action': ['secretsmanager:GetSecretValue', 'secretsmanager:DescribeSecret'],
    'Resource': sys.argv[1]
  }]
}))" "${SECRET_ARN}")"

if role_exists; then
  skip "IAM role already exists: ${IAM_ROLE_NAME}"
else
  aws iam create-role \
    --role-name "${IAM_ROLE_NAME}" \
    --assume-role-policy-document "${TRUST_POLICY}" \
    --tags "Key=Project,Value=${PROJECT}" "Key=Step,Value=${STEP}" "Key=Name,Value=${IAM_ROLE_NAME}" \
    >/dev/null
  info "Created IAM role ${IAM_ROLE_NAME}"
fi

aws iam attach-role-policy \
  --role-name "${IAM_ROLE_NAME}" \
  --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" >/dev/null

aws iam put-role-policy \
  --role-name "${IAM_ROLE_NAME}" \
  --policy-name "${PROJECT}-timescaledb-secret-read" \
  --policy-document "${SECRET_POLICY}" >/dev/null
info "Ensured SSM core + single-secret read policy on ${IAM_ROLE_NAME}"

if profile_exists; then
  skip "Instance profile already exists: ${IAM_PROFILE_NAME}"
else
  aws iam create-instance-profile \
    --instance-profile-name "${IAM_PROFILE_NAME}" \
    --tags "Key=Project,Value=${PROJECT}" "Key=Step,Value=${STEP}" "Key=Name,Value=${IAM_PROFILE_NAME}" \
    >/dev/null
  info "Created instance profile ${IAM_PROFILE_NAME}"
fi

PROFILE_HAS_ROLE="$(aws_q iam get-instance-profile \
  --instance-profile-name "${IAM_PROFILE_NAME}" \
  --query "InstanceProfile.Roles[?RoleName=='${IAM_ROLE_NAME}'].RoleName | [0]")"
if is_none "${PROFILE_HAS_ROLE}"; then
  aws iam add-role-to-instance-profile \
    --instance-profile-name "${IAM_PROFILE_NAME}" \
    --role-name "${IAM_ROLE_NAME}" >/dev/null
  info "Attached role to instance profile"
else
  skip "Role already attached to instance profile"
fi

# EC2 needs a moment to see a newly created instance profile
info "Waiting briefly for IAM instance profile propagation..."
sleep 15

log "Security groups"
SG_ECS_ID="$(find_sg_by_name "${SG_ECS_NAME}" "${VPC_ID}")"
if is_none "${SG_ECS_ID}"; then
  SG_ECS_ID="$(aws ec2 create-security-group \
    --group-name "${SG_ECS_NAME}" \
    --description "Placeholder SG for tradeflow ECS tasks (inbound rules added in later steps)" \
    --vpc-id "${VPC_ID}" \
    --tag-specifications "$(printf "${TAG_SPEC_BASE}" security-group "${SG_ECS_NAME}")" \
    --query 'GroupId' --output text)"
  info "Created ECS-tasks placeholder SG ${SG_ECS_ID} (no inbound rules yet)"
else
  skip "ECS-tasks SG already exists: ${SG_ECS_ID}"
fi

SG_DB_ID="$(find_sg_by_name "${SG_DB_NAME}" "${VPC_ID}")"
if is_none "${SG_DB_ID}"; then
  SG_DB_ID="$(aws ec2 create-security-group \
    --group-name "${SG_DB_NAME}" \
    --description "TimescaleDB: inbound 5432 from ECS tasks SG only; no SSH" \
    --vpc-id "${VPC_ID}" \
    --tag-specifications "$(printf "${TAG_SPEC_BASE}" security-group "${SG_DB_NAME}")" \
    --query 'GroupId' --output text)"
  info "Created TimescaleDB SG ${SG_DB_ID}"
else
  skip "TimescaleDB SG already exists: ${SG_DB_ID}"
fi

# Flatten UserIdGroupPairs[] before filtering. Nested projection misses existing rules
HAS_5432="$(aws_q ec2 describe-security-groups \
  --group-ids "${SG_DB_ID}" \
  --query "SecurityGroups[0].IpPermissions[?FromPort==\`5432\` && ToPort==\`5432\`].UserIdGroupPairs[].GroupId | [?@=='${SG_ECS_ID}'] | [0]")"
if is_none "${HAS_5432}"; then
  authorize_ingress_tcp "${SG_DB_ID}" 5432 "${SG_ECS_ID}"
  info "Authorized tcp/5432 from ${SG_ECS_NAME} only (no port 22)"
else
  skip "Ingress tcp/5432 from ECS SG already present"
fi

log "Resolve latest AL2023 AMI"
AMI_ID="$(aws ssm get-parameter \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query 'Parameter.Value' --output text)"
is_none "${AMI_ID}" && die "Failed to resolve latest AL2023 AMI from SSM"
info "AMI: ${AMI_ID}"

# Private subnet, no public IP, no SSH key, IMDSv2, encrypted gp3 root
log "Launch TimescaleDB EC2"
INSTANCE_ID="$(find_instance)"
if ! is_none "${INSTANCE_ID}"; then
  skip "EC2 instance already exists: ${INSTANCE_ID}"
else
  # User-data fetches the secret at boot,never embeds the password
  USER_DATA_FILE="$(mktemp)"
  cat > "${USER_DATA_FILE}" <<EOF
#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/tradeflow-timescaledb-bootstrap.log) 2>&1

dnf install -y docker jq
systemctl enable --now docker

TOKEN=\$(curl -sS -X PUT "http://169.254.169.254/latest/api/token" \\
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
REGION=\$(curl -sS -H "X-aws-ec2-metadata-token: \$TOKEN" \\
  http://169.254.169.254/latest/meta-data/placement/region)

SECRET_JSON=\$(aws secretsmanager get-secret-value \\
  --secret-id "${SECRET_NAME}" \\
  --region "\$REGION" \\
  --query SecretString --output text)

DB_USER=\$(echo "\$SECRET_JSON" | jq -r .username)
DB_PASS=\$(echo "\$SECRET_JSON" | jq -r .password)
DB_NAME=\$(echo "\$SECRET_JSON" | jq -r .dbname)
unset SECRET_JSON

mkdir -p /var/lib/timescaledb
chown 999:999 /var/lib/timescaledb || true

docker pull timescale/timescaledb:latest-pg16
docker rm -f timescaledb 2>/dev/null || true
docker run -d \\
  --name timescaledb \\
  --restart unless-stopped \\
  -e POSTGRES_USER="\$DB_USER" \\
  -e POSTGRES_PASSWORD="\$DB_PASS" \\
  -e POSTGRES_DB="\$DB_NAME" \\
  -p 5432:5432 \\
  -v /var/lib/timescaledb:/var/lib/postgresql/data \\
  timescale/timescaledb:latest-pg16

unset DB_PASS
echo "TimescaleDB container started"
EOF

  INSTANCE_ID="$(aws ec2 run-instances \
    --image-id "${AMI_ID}" \
    --instance-type "${INSTANCE_TYPE}" \
    --network-interfaces "AssociatePublicIpAddress=false,DeviceIndex=0,SubnetId=${PRIVATE_SUBNET_ID},Groups=${SG_DB_ID}" \
    --iam-instance-profile "Name=${IAM_PROFILE_NAME}" \
    --user-data "file://${USER_DATA_FILE}" \
    --metadata-options "HttpTokens=required,HttpPutResponseHopLimit=1,HttpEndpoint=enabled" \
    --block-device-mappings "[{\"DeviceName\":\"/dev/xvda\",\"Ebs\":{\"VolumeSize\":${EBS_SIZE_GB},\"VolumeType\":\"gp3\",\"Encrypted\":true,\"DeleteOnTermination\":true}}]" \
    --tag-specifications \
      "$(printf "${TAG_SPEC_BASE}" instance "${INSTANCE_NAME}")" \
      "ResourceType=volume,Tags=[{Key=Project,Value=${PROJECT}},{Key=Step,Value=${STEP}},{Key=Name,Value=${INSTANCE_NAME}-root}]" \
    --query 'Instances[0].InstanceId' --output text)"

  rm -f "${USER_DATA_FILE}"

  info "Launched instance ${INSTANCE_ID} in private subnet ${PRIVATE_SUBNET_ID}"
  info "No SSH key pair; access via SSM Session Manager only"
  info "IMDSv2 required; root EBS ${EBS_SIZE_GB}GB gp3 encrypted=true"
fi

info "Waiting for instance to reach running state..."
aws ec2 wait instance-running --instance-ids "${INSTANCE_ID}"

PRIVATE_IP="$(aws_q ec2 describe-instances \
  --instance-ids "${INSTANCE_ID}" \
  --query 'Reservations[0].Instances[0].PrivateIpAddress')"
PRIVATE_DNS="$(aws_q ec2 describe-instances \
  --instance-ids "${INSTANCE_ID}" \
  --query 'Reservations[0].Instances[0].PrivateDnsName')"
PUBLIC_IP="$(aws_q ec2 describe-instances \
  --instance-ids "${INSTANCE_ID}" \
  --query 'Reservations[0].Instances[0].PublicIpAddress')"

if ! is_none "${PUBLIC_IP}"; then
  die "Instance unexpectedly has a public IP (${PUBLIC_IP}). Aborting — private-only requirement violated."
fi

log "Summary"
echo
info "VPC:              ${VPC_ID}"
info "Public subnet:    ${PUBLIC_SUBNET_ID}"
info "Private subnet:   ${PRIVATE_SUBNET_ID}"
info "NAT Gateway:      ${NAT_ID}  << hourly cost accruing"
info "EIP allocation:   ${EIP_ALLOC_ID}"
info "Secret ARN:       ${SECRET_ARN}"
info "IAM role:         ${IAM_ROLE_NAME}"
info "Instance profile: ${IAM_PROFILE_NAME}"
info "ECS tasks SG:     ${SG_ECS_ID} (placeholder)"
info "TimescaleDB SG:   ${SG_DB_ID}"
info "Instance ID:      ${INSTANCE_ID}"
info "Private IP:       ${PRIVATE_IP}"
info "Private DNS:      ${PRIVATE_DNS}"
info "AMI:              ${AMI_ID}"
echo
log "Connect (SSM Session Manager — no SSH):"
info "aws ssm start-session --target ${INSTANCE_ID} --region ${REGION}"
echo
log "Bootstrap may take a few minutes after first boot (docker install + image pull via NAT)."
info "Check: sudo tail -f /var/log/tradeflow-timescaledb-bootstrap.log"

TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
STATE_LINE="STEP=${STEP} TIMESTAMP=${TIMESTAMP} VPC_ID=${VPC_ID} PUBLIC_SUBNET_ID=${PUBLIC_SUBNET_ID} PRIVATE_SUBNET_ID=${PRIVATE_SUBNET_ID} IGW_ID=${IGW_ID} NAT_GATEWAY_ID=${NAT_ID} EIP_ALLOC_ID=${EIP_ALLOC_ID} PUBLIC_RT_ID=${PUBLIC_RT_ID} PRIVATE_RT_ID=${PRIVATE_RT_ID} SECRET_ARN=${SECRET_ARN} SECRET_NAME=${SECRET_NAME} IAM_ROLE=${IAM_ROLE_NAME} INSTANCE_PROFILE=${IAM_PROFILE_NAME} SG_ECS_ID=${SG_ECS_ID} SG_DB_ID=${SG_DB_ID} INSTANCE_ID=${INSTANCE_ID} PRIVATE_IP=${PRIVATE_IP} PRIVATE_DNS=${PRIVATE_DNS} AMI_ID=${AMI_ID} REGION=${REGION}"

# Upsert this step's state line so re-runs don't duplicate
if [[ -f "${STATE_FILE}" ]] && grep -q '^STEP=01 ' "${STATE_FILE}" 2>/dev/null; then
  TMP="$(mktemp)"
  grep -v '^STEP=01 ' "${STATE_FILE}" > "${TMP}" || true
  echo "${STATE_LINE}" >> "${TMP}"
  mv "${TMP}" "${STATE_FILE}"
  info "Updated Step ${STEP} entry in ${STATE_FILE}"
else
  echo "${STATE_LINE}" >> "${STATE_FILE}"
  info "Appended Step ${STEP} entry to ${STATE_FILE}"
fi

log "Done. NAT Gateway cost reminder: tear it down when you no longer need private-subnet egress."
