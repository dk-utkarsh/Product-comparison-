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

// Hosts whose requests should go through ScraperAPI when SCRAPER_API_KEY is set.
// pinkblue.in blocks datacenter IPs (DigitalOcean etc.) at the firewall.
const PROXY_HOSTS = new Set(["pinkblue.in", "www.pinkblue.in"]);

function maybeProxy(url: string): string {
  const key = process.env.SCRAPER_API_KEY;
  if (!key) return url;
  try {
    const host = new URL(url).hostname;
    if (PROXY_HOSTS.has(host)) {
      return `https://api.scraperapi.com/?api_key=${key}&url=${encodeURIComponent(url)}&keep_headers=true`;
    }
  } catch {
    // fall through
  }
  return url;
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

  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const response = await fetch(maybeProxy(url), {
        headers,
        redirect: "follow",
        signal: AbortSignal.timeout(timeout),
      });

      // Retry on rate limit or server error
      if ((response.status === 429 || response.status === 503) && attempt < retries) {
        const backoff = Math.min(2000 * Math.pow(2, attempt), 10000);
        await new Promise((r) => setTimeout(r, backoff));
        // Rotate UA for retry
        headers["User-Agent"] = randomItem(USER_AGENTS);
        continue;
      }

      return response;
    } catch (e) {
      lastError = e instanceof Error ? e : new Error(String(e));
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
