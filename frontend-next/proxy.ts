import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

// Adds a shared secret to /api requests that next.config.mjs rewrites to the
// backend. The backend only trusts the visitor IP in x-vercel-forwarded-for
// when this secret matches, so direct callers cannot spoof their IP to evade
// rate limits. EDGE_PROXY_SECRET is server-only: never prefix it with NEXT_PUBLIC_.
export function proxy(request: NextRequest) {
  const secret = process.env.EDGE_PROXY_SECRET;
  const requestHeaders = new Headers(request.headers);
  requestHeaders.delete('x-edge-secret');
  if (secret) {
    requestHeaders.set('x-edge-secret', secret);
  }
  return NextResponse.next({ request: { headers: requestHeaders } });
}

export const config = {
  matcher: '/api/:path*',
};
