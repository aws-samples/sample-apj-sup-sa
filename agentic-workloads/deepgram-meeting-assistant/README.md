# Meeting Assistant

A desktop application for meeting recording, real-time speech recognition, and AI summarization.

## Features

- **Real-time speech recognition**: Streaming speech-to-text via AWS Transcribe
- **AI sentence correction**: Real-time correction via Amazon Bedrock
- **Meeting translation**: Korean translation for English meetings
- **Meeting summaries**: AI-generated key points, action items, and decisions
- **Multiple meeting types**: Client, Quick, English, Interview
- **Local data storage**: SQLite-based storage for meetings and transcripts
- **Secure credentials**: AWS credentials stored encrypted at rest
- **MCP integration**: Connect external tools via the Model Context Protocol
- **Meeting prep**: Optional CRM lookups (opportunities, tasks) through an MCP server
- **Structured logging**: Pino-based dev/prod log separation
- **API rate limiting**: Rate limiting for summary/translation API calls

> The meeting-prep and post-meeting agent features connect to a CRM through an
> MCP server (configured as `crm-mcp-server`). This is an optional integration;
> plug in your own MCP server implementation, or run the app without it.

## Requirements

- Node.js 20+
- npm
- An AWS account (with Transcribe and Bedrock permissions)

## Install and run

```bash
# Install dependencies
npm install

# Run in development mode
npm start

# Run only the renderer in a browser (for UI development)
npm run dev:web
```

## Build and package

### Build commands

```bash
# Package for the current platform (produces .app, .exe, etc.)
npm run package

# Build a macOS installer
npm run make:mac

# Local macOS release (signing/notarization checks + checksum)
npm run release:mac
```

### Local macOS release

1. Set your Apple signing/notarization values in `.env`.
2. Run `npm run release:mac`.
3. Upload the artifacts (`out/release/...`) to your distribution location.

Example `.env`:

```bash
APPLE_ID=your-apple-id@example.com
APPLE_APP_SPECIFIC_PASSWORD=xxxx-xxxx-xxxx-xxxx
APPLE_TEAM_ID=ABCD123456
APPLE_IDENTITY=Developer ID Application: Your Name (TEAM_ID)
NOTARIZE=true
```

What the `release:mac` script does:

- Runs `npm run make:mac`
- Verifies `.app` code signing (`codesign`, `spctl`)
- Validates notarization stapling when enabled (`xcrun stapler validate`)
- Runs Gatekeeper checks on the `.dmg` (`spctl --type open`)
- Copies the `.dmg`/`.zip` and generates `sha256` files

### Code signing (production distribution)

A Developer ID Application certificate is required for macOS distribution.

Pre-check:

```bash
security find-identity -v -p codesigning
```

If you do not see a certificate matching `APPLE_IDENTITY`, signing/installation will fail.

## AWS setup

After launching the app, enter your AWS credentials on the Settings page.

### Required IAM permissions

```
transcribe:StartStreamTranscription
bedrock:InvokeModel
bedrock:InvokeModelWithResponseStream
```

### Supported regions

- US East (N. Virginia) - us-east-1
- US West (Oregon) - us-west-2
- Europe (Ireland, Frankfurt)
- Asia Pacific (Tokyo, Seoul, Singapore, Sydney)

## Meeting types

| Type | Description |
|------|-------------|
| Client Meeting | Customer meetings, action-item tracking, MCP integration |
| Quick Meeting | Quick syncs, task tracking |
| English Meeting | Real-time English-to-Korean translation |
| Interview | Structured Q&A, candidate evaluation |

## Supported languages

- Korean (ko-KR)
- English (en-US)

## Bedrock models

- Claude Haiku 4.5
- Claude Sonnet 4.5
- Claude Opus 4.5
- Nova 2 Lite

## Supported platforms

- macOS (DMG, ZIP)
- Windows (Squirrel installer)
- Linux (DEB, RPM)

## License

MIT
