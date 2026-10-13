// amazon-connect-streams ships as a UMD bundle without TypeScript types.
// It attaches a global `connect` object to window when imported. We only use
// connect.core.initCCP, so a permissive shim is sufficient.
declare module "amazon-connect-streams";

interface Window {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  connect?: any;
}
