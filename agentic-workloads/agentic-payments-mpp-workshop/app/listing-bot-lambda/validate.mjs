/**
 * POST /validate — free, deterministic schema check. Delegates to the shared
 * validation module so /generate gets identical behavior before taking payment.
 */
import { validateInput } from './validation.mjs'

export function handleValidate(body) {
  return validateInput(body)
}
