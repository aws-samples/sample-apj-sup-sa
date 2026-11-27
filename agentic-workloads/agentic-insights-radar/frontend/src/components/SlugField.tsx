// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';

import { slugify, slugTyping } from '@/lib/format';

interface Props {
  value: string;
  onChange: (slug: string) => void;
  /** What the address is for, e.g. "the radar's link". */
  usedIn: string;
  errorText?: string;
}

/**
 * The address field of a create form. It keeps a hyphen the user is typing ("crm-" on the way to
 * "crm-rivals") and tidies the address only when the field loses focus; the form slugifies again
 * on submit.
 */
export default function SlugField({ value, onChange, usedIn, errorText }: Props) {
  return (
    <FormField label="Address" description={`Used in ${usedIn}. Lower-case letters, digits and hyphens.`} errorText={errorText}>
      <Input ariaLabel="Address" value={value} onChange={({ detail }) => onChange(slugTyping(detail.value))} onBlur={() => value !== slugify(value) && onChange(slugify(value))} />
    </FormField>
  );
}
