'use client';

/** Word Chef — mobile-first game client. The server is authoritative:
 *  this UI sends intents and renders what the kitchen replies. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  api,
  subscribe,
  type BoardRow,
  type IntentResult,
  type MatchView,
  type PlayerView,
} from '@/lib/api';

type Screen = 'home' | 'lobby' | 'game' | 'results';

interface Toast {
  id: number;
  text: string;
}

interface Pop {
  word: string;
  delta: number;
  combo: number;
  golden: number;
  secret: boolean;
}

const MODES = [
  { id: 'SOLO', title: 'Solo Service', sub: 'You vs. the dinner rush' },
  { id: 'QUICK_COOK', title: 'Quick Cook', sub: 'Same ticket for everyone' },
  { id: 'CHAOSS', title: 'Chaos Kitchen', sub: 'Sabotage allowed' },
];

export default function GameClient() {
  const [screen, setScreen] = useState<Screen>('home');
  const [name, setName] = useState('');
  const [playerId, setPlayerId] = useState('');
  const [mode, setMode] = useState('SOLO');
  const [rounds, setRounds] = useState(3);
  const [matchId, setMatchId] = useState('');
  const [match, setMatch] = useState<MatchView | null>(null);
  const [me, setMe] = useState<PlayerView | null>(null);
  const [compose, setCompose] = useState<number[]>([]);
  const [result, setResult] = useState<IntentResult | null>(null);
  const [pop, setPop] = useState<Pop | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [busy, setBusy] = useState(false);
  const [spiceArmed, setSpiceArmed] = useState(false);
  const [now, setNow] = useState(Date.now() / 1000);
  const [chaosFeed, setChaosFeed] = useState<string[]>([]);
  const [, setProgress] = useState<{ kitchen_name: string; xp: number; next_kitchen: { name: string; remaining: number } | null } | null>(null);
  const toastId = useRef(0);

  const say = useCallback((text: string) => {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 2600);
  }, []);

  // countdown clock
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 250);
    return () => clearInterval(timer);
  }, []);

  // live events
  useEffect(() => {
    if (!matchId || screen === 'home') return;
    const close = subscribe(matchId, (event) => {
      const type = event.type as string;
      if (type === 'CHAOS' || type === 'INTENT') {
        const resultPayload = event.result as IntentResult | undefined;
        const chaos = resultPayload?.payload?.chaos as Record<string, string> | undefined;
        if (chaos) {
          setChaosFeed((f) => [`${chaos.event_type} → ${chaos.victim_id}`, ...f].slice(0, 6));
        }
      }
      if (type === 'ROUND_STARTED') say(`Round ${event.round_no} — service!`);
      if (type === 'MATCH_FINISHED') {
        say('Match finished!');
        setScreen('results');
      }
      void refresh();
    });
    return close;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matchId, screen]);

  const refresh = useCallback(async () => {
    if (!matchId || !playerId) return;
    try {
      const [view, state] = await Promise.all([
        api.playerView(matchId, playerId),
        api.matchState(matchId),
      ]);
      setMe(view);
      setMatch(state);
      if (view.finished && screen === 'game') setScreen('results');
    } catch {
      /* transient */
    }
  }, [matchId, playerId, screen]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (playerId) void api.progression(playerId).then(setProgress).catch(() => {});
  }, [playerId, screen]);

  async function register() {
    const player = await api.register(name || 'Chef');
    setPlayerId(player.player_id);
    say(`Welcome, ${player.name}!`);
    setScreen('lobby');
  }

  async function createMatch() {
    if (!playerId) return;
    const players = mode === 'SOLO' ? [playerId] : [playerId];
    const view = await api.createMatch(mode, players, rounds, 'street');
    setMatchId(view.match_id);
    await api.startMatch(view.match_id);
    setScreen('game');
    setCompose([]);
  }

  const order = me?.order ?? null;
  const letters = useMemo(() => (order ? order.tray.split('') : []), [order]);

  const composedWord = compose.map((i) => letters[i] ?? '').join('');

  function tapLetter(index: number) {
    if (compose.includes(index)) return;
    setCompose((c) => [...c, index]);
  }

  function undo() {
    setCompose((c) => c.slice(0, -1));
  }

  async function send(intent: Record<string, unknown>) {
    if (busy) return;
    setBusy(true);
    try {
      const res = await api.intent(matchId, playerId, intent);
      setResult(res);
      setMe(res.payload.player);
      setCompose([]);
      if (res.action === 'SUBMIT_DISH' && res.accepted && res.reason === 'dish_served') {
        const dish = res.payload.dish as Record<string, number | string | boolean> | undefined;
        setPop({
          word: String(dish?.word ?? ''),
          delta: Number(res.payload.score_delta ?? 0),
          combo: Number(res.payload.player.combo ?? 0),
          golden: Number(dish?.golden_delta ?? 0),
          secret: Boolean(dish?.secret_hit),
        });
        setTimeout(() => setPop(null), 950);
        if (dish?.secret_hit) say('SECRET MENU cracked! +50%');
      } else if (res.action === 'SUBMIT_DISH' && res.accepted) {
        say(`Rejected: ${res.reason.replace(/_/g, ' ')}`);
      } else if (!res.accepted) {
        say(res.reason.replace(/_/g, ' '));
      }
      if (res.payload.chaos) {
        const chaos = res.payload.chaos as Record<string, string>;
        setChaosFeed((f) => [`${chaos.event_type} → ${chaos.victim_id}`, ...f].slice(0, 6));
      }
    } catch (err) {
      say(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
      setSpiceArmed(false);
      void refresh();
    }
  }

  const cook = () => send({ action: 'SUBMIT_DISH', word: composedWord, use_spice: spiceArmed });
  const prep = () => send({ action: 'PREP', word: composedWord });
  const skip = () => send({ action: 'SKIP' });
  const golden = (effect: string) => send({ action: 'GOLDEN', effect });
  const ringBell = () => send({ action: 'RING_BELL' });

  // ── screens ───────────────────────────────────────────────

  if (screen === 'home') {
    return (
      <div className="shell">
        <div className="brand">
          <h1>WORD CHEF</h1>
          <div className="tagline">COOK WORDS · SERVE DISHES · RIDE THE HEAT</div>
        </div>
        <div className="panel">
          <h2>CHEF PROFILE</h2>
          <div className="row">
            <input
              className="field"
              placeholder="Your chef name"
              value={name}
              maxLength={24}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void register()}
            />
          </div>
          <button className="btn primary" style={{ width: '100%', marginTop: 10 }} onClick={() => void register()}>
            ENTER THE KITCHEN
          </button>
        </div>
        <div className="panel">
          <h2>HOW TO COOK</h2>
          <div style={{ fontSize: 13, lineHeight: 1.8, color: 'var(--muted)' }}>
            🍳 Tap letters to build a <b style={{ color: 'var(--text)' }}>dish</b> (word) that satisfies the customer order.<br />
            🔥 Success feeds <b style={{ color: 'var(--accent)' }}>Heat</b> and <b style={{ color: 'var(--green)' }}>Combo</b> — bigger multipliers, harder tickets.<br />
            🌶️ Arm <b style={{ color: 'var(--red)' }}>Spice</b> for ×2 points... but a flop burns your combo.<br />
            ⭐ Rare letters are <b style={{ color: 'var(--gold)' }}>Golden Ingredients</b> — spend them on time, restocks or boosts.<br />
            🍽️ Short <b style={{ color: 'var(--text)' }}>prep</b> words bank points without breaking combo.
          </div>
        </div>
        <div className="footer-note">
          server-authoritative · every dish sealed on the Prolepsis loom<br />
          execution id · digest · artifacts · replay-verified
        </div>
      </div>
    );
  }

  if (screen === 'lobby') {
    return (
      <div className="shell">
        <div className="brand">
          <h1>WORD CHEF</h1>
          <div className="tagline">SERVICE SETUP</div>
        </div>
        <div className="panel">
          <h2>GAME MODE</h2>
          <div className="select-grid">
            {MODES.map((m) => (
              <button
                key={m.id}
                className={`select-btn ${mode === (m.id === 'CHAOSS' ? 'CHAOS_KITCHEN' : m.id) ? 'active' : ''}`}
                onClick={() => setMode(m.id === 'CHAOSS' ? 'CHAOS_KITCHEN' : m.id)}
              >
                <div className="title">{m.title}</div>
                <div className="sub">{m.sub}</div>
              </button>
            ))}
          </div>
          <h2 style={{ marginTop: 14 }}>ROUNDS</h2>
          <div className="row">
            {[2, 3, 5].map((r) => (
              <button key={r} className={`btn small ${rounds === r ? 'primary' : ''}`} onClick={() => setRounds(r)}>
                {r} rounds
              </button>
            ))}
          </div>
          <button className="btn primary" style={{ width: '100%', marginTop: 14 }} onClick={() => void createMatch()}>
            START SERVICE
          </button>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 8, textAlign: 'center' }}>
            Multiplayer: share the match code after start — chefs join the same kitchen.
          </div>
        </div>
      </div>
    );
  }

  if (screen === 'results') {
    const board = match?.leaderboard ?? me?.leaderboard ?? [];
    return (
      <div className="shell">
        <div className="brand">
          <h1>SERVICE OVER</h1>
          <div className="tagline">{match?.mode?.replace('_', ' ')}</div>
        </div>
        <div className="panel">
          <h2>FINAL STANDINGS</h2>
          {board.map((row: BoardRow) => (
            <div key={row.player_id} className={`board-row ${row.player_id === playerId ? 'me' : ''}`}>
              <div className="pos">#{row.position}</div>
              <div className="who">
                {row.display_name}
                {row.best_word ? <span style={{ color: 'var(--muted)' }}> · best “{row.best_word}”</span> : null}
              </div>
              <div className="pts">{row.score.toLocaleString()}</div>
            </div>
          ))}
        </div>
        <div className="row">
          <button className="btn" onClick={() => { setScreen('lobby'); setMatchId(''); setMatch(null); setMe(null); }}>
            NEW MATCH
          </button>
          <button
            className="btn primary"
            onClick={async () => {
              const verdict = await api.verify(matchId);
              say(`Replay verification: ${verdict.verdict}`);
            }}
          >
            REPLAY & VERIFY
          </button>
        </div>
        <div className="footer-note">
          every result is sealed as a Prolepsis execution — replay any match byte-exactly
        </div>
      </div>
    );
  }

  // ── game screen ───────────────────────────────────────────

  const deadline = me?.order_deadline ?? 0;
  const totalTime = order ? order.time_limit : 30;
  const left = Math.max(0, deadline - now);
  const pct = Math.max(0, Math.min(100, (left / Math.max(1, totalTime)) * 100));
  const low = left < 8;

  return (
    <div className="shell">
      {toasts.map((t) => (
        <div key={t.id} className="toast">{t.text}</div>
      ))}

      <div className="brand" style={{ paddingBottom: 4 }}>
        <h1 style={{ fontSize: 28 }}>WORD CHEF</h1>
      </div>

      <div className="hud">
        <div className="hud-card heat">
          <div className="label">🔥 HEAT</div>
          <div className="value">{me?.heat ?? 0}</div>
        </div>
        <div className="hud-card combo">
          <div className="label">🍳 COMBO</div>
          <div className="value">×{(1 + Math.min(4, (me?.combo ?? 0) * 0.25)).toFixed(2)}</div>
        </div>
        <div className="hud-card score">
          <div className="label">⭐ SCORE</div>
          <div className="value">{(me?.score ?? 0).toLocaleString()}</div>
        </div>
      </div>

      <div className="order-card">
        <div className="customer">
          <div className="customer-name">
            CUSTOMER · ROUND {match?.round_no ?? 1}/{match?.rounds_total ?? 3}
            {order ? ` · ${order.kind.replace(/_/g, ' ')}` : ''}
          </div>
          <div className={`timer ${low ? 'low' : ''}`}>
            {String(Math.floor(left / 60)).padStart(2, '0')}:{String(Math.floor(left % 60)).padStart(2, '0')}
          </div>
        </div>
        <div className="request">{describeOrder(order, me)}</div>
        <div className="constraints">
          {order?.flavor_request ? <span className="chip green">craves {order.flavor_request}</span> : null}
          {order?.theme ? <span className="chip blue">menu: {order.theme}</span> : null}
          {order?.required_letter ? <span className="chip">needs “{order.required_letter.toUpperCase()}”</span> : null}
          {order?.min_length ? <span className="chip">≥ {order.min_length} letters</span> : null}
          {order?.has_secret ? <span className="chip gold">a secret menu lurks…</span> : null}
          {me?.boost_armed ? <span className="chip gold">boost ×1.5 armed</span> : null}
          {order && order.streak_left > 1 ? <span className="chip blue">streak {order.streak_left} to go</span> : null}
        </div>
        <div className="progress">
          <div style={{ width: `${pct}%` }} />
        </div>
      </div>

      <div className="letters">
        {letters.map((letter, index) => (
          <button
            key={`${letter}-${index}`}
            className={`letter ${compose.includes(index) ? 'used' : ''} ${'jqxz'.includes(letter) ? 'rare' : ''}`}
            onClick={() => tapLetter(index)}
          >
            {letter.toUpperCase()}
          </button>
        ))}
      </div>

      <div className="composer">
        <div className="word-preview" onClick={undo}>
          {composedWord
            ? composedWord.split('').map((c, i) => <span key={i}>{c.toUpperCase()}</span>)
            : <span className="blank">TAP LETTERS</span>}
          <span className="cursor" />
        </div>
        <div className="actions">
          <button className="btn primary" disabled={busy || !composedWord} onClick={() => void cook()}>
            🍽️ COOK
          </button>
          <button
            className={`btn spice ${spiceArmed ? 'armed' : ''}`}
            disabled={busy || (me?.spice_charges ?? 0) <= 0}
            onClick={() => setSpiceArmed((s) => !s)}
          >
            🌶️ SPICE {me?.spice_charges ?? 0}
          </button>
          <button className="btn" disabled={busy || composedWord.length > 3 || composedWord.length < 2} onClick={() => void prep()}>
            PREP
          </button>
        </div>
        <div className="utility">
          <button className="btn small" disabled={busy || (me?.golden ?? 0) < 1} onClick={() => void golden('patience')}>
            ⏱ +15s ({me?.golden ?? 0}⭐)
          </button>
          <button className="btn small" disabled={busy || (me?.golden ?? 0) < 3} onClick={() => void golden('restock')}>
            🧺 restock (3⭐)
          </button>
          <button className="btn small" disabled={busy} onClick={() => void skip()}>
            skip order
          </button>
        </div>
      </div>

      <div className={`feedback ${result?.reason === 'dish_served' ? 'good' : result ? 'bad' : ''}`}>
        {result
          ? result.reason === 'dish_served'
            ? `Delicious! +${result.payload.score_delta ?? 0} pts`
            : result.reason.replace(/_/g, ' ')
          : 'Serve a dish to score'}
      </div>

      <div className="panel">
        <h2>STANDINGS</h2>
        {(match?.leaderboard ?? []).map((row: BoardRow) => (
          <div key={row.player_id} className={`board-row ${row.player_id === playerId ? 'me' : ''}`}>
            <div className="pos">#{row.position}</div>
            <div className="who">{row.display_name}</div>
            <div className="pts">{row.score.toLocaleString()}</div>
          </div>
        ))}
        {chaosFeed.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <h2>CHAOS FEED</h2>
            {chaosFeed.map((line, i) => (
              <div key={i} style={{ fontSize: 12, color: 'var(--accent2)', padding: '2px 0' }}>⚡ {line}</div>
            ))}
          </div>
        )}
      </div>

      <div className="row">
        <button className="btn small" disabled={busy || (me?.golden ?? 0) < 1} onClick={() => void ringBell()}>
          🔔 ring the chaos bell (1⭐)
        </button>
        <button
          className="btn small"
          disabled={busy}
          onClick={async () => {
            const dish = result?.payload.dish as { word?: string } | undefined;
            say(dish?.word ? `Last dish: ${dish.word}` : 'No dish served yet');
          }}
        >
          last dish
        </button>
      </div>

      {pop ? (
        <div className="dish-pop">
          <div className="word">{pop.word}</div>
          <div className="plate">{pop.secret ? '🏆' : '🍽️'}</div>
          <div className="delta">+{pop.delta}</div>
          <div className="meta">
            COMBO ×{pop.combo} {pop.golden > 0 ? `· +${pop.golden} GOLDEN ⭐` : ''}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function describeOrder(
  order: { kind: string; min_length: number; required_letter: string; theme: string; streak_needed: number; streak_left: number } | null,
  me: PlayerView | null,
): string {
  if (!order) return me?.order ? 'Reading the ticket…' : 'Waiting for the next round…';
  switch (order.kind) {
    case 'MIN_LENGTH':
      return `Prepare at least a ${order.min_length}-letter dish`;
    case 'CONTAINS_LETTER':
      return `Something with “${order.required_letter.toUpperCase()}”, chef!`;
    case 'THEME':
      return `Surprise me from the ${order.theme} menu`;
    case 'STREAK':
      return `${order.streak_left} dishes in a row — go go go!`;
    case 'SPEED':
      return 'Quick! A dish before I change my mind';
    default:
      return 'Prepare a dish from these ingredients';
  }
}
