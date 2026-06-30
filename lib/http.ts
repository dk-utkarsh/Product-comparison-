/**
 * Smart HTTP client for scraping — free anti-detection strategies.
 *
 * Features:
 * - Rotates User-Agent per request (pool of 20+ real browser UAs)
 * - Randomizes Accept-Language, Accept-Encoding headers
 * - Adds realistic Referer headers
 * - Exponential backoff on 429/503
 * - Fast timeout (8s default)
 * - Connection reuse
 */

const USER_AGENTS = [
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
  "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 OPR/111.0.0.0",
];

const ACCEPT_LANGUAGES = [
  "en-US,en;q=0.9",
  "en-GB,en;q=0.9",
  "en-US,en;q=0.9,hi;q=0.8",
  "en-IN,en;q=0.9,hi;q=0.8",
  "en-US,en;q=0.9,en-GB;q=0.8",
];

const REFERERS = [
  "https://www.google.com/",
  "https://www.google.co.in/",
  "",
];

function randomItem<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

// Hosts that block datacenter IPs (DigitalOcean etc.) at the firewall, so a direct
// fetch works locally (residential IP) but not on prod. For these we AUTO-fall back
// to ScraperAPI when the direct fetch is blocked/errors — no manual flag needed, so
// prod self-heals. (Add a host here if it starts blocking our server IP.)
const PROXY_HOSTS = new Set(["pinkblue.in", "www.pinkblue.in"]);

function isProxyHost(url: string): boolean {
  try {
    return PROXY_HOSTS.has(new URL(url).hostname);
  } catch {
    return false;
  }
}

// A response status that means "blocked / try the proxy" rather than a real answer.
const BLOCKED_STATUS = new Set([403, 429, 503]);

/** Wrap ANY url to route through ScraperAPI (datacenter-IP / anti-bot bypass).
 *  Returns null when no key is configured. Used as an explicit FALLBACK (not the
 *  default) so credits are spent only when a direct fetch fails. */
export function scraperApiUrl(url: string, render = false): string | null {
  const key = process.env.SCRAPER_API_KEY;
  if (!key) return null;
  const r = render ? "&render=true" : "";
  return `https://api.scraperapi.com/?api_key=${key}&url=${encodeURIComponent(url)}&keep_headers=true${r}`;
}

export interface SmartFetchOptions {
  timeout?: number;
  retries?: number;
  accept?: string;
  skipReferer?: boolean;
}

/**
 * Smart fetch with rotating headers and retry logic.
 */
export async function smartFetch(
  url: string,
  options: SmartFetchOptions = {}
): Promise<Response> {
  const { timeout = 8000, retries = 1, accept, skipReferer = false } = options;

  const headers: Record<string, string> = {
    "User-Agent": randomItem(USER_AGENTS),
    Accept:
      accept ||
      "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": randomItem(ACCEPT_LANGUAGES),
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "Upgrade-Insecure-Requests": "1",
  };

  if (!skipReferer) {
    const ref = randomItem(REFERERS);
    if (ref) headers["Referer"] = ref;
  }

  const key = process.env.SCRAPER_API_KEY;
  const proxyHost = !!key && isProxyHost(url);
  // Start through the proxy immediately ONLY when explicitly opted in (PROXY_PINKBLUE
  // skips a doomed direct attempt on prod). Otherwise try direct first and
  // auto-fall back to the proxy on a block/error — so a datacenter-blocked host
  // (pinkblue) recovers on prod without anyone setting a flag, while staying direct
  // (free, no credits) on a residential IP where the direct fetch succeeds.
  let useProxy = proxyHost && !!process.env.PROXY_PINKBLUE;

  let lastError: Error | null = null;

  // retries + 1 extra slot so the auto-proxy fallback always gets its own attempt.
  for (let attempt = 0; attempt <= retries + 1; attempt++) {
    const target = useProxy && key ? scraperApiUrl(url)! : url;
    // ScraperAPI is slower (it fetches upstream for us) — give it room.
    const t = useProxy ? Math.max(timeout, 40000) : timeout;
    try {
      const response = await fetch(target, {
        headers,
        redirect: "follow",
        signal: AbortSignal.timeout(t),
      });

      if (BLOCKED_STATUS.has(response.status)) {
        // Datacenter-blocked host → switch to the proxy and retry immediately.
        if (proxyHost && !useProxy) {
          useProxy = true;
          headers["User-Agent"] = randomItem(USER_AGENTS);
          continue;
        }
        // Otherwise back off and retry (rate limit / transient server error).
        if (response.status !== 403 && attempt < retries) {
          const backoff = Math.min(2000 * Math.pow(2, attempt), 10000);
          await new Promise((r) => setTimeout(r, backoff));
          headers["User-Agent"] = randomItem(USER_AGENTS);
          continue;
        }
      }

      return response;
    } catch (e) {
      lastError = e instanceof Error ? e : new Error(String(e));
      // Network error / timeout on a datacenter-blocked host → try the proxy.
      if (proxyHost && !useProxy) {
        useProxy = true;
        headers["User-Agent"] = randomItem(USER_AGENTS);
        continue;
      }
      if (attempt < retries) {
        await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
        headers["User-Agent"] = randomItem(USER_AGENTS);
      }
    }
  }

  throw lastError || new Error(`Failed to fetch ${url}`);
}

/**
 * Smart JSON fetch — same as smartFetch but parses JSON response.
 */
export async function smartFetchJson<T = unknown>(
  url: string,
  options: SmartFetchOptions = {}
): Promise<T> {
  const response = await smartFetch(url, {
    ...options,
    accept: "application/json",
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  return response.json() as Promise<T>;
}
