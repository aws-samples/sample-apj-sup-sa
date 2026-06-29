const MASK_CHAR = '•';
const MASK_VISIBLE_CHARS = 4;
const MASK_MIDDLE_CHARS = 4;
const MASK_PLACEHOLDER_CHARS = 8;
const MASK_SECRET_PLACEHOLDER_CHARS = 16;

const MASK_MIDDLE = MASK_CHAR.repeat(MASK_MIDDLE_CHARS);
const MASK_PLACEHOLDER = MASK_CHAR.repeat(MASK_PLACEHOLDER_CHARS);
const MASK_SECRET_PLACEHOLDER = MASK_CHAR.repeat(MASK_SECRET_PLACEHOLDER_CHARS);

export const maskAccessKey = (value: string): string => {
  if (!value) return '';
  if (value.length <= MASK_VISIBLE_CHARS * 2) return MASK_PLACEHOLDER;
  const start = value.slice(0, MASK_VISIBLE_CHARS);
  const end = value.slice(-MASK_VISIBLE_CHARS);
  return `${start}${MASK_MIDDLE}${end}`;
};

export const maskSecretKey = (value: string): string => {
  if (!value) return '';
  if (value.length <= MASK_VISIBLE_CHARS * 2) return MASK_PLACEHOLDER;
  return MASK_SECRET_PLACEHOLDER;
};
