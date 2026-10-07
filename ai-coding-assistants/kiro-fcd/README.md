# Kiro First Call Deck

A presentation for introducing Kiro and discussing customer use cases. The deck supports Korean, English, and Japanese.

- [Open the deck](https://aws-samples.github.io/sample-apj-sup-sa/ai-coding-assistants/kiro-fcd/)
- [Download the standalone HTML](https://github.com/aws-samples/sample-apj-sup-sa/raw/refs/heads/main/ai-coding-assistants/kiro-fcd/index.html)

## Usage

Open the GitHub Pages site, or download `index.html` and open it in a browser. Images, videos, fonts, and the Workflows demo are embedded in the HTML. No `assets/` directory is required. An internet connection is needed to visit linked source documentation.

Use the **한국어 · English · 日本語** buttons at the bottom left to select a language. The browser remembers your selection, and switching languages keeps the current slide. Translations and Japanese fonts are embedded, so language switching does not use an external translation service.

Speaker notes that you enter remain unchanged across languages. Product screenshots and videos retain their original language.

You can also share a link to a specific language:

- [English](https://aws-samples.github.io/sample-apj-sup-sa/ai-coding-assistants/kiro-fcd/#lang=en&slide=1)
- [日本語](https://aws-samples.github.io/sample-apj-sup-sa/ai-coding-assistants/kiro-fcd/#lang=ja&slide=1)
- [한국어](https://aws-samples.github.io/sample-apj-sup-sa/ai-coding-assistants/kiro-fcd/#lang=ko&slide=1)

The deck contains 57 slides with interactive explanations, document examples, expandable media, and speaker notes. Features and pricing can change. Check the official documentation linked from the slides for current details.

## Publishing

The `main` branch contains the editable HTML and documentation. GitHub Pages serves static files from the `gh-pages` branch. That branch contains this presentation directory and a root redirect page.

To publish an update, change `index.html` on `main`, then copy the same file to `ai-coding-assistants/kiro-fcd/index.html` on `gh-pages`. Keep the accompanying documentation in sync. The `.nojekyll` file lets GitHub Pages serve the HTML without Jekyll processing.

## Copy review

The 57 slides and detail panels were reviewed to reduce awkward phrasing, abstract language, and repetition. The edit favors short sentences, clear actions, and consistent terms while preserving numbers and support conditions. See [review criteria and scope](COPY-REVIEW.md).

## Locale sources

`locales/ko.json`, `locales/en.json`, `locales/ja.json`, and `scripts/language-switcher.js` provide reviewable sources for the copy and language controls. The published `index.html` embeds these files and does not request them at runtime. No separate compilation or source build is required.

## Licenses

Review the repository license and the copyrights and licenses of embedded materials. The SIL Open Font Licenses for the Noto Sans KR and Noto Sans CJK JP font subsets are included in `LICENSES/NotoSansKR-OFL.txt` and `LICENSES/NotoSansJP-OFL.txt`.
