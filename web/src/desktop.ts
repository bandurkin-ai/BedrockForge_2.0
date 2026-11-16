/** Bridge of the Windows desktop client (desktop/src/preload.js); null in the browser version. */
export interface DesktopBridge {
  /** server the client forwards /api to, e.g. http://localhost:8000 */
  serverUrl: string;
  version: string;
  title: string;
  /** opens an allowed https address in the system browser */
  openExternal(url: string): Promise<boolean>;
}

export const desktop: DesktopBridge | null = (window as unknown as { bfDesktop?: DesktopBridge }).bfDesktop ?? null;

/** Address of the MCP endpoint for the user's Claude app: the server itself, not the page. */
export function mcpUrl(): string {
  if (desktop) return `${desktop.serverUrl}/mcp`;
  return `${window.location.protocol}//${window.location.hostname}:8000/mcp`;
}
