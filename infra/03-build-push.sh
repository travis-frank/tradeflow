# Build and push tradeflow-api to ECR (Step 03). Shared by api/worker/beat.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STATE_FILE="${SCRIPT_DIR}/.deployed-steps"
STEP="03"
PROJECT="tradeflow"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${REGION}"

ECR_REPO_NAME="${PROJECT}-api"
DOCKERFILE="${SCRIPT_DIR}/docker/Dockerfile.api"
BUILD_CONTEXT="${REPO_ROOT}/backend"
# Fargate is amd64; force this so Apple Silicon builds still run on ECS
PLATFORM="${PLATFORM:-linux/amd64}"
SCAN_WAIT_SECONDS="${SCAN_WAIT_SECONDS:-300}"

SCAN_ALLOWLIST="${SCAN_ALLOWLIST:-CVE-2026-85091}"

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

repo_exists() {
  aws ecr describe-repositories --repository-names "${ECR_REPO_NAME}" >/dev/null 2>&1
}

image_tag_exists() {
  local tag="$1"
  aws ecr describe-images \
    --repository-name "${ECR_REPO_NAME}" \
    --image-ids "imageTag=${tag}" >/dev/null 2>&1
}

require_cmd aws
require_cmd docker
require_cmd git
require_cmd python3

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
is_none "${ACCOUNT_ID}" && die "Unable to resolve AWS account. Check credentials / AWS_PROFILE."

[[ -f "${DOCKERFILE}" ]] || die "Dockerfile not found: ${DOCKERFILE}"
[[ -d "${BUILD_CONTEXT}" ]] || die "Build context not found: ${BUILD_CONTEXT}"

GIT_SHA="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
if [[ -n "$(git -C "${REPO_ROOT}" status --porcelain 2>/dev/null || true)" ]]; then
  info "Warning: working tree is dirty; image tag is still HEAD (${GIT_SHA})"
fi

ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_URI="${ECR_URI}/${ECR_REPO_NAME}:${GIT_SHA}"

log "tradeflow Step ${STEP}: ECR build + push"
info "Account:  ${ACCOUNT_ID}"
info "Region:   ${REGION}"
info "Profile:  ${AWS_PROFILE:-<default>}"
info "Repo:     ${ECR_REPO_NAME}"
info "Tag:      ${GIT_SHA}"
info "Platform: ${PLATFORM}"
echo

log "ECR repository"
if repo_exists; then
  skip "Repository already exists: ${ECR_REPO_NAME}"
  # Enforce expected settings on re-run
  aws ecr put-image-scanning-configuration \
    --repository-name "${ECR_REPO_NAME}" \
    --image-scanning-configuration scanOnPush=true >/dev/null
  aws ecr put-image-tag-mutability \
    --repository-name "${ECR_REPO_NAME}" \
    --image-tag-mutability IMMUTABLE >/dev/null
else
  aws ecr create-repository \
    --repository-name "${ECR_REPO_NAME}" \
    --image-scanning-configuration scanOnPush=true \
    --image-tag-mutability IMMUTABLE \
    --encryption-configuration encryptionType=AES256 \
    --tags "Key=Project,Value=${PROJECT}" "Key=Step,Value=${STEP}" "Key=Name,Value=${ECR_REPO_NAME}" \
    >/dev/null
  info "Created ${ECR_REPO_NAME} (scanOnPush=true, imageTagMutability=IMMUTABLE)"
fi

REPO_ARN="$(aws ecr describe-repositories \
  --repository-names "${ECR_REPO_NAME}" \
  --query 'repositories[0].repositoryArn' --output text)"

log "Docker build + push"
if image_tag_exists "${GIT_SHA}"; then
  skip "Image tag ${GIT_SHA} already in ECR (IMMUTABLE — rebuild needs a new commit)"
else
  info "Logging in to ${ECR_URI}"
  aws ecr get-login-password --region "${REGION}" \
    | docker login --username AWS --password-stdin "${ECR_URI}" >/dev/null

  info "Building ${IMAGE_URI}"
  docker build \
    --platform "${PLATFORM}" \
    --build-arg INSTALL_DEV=false \
    -f "${DOCKERFILE}" \
    -t "${IMAGE_URI}" \
    "${BUILD_CONTEXT}"

  info "Pushing ${IMAGE_URI}"
  docker push "${IMAGE_URI}"
fi

# Poll basic scan until COMPLETE (scanOnPush starts it after push)
log "Image scan (fail on CRITICAL / HIGH)"
DEADLINE=$((SECONDS + SCAN_WAIT_SECONDS))
SCAN_STATUS=""
while (( SECONDS < DEADLINE )); do
  SCAN_STATUS="$(aws_q ecr describe-image-scan-findings \
    --repository-name "${ECR_REPO_NAME}" \
    --image-id "imageTag=${GIT_SHA}" \
    --query 'imageScanStatus.status')"
  case "${SCAN_STATUS}" in
    COMPLETE) break ;;
    FAILED)
      die "ECR image scan failed for ${ECR_REPO_NAME}:${GIT_SHA}"
      ;;
    None|"")
      info "Waiting for scan to start..."
      ;;
    *)
      info "Scan status: ${SCAN_STATUS}"
      ;;
  esac
  sleep 5
done

if [[ "${SCAN_STATUS}" != "COMPLETE" ]]; then
  die "Timed out after ${SCAN_WAIT_SECONDS}s waiting for image scan (last status: ${SCAN_STATUS:-unknown})"
fi

FINDINGS_JSON="$(aws ecr describe-image-scan-findings \
  --repository-name "${ECR_REPO_NAME}" \
  --image-id "imageTag=${GIT_SHA}" \
  --output json)"

# Fail on CRITICAL/HIGH unless CVE is in SCAN_ALLOWLIST (space/comma-separated)
printf '%s' "${FINDINGS_JSON}" | SCAN_ALLOWLIST="${SCAN_ALLOWLIST}" python3 -c '
import json, os, sys

allow = {c.strip() for c in os.environ.get("SCAN_ALLOWLIST", "").replace(",", " ").split() if c.strip()}
data = json.load(sys.stdin)
findings = data.get("imageScanFindings", {}).get("findings", []) or []
high_crit = [f for f in findings if f.get("severity") in ("CRITICAL", "HIGH")]
allowed = [f for f in high_crit if f.get("name") in allow]
bad = [f for f in high_crit if f.get("name") not in allow]

counts = data.get("imageScanFindings", {}).get("findingSeverityCounts", {}) or {}
print(f"    Severity counts: {json.dumps(counts, sort_keys=True)}")
if allow:
    print("    Allowlist: " + ", ".join(sorted(allow)))

def _print_finding(f, prefix="    -"):
    name = f.get("name", "?")
    sev = f.get("severity", "?")
    uri = f.get("uri", "")
    attrs = {a["key"]: a["value"] for a in f.get("attributes", []) if "key" in a}
    pkg = attrs.get("package_name", attrs.get("packageName", ""))
    ver = attrs.get("package_version", attrs.get("packageVersion", ""))
    pkg_s = f"{pkg}@{ver}" if pkg else ""
    extra = f" ({pkg_s})" if pkg_s else ""
    print(f"{prefix} [{sev}] {name}{extra}")
    if uri:
        print(f"      {uri}")

if allowed:
    print(f"    Accepted (allowlisted) {len(allowed)} finding(s):")
    for f in sorted(allowed, key=lambda x: (x.get("severity", ""), x.get("name", ""))):
        _print_finding(f)

if not bad:
    if high_crit:
        print("    No non-allowlisted CRITICAL/HIGH findings.")
    else:
        print("    No CRITICAL or HIGH findings.")
    sys.exit(0)

print(f"    Found {len(bad)} CRITICAL/HIGH finding(s) not on the allowlist:")
for f in sorted(bad, key=lambda x: (x.get("severity", ""), x.get("name", ""))):
    _print_finding(f)

print("ERROR: Refusing to continue with CRITICAL/HIGH vulnerabilities. Patch and rebuild.", file=sys.stderr)
sys.exit(1)
'

log "Summary"
echo
info "Repository:  ${ECR_REPO_NAME}"
info "Repository ARN: ${REPO_ARN}"
info "Image URI:   ${IMAGE_URI}"
info "Git SHA:     ${GIT_SHA}"
info "Scan:        COMPLETE (no non-allowlisted CRITICAL/HIGH)"
echo
log "Manual check:"
info "aws ecr describe-image-scan-findings --repository-name ${ECR_REPO_NAME} --image-id imageTag=${GIT_SHA}"

TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
STATE_LINE="STEP=${STEP} TIMESTAMP=${TIMESTAMP} ECR_REPO=${ECR_REPO_NAME} ECR_REPO_ARN=${REPO_ARN} IMAGE_URI=${IMAGE_URI} IMAGE_TAG=${GIT_SHA} REGION=${REGION} ACCOUNT_ID=${ACCOUNT_ID}"

if [[ -f "${STATE_FILE}" ]] && grep -q '^STEP=03 ' "${STATE_FILE}" 2>/dev/null; then
  TMP="$(mktemp)"
  grep -v '^STEP=03 ' "${STATE_FILE}" > "${TMP}" || true
  echo "${STATE_LINE}" >> "${TMP}"
  mv "${TMP}" "${STATE_FILE}"
  info "Updated Step ${STEP} entry in ${STATE_FILE}"
else
  echo "${STATE_LINE}" >> "${STATE_FILE}"
  info "Appended Step ${STEP} entry to ${STATE_FILE}"
fi

log "Done."
