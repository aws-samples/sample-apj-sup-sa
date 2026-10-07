# Kiro First Call Deck

Kiro 소개와 고객 대화를 위한 한국어·영어·일본어를 지원하는 프레젠테이션입니다.

- [온라인 장표 보기](https://aws-samples.github.io/sample-apj-sup-sa/ai-coding-assistants/kiro-fcd/)
- [단일 HTML 다운로드](https://github.com/aws-samples/sample-apj-sup-sa/raw/refs/heads/main/ai-coding-assistants/kiro-fcd/index.html)

## 사용 방법

`index.html`을 다운로드하고 브라우저에서 열거나 GitHub Pages 사이트에서 확인합니다. 이미지·영상·폰트·Workflows 데모가 HTML 안에 포함되어 있어 `assets/` 디렉토리는 필요하지 않습니다. 출처 문서 링크를 따라갈 때는 인터넷 연결이 필요합니다.

하단 왼쪽의 **한국어 · English · 日本語** 버튼으로 언어를 선택합니다. 선택한 언어를 브라우저에 기억하고 현재 페이지를 유지합니다. 번역과 일본어 글꼴도 HTML 안에 포함되어 있어 언어 전환에 외부 번역 서비스가 필요하지 않습니다. 개인이 입력한 발표 노트는 번역하지 않으며 세 언어에서 동일한 노트를 유지합니다. 제품 UI 스크린샷과 영상은 원본 언어로 표시합니다.

직접 특정 언어로 공유할 수도 있습니다.

- [English](https://aws-samples.github.io/sample-apj-sup-sa/ai-coding-assistants/kiro-fcd/#lang=en&slide=1)
- [日本語](https://aws-samples.github.io/sample-apj-sup-sa/ai-coding-assistants/kiro-fcd/#lang=ja&slide=1)

총 57페이지이며, 단계별 설명·문서 예시·미디어 확대·발표자 노트를 포함합니다. 제품 기능과 요금은 바뀔 수 있으므로 장표의 공식 문서 링크에서 최신 내용을 확인합니다.

## 배포

`main` 브랜치에는 편집할 HTML과 안내 문서를 보관합니다. GitHub Pages는 `gh-pages` 브랜치의 정적 파일을 게시하며, 게시 브랜치에는 이 프레젠테이션 디렉토리와 루트 안내 페이지만 포함됩니다.

장표를 갱신할 때는 `main`의 `index.html`을 업데이트한 뒤 같은 파일을 게시 브랜치의 `ai-coding-assistants/kiro-fcd/index.html`에도 반영합니다. `.nojekyll`로 HTML을 변환 없이 제공합니다.

## 번역 소스

`locales/en.json`, `locales/ja.json`과 `scripts/language-switcher.js`는 번역과 언어 전환을 검토하기 위한 소스입니다. 배포된 `index.html`은 이 데이터를 내장하며 해당 경로를 실행 중에 요청하지 않습니다. HTML 자체에 별도 컴파일이나 빌드는 필요하지 않습니다.

## 라이선스

저장소의 라이선스와 함께 포함된 자료의 저작권·라이선스를 확인합니다. 한글·일본어 표시용 Noto Sans KR / Noto Sans CJK JP 서브셋의 SIL Open Font License는 `LICENSES/NotoSansKR-OFL.txt`, `LICENSES/NotoSansJP-OFL.txt`에 보존되어 있습니다.
