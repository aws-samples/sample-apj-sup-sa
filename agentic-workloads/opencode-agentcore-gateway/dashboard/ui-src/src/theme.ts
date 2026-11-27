// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { applyMode, Mode } from '@cloudscape-design/global-styles';

const MODE_KEY = 'ocgw-mode';

export function storedDark(): boolean {
  try {
    return localStorage.getItem(MODE_KEY) === 'dark';
  } catch {
    return false;
  }
}

export function setDark(dark: boolean): void {
  applyMode(dark ? Mode.Dark : Mode.Light);
  try {
    localStorage.setItem(MODE_KEY, dark ? 'dark' : 'light');
  } catch {
    // storage blocked: the mode still applies for this page view
  }
}
