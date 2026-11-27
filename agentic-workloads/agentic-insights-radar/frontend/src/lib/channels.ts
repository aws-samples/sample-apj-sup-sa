// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * A Slack channel ID: C (public) or G (an older private channel), then at least 8 capitals or digits. The same
 * rule as the API's CHANNEL_ID_RE; shared by the admin form and the subscriber picker (scan cycle 38).
 */
export const CHANNEL_ID = /^[CG][A-Z0-9]{8,}$/;
