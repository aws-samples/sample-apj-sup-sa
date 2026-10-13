require('dotenv').config();

const { execFileSync } = require('node:child_process');

const APP_NAME = 'Meeting Assistant';
const APP_BUNDLE_ID = 'com.meeting-assistant.app';
const ENTITLEMENTS_PATH = 'entitlements.plist';

const hasNotaryCredentials = Boolean(
  process.env.APPLE_ID &&
    process.env.APPLE_APP_SPECIFIC_PASSWORD &&
    process.env.APPLE_TEAM_ID
);

const shouldNotarize =
  process.env.NOTARIZE === 'false'
    ? false
    : process.env.NOTARIZE === 'true'
      ? true
      : hasNotaryCredentials;

const shouldSign = Boolean(process.env.APPLE_IDENTITY);

if (shouldNotarize && !shouldSign) {
  throw new Error(
    'NOTARIZE is enabled but APPLE_IDENTITY is missing. Set APPLE_IDENTITY in .env.'
  );
}

function shouldIgnorePackagedFile(filePath) {
  if (!filePath) {
    return false;
  }

  const normalized = filePath.replace(/\\/g, '/');

  if (normalized.startsWith('/.vite')) {
    return false;
  }

  // Native and external runtime dependencies (e.g., better-sqlite3, MCP SDK)
  // must be present in the packaged app for main-process requires.
  if (normalized.startsWith('/node_modules')) {
    return false;
  }

  return true;
}

async function notarizeAndStapleDmg(makeResults) {
  if (!shouldNotarize || process.platform !== 'darwin') {
    return;
  }

  const dmgArtifacts = makeResults
    .flatMap((result) => result.artifacts)
    .filter((artifactPath) => artifactPath.endsWith('.dmg'));

  if (dmgArtifacts.length === 0) {
    return;
  }

  for (const dmgPath of dmgArtifacts) {
    if (!process.env.APPLE_IDENTITY) {
      throw new Error('APPLE_IDENTITY is required to sign DMG artifacts before notarization.');
    }

    console.log(`[codesign] Sign DMG: ${dmgPath}`);
    execFileSync(
      'codesign',
      ['--force', '--sign', process.env.APPLE_IDENTITY, '--timestamp', dmgPath],
      { stdio: 'inherit' }
    );
    execFileSync('codesign', ['--verify', '--verbose=2', dmgPath], { stdio: 'inherit' });

    console.log(`[notary] Submit DMG: ${dmgPath}`);
    execFileSync(
      'xcrun',
      [
        'notarytool',
        'submit',
        dmgPath,
        '--apple-id',
        process.env.APPLE_ID,
        '--password',
        process.env.APPLE_APP_SPECIFIC_PASSWORD,
        '--team-id',
        process.env.APPLE_TEAM_ID,
        '--wait',
      ],
      { stdio: 'inherit' }
    );

    console.log(`[notary] Staple DMG: ${dmgPath}`);
    execFileSync('xcrun', ['stapler', 'staple', dmgPath], { stdio: 'inherit' });
    execFileSync('xcrun', ['stapler', 'validate', dmgPath], { stdio: 'inherit' });
  }
}

module.exports = {
  packagerConfig: {
    ignore: shouldIgnorePackagedFile,
    asar: {
      unpack: '**/*.node',
    },
    name: APP_NAME,
    appBundleId: APP_BUNDLE_ID,
    osxSign: shouldSign
        ? {
            identity: process.env.APPLE_IDENTITY,
            hardenedRuntime: true,
            'gatekeeper-assess': false,
            entitlements: ENTITLEMENTS_PATH,
            'entitlements-inherit': ENTITLEMENTS_PATH,
            strictVerify: false,
            signatureFlags: 'library',
            timestamp: 'https://timestamp.apple.com/ts01',
          }
      : undefined,
    osxNotarize: shouldNotarize
      ? {
          tool: 'notarytool',
          appleId: process.env.APPLE_ID,
          appleIdPassword: process.env.APPLE_APP_SPECIFIC_PASSWORD,
          teamId: process.env.APPLE_TEAM_ID,
          timeout: 1800000,
        }
      : undefined,
  },
  rebuildConfig: {},
  hooks: {
    postMake: async (_config, makeResults) => {
      await notarizeAndStapleDmg(makeResults);
    },
  },
  makers: [
    {
      name: '@electron-forge/maker-zip',
      platforms: ['darwin'],
    },
    {
      name: '@electron-forge/maker-dmg',
      platforms: ['darwin'],
    },
    {
      name: '@electron-forge/maker-squirrel',
      config: {},
    },
    {
      name: '@electron-forge/maker-deb',
      config: {},
    },
    {
      name: '@electron-forge/maker-rpm',
      config: {},
    },
  ],
  plugins: [
    {
      name: '@electron-forge/plugin-vite',
      config: {
        build: [
          {
            entry: 'src/main/main.ts',
            config: 'vite.main.config.ts',
            target: 'main',
          },
          {
            entry: 'src/preload/preload.ts',
            config: 'vite.preload.config.ts',
            target: 'preload',
          },
        ],
        renderer: [
          {
            name: 'main_window',
            config: 'vite.renderer.config.ts',
          },
        ],
      },
    },
  ],
};
