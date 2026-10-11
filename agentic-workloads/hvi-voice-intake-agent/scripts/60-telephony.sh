#!/usr/bin/env bash
# Retired: the former SIP Media Application path depended on a deleted TTS
# Lambda and a half-duplex action model that cannot support barge-in.
#
# The active media path is Asterisk AudioSocket plus the media service. Its
# deployment/configuration entry points are:
#   scripts/70-asterisk.sh
#   scripts/71-asterisk-config.sh
#   scripts/72-media-deploy.sh
#
# Those scripts change live telephony and compute resources. Review them and
# obtain explicit approval before running them; this retired script performs no
# AWS calls and cannot repoint the SIP Media Application.

set -euo pipefail
printf '%s\n' \
  'scripts/60-telephony.sh is retired; use the reviewed Asterisk media path.' >&2
exit 1
