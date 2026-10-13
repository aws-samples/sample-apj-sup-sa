/**
 * Build-time config. VITE_ vars are baked at build (see WebStack).
 *
 * VITE_SESSION_API_URL — the deployed "start" endpoint (POST → { room_url }).
 * Set this to your deployed API Gateway URL; the placeholder below is only a
 * shape example and will not resolve.
 */
export const SESSION_API_URL =
  import.meta.env.VITE_SESSION_API_URL ??
  "https://<your-session-api-id>.execute-api.ap-southeast-2.amazonaws.com/prod/";
