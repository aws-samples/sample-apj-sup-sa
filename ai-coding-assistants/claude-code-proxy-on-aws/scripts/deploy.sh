#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────
# Claude Code Proxy on AWS — Deployment Script
# ──────────────────────────────────────────────

INFRA_DIR="$(cd "$(dirname "$0")/../infra" && pwd)"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CDK_JSON="$INFRA_DIR/cdk.json"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
prompt() { echo -en "${CYAN}▸${NC} $*"; }

# ──────────────────────────────────────────────
# 1. Check Prerequisites
# ──────────────────────────────────────────────
check_prerequisites() {
    info "Checking prerequisites..."

    command -v aws >/dev/null 2>&1 || error "AWS CLI is not installed."
    command -v cdk >/dev/null 2>&1 || error "CDK CLI is not installed. (npm install -g aws-cdk)"
    command -v docker >/dev/null 2>&1 || error "Docker is not installed."
    command -v uv >/dev/null 2>&1 || error "uv is not installed."
    command -v jq >/dev/null 2>&1 || error "jq is not installed."

    # Check Docker is running
    docker info >/dev/null 2>&1 || error "Docker daemon is not running."

    ok "All prerequisites satisfied"
}

# ──────────────────────────────────────────────
# 2. Check AWS Authentication
# ──────────────────────────────────────────────
check_aws_auth() {
    info "Checking AWS authentication..."

    local identity
    identity=$(aws sts get-caller-identity 2>/dev/null) || error "AWS authentication failed. Run 'aws configure' or 'aws sso login'."

    local account
    account=$(echo "$identity" | jq -r '.Account')

    ok "AWS Account: $account"
    export CDK_DEFAULT_ACCOUNT="$account"
}

# ──────────────────────────────────────────────
# 3. Select Region
# ──────────────────────────────────────────────
select_region() {
    local current_region
    current_region=$(jq -r '.context.region // "ap-northeast-2"' "$CDK_JSON")

    info "Current configured region: $current_region"
    prompt "Deployment region [$current_region]: "
    read -er input_region

    local region="${input_region:-$current_region}"

    if [[ "$region" != "$current_region" ]]; then
        _set_cdk_context "region" "$region"
        ok "Region changed: $region"
    fi

    export CDK_DEFAULT_REGION="$region"
    ok "Deployment region: $region"
}

# ──────────────────────────────────────────────
# 4. Select Identity Store ID
# ──────────────────────────────────────────────
select_identity_store_id() {
    local region current_id discovered_id
    region="$CDK_DEFAULT_REGION"
    current_id=$(jq -r '.context.identity_store_id // empty' "$CDK_JSON")
    DISCOVERED_IDENTITY_STORE_REGION=""
    discovered_id=$(_discover_identity_store_id "$region")

    if [[ -n "$discovered_id" && "$current_id" != "$discovered_id" ]]; then
        info "Found active Identity Store ID from AWS: $discovered_id (region: ${DISCOVERED_IDENTITY_STORE_REGION:-$region})"
    fi

    local default_id="$current_id"
    if [[ -z "$default_id" || "$default_id" == "placeholder" ]]; then
        default_id="$discovered_id"
    fi

    if [[ -n "$default_id" ]]; then
        prompt "Identity Store ID [$default_id]: "
        read -er input_id
    else
        warn "Could not automatically discover an active Identity Store ID."
        info "Manual lookup: aws sso-admin list-instances --region <region> --query 'Instances[].IdentityStoreId' --output text"
        prompt "Identity Store ID: "
        read -er input_id
    fi

    local identity_store_id="${input_id:-$default_id}"
    if [[ -z "$identity_store_id" || "$identity_store_id" == "placeholder" ]]; then
        error "Identity Store ID is required. Please enter a valid value."
    fi

    if [[ "$identity_store_id" != "$current_id" ]]; then
        _set_cdk_context "identity_store_id" "$identity_store_id"
        ok "Identity Store ID configured: $identity_store_id"
    else
        ok "Identity Store ID unchanged: $identity_store_id"
    fi

    # Identity Store region configuration
    local current_region
    current_region=$(jq -r '.context.identity_store_region // empty' "$CDK_JSON")
    local default_region="${DISCOVERED_IDENTITY_STORE_REGION:-$current_region}"
    if [[ -z "$default_region" ]]; then
        default_region="$region"
    fi

    if [[ "$default_region" != "$region" ]]; then
        prompt "Identity Store region [$default_region]: "
        read -er input_region
        local identity_store_region="${input_region:-$default_region}"
        _set_cdk_context "identity_store_region" "$identity_store_region"
        ok "Identity Store region configured: $identity_store_region"
    else
        _set_cdk_context "identity_store_region" "$region"
    fi
}

_discover_identity_store_id() {
    local region="$1"
    local search_regions=("$region" "us-east-1" "eu-west-1" "ap-southeast-1")
    for r in "${search_regions[@]}"; do
        local instances
        instances=$(aws sso-admin list-instances \
            --region "$r" \
            --query 'Instances[?Status==`ACTIVE`]' \
            --output json 2>/dev/null || true)

        if [[ -z "$instances" || "$instances" == "null" ]]; then
            continue
        fi

        local count
        count=$(echo "$instances" | jq 'length')
        if [[ "$count" -eq 0 ]]; then
            continue
        fi
        if [[ "$count" -eq 1 ]]; then
            DISCOVERED_IDENTITY_STORE_REGION="$r"
            echo "$instances" | jq -r '.[0].IdentityStoreId'
            return 0
        fi

        if [[ "$count" -gt 1 ]]; then
            DISCOVERED_IDENTITY_STORE_REGION="$r"
            warn "Multiple active IAM Identity Center instances found (region: $r)." >&2
            echo "$instances" | jq -r '
                .[]
                | "  - IdentityStoreId: \(.IdentityStoreId)\n    OwnerAccountId: \(.OwnerAccountId)\n    InstanceArn: \(.InstanceArn)"
            ' >&2
        fi
    done
}


# ──────────────────────────────────────────────
# 5. Check/Create ACM Certificate
# ──────────────────────────────────────────────
ensure_acm_certificate() {
    info "Checking ACM certificate..."

    local existing_arn
    existing_arn=$(jq -r '.context.acm_certificate_arn // empty' "$CDK_JSON")

    if [[ -n "$existing_arn" ]]; then
        ok "ACM certificate configured: ${existing_arn:0:60}..."
        return
    fi

    warn "No ACM certificate configured in cdk.json."
    echo ""
    echo "  1) Enter an existing ACM certificate ARN"
    echo "  2) Create a new ACM certificate (domain required)"
    echo "  3) Skip (deploy with HTTP only, no HTTPS)"
    echo ""
    while true; do
        prompt "Choose [1/2/3]: "
        read -er choice
        case "${choice}" in
            1)
                prompt "ACM certificate ARN: "
                read -er cert_arn
                [[ -z "$cert_arn" ]] && { warn "Certificate ARN is empty."; continue; }
                _set_cdk_context "acm_certificate_arn" "$cert_arn"
                ok "ACM certificate configured"
                break
                ;;
            2)
                _create_acm_certificate
                break
                ;;
            3)
                warn "Deploying without HTTPS. ALB will use HTTP(80) only."
                _set_cdk_context "acm_certificate_arn" ""
                export SKIP_HTTPS=true
                break
                ;;
            *)
                warn "Please choose 1, 2, or 3."
                ;;
        esac
    done
}

_create_acm_certificate() {
    prompt "Domain name (e.g., proxy.example.com): "
    read -er domain
    [[ -z "$domain" ]] && error "Domain is empty."

    local region
    region=$(jq -r '.context.region // "ap-northeast-2"' "$CDK_JSON")

    info "Requesting ACM certificate: $domain"
    local cert_arn
    cert_arn=$(aws acm request-certificate \
        --domain-name "$domain" \
        --validation-method DNS \
        --region "$region" \
        --query 'CertificateArn' \
        --output text)

    ok "Certificate request complete: $cert_arn"
    echo ""
    warn "DNS validation is required."
    info "Fetching validation records (waiting up to 30 seconds)..."

    local validation_info=""
    local attempt
    for attempt in $(seq 1 6); do
        sleep 5
        validation_info=$(aws acm describe-certificate \
            --certificate-arn "$cert_arn" \
            --region "$region" \
            --query 'Certificate.DomainValidationOptions[0].ResourceRecord' \
            --output json 2>/dev/null || true)
        if [[ -n "$validation_info" && "$validation_info" != "null" ]]; then
            break
        fi
        info "Waiting for validation records... (${attempt}/6)"
    done

    if [[ -n "$validation_info" && "$validation_info" != "null" ]]; then
        local cname_name cname_value
        cname_name=$(echo "$validation_info" | jq -r '.Name')
        cname_value=$(echo "$validation_info" | jq -r '.Value')
        echo ""
        echo -e "  ${CYAN}Add the following CNAME record to your DNS:${NC}"
        echo -e "  Name:  ${GREEN}$cname_name${NC}"
        echo -e "  Value: ${GREEN}$cname_value${NC}"
        echo ""
    else
        warn "Could not fetch validation records. Check the AWS console: $cert_arn"
    fi

    prompt "Press Enter after adding the DNS record to wait for validation..."
    read -er

    info "Waiting for certificate validation (up to 5 minutes)..."
    if aws acm wait certificate-validated \
        --certificate-arn "$cert_arn" \
        --region "$region" 2>/dev/null; then
        ok "Certificate validated!"
    else
        warn "Validation wait timed out. Deployment will continue, but HTTPS will not work until the certificate is validated."
    fi

    _set_cdk_context "acm_certificate_arn" "$cert_arn"
    ok "Certificate ARN saved to cdk.json"
}

_set_cdk_context() {
    local key="$1" value="$2"
    local tmp
    tmp=$(mktemp)
    jq --arg k "$key" --arg v "$value" '.context[$k] = $v' "$CDK_JSON" > "$tmp"
    mv "$tmp" "$CDK_JSON"
}

# ──────────────────────────────────────────────
# 6. CDK Bootstrap
# ──────────────────────────────────────────────
ensure_bootstrap() {
    info "Checking CDK bootstrap..."

    local account region
    account="$CDK_DEFAULT_ACCOUNT"
    region="$CDK_DEFAULT_REGION"
    local min_version=30

    local stack_status
    stack_status=$(aws cloudformation describe-stacks \
        --stack-name CDKToolkit \
        --region "$region" \
        --query 'Stacks[0].StackStatus' \
        --output text 2>/dev/null || echo "NOT_FOUND")

    local current_version=0
    if [[ "$stack_status" != "NOT_FOUND" ]]; then
        current_version=$(aws ssm get-parameter \
            --name "/cdk-bootstrap/hnb659fds/version" \
            --region "$region" \
            --query 'Parameter.Value' \
            --output text 2>/dev/null || echo "0")
    fi

    if [[ "$stack_status" == "NOT_FOUND" || "$stack_status" == *"ROLLBACK"* || "$current_version" -lt "$min_version" ]]; then
        info "Running CDK bootstrap... (current version: $current_version, required: $min_version+)"
        cd "$INFRA_DIR"
        cdk bootstrap "aws://$account/$region"
        ok "CDK bootstrap complete"
    else
        ok "CDK bootstrap already done (version: $current_version)"
    fi
}

# ──────────────────────────────────────────────
# 7. Install Dependencies
# ──────────────────────────────────────────────
install_dependencies() {
    info "Installing Python dependencies..."
    cd "$PROJECT_ROOT"
    uv sync --group dev --group infra
    ok "Dependencies installed"
}

# ──────────────────────────────────────────────
# 8. CDK Deploy
# ──────────────────────────────────────────────
deploy_stacks() {
    cd "$INFRA_DIR"

    info "Reviewing changes..."
    echo ""
    cdk diff 2>&1 || true
    echo ""

    while true; do
        prompt "Deploy the above changes? [y/N]: "
        read -er confirm
        case "${confirm}" in
            y|Y) break ;;
            n|N|"") info "Deployment cancelled."; exit 0 ;;
            *) warn "Please enter y or N." ;;
        esac
    done

    info "Deploying CDK stacks... (includes Docker build, may take several minutes)"
    echo ""

    cdk deploy --all \
        --require-approval never \
        --no-path-metadata \
        --outputs-file "$PROJECT_ROOT/cdk-outputs.json"

    ok "Deployment complete!"
    echo ""

    if [[ -f "$PROJECT_ROOT/cdk-outputs.json" ]]; then
        info "Deployment outputs:"
        jq '.' "$PROJECT_ROOT/cdk-outputs.json"
    fi
}

# ──────────────────────────────────────────────
# 9. CDK Destroy
# ──────────────────────────────────────────────
destroy_stacks() {
    cd "$INFRA_DIR"

    warn "This will delete all CDK stacks."
    echo ""
    echo "Stacks to be deleted:"
    cdk list 2>/dev/null || true
    echo ""

    while true; do
        prompt "Are you sure you want to delete all stacks? [y/N]: "
        read -er confirm
        case "${confirm}" in
            y|Y) break ;;
            n|N|"") info "Deletion cancelled."; exit 0 ;;
            *) warn "Please enter y or N." ;;
        esac
    done

    info "Deleting CDK stacks..."
    cdk destroy --all --force

    ok "All stacks have been deleted!"

    # Delete cdk-outputs.json
    [[ -f "$PROJECT_ROOT/cdk-outputs.json" ]] && rm -f "$PROJECT_ROOT/cdk-outputs.json"
}

# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
usage() {
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  deploy   Deploy (default)"
    echo "  destroy  Delete all stacks"
    echo ""
}

main() {
    if [[ "${1:-}" =~ ^(-h|--help|help)$ ]]; then usage; exit 0; fi

    local command="${1:-deploy}"

    echo ""
    echo -e "${CYAN} ██████╗██╗      █████╗ ██╗   ██╗██████╗ ███████╗     ██████╗ ██████╗ ██████╗ ███████╗${NC}"
    echo -e "${CYAN}██╔════╝██║     ██╔══██╗██║   ██║██╔══██╗██╔════╝    ██╔════╝██╔═══██╗██╔══██╗██╔════╝${NC}"
    echo -e "${CYAN}██║     ██║     ███████║██║   ██║██║  ██║█████╗      ██║     ██║   ██║██║  ██║█████╗  ${NC}"
    echo -e "${CYAN}██║     ██║     ██╔══██║██║   ██║██║  ██║██╔══╝      ██║     ██║   ██║██║  ██║██╔══╝  ${NC}"
    echo -e "${CYAN}╚██████╗███████╗██║  ██║╚██████╔╝██████╔╝███████╗    ╚██████╗╚██████╔╝██████╔╝███████╗${NC}"
    echo -e "${CYAN} ╚═════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝     ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝${NC}"
    echo -e "${CYAN}                                                                                      ${NC}"
    echo -e "${CYAN} ██████╗ ███╗   ██╗    ██████╗ ███████╗██████╗ ██████╗  ██████╗  ██████╗██╗  ██╗      ${NC}"
    echo -e "${CYAN}██╔═══██╗████╗  ██║    ██╔══██╗██╔════╝██╔══██╗██╔══██╗██╔═══██╗██╔════╝██║ ██╔╝      ${NC}"
    echo -e "${CYAN}██║   ██║██╔██╗ ██║    ██████╔╝█████╗  ██║  ██║██████╔╝██║   ██║██║     █████╔╝       ${NC}"
    echo -e "${CYAN}██║   ██║██║╚██╗██║    ██╔══██╗██╔══╝  ██║  ██║██╔══██╗██║   ██║██║     ██╔═██╗       ${NC}"
    echo -e "${CYAN}╚██████╔╝██║ ╚████║    ██████╔╝███████╗██████╔╝██║  ██║╚██████╔╝╚██████╗██║  ██╗      ${NC}"
    echo -e "${CYAN} ╚═════╝ ╚═╝  ╚═══╝    ╚═════╝ ╚══════╝╚═════╝ ╚═╝  ╚═╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝      ${NC}"
    echo ""

    case "$command" in
        deploy)
            check_prerequisites
            check_aws_auth
            select_region
            select_identity_store_id
            install_dependencies
            ensure_acm_certificate
            ensure_bootstrap
            deploy_stacks
            echo ""
            ok "All deployment steps complete!"
            ;;
        destroy)
            check_prerequisites
            check_aws_auth
            select_region
            destroy_stacks
            ;;
        -h|--help|help)
            usage
            exit 0
            ;;
        *)
            error "Unknown command: $command"
            ;;
    esac
    echo ""
}

main "$@"

