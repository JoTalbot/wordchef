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
import { dishNote } from "../lib/dishNotes";

type Screen = "home" | "level" | "grand" | "multi";

const DISH_ART: Record<string, string> = {
  "Салат": "img/dish_salad.webp",
  "Пицца": "img/dish_pizza.webp",
  "Суши": "img/dish_sushi.webp",
  "Лапша": "img/dish_noodles.webp",
  "Торт": "img/dish_cake.webp",
  "Рагу": "img/dish_ragout.webp",
  "Пельмени": "img/dish_pelmeni.webp",
  "Креветки": "img/dish_shrimp.webp",
  "Капкейк": "img/dish_cupcake.webp",
  "Сэндвич": "img/dish_sandwich.webp",
  "Суп": "img/dish_soup.webp",
  "Рулет": "img/dish_roll.webp",
  "Борщ": "img/dish_borsch.webp",
  "Шашлык": "img/dish_shashlik.webp",
  "Блины": "img/dish_bliny.webp",
  "Пирог": "img/dish_pirog.webp",
  "Пончик": "img/dish_donut.webp",
  "Вафли": "img/dish_waffles.webp",
  "Мороженое": "img/dish_icecream.webp",
  "Тако": "img/dish_taco.webp",
  "Плов": "img/dish_plov.webp",
  "Смузи": "img/dish_smoothie.webp",
  "Бургер": "img/dish_burger.webp",
  "Шаурма": "img/dish_shaurma.webp",
  "Фалафель": "img/dish_falafel.webp",
  "Омлет": "img/dish_omlet.webp",
  "Кулич": "img/dish_kulich.webp",
  "Гуляш": "img/dish_gulyash.webp",
  "Чебурек": "img/dish_cheburek.webp",
  "Хинкали": "img/dish_hinkali.webp",
  "Рамен": "img/dish_ramen.webp",
  "Буррито": "img/dish_burrito.webp",
  "Паэлья": "img/dish_paella.webp",
  "Штрудель": "img/dish_strudel.webp",
  "Чизкейк": "img/dish_cheesecake.webp",
  "Кебаб": "img/dish_kebab.webp",
  "Окрошка": "img/dish_okroshka.webp",
  "Сырники": "img/dish_syrniki.webp",
  "Печенье": "img/dish_pechenye.webp",
  "Ватрушка": "img/dish_vatrushka.webp",
  "Шербет": "img/dish_sherbet.webp",
  "Мармелад": "img/dish_marmelad.webp",
  "Леденцы": "img/dish_ledency.webp",
  "Шоколад": "img/dish_shokolad.webp",
  "Попкорн": "img/dish_popkorn.webp",
  "Картошка": "img/dish_kartoshka.webp",
  "Лимонад": "img/dish_limonad.webp",
  "Какао": "img/dish_kakao.webp",
  "Оладушки": "img/dish_oladushki.webp",
  "Драники": "img/dish_draniki.webp",
  "Голубцы": "img/dish_golubcy.webp",
  "Бефстроганов": "img/dish_beefstroganoff.webp",
  "Пахлава": "img/dish_pahlava.webp",
  "Эклер": "img/dish_ekler.webp",
};

const GUEST_ART: Record<string, { img: string; nick: string }> = {
  street: { img: "img/guest_street.webp", nick: "Марко" },
  bakery: { img: "img/guest_bakery.webp", nick: "Бабушка Зита" },
  sushi: { img: "img/guest_sushi.webp", nick: "Юки" },
  space: { img: "img/guest_space.webp", nick: "Космо-шеф" },
  cyber: { img: "img/guest_cyber.webp", nick: "Нейро" },
  ancient: { img: "img/guest_ancient.webp", nick: "Мудрец" },
  midnight: { img: "img/guest_midnight.webp", nick: "Кот Борис" },
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

/** The guest reacts while the dish is being assembled (1/3 and 2/3 of the board). */
const GUEST_PROGRESS: Record<string, [string, string]> = {
  street: ["Уже пахнет! Неси сюда.", "Ещё чуть-чуть — и я поверю в тебя."],
  bakery: ["Тесто поднялось — продолжай.", "Почти как у бабушки. Почти!"],
  sushi: ["Рез чище, шеф.", "Ещё немного — и это уровень мастеров."],
  space: ["Системы в норме, кухня летит.", "Орбита близко — добьём."],
  cyber: ["[OK] прогресс зафиксирован.", "[WARN] ещё два слова до апгрейда."],
  ancient: ["Слово к слову — и трапеза близка.", "Мудрость уже видна, шеф."],
  midnight: ["Мур. Уже вкуснее.", "Ещё немного — и я останусь тут жить."],
};

function guestProgress(kitchen: string, found: number, total: number): string | null {
  if (total <= 0) return null;
  const ratio = found / total;
  const pair = GUEST_PROGRESS[kitchen] ?? GUEST_PROGRESS.street;
  if (ratio >= 2 / 3 && found < total) return pair[1];
  if (ratio >= 1 / 3) return pair[0];
  return null;
}

function dishArt(name: string): string {
  return DISH_ART[name] ?? "img/dish_salad.webp";
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

function load(key: string, dflt: number, min = 0): number {
  if (typeof window === "undefined") return dflt;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null || raw.trim() === "") return dflt;
    const value = Number(raw);
    return Number.isSafeInteger(value) && value >= min ? value : dflt;
  } catch {
    return dflt;
  }
}

function save(key: string, value: number): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // The campaign remains playable if browser storage is unavailable.
  }
}

export default function ChefGame() {
  const [screen, setScreen] = useState<Screen>("home");
  const [coins, setCoins] = useState(0);
  const [levelNo, setLevelNo] = useState(1);
  const [grandBest, setGrandBest] = useState(0);
  const [grandRound, setGrandRound] = useState(1);
  const [storageReady, setStorageReady] = useState(false);
  const [toast, setToast] = useState("");
  const toastTimer = useRef<number | null>(null);

  useEffect(() => {
    setCoins(load("wc_coins", 100));
    setLevelNo(load("wc_level", 1, 1));
    setGrandBest(load("wc_grand_best", 0));
    setStorageReady(true);
  }, []);

  useEffect(() => {
    if (storageReady) save("wc_coins", coins);
  }, [coins, storageReady]);
  useEffect(() => {
    if (storageReady && screen !== "grand") save("wc_level", levelNo);
  }, [levelNo, screen, storageReady]);
  useEffect(() => {
    if (storageReady) save("wc_grand_best", grandBest);
  }, [grandBest, storageReady]);

  const flash = useCallback((msg: string) => {
    setToast(msg);
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => {
      setToast("");
      toastTimer.current = null;
    }, 1800);
  }, []);

  useEffect(() => () => {
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
  }, []);

  return (
    <div className="wc-shell">
      <div className="wc-splash" aria-hidden style={{ backgroundImage: "url(img/splash.webp)" }} />
      {screen !== "level" && screen !== "grand" && (
        <div className="wc-top">
          <div className="wc-logo">WORD CHEF</div>
          <div className="wc-pill"><img className="wc-coin" src="img/coins.webp" alt="" /> {coins}</div>
        </div>
      )}

      {screen === "home" && (
        <HomeScreen
          levelNo={levelNo}
          grandBest={grandBest}
          onPlay={() => setScreen("level")}
          onGrand={() => {
            setGrandRound(grandBest + 1);
            setScreen("grand");
          }}
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
          levelNo={2000 + grandRound}
          coins={coins}
          addCoins={(n) => setCoins((c) => c + n)}
          spendCoins={(n) => setCoins((c) => Math.max(0, c - n))}
          onNext={() => setGrandRound((n) => n + 1)}
          onHome={() => setScreen("home")}
          onGrandComplete={() => setGrandBest((best) => Math.max(best, grandRound))}
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

      {toast && <div className="wc-toast" role="status" aria-live="polite">{toast}</div>}
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
        <img src="img/hero.webp" alt="" />
        <div className="wc-hero-title">
          <img className="wc-avatar-chip" src="img/chef_avatar.webp" alt="" />
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
          style={{ backgroundImage: "url(img/grandtour.webp)" }}
          onClick={onGrand}
        >
          <span>🌀 Гранд Тур</span>
          <small>бесконечный режим{grandBest > 0 ? ` · рекорд ${grandBest}` : ""}</small>
        </button>
        <button
          className="wc-btn wc-banner-btn"
          style={{ backgroundImage: "url(img/mode_chaos.webp)" }}
          onClick={onMulti}
        >
          <span>
            <img className="wc-inline-icon" src="img/ui_trophy.webp" alt="" /> Мультиплеер
          </span>
          <small>Quick Cook · Chaos Kitchen</small>
        </button>
      </div>

      <div className="wc-card">
        <div className="wc-banner-strip" style={{ backgroundImage: "url(img/grand_map.webp)" }}>
          <span>🗺 Карта кухонь</span>
        </div>
        {kitchens.map(([id, info], i) => {
          const from = i * 20 + 1;
          const unlocked = levelNo >= from;
          return (
            <div key={id} className={`wc-map-row ${unlocked ? "" : "locked"}`}>
              <div className="wc-map-emoji">
                <img src={`img/kitchen_${id}.webp`} alt="" className={unlocked ? "" : "locked"} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 800 }}>{info.name}</div>
                <div style={{ opacity: 0.7, fontSize: 13 }}>
                  Уровни {from}–{from + 19}
                </div>
              </div>
              <img
                className="wc-medal"
                src={levelNo >= from + 20 ? "img/ach_gold.webp" : unlocked ? "img/ach_silver.webp" : "img/ach_bronze.webp"}
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
  levelNo, coins, addCoins, spendCoins, onNext, onHome, onGrandComplete, grand, flash,
}: {
  levelNo: number; coins: number;
  addCoins: (n: number) => void; spendCoins: (n: number) => void;
  onNext: () => void; onHome: () => void; onGrandComplete?: () => void; grand?: boolean;
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
      flash("Такого слова нет в этом заказе");
    }
    setCurrent([]);
  }, [level, found, bonusFound, addCoins, flash]);

  useEffect(() => {
    if (!done && boardWords.length > 0 && boardWords.every((w) => found.includes(w))) {
      setDone(true);
      const reward = 30 + levelNo;
      addCoins(reward);
      if (grand) onGrandComplete?.();
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
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // Pointer capture is best-effort; ordinary taps still work without it.
    }
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
    // Жест, прошедший по двум и более плиткам, — это свайп: сразу готовим блюдо.
    // Одиночный тап selection не трогает: иначе отпускание пальца стирало
    // букву и «тапай буквы» из подсказки не работало вовсе. Тапами можно
    // набрать слово и отправить его кнопкой ✅.
    if (usedInSwipe.current.size >= 2) submitWord(current.map((i) => wheel[i]));
  };

  // keyboard support (desktop)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target;
      if (target instanceof HTMLElement && target.closest("button, a, input, textarea, select, [contenteditable='true']")) return;
      if (e.key === "Enter") { submitWord(current.map((i) => wheel[i])); return; }
      if (e.key === "Backspace") { e.preventDefault(); setCurrent((c) => c.slice(0, -1)); return; }
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
    const target = remaining.find((word) =>
      [...word].some((_, i) => !hinted[`${word}:${i}`]),
    );
    if (!target) { flash("Все доступные буквы уже открыты"); return; }
    if (coins < HINT_COST) { flash(`Мало монет — нужно ${HINT_COST} 🪙`); return; }

    const nextIdx = [...target].findIndex((_, i) => !hinted[`${target}:${i}`]);
    setHinted((h) => ({ ...h, [`${target}:${nextIdx}`]: true }));
    spendCoins(HINT_COST);
    flash(`Открыта буква «${target[nextIdx]}» · −${HINT_COST} 🪙`);
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
        <button
          type="button"
          className="wc-level-back"
          onClick={onHome}
          aria-label="Назад к карте кухонь"
          title="Назад к карте"
        >
          <span aria-hidden="true">←</span>
        </button>
        <div className="wc-level-badge">
          {grand ? "ГРАНД ТУР" : `УРОВЕНЬ ${levelNo}`}
        </div>
        <div className="wc-level-coins" role="status" aria-label={`Монеты: ${coins}`}>
          <img className="wc-coin" src="img/coins.webp" alt="" />
          <span>{coins.toLocaleString("ru-RU")}</span>
        </div>
        <button
          type="button"
          className="wc-hint-action"
          onClick={doHint}
          title={`Открыть букву · ${HINT_COST} монет`}
          aria-label={`Открыть букву за ${HINT_COST} монет`}
        >
          <img src="img/ui_hint.webp" alt="" />
          <span>{HINT_COST}</span>
        </button>
      </div>

      <h1 className="wc-dish-heading">{level.dish.name}</h1>

      <section className="wc-card wc-game-status" aria-label="Прогресс заказа">
        <img
          className="wc-guest"
          src={guestArt(level.kitchen).img}
          alt={guestArt(level.kitchen).nick}
        />
        <div className="wc-order-progress">
          <div className="wc-progress-caption">
            <span>Слова блюда</span>
            <strong>{found.length} <span aria-hidden="true">/</span> {boardWords.length}</strong>
          </div>
          <div
            className="wc-bar"
            role="progressbar"
            aria-label="Прогресс приготовления блюда"
            aria-valuemin={0}
            aria-valuemax={boardWords.length}
            aria-valuenow={found.length}
            aria-valuetext={`${found.length} из ${boardWords.length} слов`}
          >
            <div style={{ width: `${(found.length / Math.max(1, boardWords.length)) * 100}%` }} />
          </div>
          <div className="wc-guest-say" aria-live="polite">
            «{guestProgress(level.kitchen, found.length, boardWords.length) ?? guestLine(level.kitchen, levelNo)}»
          </div>
        </div>
        <span
          className="wc-bonus-jar"
          role="status"
          aria-label={`Найдено бонусных слов: ${bonusFound.length}`}
        >
          <img className="wc-jar-img" src="img/bonus_jar.webp" alt="" />
          <span className="wc-bonus-count">{bonusFound.length}</span>
        </span>
      </section>

      <div className="wc-grid-scroll" role="region" aria-label="Кроссворд блюда" tabIndex={0}>
        <div
          className={`wc-grid ${shake ? "wc-shake" : ""}`}
          role="group"
          aria-label="Буквенная доска заказа"
          style={{ gridTemplateColumns: `repeat(${grid.cols}, 38px)` }}
        >
          {Array.from({ length: grid.rows }).map((_, ri) =>
            Array.from({ length: grid.cols }).map((_, ci) => {
              const r = grid.minR + ri, c = grid.minC + ci;
              const st = cellState(r, c);
              if (!st) return <div key={`${r},${c}`} aria-hidden="true" />;
              const visible = st.filled || st.revealed;
              return (
                <div
                  key={`${r},${c}`}
                  className={`wc-cell ${st.filled ? "filled" : st.revealed ? "hinted" : "open"} ${st.hot ? "hot" : ""}`}
                  aria-label={visible ? `Буква ${st.letter}` : "Закрытая клетка"}
                >
                  {visible ? st.letter : ""}
                </div>
              );
            }),
          )}
        </div>
      </div>

      <div className="wc-word-entry" role="status" aria-live="polite" aria-atomic="true">
        <div className={`wc-wordbar ${current.length > 0 ? "has-word" : "is-empty"}`}>
          <div className="wc-wordbar-letters">
            {current.length === 0 ? (
              <span className="wc-wordbar-placeholder">Выберите буквы</span>
            ) : (
              current.map((i, k) => <span className="wc-word-chip" key={`${i}-${k}`}>{wheel[i]}</span>)
            )}
          </div>
        </div>
        <span className="wc-wordbar-label">
          {current.length > 0 ? `СЛОВО · ${current.length} БУКВ` : "СЛОВО"}
        </span>
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
            role="button"
            tabIndex={0}
            aria-label={`Буква ${letter}, плитка ${i + 1}`}
            aria-pressed={current.includes(i)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                e.stopPropagation();
                pickTile(i);
              }
            }}
            className={`wc-tile ${current.includes(i) ? "on" : ""}`}
            style={{
              left: centers[i].x,
              top: centers[i].y,
              // picked letters become golden ingredients
              ...(current.includes(i)
                ? { backgroundImage: "url(img/ui_gold_tile.webp)", backgroundSize: "cover" }
                : null),
            }}
          >
            {letter}
          </div>
        ))}
      </div>

      <div className="wc-wheel-tools" role="group" aria-label="Действия со словом">
        <button
          type="button"
          className="wc-action-button"
          onClick={doShuffle}
          aria-label="Перемешать буквы"
          title="Перемешать буквы"
        >
          <span className="wc-action-icon" aria-hidden="true">🔀</span>
          <span>Перемешать</span>
        </button>
        <button
          type="button"
          className="wc-action-button primary"
          onClick={() => submitWord(current.map((i) => wheel[i]))}
          disabled={current.length < 2}
          aria-label="Приготовить слово"
          title="Приготовить слово"
        >
          <span className="wc-action-icon" aria-hidden="true">🍲</span>
          <span>Готовить</span>
        </button>
        <button
          type="button"
          className="wc-action-button"
          onClick={() => setCurrent((c) => c.slice(0, -1))}
          disabled={current.length === 0}
          aria-label="Удалить последнюю букву"
          title="Удалить последнюю букву"
        >
          <span className="wc-action-icon" aria-hidden="true">↩</span>
          <span>Назад</span>
        </button>
      </div>

      {done && (
        <div className="wc-overlay">
          <div
            className="wc-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="wc-completion-title"
            aria-describedby="wc-completion-reward"
            tabIndex={-1}
            style={{ backgroundImage: "url(img/celebrate.webp)" }}
          >
            <div className="confetti">
              {["#ff7a2f", "#37b24d", "#3f97d4", "#f2c14e", "#e03131"].map((c, i) => (
                <i key={i} style={{
                  left: `${8 + i * 18}%`, background: c,
                  animationDelay: `${i * 0.35}s`,
                }} />
              ))}
            </div>
            <div className="wc-banner-strip" style={{ backgroundImage: "url(img/recipe_book.webp)" }}>
              <span>📖 Новый рецепт записан!</span>
            </div>
            <img className="big-img" src={dishArt(level.dish.name)} alt="" />
            <div id="wc-completion-title" style={{ fontSize: 24, fontWeight: 900 }}>{level.dish.name} готов!</div>
            {dishNote(level.dish.name) && (
              <div className="wc-recipe-note">«{dishNote(level.dish.name)}»</div>
            )}
            <div className="wc-stars">
              <img className="wc-medal big" src="img/ach_gold.webp" alt="" />
              ⭐⭐⭐
            </div>
            <div id="wc-completion-reward" style={{ fontWeight: 800 }}>
              +{30 + levelNo} 🪙 · бонусных слов: {bonusFound.length}
            </div>
            <button
              className="wc-btn green"
              onClick={() => {
                setDone(false);
                if (grand) flash("Готовим следующий заказ!");
                onNext();
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
