# Frontend static site on S3 website hosting
# No CloudFront — keeps page + API both on plain HTTP (avoids mixed-content).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STATE_FILE="${SCRIPT_DIR}/.deployed-steps"
STEP="06"
PROJECT="tradeflow"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${REGION}"

FRONTEND_DIR="${REPO_ROOT}/frontend"
DIST_DIR="${FRONTEND_DIR}/dist"

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

website_endpoint() {
  local bucket="$1" region="$2"
  # us-east-1 uses a hyphenated dualstack-style website host
  if [[ "${region}" == "us-east-1" ]]; then
    printf 'http://%s.s3-website-us-east-1.amazonaws.com\n' "${bucket}"
  else
    printf 'http://%s.s3-website.%s.amazonaws.com\n' "${bucket}" "${region}"
  fi
}

require_cmd aws
require_cmd npm
require_cmd python3

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
is_none "${ACCOUNT_ID}" && die "Unable to resolve AWS account. Check credentials / AWS_PROFILE."

[[ -f "${STATE_FILE}" ]] || die "Missing ${STATE_FILE}. Run step 05 first."
[[ -d "${FRONTEND_DIR}" ]] || die "Frontend dir not found: ${FRONTEND_DIR}"
[[ -f "${FRONTEND_DIR}/package.json" ]] || die "Missing ${FRONTEND_DIR}/package.json"

ALB_DNS="$(state_get 05 ALB_DNS || true)"
STEP05_REGION="$(state_get 05 REGION || true)"
is_none "${ALB_DNS}" && die "STEP=05 missing ALB_DNS — run infra/05-alb.sh first."

if ! is_none "${STEP05_REGION}"; then
  REGION="${STEP05_REGION}"
  export AWS_DEFAULT_REGION="${REGION}"
fi

BUCKET="${PROJECT}-${ACCOUNT_ID}-frontend"
API_BASE_URL="http://${ALB_DNS}"
SITE_URL="$(website_endpoint "${BUCKET}" "${REGION}")"

log "tradeflow Step ${STEP}: frontend → S3 website hosting (no CloudFront)"
info "Account:  ${ACCOUNT_ID}"
info "Region:   ${REGION}"
info "Profile:  ${AWS_PROFILE:-<default>}"
info "ALB DNS:  ${ALB_DNS}"
info "VITE_API_URL (build-time): ${API_BASE_URL}"
info "Bucket:   ${BUCKET}"
echo
log "LIMITATION: no HTTPS on frontend or API — credentials travel over plain HTTP."
info "OK for a brief demo + teardown; not for real user data."
echo

log "Build React SPA"
info "npm ci (or npm install) + npm run build with VITE_API_URL"
(
  cd "${FRONTEND_DIR}"
  if [[ -f package-lock.json ]]; then
    npm ci
  else
    npm install
  fi
  VITE_API_URL="${API_BASE_URL}" npm run build
)
[[ -f "${DIST_DIR}/index.html" ]] || die "Build failed — ${DIST_DIR}/index.html missing"

log "S3 bucket (static website hosting)"
if aws s3api head-bucket --bucket "${BUCKET}" 2>/dev/null; then
  skip "Bucket already exists: ${BUCKET}"
else
  if [[ "${REGION}" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}" >/dev/null
  else
    aws s3api create-bucket \
      --bucket "${BUCKET}" \
      --region "${REGION}" \
      --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
  fi
  info "Created s3://${BUCKET}"
fi

aws s3api put-bucket-tagging \
  --bucket "${BUCKET}" \
  --tagging "TagSet=[{Key=Project,Value=${PROJECT}},{Key=Step,Value=${STEP}},{Key=Name,Value=${BUCKET}}]" >/dev/null

# Website hosting needs public GetObject — disable block-public-access for THIS bucket only
aws s3api put-public-access-block \
  --bucket "${BUCKET}" \
  --public-access-block-configuration \
  "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false" >/dev/null
info "Block public access disabled for ${BUCKET} only (required for website hosting)"

# Ownership controls: bucket-owner-enforced works with public bucket policy (no ACLs)
aws s3api put-bucket-ownership-controls \
  --bucket "${BUCKET}" \
  --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerEnforced}]' >/dev/null 2>&1 || true

BUCKET_POLICY="$(python3 -c "
import json
bucket='${BUCKET}'
print(json.dumps({
  'Version': '2012-10-17',
  'Statement': [{
    'Sid': 'PublicReadGetObjectOnly',
    'Effect': 'Allow',
    'Principal': '*',
    'Action': 's3:GetObject',
    'Resource': f'arn:aws:s3:::{bucket}/*',
  }],
}))
")"
aws s3api put-bucket-policy --bucket "${BUCKET}" --policy "${BUCKET_POLICY}" >/dev/null
info "Bucket policy: s3:GetObject only for Principal *"

# Actual S3 website hosting feature (index + error → index.html for SPA routes)
aws s3api put-bucket-website \
  --bucket "${BUCKET}" \
  --website-configuration '{
    "IndexDocument": {"Suffix": "index.html"},
    "ErrorDocument": {"Key": "index.html"}
  }' >/dev/null
info "Website hosting enabled (index.html / error → index.html)"

log "Upload dist/ → s3://${BUCKET}/"
aws s3 sync "${DIST_DIR}/" "s3://${BUCKET}/" --delete
# Short-cache HTML shell so redeploys show up (bash 3.2–safe; no process substitution)
find "${DIST_DIR}" -type f -name '*.html' -print | while IFS= read -r html; do
  key="${html#"${DIST_DIR}/"}"
  aws s3 cp "${html}" "s3://${BUCKET}/${key}" \
    --cache-control "public,max-age=0,must-revalidate" \
    --content-type "text/html" >/dev/null
done
info "Sync complete"

log "Summary"
echo
info "Website URL:  ${SITE_URL}"
info "API base:     ${API_BASE_URL}  (baked into the build via VITE_API_URL)"
info "Bucket:       s3://${BUCKET}"
info "CloudFront:   skipped (intentional — see script header)"
echo
log "LIMITATION (again): no HTTPS anywhere. Login/register send credentials over HTTP."
info "Fine for short demos with infra/07-teardown.sh; not for real users."
echo
log "CORS reminder:"
info "API currently allow_origins=[http://localhost:5173] only."
info "Browser calls from ${SITE_URL} will fail CORS until you add that origin"
info "(or temporarily allow the S3 website origin) in backend/api/main.py, then rebuild/push ECS."
echo
log "Manual test:"
info "Open ${SITE_URL} — SPA should load even if backend is down."
info "With steps 1–5 up: UI should fetch data via the ALB."

TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
STATE_LINE="STEP=${STEP} TIMESTAMP=${TIMESTAMP} FRONTEND_BUCKET=${BUCKET} WEBSITE_URL=${SITE_URL} VITE_API_URL=${API_BASE_URL} ALB_DNS=${ALB_DNS} REGION=${REGION} ACCOUNT_ID=${ACCOUNT_ID}"

if [[ -f "${STATE_FILE}" ]] && grep -q '^STEP=06 ' "${STATE_FILE}" 2>/dev/null; then
  TMP="$(mktemp)"
  grep -v '^STEP=06 ' "${STATE_FILE}" > "${TMP}" || true
  echo "${STATE_LINE}" >> "${TMP}"
  mv "${TMP}" "${STATE_FILE}"
  info "Updated Step ${STEP} entry in ${STATE_FILE}"
else
  echo "${STATE_LINE}" >> "${STATE_FILE}"
  info "Appended Step ${STEP} entry to ${STATE_FILE}"
fi

log "Done."
