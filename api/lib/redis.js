/**
 * Upstash Redis client using REST API (no npm package needed).
 *
 * Env vars required:
 *   UPSTASH_REDIS_REST_URL   — e.g. https://your-db.upstash.io
 *   UPSTASH_REDIS_REST_TOKEN — your API token
 *
 * Set these in Vercel project settings or .env
 */

const REDIS_URL = process.env.UPSTASH_REDIS_REST_URL;
const REDIS_TOKEN = process.env.UPSTASH_REDIS_REST_TOKEN;

async function redisCommand(...args) {
  if (!REDIS_URL || !REDIS_TOKEN) {
    return null; // Redis not configured, fall back to file-based
  }

  const res = await fetch(`${REDIS_URL}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${REDIS_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(args),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Redis error ${res.status}: ${text}`);
  }

  const data = await res.json();
  return data.result;
}

export async function redisGet(key) {
  const raw = await redisCommand("GET", key);
  if (raw === null || raw === undefined) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

export async function redisSet(key, value) {
  const raw = typeof value === "string" ? value : JSON.stringify(value);
  return redisCommand("SET", key, raw);
}

export async function redisDel(key) {
  return redisCommand("DEL", key);
}

export function isRedisConfigured() {
  return !!(REDIS_URL && REDIS_TOKEN);
}
