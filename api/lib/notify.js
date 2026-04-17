/**
 * ORACLE Notification Layer (ntfy.sh)
 *
 * Features:
 *   - Severity tiers (min/default/high/urgent) with auto-priority/tags
 *   - Action buttons (close trade, snooze, pause/resume trading, status)
 *   - HMAC-signed action URLs (day-scoped tokens, auto-expire in 24h)
 *   - Quiet hours (default 00:00–07:00 UTC; urgent bypasses)
 *   - Deferred queue flushed by the morning digest
 *   - Kill switch — if trading is paused, opens are blocked but alerts still fire
 *
 * Topic resolution order:
 *   1. process.env.NTFY_TOPIC
 *   2. Redis key "oracle:config:ntfy_topic"
 *   3. DEFAULT_TOPIC constant below
 */

import crypto from 'crypto';
import { redisGet, redisSet } from './redis.js';

const NTFY_URL = "https://ntfy.sh";
export const DEFAULT_TOPIC = "oracle-188d7e28a2af1544";
export const API_BASE = process.env.ORACLE_API_BASE || "https://oracle-psi-orpin.vercel.app";

// Deterministic fallback; rotate if the topic rotates.
const DEFAULT_ACTION_SECRET = "oracle-action-ee6f4a9c-v1";

const QUEUE_KEY = "oracle:ntfy:queue";
const QUIET_KEY = "oracle:config:quiet_hours";
const TRADING_ENABLED_KEY = "oracle:config:trading_enabled";
const SNOOZE_PREFIX = "oracle:snooze:";

let _topicCache = null;
let _topicCacheAt = 0;

async function resolveTopic() {
  if (process.env.NTFY_TOPIC) return process.env.NTFY_TOPIC;
  if (_topicCache && Date.now() - _topicCacheAt < 300000) return _topicCache;
  try {
    const t = await redisGet("oracle:config:ntfy_topic");
    if (t) {
      _topicCache = typeof t === "string" ? t : String(t);
      _topicCacheAt = Date.now();
      return _topicCache;
    }
  } catch {}
  return DEFAULT_TOPIC;
}

function actionSecret() {
  return process.env.NTFY_ACTION_SECRET || DEFAULT_ACTION_SECRET;
}

/**
 * HMAC token, scoped to the UTC day so buttons pressed after 24h fail cleanly.
 * @param {string} cmd
 * @param {string} arg
 * @param {string} [dayOverride] - YYYY-MM-DD (mostly for verify())
 */
export function signAction(cmd, arg = "", dayOverride) {
  const day = dayOverride || new Date().toISOString().slice(0, 10);
  const mac = crypto.createHmac("sha256", actionSecret())
    .update(`${cmd}:${arg}:${day}`)
    .digest("hex")
    .slice(0, 16);
  return { token: mac, day };
}

/**
 * Verify a token; accepts today's or yesterday's day (so buttons work across midnight).
 */
export function verifyAction(cmd, arg, token) {
  if (!token || typeof token !== "string") return false;
  const today = new Date().toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  for (const day of [today, yesterday]) {
    const { token: expected } = signAction(cmd, arg, day);
    if (token.length !== expected.length) continue;
    try {
      if (crypto.timingSafeEqual(Buffer.from(token), Buffer.from(expected))) return true;
    } catch {}
  }
  return false;
}

function actionUrl(cmd, arg = "") {
  const { token, day } = signAction(cmd, arg);
  const params = new URLSearchParams({ cmd, arg, t: token, d: day });
  return `${API_BASE}/api/strategy100-run?action=ntfy-cmd&${params.toString()}`;
}

/**
 * Build an ntfy Actions header from a list of {label, cmd, arg, method, style}.
 * Examples:
 *   buildActions([{label:"Close #3", cmd:"close", arg:"3"}])
 */
export function buildActions(actions = []) {
  if (!actions.length) return null;
  return actions.map(a => {
    const url = a.url || actionUrl(a.cmd, a.arg || "");
    const label = a.label.replace(/[,;]/g, " ");
    const method = a.method || "POST";
    return `http, ${label}, ${url}, method=${method}, clear=true`;
  }).join("; ");
}

// ─── Quiet hours / kill switch ─────────────────────────────────

async function isQuietHour() {
  try {
    const cfg = await redisGet(QUIET_KEY);
    const h = new Date().getUTCHours();
    const start = cfg?.start ?? 0;
    const end = cfg?.end ?? 7;
    if (cfg?.enabled === false) return false;
    return start <= end ? (h >= start && h < end) : (h >= start || h < end);
  } catch {
    const h = new Date().getUTCHours();
    return h >= 0 && h < 7;
  }
}

export async function isTradingEnabled() {
  try {
    const v = await redisGet(TRADING_ENABLED_KEY);
    return v !== false && v !== "false" && v !== 0;
  } catch {
    return true;
  }
}

export async function setTradingEnabled(enabled) {
  await redisSet(TRADING_ENABLED_KEY, !!enabled);
}

async function isSnoozed(key) {
  if (!key) return false;
  try {
    const until = await redisGet(SNOOZE_PREFIX + key);
    return until && Date.now() < Number(until);
  } catch {
    return false;
  }
}

export async function snoozeAlert(key, hours = 24) {
  await redisSet(SNOOZE_PREFIX + key, Date.now() + hours * 3600 * 1000);
}

async function enqueue(payload) {
  const queue = (await redisGet(QUEUE_KEY)) || [];
  queue.push({ ...payload, queued_at: new Date().toISOString() });
  await redisSet(QUEUE_KEY, queue.slice(-50));
}

export async function flushQueue() {
  const queue = (await redisGet(QUEUE_KEY)) || [];
  await redisSet(QUEUE_KEY, []);
  return queue;
}

// ─── The main send function ────────────────────────────────────

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.body
 * @param {"min"|"low"|"default"|"high"|"urgent"} [opts.priority]
 * @param {"info"|"trade"|"alert"|"urgent"|"health"} [opts.severity] - semantic tier
 * @param {string[]} [opts.tags]
 * @param {string} [opts.click]
 * @param {Array} [opts.actions] - see buildActions()
 * @param {string} [opts.dedupeKey] - skipped if snoozed
 */
export async function notify(opts = {}) {
  const {
    title, body, tags = [], click, actions = [],
    dedupeKey,
  } = opts;

  // Map severity → priority/tags if priority not explicit
  const sev = opts.severity || "info";
  const priority = opts.priority ||
    (sev === "urgent" ? "urgent" :
     sev === "alert" ? "high" :
     sev === "health" ? "min" :
     sev === "trade" ? "default" : "default");

  // Honour snooze
  if (dedupeKey && await isSnoozed(dedupeKey)) {
    return { sent: false, reason: "snoozed", dedupeKey };
  }

  // Defer non-urgent during quiet hours
  if (priority !== "urgent" && priority !== "high" && await isQuietHour()) {
    await enqueue({ title, body, priority, tags, click, actions });
    return { sent: false, reason: "queued", queued: true };
  }

  return sendNow({ title, body, priority, tags, click, actions });
}

async function sendNow({ title, body, priority, tags, click, actions }) {
  const topic = await resolveTopic();
  if (!topic) return { sent: false, reason: "no topic" };

  const headers = { "Content-Type": "text/plain; charset=utf-8" };
  if (title) headers["Title"] = title.slice(0, 250);
  if (priority) headers["Priority"] = priority;
  if (tags && tags.length) headers["Tags"] = tags.join(",");
  if (click) headers["Click"] = click;

  const actionsHeader = buildActions(actions);
  if (actionsHeader) headers["Actions"] = actionsHeader;

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

/** Raw send that ignores quiet hours / snoozes. Use for digests and health pings. */
export async function notifyRaw(opts) {
  const { priority = "default", tags = [], actions = [] } = opts;
  return sendNow({ ...opts, priority, tags, actions });
}

export function isNotifyConfigured() {
  return true;
}
