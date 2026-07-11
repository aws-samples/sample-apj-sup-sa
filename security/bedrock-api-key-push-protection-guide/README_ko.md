# Amazon Bedrock Long-term API Key Push Protection 가이드

> **목적**: GitHub 레포지토리에 Amazon Bedrock Long-term API Key (`ABSK...`로 시작)가 실수로 push 되는 것을 차단하는 방법을 GitHub 구독 플랜별로 안내합니다.
>
> **대상**: AWS 고객사 (GitHub를 사용하는 개발팀)  
> **작성일**: 2026-06-08
>
> ⚠️ **주의**: 본 가이드의 설정을 프로덕션 환경에 적용하기 전에 반드시 테스트 환경에서 충분히 검증한 후 적용하세요. 특히 Custom Pattern과 Push Protection은 Dry Run을 통해 오탐(false positive) 여부를 확인한 뒤 활성화하는 것을 권장합니다.

---

## 1. 배경

### 1.1 Amazon Bedrock API Key란?

2025년 7월, AWS는 Amazon Bedrock API Keys를 출시했습니다. 기존 IAM Access Key/Secret Key 대신 **Bearer Token 방식**으로 Bedrock을 호출할 수 있는 전용 키입니다.

| 유형 | 접두사 | 유효기간 | 용도 |
|------|--------|----------|------|
| **Long-term** | `ABSK...` | 설정된 만료일까지 (최대 100년) | 탐색/개발용 |

### 1.2 문제점

현재(2026년 6월 기준) GitHub의 [Supported Secret Scanning Patterns](https://docs.github.com/en/code-security/reference/secret-security/supported-secret-scanning-patterns)에는 **Bedrock API Key 패턴이 포함되어 있지 않습니다**.

따라서 Bedrock API Key가 코드에 하드코딩된 채 push되어도 GitHub이 자동으로 감지하지 못합니다.

### 1.3 탐지할 정규식 패턴

```regex
# Long-term Bedrock API Key
ABSK[A-Za-z0-9+/=]+
```

---

## 2. 플랜별 구현 방법 요약

| GitHub 플랜 | 추천 방법 | Push 차단 위치 | 비용 |
|-------------|----------|--------------|------|
| **Free** | Pre-commit Hook (`git-secrets`) | 개발자 로컬 | 무료 |
| **Team** | GitHub Secret Protection + Custom Pattern + Push Protection | GitHub 서버 | Active committer 기반 과금 |
| **Enterprise Cloud** | Org/Enterprise 레벨 Custom Pattern | GitHub 서버 | Active committer 기반 과금 (GHAS 포함 시 추가비용 없음) |

---

## 3. GitHub Free 플랜 — Pre-commit Hook 방식

> **핵심**: 서버 차단 불가 → 개발자 로컬에서 커밋 전에 차단

### 3.1 AWS git-secrets 설치

#### macOS

```bash
brew install git-secrets
```

#### Linux (Ubuntu/Debian)

```bash
git clone https://github.com/awslabs/git-secrets.git
cd git-secrets
sudo make install
```

#### Windows

```powershell
git clone https://github.com/awslabs/git-secrets.git
cd git-secrets
./install.ps1
```

#### 설치 확인

```bash
git secrets --version
# git-secrets 1.3.0
```

---

### 3.2 레포지토리에 git-secrets 적용

#### Step 1: 레포지토리 이동 및 초기화

```bash
cd /path/to/your-repo
git secrets --install
```

**실행 결과:**
```
✓ Installed commit-msg hook to .git/hooks/commit-msg
✓ Installed pre-commit hook to .git/hooks/pre-commit
✓ Installed prepare-commit-msg hook to .git/hooks/prepare-commit-msg
```

#### Step 2: Bedrock API Key 패턴 등록

```bash
# Long-term Bedrock API Key 패턴
git secrets --add 'ABSK[A-Za-z0-9+/=]+'
```

**등록 확인:**
```bash
git secrets --list
```

**출력 예시:**
```
secrets.patterns ABSK[A-Za-z0-9+/=]+
```

---

#### Step 3: 동작 테스트

```bash
# 테스트 파일 생성 (가짜 키)
echo 'API_KEY="ABSKQmVkcm9ja0FQSUtleS1hYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ejAxMjM0NTY3ODk="' > test_secret.txt

# 스테이징
git add test_secret.txt

# 커밋 시도
git commit -m "test commit"
```

**차단 결과:**
```
test_secret.txt:1:API_KEY="ABSKQmVkcm9ja0FQSUtleS1hYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ejAxMjM0NTY3ODk="

[ERROR] Matched one or more prohibited patterns

Possible mitigations:
- Mark false positives as allowed using: git config --add secrets.allowed ...
- Mark the commit as allowed using: git config --add secrets.allowed ...
- List your configured patterns: git secrets --list
- Undo the last commit: git reset HEAD~1

ERROR: commit is not allowed
```

> ✅ 커밋이 차단되었습니다!

---

#### Step 4: 테스트 파일 정리

```bash
git reset HEAD test_secret.txt
rm test_secret.txt
```

---

### 3.3 글로벌 설정 (모든 레포에 자동 적용)

개발자가 새 레포를 clone할 때마다 자동으로 git-secrets이 적용되도록 설정합니다.

```bash
# 1. 글로벌 패턴 등록
git secrets --add --global 'ABSK[A-Za-z0-9+/=]+'

# 2. Git 템플릿 디렉터리에 hooks 설치
git secrets --install ~/.git-templates/git-secrets

# 3. 글로벌 템플릿 설정
git config --global init.templateDir ~/.git-templates/git-secrets
```

> 📌 이제 `git clone` 또는 `git init`으로 생성하는 모든 레포에 자동으로 git-secrets hook이 설치됩니다.

---

### 3.4 기존 레포 일괄 스캔

이미 존재하는 코드에서 Bedrock API Key를 찾으려면:

```bash
# 현재 코드 스캔
git secrets --scan

# Git 히스토리 전체 스캔
git secrets --scan-history
```

---

### 3.5 GitHub Actions 보조 알림 (선택사항)

Pre-commit hook을 우회하는 경우를 대비하여 GitHub Actions로 이중 검사를 설정합니다.

#### `.github/workflows/bedrock-key-scan.yml` 생성

```yaml
name: Bedrock API Key Scan

on:
  push:
    branches: ['**']
  pull_request:
    branches: ['**']

jobs:
  scan-secrets:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Scan for Bedrock API Keys
        run: |
          echo "🔍 Scanning for Amazon Bedrock API Keys..."
          
          FOUND=0
          
          # Long-term key pattern (ABSK...)
          if grep -rP 'ABSK[A-Za-z0-9+/=]+' \
            --include='*.py' --include='*.js' --include='*.ts' \
            --include='*.java' --include='*.yml' --include='*.yaml' \
            --include='*.json' --include='*.env' --include='*.tf' \
            --include='*.sh' --include='*.go' --include='*.rb' \
            . 2>/dev/null; then
            echo "::error::⚠️ Amazon Bedrock Long-term API Key detected!"
            FOUND=1
          fi
          
          if [ $FOUND -eq 1 ]; then
            echo ""
            echo "❌ Bedrock API Key가 코드에서 발견되었습니다."
            echo "   즉시 해당 키를 비활성화(Deactivate)하고 새 키를 발급하세요."
            echo "   참고: https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html"
            exit 1
          fi
          
          echo "✅ No Bedrock API Keys found."
```

> ⚠️ **주의**: GitHub Actions는 push *이후* 실행됩니다. 키가 이미 Git 히스토리에 기록되므로, 탐지 시 즉시 키를 **비활성화(Deactivate)**해야 합니다.

---

## 4. GitHub Team 플랜 — GitHub Secret Protection + Push Protection

> **핵심**: GitHub 서버에서 push 자체를 차단 (개발자 로컬 설정 불필요)

### 4.1 사전 요구사항

- GitHub Team 플랜 이상
- **GitHub Secret Protection** 라이선스 활성화

### 4.2 라이선스 및 과금 구조

GitHub Advanced Security는 2개의 독립 SKU로 구분됩니다:

| SKU | 포함 기능 | 용도 |
|-----|----------|------|
| **GitHub Secret Protection** | Secret scanning, Push protection, Custom patterns | 시크릿 유출 탐지/차단 |
| **GitHub Code Security** | Code scanning, Dependabot premium, Dependency review | 취약점 탐지/수정 |

Bedrock API Key push 차단에는 **Secret Protection만 필요**합니다 (Code Security는 별도).

**과금 방식:**
- **Active committer 기반**: Secret Protection이 활성화된 레포에 최근 90일 내 커밋한 고유 사용자 수로 측정
- 한 사용자가 여러 레포에 커밋해도 **1 라이선스**만 소모
- 90일간 커밋이 없으면 자동으로 라이선스 해제
- Public 레포에서는 Secret scanning과 Push protection을 **무료**로 사용 가능 (Private 레포에서만 유료)
- 정확한 단가는 [GitHub Advanced Security pricing](https://github.com/enterprise/advanced-security#pricing) 참조

---

### 4.3 Step 1: GitHub Secret Protection 활성화

GitHub Team 플랜에서는 Organization의 **Security configurations**를 통해 Secret Protection을 활성화합니다.

**경로:** Organization → Settings → (Security 섹션) Advanced Security → Configurations

**단계:**

1. GitHub에서 Organization 페이지 이동
2. 상단 탭에서 **Settings** 클릭
3. 좌측 사이드바 → Security 섹션 → **Advanced Security** 드롭다운 클릭
4. **Configurations** 선택
5. 기존 Configuration을 편집하거나 **New configuration** 생성
6. "Secret Protection" 항목을 **Enabled**로 설정
7. 적용 대상 레포지토리를 선택하여 Configuration 적용

---

### 4.4 Step 2: Custom Pattern 등록

**경로:** Organization → Settings → (Security 섹션) Advanced Security → Global settings → Custom patterns → New pattern

**단계:**

1. 좌측 사이드바 → Security 섹션 → **Advanced Security** 드롭다운 클릭
2. **Global settings** 선택
3. "Custom patterns" 섹션에서 **New pattern** 클릭
4. 다음 값 입력:

| 필드 | 입력값 |
|------|--------|
| Pattern name | `Amazon Bedrock Long-term API Key` |
| Secret format | `ABSK[A-Za-z0-9+/=]+` |
| Test string | `ABSKQmVkcm9ja0FQSUtleS1hYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ejAxMjM0NTY3ODk=` |

5. Test string 입력 후 "1 match found" 표시 확인
6. **Save and dry run** 클릭 (권장: Dry Run으로 오탐 확인)
   - 전체 Organization 또는 선택한 레포지토리(최대 10개)에서 Dry Run 수행 가능
7. Dry Run 결과 확인 후 → **Publish pattern** 클릭

---

### 4.5 Step 3: Push Protection 활성화

**경로:** Organization → Settings → Advanced Security → Global settings → Custom patterns → [패턴 선택] → Pattern Configurations → Custom 탭

**단계:**

1. 4.4에서 생성한 Custom Pattern(`Amazon Bedrock Long-term API Key`) 클릭
2. 하단의 **Pattern Configurations** 링크 클릭
3. **Custom** 탭 클릭
4. Organization setting 컬럼에서 **Enabled**로 선택

> **참고**: Push protection 활성화 옵션은 패턴을 Publish한 후에만 표시됩니다. 또한 대상 레포지토리에 Secret scanning as push protection이 활성화되어 있어야 합니다.

---

### 4.6 Step 4: Push 차단 동작 확인

개발자가 ABSK 키를 포함한 코드를 push하면:

```bash
$ git push origin main

remote: error: GH013: Push cannot contain secrets
remote: 
remote: ─── Amazon Bedrock Long-term API Key ──────────────
remote:  locations:
remote:    - commit: a1b2c3d
remote:      path: config/settings.py:15
remote: 
remote: (?) To push, remove the secret from your commits.
remote:     https://docs.github.com/code-security/secret-scanning
remote: 
To https://github.com/your-org/your-repo.git
 ! [remote rejected] main -> main (push rule violation)
error: failed to push some refs
```

> Push가 서버에서 차단됩니다!

---

## 5. GitHub Enterprise Cloud — Org/Enterprise 전체 적용

> **핵심**: 한 번 설정으로 모든 Organization의 모든 레포에 자동 적용

### 5.1 Enterprise 레벨 설정

**경로:** Enterprise → Settings → Advanced Security

**단계:**

1. `https://github.com/enterprises/YOUR-ENTERPRISE/settings/security_analysis` 접속
2. 좌측 메뉴에서 **Advanced Security** 클릭
3. **Configurations** 섹션에서 Security Configuration을 생성하거나 기존 Configuration 편집
   - Secret Protection을 **Enabled**로 설정
   - **Update configuration** 버튼 클릭
   - 필요 시 **Enforced**로 설정하여 Organization에서 비활성화 불가하도록 강제
4. **Apply to** 버튼으로 대상 레포지토리에 적용

---

### 5.2 Custom Pattern 등록 및 Push Protection 활성화

Team 플랜과 동일한 방식으로 설정합니다. Section 4.4, 4.5를 참조하세요.

> Enterprise 레벨에서 설정한 패턴은 모든 Organization의 모든 레포에 자동 적용됩니다.

> **참고**: 2026년 7월부터 Enterprise Cloud 고객은 [Public Monitoring](https://github.blog/changelog/2026-07-01-secret-scanning-public-monitoring-for-enterprises/) 기능을 통해 자사 레포 외부 공개 영역(personal fork, 외부 OSS, public issue/PR 등)에서의 시크릿 유출도 감지할 수 있습니다. 단, Custom Pattern의 Public Monitoring 적용 여부는 별도 확인이 필요합니다.

---

## 6. Dry Run 및 Bypass 관리 (Team/Enterprise 공통)

### 6.1 Dry Run 활용 (오탐 확인)

패턴 적용 전에 **Dry Run**으로 기존 레포에서 얼마나 매칭되는지 확인할 수 있습니다.

- Dry Run 실행 시 기존 레포를 스캔하여 매칭 건수를 표시
- 오탐(false positive)이 있는지 사전 확인 가능
- 발견된 기존 키들은 즉시 **비활성화(Deactivate)**하고 새 키를 발급해야 합니다

### 6.2 Bypass 관리

특정 상황에서 push protection을 우회해야 할 때, Security Configuration에서 Bypass privileges를 설정할 수 있습니다:

- **Bypass 허용 대상**: Specific actors (특정 역할/팀) 선택
- **Exempt 옵션**: 신뢰할 수 있는 자동화에 대해 push protection을 완전 면제 가능 (주의 필요)
- 선택 가능한 사유: "It's used in tests" / "I'll fix it later" / "It's a false positive"

> 모든 bypass는 **Audit Log**에 기록되며, 보안팀이 추적할 수 있습니다.

---

## 7. 보조 도구: .gitignore 설정

모든 플랜에서 공통으로 적용해야 할 기본 보호 설정입니다.

### `.gitignore` 예시

```gitignore
# ===== Environment & Secret Files =====
.env
.env.*
!.env.example

# AWS credentials
.aws/credentials
**/credentials

# Bedrock config files
bedrock-config.yaml
bedrock-config.json

# ===== IDE/Editor =====
.idea/
.vscode/settings.json
*.swp

# ===== OS =====
.DS_Store
Thumbs.db
```

### `.env.example` 템플릿

```bash
# Amazon Bedrock Configuration
# ⚠️ DO NOT commit actual keys! Use environment variables.
AWS_BEARER_TOKEN_BEDROCK=your-bedrock-api-key-here
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-6
```

---

## 8. 키 노출 시 대응 절차

만약 Bedrock API Key가 노출된 경우:

### 8.1 즉시 조치 (5분 이내)

1. **키 비활성화**
   - AWS Console → IAM → Security credentials → API keys for Amazon Bedrock → **Deactivate**
   - 또는 CLI:
   ```bash
   aws iam update-service-specific-credential \
     --service-specific-credential-id <ID> \
     --status Inactive
   ```

2. **CloudTrail 확인**
   - `"additionalEventData.callWithBearerToken": true` 필터로 비정상 호출 확인

3. **새 키 발급 및 배포**

4. **Git 히스토리 정리**
   ```bash
   # BFG Repo-Cleaner 사용 (권장)
   bfg --replace-text passwords.txt your-repo.git
   
   # passwords.txt 내용:
   # ABSKQmVkcm9ja0FQSUtleS1... ==> [REMOVED]
   ```

---

## 9. 권장 아키텍처 (Defense in Depth)

가장 안전한 구성은 **다중 레이어 방어**입니다:

| Layer | 위치 | 도구 | 역할 |
|-------|------|------|------|
| **1** | 개발자 로컬 | git-secrets pre-commit hook | 커밋 단계에서 즉시 차단 |
| **2** | GitHub 서버 | Custom Pattern + Push Protection | Push 단계에서 서버 차단 (Team/Enterprise) |
| **3** | CI/CD | GitHub Actions Secret Scan Workflow | Push 후 감지 및 알림 |
| **4** | AWS 모니터링 | CloudTrail + GuardDuty | 비정상 API Key 사용 실시간 탐지 |

---

## 10. 팀 배포 체크리스트

### Free 플랜 팀

- [ ] git-secrets 설치 가이드를 팀 wiki에 공유
- [ ] 글로벌 Git 템플릿 설정 스크립트 배포
- [ ] GitHub Actions 워크플로우 파일을 모든 레포에 추가
- [ ] .gitignore 표준 템플릿 적용
- [ ] 분기별 `git secrets --scan-history` 수행

### Team/Enterprise 플랜 팀

- [ ] GitHub Secret Protection 활성화
- [ ] Custom Pattern 등록 (Long-term)
- [ ] Dry Run으로 기존 노출 키 확인
- [ ] Push Protection 활성화
- [ ] Bypass 정책 수립 (누가, 어떤 사유로 우회 가능?)
- [ ] 기존 노출 키 비활성화 및 새 키 발급
- [ ] 보조 레이어로 git-secrets도 병행 권장

---

## 11. 참고 자료

| 리소스 | 링크 |
|--------|------|
| GitHub Custom Pattern 정의 | [docs.github.com](https://docs.github.com/en/code-security/secret-scanning/using-advanced-secret-scanning-and-push-protection-features/custom-patterns/defining-custom-patterns-for-secret-scanning) |
| GitHub Secret Protection 가격 | [resources.github.com](https://resources.github.com/evolving-github-advanced-security/) |
| AWS git-secrets | [github.com/awslabs/git-secrets](https://github.com/awslabs/git-secrets) |
| Amazon Bedrock API Keys 문서 | [docs.aws.amazon.com](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html) |
| Bedrock API Key 보안 모범 사례 | [aws.amazon.com/blogs](https://aws.amazon.com/blogs/security/securing-amazon-bedrock-api-keys-best-practices-for-implementation-and-management/) |
| Bedrock API Key 보안 분석 (3rd party) | [medium.com/@adan.alvarez](https://medium.com/@adan.alvarez/api-keys-for-bedrock-a-brief-security-overview-2133ed9a2b3f) |
| GitHub Supported Patterns | [docs.github.com](https://docs.github.com/en/code-security/reference/secret-security/supported-secret-scanning-patterns) |

---

*문서 작성일: 2026-06-08*  
*최종 수정일: 2026-06-19*  
*대상: AWS 고객사 (GitHub를 사용하는 개발팀)*  
*버전: 2.0 (GitHub UI 경로 업데이트, 과금 구조 반영, 정규식 패턴 수정)*
