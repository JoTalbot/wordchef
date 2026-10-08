export type WordChefThemeId = "classic" | "neon" | "bakery";

export type WordChefTheme = {
  id: WordChefThemeId;
  name: string;
  emoji: string;
  className: string;
};

export const WORDCHEF_THEMES: WordChefTheme[] = [
  { id: "classic", name: "Классика", emoji: "🍳", className: "wc-theme-classic" },
  { id: "neon", name: "Неон", emoji: "⚡", className: "wc-theme-neon" },
  { id: "bakery", name: "Пекарня", emoji: "🥐", className: "wc-theme-bakery" },
];

export function getWordChefTheme(id: string): WordChefTheme {
  return WORDCHEF_THEMES.find((theme) => theme.id === id) ?? WORDCHEF_THEMES[0];
}
