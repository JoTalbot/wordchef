"use client";

/**
 * Word Chef — campaign UI in the «Повар Слов» tradition:
 * a letter wheel (tap or swipe), a crossword board that cooks a dish as
 * answers appear, bonus-word jar, coins, hints and the kitchen map.
 * Multiplayer stays available behind the 🏆 button.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import GameClient from "./GameClient";
import { classifyWord, generateLevel, type Level } from "../lib/levels";

type Screen = "home" | "level" | "grand" | "multi";

const DISH_ART: Record<string, string> = {
  "Салат": "img/dish_salad.jpg",
  "Пицца": "img/dish_pizza.jpg",
  "Суши": "img/dish_sushi.jpg",
  "Лапша": "img/dish_noodles.jpg",
  "Торт": "img/dish_cake.jpg",
  "Рагу": "img/dish_ragout.jpg",
  "Пельмени": "img/dish_pelmeni.jpg",
  "Креветки": "img/dish_shrimp.jpg",
  "Капкейк": "img/dish_cupcake.jpg",
  "Сэндвич": "img/dish_sandwich.jpg",
  "Суп": "img/dish_soup.jpg",
  "Рулет": "img/dish_roll.jpg",
  "Борщ": "img/dish_borsch.jpg",
  "Шашлык": "img/dish_shashlik.jpg",
  "Блины": "img/dish_bliny.jpg",
  "Пирог": "img/dish_pirog.jpg",
  "Пончик": "img/dish_donut.jpg",
  "Вафли": "img/dish_waffles.jpg",
  "Мороженое": "img/dish_icecream.jpg",
  "Тако": "img/dish_taco.jpg",
  "Плов": "img/dish_plov.jpg",
  "Смузи": "img/dish_smoothie.jpg",
  "Бургер": "img/dish_burger.jpg",
  "Шаурма": "img/dish_shaurma.jpg",
  "Фалафель": "img/dish_falafel.jpg",
  "Омлет": "img/dish_omlet.jpg",
  "Кулич": "img/dish_kulich.jpg",
  "Гуляш": "img/dish_gulyash.jpg",
  "Чебурек": "img/dish_cheburek.jpg",
  "Хинкали": "img/dish_hinkali.jpg",
  "Рамен": "img/dish_ramen.jpg",
  "Буррито": "img/dish_burrito.jpg",
  "Паэлья": "img/dish_paella.jpg",
  "Штрудель": "img/dish_strudel.jpg",
  "Чизкейк": "img/dish_cheesecake.jpg",
  "Кебаб": "img/dish_kebab.jpg",
  "Окрошка": "img/dish_okroshka.jpg",
  "Сырники": "img/dish_syrniki.jpg",
};

const GUEST_ART: Record<string, { img: string; nick: string }> = {
  street: { img: "img/guest_street.jpg", nick: "Марко" },
  bakery: { img: "img/guest_bakery.jpg", nick: "Бабушка Зита" },
  sushi: { img: "img/guest_sushi.jpg", nick: "Юки" },
  space: { img: "img/guest_space.jpg", nick: "Космо-шеф" },
  cyber: { img: "img/guest_cyber.jpg", nick: "Нейро" },
  ancient: { img: "img/guest_ancient.jpg", nick: "Мудрец" },
  midnight: { img: "img/guest_midnight.jpg", nick: "Кот Борис" },
};

function guestArt(kitchen: string) {
  return GUEST_ART[kitchen] ?? GUEST_ART.street;
}

/** One flavour line per guest per level — stable per level, so replays read the same. */
const GUEST_LINES: Record<string, string[]> = {
  street: [
    "Слышал, у тебя тут лучшая кухня в квартале. Не подведи!",
    "Я проголодался ещё на прошлом уровне.",
    "Готовь быстрее — у меня перерыв всего на обед.",
  ],
  bakery: [
    "Внучек, главное — не пересуши тесто.",
    "Слово — как дрожжи: дай ему подняться.",
    "Сладкое блюдо — и хмурый день светлее.",
  ],
  sushi: [
    "Точность важнее спешки. Хотя спешка тоже не повредит.",
    "Режь чисто: лишняя буква — лишний рис.",
    "Короткое слово — острый вкус.",
  ],
  space: [
    "Гравитация тут слабая, а вот аппетит — нет.",
    "На орбите суп не прольётся. Проверим?",
    "Собери слово из звёзд-букв, пока топливо не кончилось.",
  ],
  cyber: [
    "Оптимизирую вкус до 100%. Твой ход, шеф.",
    "Ошибка 404: блюдо не найдено.",
    "Расклад посчитан. Попробуй меня обогнать.",
  ],
  ancient: [
    "В начале было Слово. В конце — ужин.",
    "Мудрость — в специях, терпение — в варке.",
    "Ищи слово там, где его не видно.",
  ],
  midnight: [
    "Мяу. Рыбу я уже съел, остальное — твоё.",
    "Ночью вкус острее, а слова — тише.",
    "Мур. Продолжай, я слушаю ухом.",
  ],
};

function guestLine(kitchen: string, levelNo: number): string {
  const lines = GUEST_LINES[kitchen] ?? GUEST_LINES.street;
  return lines[(Math.max(1, levelNo) - 1) % lines.length];
}

function dishArt(name: string): string {
  return DISH_ART[name] ?? "img/dish_salad.jpg";
}

const KITCHEN_RU: Record<string, { name: string; emoji: string }> = {
  street: { name: "Уличная кухня", emoji: "🌭" },
  bakery: { name: "Пекарня", emoji: "🥐" },
  sushi: { name: "Суши-бар", emoji: "🍣" },
  space: { name: "Космическая кухня", emoji: "🚀" },
  cyber: { name: "Кибер-кухня", emoji: "🤖" },
  ancient: { name: "Древняя кухня", emoji: "🏛️" },
  midnight: { name: "Полночная кухня", emoji: "🌙" },
};

const HINT_COST = 25;

function load(key: string, dflt: number): number {
  if (typeof window === "undefined") return dflt;
  const v = Number(window.localStorage.getItem(key));
  return Number.isFinite(v) && v > 0 ? v : dflt;
}

export default function ChefGame() {
  const [screen, setScreen] = useState<Screen>("home");
  const [coins, setCoins] = useState(0);
  const [levelNo, setLevelNo] = useState(1);
  const [grandBest, setGrandBest] = useState(0);
  const [toast, setToast] = useState("");

  useEffect(() => {
    setCoins(load("wc_coins", 100));
    setLevelNo(load("wc_level", 1));
    setGrandBest(load("wc_grand_best", 0));
  }, []);

  useEffect(() => {
    if (typeof window !== "undefined") window.localStorage.setItem("wc_coins", String(coins));
  }, [coins]);
  useEffect(() => {
    if (typeof window !== "undefined" && screen !== "grand")
      window.localStorage.setItem("wc_level", String(levelNo));
  }, [levelNo, screen]);
  useEffect(() => {
    if (typeof window !== "undefined")
      window.localStorage.setItem("wc_grand_best", String(grandBest));
  }, [grandBest]);

  const flash = useCallback((msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(""), 1600);
  }, []);

  return (
    <div className="wc-shell">
      <div className="wc-splash" aria-hidden style={{ backgroundImage: "url(img/splash.jpg)" }} />
      <div className="wc-top">
        <div className="wc-logo">WORD CHEF</div>
        <div className="wc-pill"><img className="wc-coin" src="img/coins.jpg" alt="" /> {coins}</div>
      </div>

      {screen === "home" && (
        <HomeScreen
          levelNo={levelNo}
          grandBest={grandBest}
          onPlay={() => setScreen("level")}
          onGrand={() => setScreen("grand")}
          onMulti={() => setScreen("multi")}
        />
      )}
      {screen === "level" && (
        <LevelScreen
          levelNo={levelNo}
          coins={coins}
          addCoins={(n) => setCoins((c) => c + n)}
          spendCoins={(n) => setCoins((c) => Math.max(0, c - n))}
          onNext={() => setLevelNo((n) => n + 1)}
          onHome={() => setScreen("home")}
          flash={flash}
        />
      )}
      {screen === "grand" && (
        <LevelScreen
          levelNo={2000 + grandBest + 1}
          coins={coins}
          addCoins={(n) => {
            setCoins((c) => c + n);
            setGrandBest((b) => b + 1);
          }}
          spendCoins={(n) => setCoins((c) => Math.max(0, c - n))}
          onNext={() => undefined}
          onHome={() => setScreen("home")}
          grand
          flash={flash}
        />
      )}
      {screen === "multi" && (
        <div>
          <button className="wc-btn ghost" onClick={() => setScreen("home")}>
            ← Назад в кухню
          </button>
          <GameClient />
        </div>
      )}

      {toast && <div className="wc-toast">{toast}</div>}
    </div>
  );
}

function HomeScreen({
  levelNo, grandBest, onPlay, onGrand, onMulti,
}: {
  levelNo: number; grandBest: number;
  onPlay: () => void; onGrand: () => void; onMulti: () => void;
}) {
  const preview = useMemo(() => generateLevel(levelNo), [levelNo]);
  const kitchens = Object.entries(KITCHEN_RU);
  return (
    <div>
      <div className="wc-hero">
        <img src="img/hero.jpg" alt="" />
        <div className="wc-hero-title">
          <img className="wc-avatar-chip" src="img/chef_avatar.jpg" alt="" />
          Готовь слова — корми гостей!
        </div>
      </div>
      <div className="wc-card">
        <div className="wc-dish-hero">
          <img className="wc-dish-img" src={dishArt(preview.dish.name)} alt="" />
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 900, fontSize: 20 }}>
              Уровень {levelNo} · {preview.dish.name}
            </div>
            <div style={{ opacity: 0.75, marginTop: 2 }}>
              Собери слова из букв — и блюдо будет готово!
            </div>
            <div className="wc-bar">
              <div style={{ width: `${Math.min(100, ((levelNo - 1) % 20) * 5 + 5)}%` }} />
            </div>
          </div>
        </div>
        <button className="wc-btn green" onClick={onPlay}>
          ▶ Играть
        </button>
        <button
          className="wc-btn wc-banner-btn"
          style={{ backgroundImage: "url(img/grandtour.jpg)" }}
          onClick={onGrand}
        >
          <span>🌀 Гранд Тур</span>
          <small>бесконечный режим{grandBest > 0 ? ` · рекорд ${grandBest}` : ""}</small>
        </button>
        <button
          className="wc-btn wc-banner-btn"
          style={{ backgroundImage: "url(img/mode_chaos.jpg)" }}
          onClick={onMulti}
        >
          <span>
            <img className="wc-inline-icon" src="img/ui_trophy.jpg" alt="" /> Мультиплеер
          </span>
          <small>Quick Cook · Chaos Kitchen</small>
        </button>
      </div>

      <div className="wc-card">
        <div className="wc-banner-strip" style={{ backgroundImage: "url(img/grand_map.jpg)" }}>
          <span>🗺 Карта кухонь</span>
        </div>
        {kitchens.map(([id, info], i) => {
          const from = i * 20 + 1;
          const unlocked = levelNo >= from;
          return (
            <div key={id} className={`wc-map-row ${unlocked ? "" : "locked"}`}>
              <div className="wc-map-emoji">
                <img src={`img/kitchen_${id}.jpg`} alt="" className={unlocked ? "" : "locked"} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 800 }}>{info.name}</div>
                <div style={{ opacity: 0.7, fontSize: 13 }}>
                  Уровни {from}–{from + 19}
                </div>
              </div>
              <img
                className="wc-medal"
                src={levelNo >= from + 20 ? "img/ach_gold.jpg" : unlocked ? "img/ach_silver.jpg" : "img/ach_bronze.jpg"}
                alt=""
                style={{ opacity: unlocked ? 1 : 0.45 }}
              />
              {levelNo >= from && levelNo < from + 20 && (
                <div className="wc-pill" style={{ padding: "4px 10px", fontSize: 13 }}>
                  сейчас
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function LevelScreen({
  levelNo, coins, addCoins, spendCoins, onNext, onHome, grand, flash,
}: {
  levelNo: number; coins: number;
  addCoins: (n: number) => void; spendCoins: (n: number) => void;
  onNext: () => void; onHome: () => void; grand?: boolean;
  flash: (m: string) => void;
}) {
  const level = useMemo<Level>(() => generateLevel(levelNo), [levelNo]);
  const [found, setFound] = useState<string[]>([]);
  const [bonusFound, setBonusFound] = useState<string[]>([]);
  const [current, setCurrent] = useState<number[]>([]); // wheel indices
  const [wheel, setWheel] = useState<string[]>(level.wheel);
  const [hinted, setHinted] = useState<Record<string, boolean>>({});
  const [shake, setShake] = useState(false);
  const [done, setDone] = useState(false);
  const wheelRef = useRef<HTMLDivElement | null>(null);
  const dragging = useRef(false);
  const usedInSwipe = useRef<Set<number>>(new Set());

  useEffect(() => {
    setFound([]); setBonusFound([]); setCurrent([]); setHinted({}); setDone(false);
    setWheel(generateLevel(levelNo).wheel);
  }, [levelNo]);

  const boardWords = level.board.map((b) => b.word);
  const remaining = boardWords.filter((w) => !found.includes(w));

  const submitWord = useCallback((letters: string[]) => {
    const word = letters.join("").toLowerCase();
    if (word.length < 2) return;
    const kind = classifyWord(level, word);
    if (kind === "board") {
      if (found.includes(word)) { flash("Уже в блюде!"); }
      else {
        setFound((f) => [...f, word]);
        addCoins(2 * word.length);
        flash(`+${2 * word.length} 🪙 · ${word}`);
      }
    } else if (kind === "bonus") {
      if (bonusFound.includes(word)) flash("Бонус уже найден");
      else {
        setBonusFound((b) => [...b, word]);
        addCoins(word.length);
        flash(`Бонусное слово ${word} · +${word.length} 🪙`);
      }
    } else {
      setShake(true);
      window.setTimeout(() => setShake(false), 300);
    }
    setCurrent([]);
  }, [level, found, bonusFound, addCoins, flash]);

  useEffect(() => {
    if (!done && boardWords.length > 0 && boardWords.every((w) => found.includes(w))) {
      setDone(true);
      const reward = 30 + levelNo;
      addCoins(reward);
      if (typeof window !== "undefined" && typeof fetch === "function") {
        // best-effort server sync (offline play stays fully local)
        fetch("/api/levels/complete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ level_no: levelNo, words: found, bonus: bonusFound }),
        }).catch(() => undefined);
      }
      void reward;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [found]);

  // ── wheel geometry ──
  const size = typeof window !== "undefined" ? Math.min(320, window.innerWidth * 0.86) : 320;
  const radius = size * 0.36;
  const centers = useMemo(() => {
    const n = wheel.length || 1;
    return wheel.map((_, i) => {
      const a = (-Math.PI / 2) + (i * 2 * Math.PI) / n;
      return { x: size / 2 + radius * Math.cos(a), y: size / 2 + radius * Math.sin(a) };
    });
  }, [wheel, size, radius]);

  const pickTile = useCallback((idx: number) => {
    if (current.includes(idx)) return;
    setCurrent((c) => [...c, idx]);
  }, [current]);

  const hitTest = useCallback((x: number, y: number): number | null => {
    let best: number | null = null;
    let bestD = 34 * 34;
    centers.forEach((p, i) => {
      const d = (p.x - x) ** 2 + (p.y - y) ** 2;
      if (d < bestD) { bestD = d; best = i; }
    });
    return best;
  }, [centers]);

  const onPointerDown = (e: React.PointerEvent) => {
    dragging.current = true;
    usedInSwipe.current = new Set();
    const rect = wheelRef.current?.getBoundingClientRect();
    if (!rect) return;
    const hit = hitTest(e.clientX - rect.left, e.clientY - rect.top);
    if (hit !== null) {
      usedInSwipe.current.add(hit);
      pickTile(hit);
    }
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragging.current) return;
    const rect = wheelRef.current?.getBoundingClientRect();
    if (!rect) return;
    const hit = hitTest(e.clientX - rect.left, e.clientY - rect.top);
    if (hit !== null && !usedInSwipe.current.has(hit)) {
      usedInSwipe.current.add(hit);
      pickTile(hit);
    }
  };
  const onPointerUp = () => {
    if (!dragging.current) return;
    dragging.current = false;
    if (current.length >= 2) submitWord(current.map((i) => wheel[i]));
    else setCurrent([]);
  };

  // keyboard support (desktop)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter") { submitWord(current.map((i) => wheel[i])); return; }
      if (e.key === "Backspace") { setCurrent((c) => c.slice(0, -1)); return; }
      const ch = e.key.toLowerCase().replace("ё", "е");
      if (/^[а-я]$/.test(ch)) {
        const idx = wheel.findIndex((l, i) => l === ch && !current.includes(i));
        if (idx >= 0) pickTile(idx);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, wheel, pickTile, submitWord]);

  const doHint = () => {
    if (coins < HINT_COST) { flash("Мало монет — нужно 25 🪙"); return; }
    const target = remaining[0];
    if (!target) return;
    const key0 = `${target}:0`;
    const nextIdx = [...target].findIndex((_, i) => !hinted[`${target}:${i}`]);
    const key = `${target}:${nextIdx < 0 ? 0 : nextIdx}`;
    spendCoins(HINT_COST);
    setHinted((h) => ({ ...h, [key]: true, ...(nextIdx < 0 ? { [key0]: true } : {}) }));
  };

  const doShuffle = () => setWheel((w) => {
    const arr = [...w];
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  });

  // grid geometry
  const grid = useMemo(() => {
    const rs = level.board.map((b) => b.r);
    const cs = level.board.map((b) => b.c);
    const minR = Math.min(...rs), minC = Math.min(...cs);
    const maxR = Math.max(...level.board.map((b) => b.r + (b.across ? 0 : b.word.length - 1)));
    const maxC = Math.max(...level.board.map((b) => b.c + (b.across ? b.word.length - 1 : 0)));
    const cells: Record<string, { letter: string; word: string; idx: number }[]> = {};
    level.board.forEach((b) => {
      [...b.word].forEach((ch, i) => {
        const r = b.r + (b.across ? 0 : i);
        const c = b.c + (b.across ? i : 0);
        const k = `${r},${c}`;
        (cells[k] ||= []).push({ letter: ch, word: b.word, idx: i });
      });
    });
    return { minR, minC, rows: maxR - minR + 1, cols: maxC - minC + 1, cells };
  }, [level]);

  const cellState = (r: number, c: number) => {
    const list = grid.cells[`${r},${c}`];
    if (!list) return null;
    const revealed = list.some((x) => hinted[`${x.word}:${x.idx}`]);
    const filled = list.some((x) => found.includes(x.word));
    return { letter: list[0].letter, filled, revealed, hot: list.some((x) => remaining.includes(x.word)) };
  };

  return (
    <div>
      <div className="wc-level-header">
        <button className="wc-tool" onClick={onHome}>←</button>
        <div className="wc-pill" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <img className="wc-dish-img small" src={dishArt(level.dish.name)} alt="" />
          <span>
            {grand ? "🌀 Гранд Тур" : `Уровень ${levelNo}`} · {level.dish.name}
          </span>
        </div>
        <button className="wc-tool" onClick={doHint} title={`Подсказка · ${HINT_COST} 🪙`} aria-label="Подсказка">
          <img src="img/ui_hint.jpg" alt="" />
        </button>
      </div>

      <div className="wc-card" style={{ marginTop: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontWeight: 800, gap: 10 }}>
          <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
            <img
              className="wc-guest"
              src={guestArt(level.kitchen).img}
              alt=""
            />
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              Блюдо: {found.length}/{boardWords.length} · гость {guestArt(level.kitchen).nick}
            </span>
          </span>
          <span className="wc-bonus-jar">
            <img className="wc-jar-img" src="img/bonus_jar.jpg" alt="" /> {bonusFound.length}
          </span>
        </div>
        <div className="wc-bar">
          <div style={{ width: `${(found.length / Math.max(1, boardWords.length)) * 100}%` }} />
        </div>
        <div className="wc-guest-say">«{guestLine(level.kitchen, levelNo)}»</div>
      </div>

      <div className={`wc-grid ${shake ? "wc-shake" : ""}`}
        style={{ gridTemplateColumns: `repeat(${grid.cols}, 38px)` }}>
        {Array.from({ length: grid.rows }).map((_, ri) =>
          Array.from({ length: grid.cols }).map((_, ci) => {
            const r = grid.minR + ri, c = grid.minC + ci;
            const st = cellState(r, c);
            if (!st) return <div key={`${r},${c}`} />;
            return (
              <div
                key={`${r},${c}`}
                className={`wc-cell ${st.filled ? "filled" : st.revealed ? "hinted" : "open"} ${st.hot ? "hot" : ""}`}
              >
                {st.filled || st.revealed ? st.letter : ""}
              </div>
            );
          }),
        )}
      </div>

      <div className="wc-wordbar">
        {current.length === 0 ? (
          <span style={{ opacity: 0.35, fontSize: 18, letterSpacing: 0 }}>
            Свайпни или тапни буквы…
          </span>
        ) : (
          current.map((i, k) => <span key={k}>{wheel[i]}</span>)
        )}
      </div>

      <div
        className="wc-wheel"
        ref={wheelRef}
        style={{ width: size, height: size }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {wheel.map((letter, i) => (
          <div
            key={`${letter}-${i}`}
            className={`wc-tile ${current.includes(i) ? "on" : ""}`}
            style={{
              left: centers[i].x,
              top: centers[i].y,
              // picked letters become golden ingredients
              ...(current.includes(i)
                ? { backgroundImage: "url(img/ui_gold_tile.jpg)", backgroundSize: "cover" }
                : null),
            }}
          >
            {letter}
          </div>
        ))}
      </div>

      <div className="wc-wheel-tools">
        <button className="wc-tool" onClick={doShuffle}>🔀</button>
        <button className="wc-tool" onClick={() => submitWord(current.map((i) => wheel[i]))}>✅</button>
        <button className="wc-tool" onClick={() => setCurrent([])}>⌫</button>
      </div>

      {done && (
        <div className="wc-overlay">
          <div className="wc-modal" style={{ backgroundImage: "url(img/celebrate.jpg)" }}>
            <div className="confetti">
              {["#ff7a2f", "#37b24d", "#3f97d4", "#f2c14e", "#e03131"].map((c, i) => (
                <i key={i} style={{
                  left: `${8 + i * 18}%`, background: c,
                  animationDelay: `${i * 0.35}s`,
                }} />
              ))}
            </div>
            <div className="wc-banner-strip" style={{ backgroundImage: "url(img/recipe_book.jpg)" }}>
              <span>📖 Новый рецепт записан!</span>
            </div>
            <img className="big-img" src={dishArt(level.dish.name)} alt="" />
            <div style={{ fontSize: 24, fontWeight: 900 }}>{level.dish.name} готов!</div>
            <div className="wc-stars">
              <img className="wc-medal big" src="img/ach_gold.jpg" alt="" />
              ⭐⭐⭐
            </div>
            <div style={{ fontWeight: 800 }}>
              +{30 + levelNo} 🪙 · бонусных слов: {bonusFound.length}
            </div>
            <button
              className="wc-btn green"
              onClick={() => {
                setDone(false);
                if (grand) { flash("Готовим следующий заказ!"); window.location.reload(); }
                else onNext();
              }}
            >
              {grand ? "🌀 Следующий заказ" : "▶ Дальше"}
            </button>
            <button className="wc-btn ghost" onClick={onHome}>На карту кухонь</button>
          </div>
        </div>
      )}
    </div>
  );
}
