# plugin-importer

[English](README.md) | **한국어**

**Claude Code나 Codex에서 설치해 쓰던 플러그인을, 내용 그대로 Kiro에서.**

Kiro용 [Agent Plugins 1.0.0](https://agent-plugins.org/) 규격 플러그인입니다. 한 번 설치하고 옮겨달라고
말하면 됩니다. Claude Code(`/plugin install …`)나 Codex(`codex plugin add …`)로 설치한 기록을 읽어 각
플러그인을 바이트 단위로 복사하고, Kiro가 읽는 파일 하나를 더해 등록합니다. Claude Code나 Codex 형식으로만
배포되는 제품은 마켓플레이스 GitHub 주소만으로 가져옵니다.

> "Claude Code에서 쓰던 플러그인 전부 Kiro로 옮겨줘"
> "obra/superpowers-marketplace 마켓플레이스 Kiro로 가져와줘"

두 형식은 **내용물**을 이미 공유합니다. SKILL.md, 훅 스크립트, MCP 정의가 양쪽에서 같습니다. 다른 것은
포장뿐입니다. Anthropic의 포장은 Agent Plugins 표준에 포함되지 않아, Claude Code 플러그인은 Kiro에서 그대로
열리지 않습니다. 이 플러그인은 그 포장만 더합니다.

## 설치

```bash
git clone --filter=blob:none --sparse https://github.com/aws-samples/sample-apj-sup-sa.git
cd sample-apj-sup-sa
git sparse-checkout set ai-coding-assistants/agent-plugins/plugin-importer
```

그다음 아래 둘 중 하나로 Power로 등록합니다.

- **Kiro IDE**: **Powers** 패널 → **Add Custom Power** → **Import power from a folder** →
  `ai-coding-assistants/agent-plugins/plugin-importer` 선택. 이 디렉터리의 GitHub 주소를 붙여 넣어도 됩니다.
- **Kiro CLI만 쓰는 경우** (CLI에는 Power 추가 명령이 없습니다): 변환기가 자기 자신을 설치합니다.

  ```bash
  cd ai-coding-assistants/agent-plugins/plugin-importer
  python3 skills/import-plugins/scripts/kiro-port.py port --from . plugin-importer --as power
  ```

Power는 세션 시작 시 로드되므로 설치 후 새 세션을 여세요.

**Kiro는 `kiro-cli --v3`로 실행하세요.** 클래식 모드나 `--no-interactive`에서도 Power·스킬은 정상 로드되지만,
훅은 조용히 아무 동작도 하지 않습니다 — 어느 쪽이든 `/hooks`는 0으로 표시됩니다. 이식된 플러그인의 훅이
실제로 발동하려면 `--v3`가 필요합니다.

`python3`(3.11+)와 `git`이 `PATH`에 있어야 합니다. macOS·kiro-cli 2.21.0 환경을 기준으로 만들고
점검했습니다. 다른 플랫폼·버전은 아직 확인하지 않았습니다.

## 사용

말하면 됩니다. 명령을 외울 필요는 없습니다.

```
> Claude Code와 Codex에서 쓰던 플러그인 보여줘        # 목록만 보여주고 아무것도 바꾸지 않는다
> commit-commands, frontend-design 옮겨줘            # 원하는 것만 이름으로 지목
> commit-commands 제거해줘                           # 되돌리기
```

`~/.claude/plugins/` 와 `~/.codex/` 에서 찾은 것을 구성과 목적지와 함께 나열합니다. 옮긴 것을 쓰려면 새
세션을 엽니다.

Claude Code의 **프로젝트 스코프** 설치는 Kiro를 그 프로젝트 안에서 실행할 때만 대상이 됩니다. Claude Code의
가시성 규칙과 같습니다. *"이 프로젝트에만"* 이라고 하면 전역 설치 대신 프로젝트의 `.kiro/` 에 씁니다.

### 남의 마켓플레이스에서

벤더가 `/plugin marketplace add acme/acme-plugins` 후 `/plugin install acme-review@acme-plugins` 를
안내하는 경우, Kiro에서는 문장 하나입니다.

```
> acme/acme-plugins 에서 acme-review 가져와줘
```

저장소를 받아 `.claude-plugin/marketplace.json` 을 읽고 지정한 플러그인을 변환해 등록합니다. 항목이 다른
git 저장소를 가리켜도 따라갑니다. 원하는 것을 이름으로 지목하세요. 큰 마켓플레이스는 항목이 수천 개입니다.

## 플러그인이 어디로 가는가

판단 기준은 하나입니다. **Power 없이도 그 자리에서 작동하는가.** 작동하면 Power를 씌우지 않습니다. Power는
키워드를 통과해야 켜지고 슬래시 명령을 잃는 대가가 있으니, 꼭 필요할 때만 씁니다.

- **스킬**은 Kiro가 이미 표준으로 읽습니다. `~/.kiro/skills/`에 놓기만 하면 되고, Power가 전혀 필요 없습니다.
- **훅**은 파일이 `~/.kiro/hooks/`에 있으면 그 자체로 발화합니다. Power 등록 여부와 무관합니다. 그래서 스킬과
  훅이 같이 있는 플러그인이라도 훅 때문에 Power가 필요해지지 않습니다. 둘 다 스킬 쪽 경로로 보냅니다.
- **명령**은 Kiro에 그 개념이 없어서 스킬로 바꿔야 하는데, 그 변환 결과를 담을 곳이 Power뿐입니다. Power 필수.
- **MCP 서버**는 Power가 활성화될 때만 연결됩니다. Power 필수.
- **에이전트만 있고 스킬이 없으면** 옮길 스킬이 없으니 Power를 만들어 그 안에 채웁니다.
- **마크다운 지침뿐**이면 아예 옮기지 않습니다. Power는 키워드로 켜지는데 지침은 늘 적용돼야 하므로, Power로
  만드는 순간 제작자의 의도가 바뀝니다.

| 플러그인 구성 | 목적지 | 이유 |
|---|---|---|
| `commands/` 또는 `.mcp.json` 있음 | Power | 이 둘은 Power만 담을 수 있습니다 |
| 스킬 있음 (훅·에이전트가 함께 있어도) | `~/.kiro/skills/` + `~/.kiro/hooks/` + `~/.kiro/agents/` | 스킬은 이미 Power 없이 작동하고, 훅도 마찬가지라 슬래시와 훅을 둘 다 유지합니다 |
| 스킬 없이 훅·에이전트만 | Power | 담을 자리가 필요해서입니다 |
| 마크다운 지침뿐 | 옮기지 않고 `~/.kiro/steering/` 경로만 안내 | Power는 조건부, steering은 항상 적용이라 성격이 다릅니다 |

`--as skills` 와 `--as power` 로 뒤집을 수 있습니다. Power 안 스킬은 슬래시 명령이 아닙니다. Claude Code 번들에서
`/명령`으로 하나씩 골라 쓰던 것처럼 `/이름`으로도 부르고 싶으면 `--slash` 를 붙이세요 — 스킬을 복사하지 않고
`~/.kiro/skills/` 에 링크만 놓습니다. 지침 파일은 자동 변환하지 않습니다. `inclusion: always`
가 붙은 steering 파일이 원래 지침처럼 항상 적용된다는 점에서 더 가깝습니다.

본문은 바이트 단위로 복사되고, 하나라도 다르면 이식이 중단됩니다. 원본 `.claude-plugin/`·`commands/`·
`agents/` 는 제자리에 남아서 그 디렉터리가 Claude Code 플러그인으로도 계속 쓰입니다. Kiro에 대응이 없는 것
— `SessionEnd`·`UserPromptExpansion` 훅, `argument-hint`, `.toml` 명령, 원격(`http`·`sse`) MCP 서버,
에이전트의 `tools`·`model` 제한 — 은 비슷한 것으로 흉내 내지 않고 기록합니다.

**컴포넌트별 상세는 `references/kiro-mapping.md`** 에 있습니다. 버그를 찾아 나서기 전에 알아둘 Kiro 동작도
함께 있습니다. Power 안 스킬은 슬래시로 호출되지 않고, Power의 원격 MCP 서버는 무시되며, 마크다운 에이전트는
세션을 그 에이전트로 시작해야 적용됩니다.

## 키워드 — 사람이 정하는 항목

Kiro는 슬래시가 아니라 **키워드**로 Power를 켭니다. 이 플러그인은 키워드를 플러그인·스킬 이름에서만 채우고
그 이상은 지어내지 않습니다. 제작자가 고르지 않은 단어는 제작자가 의도하지 않은 상황에서 Power를 켜기
때문입니다. 대신 이식이 끝나면 각 표현이 맡는 상황과 함께 몇 개를 제안하고, `plugin.json` 에 넣는 것은
승인을 받은 뒤에 합니다. 옮긴 뒤 뭔가 켜지지 않는다면 거의 항상 이것이 원인입니다.

## 구성

| 컴포넌트 | 종류 | 역할 |
|---|---|---|
| `skills/import-plugins` | 스킬 | 이식을 진행하고 보고서를 읽어주고 키워드를 제안합니다. |
| `skills/import-plugins/scripts/kiro-port.py` | 스크립트 | 변환기. Python 3.11+, 표준 라이브러리만. |
| `skills/import-plugins/references/kiro-mapping.md` | 참고 자료 | 컴포넌트별 대응과 Kiro 동작. |
| `tests/regression-test.py` | 테스트 | `python3 tests/regression-test.py` — 임시 `KIRO_HOME`에 합성 픽스처를 이식해 확인합니다. 프레임워크·네트워크 없음. |

변환기를 직접 쓰려면:

```bash
S=~/.kiro/powers/installed/plugin-importer/skills/import-plugins/scripts/kiro-port.py
python3 $S scan                                   # Claude Code / Codex 설치 목록
python3 $S port superpowers commit-commands       # 변환·설치·등록
python3 $S port --from acme/acme-plugins --all    # 마켓플레이스 저장소에서 직접
python3 $S port eli5 --as skills                  # 강제로 폴더 복사
python3 $S port ralph-loop --project-local        # 전역 대신 ./.kiro/ 에
python3 $S port --all --out ./powers              # 생성만 — 모노레포로 배포할 때
python3 $S unport superpowers                     # 제거
```

`--out` 은 Claude Code 마켓플레이스를 Kiro 설치용 모노레포로 바꾸는 방법입니다. 생성된 디렉터리 하나하나가
완전한 플러그인이면서 Claude Code 플러그인으로도 그대로 작동합니다.

## 라이선스

MIT-0 — [LICENSE](LICENSE) 참고.
