/**
 * Notification helper — pushes alerts to ntfy.sh.
 *
 * Topic is read from:
 *   1. process.env.NTFY_TOPIC (preferred — set in Vercel)
 *   2. Redis key "oracle:config:ntfy_topic" (fallback — set via API)
 *
 * If neither is set, notifications silently no-op so the pipeline never breaks.
 *
 * Usage:
 *   import { notify } from './lib/notify.js';
 *   await notify({ title: "New Alert", body: "Details...", priority: "high", tags: ["warning"] });
 */

import { redisGet } from './redis.js';

const NTFY_URL = "https://ntfy.sh";

let _topicCache = null;
let _topicCacheAt = 0;

async function resolveTopic() {
  // Env var wins
  if (process.env.NTFY_TOPIC) return process.env.NTFY_TOPIC;

  // Redis fallback, cached for 5 min
  if (_topicCache && Date.now() - _topicCacheAt < 300000) return _topicCache;
  try {
    const t = await redisGet("oracle:config:ntfy_topic");
    if (t) {
      _topicCache = typeof t === "string" ? t : String(t);
      _topicCacheAt = Date.now();
      return _topicCache;
    }
  } catch {}
  return null;
}

/**
 * Send a notification.
 * @param {object} opts
 * @param {string} opts.title - Short headline (shows as notification title)
 * @param {string} opts.body - Body text
 * @param {string} [opts.priority] - "min" | "low" | "default" | "high" | "urgent"
 * @param {string[]} [opts.tags] - e.g. ["warning","money"] — shows as emoji
 * @param {string} [opts.click] - URL to open when notification tapped
 */
export async function notify({ title, body, priority = "default", tags = [], click } = {}) {
  const topic = await resolveTopic();
  if (!topic) return { sent: false, reason: "NTFY_TOPIC not configured" };

  const headers = {
    "Content-Type": "text/plain; charset=utf-8",
  };
  if (title) headers["Title"] = title.slice(0, 250);
  if (priority) headers["Priority"] = priority;
  if (tags.length > 0) headers["Tags"] = tags.join(",");
  if (click) headers["Click"] = click;

  try {
    const r = await fetch(`${NTFY_URL}/${encodeURIComponent(topic)}`, {
      method: "POST",
      headers,
      body: body || "",
      signal: AbortSignal.timeout(8000),
    });
    if (!r.ok) return { sent: false, status: r.status, reason: await r.text() };
    return { sent: true };
  } catch (err) {
    return { sent: false, reason: err.message };
  }
}

export function isNotifyConfigured() {
  return !!process.env.NTFY_TOPIC;
}
