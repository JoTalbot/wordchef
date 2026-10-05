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
  { id: 'SOLO', title: 'Соло-смена', sub: 'Ты против вечернего наплыва' },
  { id: 'QUICK_COOK', title: 'Быстрая готовка', sub: 'Всем один и тот же заказ' },
  { id: 'CHAOSS', title: 'Хаос-кухня', sub: 'Саботаж разрешён' },
];

// ── RU labels for protocol enums (server speaks codes, players read Russian) ──
const MODE_RU: Record<string, string> = {
  SOLO: 'соло-смена', QUICK_COOK: 'быстрая готовка', CHAOS_KITCHEN: 'хаос-кухня',
};
const KIND_RU: Record<string, string> = {
  SINGLE_WORD: 'любое блюдо', MIN_LENGTH: 'минимум букв', CONTAINS_LETTER: 'нужна буква',
  THEME: 'меню', STREAK: 'серия', SPEED: 'на скорость',
};
const FLAVOR_RU: Record<string, string> = {
  sweet: 'сладкое', savory: 'солёное', umami: 'умами',
  tangy: 'кисленькое', rich: 'насыщенное', classic: 'классика',
};
const THEME_RU: Record<string, string> = {
  'street food': 'уличная еда', bakery: 'пекарня', sushi: 'суши', space: 'космос',
  cyber: 'кибер', ancient: 'древность', 'night market': 'ночной рынок',
};

/** Rejection codes from the engine (orders.check_order / scoring) → human RU. */
function ruReason(reason: string): string {
  if (reason.startsWith('needs_at_least_')) {
    const n = reason.replace(/\D/g, '');
    return `нужно минимум ${n} букв`;
  }
  if (reason.startsWith('needs_letter_')) return `нужна буква «${reason.slice(-1).toUpperCase()}»`;
  if (reason.startsWith('not_on_the_')) {
    const theme = reason.slice('not_on_the_'.length, -'_menu'.length).replace(/_/g, ' ');
    return `нет в меню «${THEME_RU[theme] ?? theme}»`;
  }
  const MAP: Record<string, string> = {
    empty_word: 'пустое слово',
    not_in_dictionary: 'такого слова нет в словаре',
    not_formable_from_tray: 'из этих букв не собрать',
    word_already_served: 'это слово уже подано',
    order_expired: 'заказ сгорел — не успел',
    ok: 'готово',
  };
  return MAP[reason] ?? reason.replace(/_/g, ' ');
}

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
      if (type === 'ROUND_STARTED') say(`Раунд ${event.round_no} — смена пошла!`);
      if (type === 'MATCH_FINISHED') {
        say('Матч завершён!');
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
    const player = await api.register(name || 'Повар');
    setPlayerId(player.player_id);
    say(`Добро пожаловать, ${player.name}!`);
    setScreen('lobby');
  }

  async function createMatch() {
    if (!playerId) return;
    try {
      const view = await api.createMatch(mode, [playerId], rounds, 'street');
      setMatchId(view.match_id);
      setMatch(view);
      setScreen('lobby');
      say(mode === 'SOLO' ? 'Смена готова. Запускаем.' : 'Лобби создано. Передай код матча шефам.');
      if (mode === 'SOLO') {
        const started = await api.startMatch(view.match_id);
        setMatch(started);
        setScreen('game');
        setCompose([]);
      }
    } catch (err) {
      say(String(err instanceof Error ? err.message : err));
    }
  }

  async function joinExistingMatch() {
    if (!playerId || !matchId.trim()) return;
    try {
      const view = await api.joinMatch(matchId.trim(), playerId);
      setMatch(view);
      setScreen('lobby');
      say('Ты вошёл в лобби. Ждём старта шефа.');
    } catch (err) {
      say(String(err instanceof Error ? err.message : err));
    }
  }

  async function startLobbyMatch() {
    if (!matchId) return;
    try {
      const started = await api.startMatch(matchId);
      setMatch(started);
      setScreen('game');
      setCompose([]);
    } catch (err) {
      say(String(err instanceof Error ? err.message : err));
    }
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
        if (dish?.secret_hit) say('Секретное меню раскрыто! +50%');
      } else if (res.action === 'SUBMIT_DISH' && res.accepted) {
        say(`Отклонено: ${ruReason(res.reason)}`);
      } else if (!res.accepted) {
        say(ruReason(res.reason));
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
          <div className="tagline">ГОТОВЬ СЛОВА · ПОДАВАЙ БЛЮДА · ДЕРЖИ ЖАР</div>
        </div>
        <div className="panel">
          <h2>ПРОФИЛЬ ШЕФА</h2>
          <div className="row">
            <input
              className="field"
              placeholder="Имя вашего шефа"
              value={name}
              maxLength={24}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void register()}
            />
          </div>
          <button className="btn primary" style={{ width: '100%', marginTop: 10 }} onClick={() => void register()}>
            ВОЙТИ НА КУХНЮ
          </button>
        </div>
        <div className="panel">
          <div className="gc-banner" style={{ backgroundImage: "url(img/howto.webp)" }} />
          <h2>КАК ГОТОВИТЬ</h2>
          <div style={{ fontSize: 13, lineHeight: 1.8, color: 'var(--muted)' }}>
            🍳 Тапай буквы и собирай <b style={{ color: 'var(--text)' }}>блюдо</b> (слово) под заказ гостя.<br />
            🔥 Успех кормит <b style={{ color: 'var(--accent)' }}>Жар</b> и <b style={{ color: 'var(--green)' }}>Комбо</b> — множители выше, заказы сложнее.<br />
            🌶️ Заряди <b style={{ color: 'var(--red)' }}>Приправу</b> на ×2 очка… но провал сжигает комбо.<br />
            ⭐ Редкие буквы — <b style={{ color: 'var(--gold)' }}>золотые ингредиенты</b>: трать их на время, замену букв или бусты.<br />
            🍽️ Короткие <b style={{ color: 'var(--text)' }}>заготовки</b> копят очки и не рвут комбо.
          </div>
        </div>
        <div className="footer-note">
          всё считает сервер · каждое блюдо запечатано на станке Prolepsis<br />
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
          <div className="tagline">СБОР СМЕНЫ</div>
        </div>
        <div className="gc-banner" style={{ backgroundImage: "url(img/lobby_banner.webp)" }} />
        <div className="panel">
          <h2>РЕЖИМ ИГРЫ</h2>
          <div className="select-grid">
            {MODES.map((m) => (
              <button
                key={m.id}
                className={`select-btn mode-banner ${mode === (m.id === 'CHAOSS' ? 'CHAOS_KITCHEN' : m.id) ? 'active' : ''}`}
                style={{ backgroundImage: `url(img/${m.id === 'CHAOSS' ? 'mode_chaos' : 'mode_quick'}.webp)` }}
                onClick={() => setMode(m.id === 'CHAOSS' ? 'CHAOS_KITCHEN' : m.id)}
              >
                <div className="title">{m.title}</div>
                <div className="sub">{m.sub}</div>
              </button>
            ))}
          </div>
          <h2 style={{ marginTop: 14 }}>РАУНДЫ</h2>
          <div className="row">
            {[2, 3, 5].map((r) => (
              <button key={r} className={`btn small ${rounds === r ? 'primary' : ''}`} onClick={() => setRounds(r)}>
                {r} {r === 5 ? 'раундов' : 'раунда'}
              </button>
            ))}
          </div>
          {!matchId ? (
            <>
              <button className="btn primary" style={{ width: '100%', marginTop: 14 }} onClick={() => void createMatch()}>
                СОЗДАТЬ ЛОББИ
              </button>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 8, textAlign: 'center' }}>
                Для соло матч стартует сразу. Для мультиплеера сначала создаётся лобби.
              </div>
              {mode !== 'SOLO' ? (
                <div style={{ marginTop: 14, borderTop: '1px solid var(--line)', paddingTop: 14 }}>
                  <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 7, textAlign: 'center' }}>
                    ИЛИ ВОЙТИ В ГОТОВЫЙ МАТЧ
                  </div>
                  <input
                    className="field"
                    placeholder="Код матча, например m_ab12cd"
                    value={matchId}
                    onChange={(e) => setMatchId(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && void joinExistingMatch()}
                  />
                  <button className="btn" style={{ width: '100%', marginTop: 8 }} onClick={() => void joinExistingMatch()}>
                    ВОЙТИ В ЛОББИ
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <>
              <div className="panel" style={{ marginTop: 14, marginBottom: 0 }}>
                <h2>ЛОББИ · {matchId}</h2>
                <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
                  Передай этот код другим шефам. Сервер хранит состав матча и обновляет его через WebSocket.
                </div>
                {(match?.leaderboard ?? []).map((row: BoardRow) => (
                  <div key={row.player_id} className="board-row">
                    <div className="pos">👨‍🍳</div>
                    <div className="who">{row.display_name}</div>
                    <div className="pts">{row.player_id === playerId ? 'ты' : 'готов'}</div>
                  </div>
                ))}
                <button className="btn primary" style={{ width: '100%', marginTop: 12 }} onClick={() => void startLobbyMatch()}>
                  НАЧАТЬ СМЕНУ
                </button>
              </div>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 8, textAlign: 'center' }}>
                Шефы могут войти до старта. Максимум 8 игроков.
              </div>
            </>
          )}
        </div>
      </div>
    );
  }

  if (screen === 'results') {
    const board = match?.leaderboard ?? me?.leaderboard ?? [];
    return (
      <div className="shell">
        <div className="brand">
          <h1>СМЕНА ОКОНЧЕНА</h1>
          <div className="tagline">{MODE_RU[match?.mode ?? ''] ?? match?.mode}</div>
        </div>
        <div className="gc-banner" style={{ backgroundImage: "url(img/results_banner.webp)" }} />
        <div className="panel">
          <h2>ИТОГИ</h2>
          {board.map((row: BoardRow) => (
            <div key={row.player_id} className={`board-row ${row.player_id === playerId ? 'me' : ''}`}>
              <div className="pos">#{row.position}</div>
              <div className="who">
                {row.display_name}
                {row.best_word ? <span style={{ color: 'var(--muted)' }}> · лучшее «{row.best_word}»</span> : null}
              </div>
              <div className="pts">{row.score.toLocaleString()}</div>
            </div>
          ))}
        </div>
        <div className="row">
          <button className="btn" onClick={() => { setScreen('lobby'); setMatchId(''); setMatch(null); setMe(null); }}>
            НОВЫЙ МАТЧ
          </button>
          <button
            className="btn primary"
            onClick={async () => {
              const verdict = await api.verify(matchId);
              say(`Проверка реплея: ${verdict.verdict}`);
            }}
          >
            РЕПЛЕЙ И ПРОВЕРКА
          </button>
        </div>
        <div className="footer-note">
          каждый результат запечатан как Prolepsis-исполнение — любой матч воспроизводится байт-в-байт
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
    <div
      className="shell gc-shell-bg"
      style={{ backgroundImage: "linear-gradient(rgba(22, 13, 8, 0.62), rgba(22, 13, 8, 0.74)), url(img/bg_game_kitchen.webp)" }}
    >
      {toasts.map((t) => (
        <div key={t.id} className="toast">{t.text}</div>
      ))}

      <div className="brand" style={{ paddingBottom: 4 }}>
        <h1 style={{ fontSize: 28 }}>WORD CHEF</h1>
      </div>

      <div className="hud">
        <div className="hud-card heat">
          <div className="label"><img className="gc-flame" src="img/combo_flame.webp" alt="" /> ЖАР</div>
          <div className="value">{me?.heat ?? 0}</div>
        </div>
        <div className="hud-card combo">
          <div className="label">🍳 КОМБО</div>
          <div className="value">×{(1 + Math.min(4, (me?.combo ?? 0) * 0.25)).toFixed(2)}</div>
        </div>
        <div className="hud-card score">
          <div className="label">⭐ ОЧКИ</div>
          <div className="value">{(me?.score ?? 0).toLocaleString()}</div>
        </div>
      </div>

      <div className="order-card">
        <div className="customer">
          <img
            className="gc-customer"
            src={`img/guest_${["street", "bakery", "sushi", "space", "cyber", "ancient", "midnight"][(match?.round_no ?? 1) % 7]}.webp`}
            alt=""
          />
          <div className="customer-name">
            ГОСТЬ · РАУНД {match?.round_no ?? 1}/{match?.rounds_total ?? 3}
            {order ? ` · ${KIND_RU[order.kind] ?? order.kind}` : ''}
          </div>
          <div className={`timer ${low ? 'low' : ''}`}>
            {String(Math.floor(left / 60)).padStart(2, '0')}:{String(Math.floor(left % 60)).padStart(2, '0')}
          </div>
        </div>
        <div className="request">{describeOrder(order, me)}</div>
        <div className="constraints">
          {order?.flavor_request ? <span className="chip green">хочет: {FLAVOR_RU[order.flavor_request] ?? order.flavor_request}</span> : null}
          {order?.theme ? <span className="chip blue">меню: {THEME_RU[order.theme] ?? order.theme}</span> : null}
          {order?.required_letter ? <span className="chip">нужна буква «{order.required_letter.toUpperCase()}»</span> : null}
          {order?.min_length ? <span className="chip">≥ {order.min_length} букв</span> : null}
          {order?.has_secret ? <span className="chip gold">где-то здесь секретное меню…</span> : null}
          {me?.boost_armed ? <span className="chip gold">буст ×1.5 заряжен</span> : null}
          {order && order.streak_left > 1 ? <span className="chip blue">серия: осталось {order.streak_left}</span> : null}
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
            : <span className="blank">ТАПАЙ БУКВЫ</span>}
          <span className="cursor" />
        </div>
        <div className="actions">
          <button className="btn primary" disabled={busy || !composedWord} onClick={() => void cook()}>
            🍽️ ГОТОВИТЬ
          </button>
          <button
            className={`btn spice ${spiceArmed ? 'armed' : ''}`}
            disabled={busy || (me?.spice_charges ?? 0) <= 0}
            onClick={() => setSpiceArmed((s) => !s)}
          >
            <img className="gc-spice" src="img/spice.webp" alt="" /> ПРИПРАВА {me?.spice_charges ?? 0}
          </button>
          <button className="btn" disabled={busy || composedWord.length > 3 || composedWord.length < 2} onClick={() => void prep()}>
            ЗАГОТОВКА
          </button>
        </div>
        <div className="utility">
          <button className="btn small" disabled={busy || (me?.golden ?? 0) < 1} onClick={() => void golden('patience')}>
            ⏱ +15с ({me?.golden ?? 0}⭐)
          </button>
          <button className="btn small" disabled={busy || (me?.golden ?? 0) < 3} onClick={() => void golden('restock')}>
            🧺 замена букв (3⭐)
          </button>
          <button className="btn small" disabled={busy} onClick={() => void skip()}>
            пропустить заказ
          </button>
        </div>
      </div>

      <div className={`feedback ${result?.reason === 'dish_served' ? 'good' : result ? 'bad' : ''}`}>
        {result
          ? result.reason === 'dish_served'
            ? `Вкусно! +${result.payload.score_delta ?? 0} очков`
            : ruReason(result.reason)
          : 'Подай блюдо — получишь очки'}
      </div>

      <div className="panel">
        <h2>
          <img className="gc-inline-icon" src="img/ui_trophy.webp" alt="" /> ТАБЛИЦА
        </h2>
        {(match?.leaderboard ?? []).map((row: BoardRow) => (
          <div key={row.player_id} className={`board-row ${row.player_id === playerId ? 'me' : ''}`}>
            <div className="pos">#{row.position}</div>
            <div className="who">{row.display_name}</div>
            <div className="pts">{row.score.toLocaleString()}</div>
          </div>
        ))}
        {chaosFeed.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div className="gc-banner slim" style={{ backgroundImage: "url(img/chaos_feed.webp)" }} />
            <h2>ХАОС-ЛЕНТА</h2>
            {chaosFeed.map((line, i) => (
              <div key={i} style={{ fontSize: 12, color: 'var(--accent2)', padding: '2px 0' }}>⚡ {line}</div>
            ))}
          </div>
        )}
      </div>

      <div className="row">
        <button className="btn small" disabled={busy || (me?.golden ?? 0) < 1} onClick={() => void ringBell()}>
          <img className="gc-inline-icon" src="img/ui_bell.webp" alt="" /> звон в хаос-колокол (1⭐)
        </button>
        <button
          className="btn small"
          disabled={busy}
          onClick={async () => {
            const dish = result?.payload.dish as { word?: string } | undefined;
            say(dish?.word ? `Последнее блюдо: ${dish.word}` : 'Блюдо пока не подано');
          }}
        >
          последнее блюдо
        </button>
      </div>

      {pop ? (
        <div className="dish-pop">
          <div className="word">{pop.word}</div>
          <div className="plate">{pop.secret ? '🏆' : '🍽️'}</div>
          <div className="delta">+{pop.delta}</div>
          <div className="meta">
            КОМБО ×{pop.combo} {pop.golden > 0 ? `· +${pop.golden} ЗОЛОТО ⭐` : ''}
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
  if (!order) return me?.order ? 'Читаем заказ…' : 'Ждём следующий раунд…';
  switch (order.kind) {
    case 'MIN_LENGTH':
      return `Приготовь блюдо минимум из ${order.min_length} букв`;
    case 'CONTAINS_LETTER':
      return `Блюдо с буквой «${order.required_letter.toUpperCase()}», шеф!`;
    case 'THEME':
      return `Удиви меня из меню «${THEME_RU[order.theme] ?? order.theme}»`;
    case 'STREAK':
      return `${order.streak_left} блюда подряд — давай-давай!`;
    case 'SPEED':
      return 'Быстро! Блюдо, пока я не передумал';
    default:
      return 'Приготовь блюдо из этих букв';
  }
}
