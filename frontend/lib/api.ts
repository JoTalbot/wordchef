/** Typed Word Chef API client. All game truth lives on the server. */

/** Base origin: same-origin on the web, the hosted server inside the Android
 * wrapper (file:// origin), overridable via window.__WC_API__. */
export const API_BASE: string =
  (typeof window !== "undefined" && (window as unknown as { __WC_API__?: string }).__WC_API__) ||
  (typeof window !== "undefined" && window.location.protocol === "file:"
    ? "http://129.213.177.56"
    : "");

function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

function wsUrl(path: string): string {
  if (API_BASE) {
    const u = new URL(API_BASE);
    const proto = u.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${u.host}${path}`;
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}

export interface Player {
  player_id: string;
  name: string;
  xp: number;
}

export interface OrderView {
  order_id: string;
  kind: string;
  difficulty: number;
  tray: string;
  time_limit: number;
  base_reward: number;
  min_length: number;
  required_letter: string;
  theme: string;
  streak_needed: number;
  flavor_request: string;
  secret: string;
  has_secret: boolean;
  streak_left: number;
  used_words: string[];
}

export interface PlayerView {
  player_id: string;
  name: string;
  score: number;
  combo: number;
  heat: number;
  spice_charges: number;
  golden: number;
  prep_tokens: number;
  boost_armed: boolean;
  round_dishes: number;
  dishes: number;
  order: OrderView | null;
  order_deadline: number;
  order_started_at: number;
  leaderboard?: BoardRow[];
  round_no?: number;
  rounds_total?: number;
  finished?: boolean;
  mode?: string;
}

export interface BoardRow {
  position: number;
  player_id: string;
  display_name: string;
  score: number;
  dishes: number;
  best_word: string;
}

export interface MatchView {
  match_id: string;
  mode: string;
  host_player_id: string;
  kitchen: string;
  round_no: number;
  rounds_total: number;
  round_active: boolean;
  finished: boolean;
  leaderboard: BoardRow[];
  chaos_log: Array<Record<string, string>>;
}

export interface IntentResult {
  accepted: boolean;
  action: string;
  reason: string;
  player_id: string;
  payload: {
    player: PlayerView;
    leaderboard: BoardRow[];
    round_complete: boolean;
    dish?: Record<string, unknown>;
    score_delta?: number;
    next_order?: OrderView;
    chaos?: Record<string, string>;
    execution?: { execution_id: string; digest: string; verified: boolean | null; checkpoint_id: string };
    [key: string]: unknown;
  };
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(apiUrl(url), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  const body = await res.json();
  if (!res.ok) {
    const detail = (body as { detail?: { message?: string } })?.detail;
    throw new Error(detail?.message || `HTTP ${res.status}`);
  }
  return body as T;
}

export const api = {
  register: (name: string) =>
    json<Player>('/api/players', { method: 'POST', body: JSON.stringify({ name }) }),

  info: () => json<{ kitchens: Array<Record<string, unknown>> }>('/api/info'),

  createMatch: (mode: string, player_ids: string[], rounds: number, kitchen_id: string) =>
    json<MatchView>('/api/matches', {
      method: 'POST',
      body: JSON.stringify({ mode, player_ids, rounds, kitchen_id }),
    }),

  joinMatch: (matchId: string, playerId: string) =>
    json<MatchView>(`/api/matches/${matchId}/join`, {
      method: 'POST', body: JSON.stringify({ player_id: playerId }),
    }),

  startMatch: (matchId: string, playerId?: string) =>
    json<MatchView>(`/api/matches/${matchId}/start`, { method: 'POST', body: JSON.stringify({ player_id: playerId }) }),

  matchState: (matchId: string) => json<MatchView>(`/api/matches/${matchId}`),

  playerView: (matchId: string, playerId: string) =>
    json<PlayerView>(`/api/matches/${matchId}/players/${playerId}`),

  intent: (matchId: string, playerId: string, intent: Record<string, unknown>) =>
    json<IntentResult>(`/api/matches/${matchId}/intent`, {
      method: 'POST',
      body: JSON.stringify({ player_id: playerId, ...intent }),
    }),

  verify: (matchId: string) =>
    json<{ verdict: string }>(`/api/matches/${matchId}/verify`, { method: 'POST' }),

  leaderboard: () => json<{ entries: BoardRow[] }>('/api/leaderboard'),

  progression: (playerId: string) =>
    json<{ xp: number; kitchen_name: string; next_kitchen: { name: string; remaining: number } | null }>(
      `/api/players/${playerId}/progression`),
};

export function subscribe(matchId: string, onEvent: (event: Record<string, unknown>) => void): () => void {
  let ws: WebSocket | null = null;
  let stopped = false;
  let retry: ReturnType<typeof setTimeout> | null = null;

  const connect = () => {
    if (stopped) return;
    ws = new WebSocket(wsUrl(`/api/ws/matches/${matchId}`));
    ws.onmessage = (message) => {
      try {
        const data = JSON.parse(message.data);
        if (data.type === 'EVENT' && data.event) onEvent(data.event);
        if (data.type === 'SYNC' && Array.isArray(data.events)) {
          for (const event of data.events) onEvent(event);
        }
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = () => {
      if (stopped) return;
      retry = setTimeout(connect, 1200);
    };
    ws.onerror = () => ws?.close();
  };

  connect();
  return () => {
    stopped = true;
    if (retry) clearTimeout(retry);
    ws?.close();
  };
}
