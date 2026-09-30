// Campaign level engine — exact mirror of game/wordchef_game/levels.py.
// Same LCG (BigInt), same sort orders, same placement scans. Parity with the
// Python generator is enforced by tests (golden fixtures).
import { WORDS_RU } from "./wordsRu";

const LCG_MULT = 1103515245n;
const LCG_ADD = 12345n;
const LCG_MOD = 2147483648n;

class Lcg {
  state: bigint;
  constructor(seed: number) {
    this.state = BigInt(seed) % LCG_MOD;
  }
  next(): number {
    this.state = (this.state * LCG_MULT + LCG_ADD) % LCG_MOD;
    return Number(this.state);
  }
}

export interface LevelWord {
  word: string;
  r: number;
  c: number;
  across: boolean;
}

export interface Level {
  level_no: number;
  pool: string;
  wheel: string[];
  board: LevelWord[];
  bonus: string[];
  dish: { emoji: string; name: string };
  kitchen: string;
}

const DISHES = [
  { emoji: "🥗", name: "Салат" },
  { emoji: "🍕", name: "Пицца" },
  { emoji: "🍣", name: "Суши" },
  { emoji: "🍜", name: "Лапша" },
  { emoji: "🍰", name: "Торт" },
  { emoji: "🥘", name: "Рагу" },
  { emoji: "🥟", name: "Пельмени" },
  { emoji: "🍤", name: "Креветки" },
  { emoji: "🧁", name: "Капкейк" },
  { emoji: "🥪", name: "Сэндвич" },
  { emoji: "🍲", name: "Суп" },
  { emoji: "🍥", name: "Рулет" },
];

const KITCHENS = ["street", "bakery", "sushi", "space", "cyber", "ancient", "midnight"];

function subset(word: string, pool: string): boolean {
  const left = pool.split("");
  for (const ch of word) {
    const i = left.indexOf(ch);
    if (i < 0) return false;
    left.splice(i, 1);
  }
  return true;
}

function poolLength(levelNo: number): number {
  if (levelNo < 10) return 5;
  if (levelNo < 30) return 6;
  return 7;
}

function boardSize(levelNo: number): number {
  return Math.min(4 + Math.floor(levelNo / 20), 8);
}

function placeWords(words: string[]): LevelWord[] {
  const ordered = [...words].sort((a, b) =>
    b.length - a.length || (a < b ? -1 : a > b ? 1 : 0));
  const grid = new Map<string, string>();
  const placed: LevelWord[] = [];
  const key = (r: number, c: number) => `${r},${c}`;

  const canPlace = (word: string, r: number, c: number, across: boolean): boolean => {
    const [dr, dc] = across ? [0, 1] : [1, 0];
    if (grid.has(key(r - dr, c - dc)) || grid.has(key(r + dr * word.length, c + dc * word.length)))
      return false;
    let crossings = 0;
    for (let i = 0; i < word.length; i++) {
      const rr = r + dr * i, cc = c + dc * i;
      const cur = grid.get(key(rr, cc));
      if (cur !== undefined) {
        if (cur !== word[i]) return false;
        crossings += 1;
        continue;
      }
      if (across) {
        if (grid.has(key(rr - 1, cc)) || grid.has(key(rr + 1, cc))) return false;
      } else {
        if (grid.has(key(rr, cc - 1)) || grid.has(key(rr, cc + 1))) return false;
      }
    }
    return crossings === (placed.length === 0 ? 0 : 1);
  };

  const doPlace = (word: string, r: number, c: number, across: boolean): void => {
    const [dr, dc] = across ? [0, 1] : [1, 0];
    for (let i = 0; i < word.length; i++) grid.set(key(r + dr * i, c + dc * i), word[i]);
    placed.push({ word, r, c, across });
  };

  doPlace(ordered[0], 0, 0, true);

  for (const word of ordered.slice(1)) {
    const cells = [...grid.entries()]
      .map(([k, letter]) => {
        const [rr, cc] = k.split(",").map(Number);
        return { rr, cc, letter };
      })
      .sort((a, b) => a.rr - b.rr || a.cc - b.cc);
    let done = false;
    for (const { rr, cc, letter } of cells) {
      if (done) break;
      for (let i = 0; i < word.length; i++) {
        if (done) break;
        if (word[i] !== letter) continue;
        for (const across of [true, false]) {
          const fits = placed.some(
            (p) =>
              p.across !== across &&
              rr >= p.r &&
              rr <= p.r + (p.across ? 0 : p.word.length - 1) &&
              cc >= p.c &&
              cc <= p.c + (p.across ? p.word.length - 1 : 0),
          );
          if (!fits) continue;
          const r0 = across ? rr : rr - i;
          const c0 = across ? cc - i : cc;
          if (canPlace(word, r0, c0, across)) {
            doPlace(word, r0, c0, across);
            done = true;
            break;
          }
        }
      }
    }
  }
  return placed;
}

export function generateLevel(levelNo: number): Level {
  const words = WORDS_RU;
  const rng = new Lcg(levelNo * 2654435761 + 12345);

  const poolLen = poolLength(levelNo);
  const candidates = words.filter((w) => w.length === poolLen);
  const size = boardSize(levelNo);
  const needed = size + 2;

  let pool = "";
  let formable: string[] = [];
  for (let t = 0; t < 24; t++) {
    const cand = candidates[rng.next() % candidates.length];
    const f = words.filter((w) => w.length >= 3 && subset(w, cand));
    if (f.length >= needed) {
      pool = cand;
      formable = f;
      break;
    }
    if (pool === "" || f.length > formable.length) {
      pool = cand;
      formable = f;
    }
  }

  const formableRest = formable
    .filter((w) => w !== pool)
    .sort((a, b) => a.length - b.length || (a < b ? -1 : a > b ? 1 : 0));
  const boardWords = [pool, ...formableRest.slice(0, size - 1)];
  let bonus = [...new Set(formable.filter((w) => !boardWords.includes(w)))].sort();

  const wheel = pool.split("");
  for (let i = wheel.length - 1; i > 0; i--) {
    const j = rng.next() % (i + 1);
    [wheel[i], wheel[j]] = [wheel[j], wheel[i]];
  }

  const placed = placeWords(boardWords);
  const placedWords = new Set(placed.map((p) => p.word));
  const skipped = boardWords.filter((w) => !placedWords.has(w));
  bonus = [...new Set([...bonus, ...skipped])].sort();

  return {
    level_no: levelNo,
    pool,
    wheel,
    board: placed,
    bonus,
    dish: { ...DISHES[Math.floor(levelNo / 10) % DISHES.length] },
    kitchen: KITCHENS[Math.min(KITCHENS.length - 1, Math.floor(levelNo / 20))],
  };
}

export function classifyWord(level: Level, word: string): "board" | "bonus" | "invalid" {
  const w = word.trim().toLowerCase().replace(/ё/g, "е");
  if (level.board.some((p) => p.word === w)) return "board";
  if (level.bonus.includes(w)) return "bonus";
  return "invalid";
}
