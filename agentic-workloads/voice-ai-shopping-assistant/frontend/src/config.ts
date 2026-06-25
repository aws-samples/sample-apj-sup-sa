/**
 * Build-time config. VITE_ vars are baked at build (see WebStack).
 *
 * VITE_SESSION_API_URL — the deployed "start" endpoint (POST → { room_url }).
 * Defaults to the live hackathon endpoint so `npm run dev` works with no .env.
 */
export const SESSION_API_URL =
  import.meta.env.VITE_SESSION_API_URL ??
  "https://l5uf3gixrj.execute-api.ap-southeast-2.amazonaws.com/prod/";
