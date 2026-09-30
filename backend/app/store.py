"""SQLite persistence — players, matches, intents, executions, leaderboard.

Restart-safe: match state snapshots and the full intent log live here; the
Prolepsis execution envelopes live in their own JSON store. Everything a
client ever sent is recorded (anti-cheat replay material).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    player_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    xp          INTEGER NOT NULL DEFAULT 0,
    golden_earned INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    match_id    TEXT PRIMARY KEY,
    mode        TEXT NOT NULL,
    seed        TEXT NOT NULL,
    kitchen_id  TEXT NOT NULL,
    rounds      INTEGER NOT NULL,
    status      TEXT NOT NULL,
    state_json  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS match_players (
    match_id    TEXT NOT NULL,
    player_id   TEXT NOT NULL,
    score       INTEGER NOT NULL DEFAULT 0,
    dishes      INTEGER NOT NULL DEFAULT 0,
    best_word   TEXT NOT NULL DEFAULT '',
    position    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (match_id, player_id)
);
CREATE TABLE IF NOT EXISTS intents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id    TEXT NOT NULL,
    player_id   TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    intent_json TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    op           TEXT NOT NULL,
    request_id   TEXT NOT NULL,
    match_id     TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL,
    digest       TEXT NOT NULL DEFAULT '',
    verified     INTEGER,
    checkpoint_id TEXT NOT NULL DEFAULT '',
    artifact_refs TEXT NOT NULL DEFAULT '[]',
    created_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS leaderboard (
    season      TEXT NOT NULL,
    player_id   TEXT NOT NULL,
    score       INTEGER NOT NULL DEFAULT 0,
    dishes      INTEGER NOT NULL DEFAULT 0,
    matches     INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (season, player_id)
);
CREATE INDEX IF NOT EXISTS idx_intents_match ON intents(match_id, seq);
CREATE INDEX IF NOT EXISTS idx_exec_match ON executions(match_id);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── players ──────────────────────────────────────────────

    def upsert_player(self, player_id: str, name: str) -> dict:
        with self._lock:
            self._conn.execute(
                "INSERT INTO players(player_id, name, created_at) VALUES(?,?,?) "
                "ON CONFLICT(player_id) DO UPDATE SET name=excluded.name",
                (player_id, name, time.time()))
            self._conn.commit()
        return self.get_player(player_id)

    def get_player(self, player_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM players WHERE player_id=?", (player_id,)).fetchone()
        return dict(row) if row else None

    def add_xp(self, player_id: str, xp: int, golden: int = 0) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE players SET xp=xp+?, golden_earned=golden_earned+? WHERE player_id=?",
                (max(0, xp), max(0, golden), player_id))
            self._conn.commit()

    # ── matches ──────────────────────────────────────────────

    def save_match(self, match_id: str, mode: str, seed: str, kitchen_id: str,
                   rounds: int, status: str, state: dict,
                   created_at: float | None = None,
                   finished_at: float | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO matches(match_id, mode, seed, kitchen_id, rounds, status,"
                " state_json, created_at, finished_at) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(match_id) DO UPDATE SET status=excluded.status,"
                " state_json=excluded.state_json, finished_at=excluded.finished_at",
                (match_id, mode, seed, kitchen_id, rounds, status,
                 json.dumps(state, sort_keys=True), created_at or time.time(),
                 finished_at))
            self._conn.commit()

    def get_match(self, match_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM matches WHERE match_id=?", (match_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["state"] = json.loads(data.pop("state_json"))
        return data

    def save_match_player(self, match_id: str, player_id: str, score: int,
                          dishes: int, best_word: str, position: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO match_players(match_id, player_id, score, dishes,"
                " best_word, position) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(match_id, player_id) DO UPDATE SET score=excluded.score,"
                " dishes=excluded.dishes, best_word=excluded.best_word,"
                " position=excluded.position",
                (match_id, player_id, score, dishes, best_word, position))
            self._conn.commit()

    def match_players(self, match_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM match_players WHERE match_id=? ORDER BY position",
                (match_id,)).fetchall()
        return [dict(r) for r in rows]

    # ── intents (the replay log) ─────────────────────────────

    def record_intent(self, match_id: str, player_id: str, seq: int,
                      intent: dict, outcome: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO intents(match_id, player_id, seq, intent_json,"
                " outcome_json, created_at) VALUES(?,?,?,?,?,?)",
                (match_id, player_id, seq, json.dumps(intent, sort_keys=True),
                 json.dumps(outcome, sort_keys=True), time.time()))
            self._conn.commit()

    def intents(self, match_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM intents WHERE match_id=? ORDER BY seq",
                (match_id,)).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["intent"] = json.loads(data.pop("intent_json"))
            data["outcome"] = json.loads(data.pop("outcome_json"))
            out.append(data)
        return out

    # ── prolepsis executions ─────────────────────────────────

    def record_execution(self, record: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO executions(execution_id, op, request_id,"
                " match_id, state, digest, verified, checkpoint_id, artifact_refs,"
                " created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (record["execution_id"], record.get("op", ""),
                 record.get("request_id", ""), record.get("match_id", ""),
                 record.get("state", ""), record.get("digest") or "",
                 None if record.get("verified") is None else int(bool(record["verified"])),
                 record.get("checkpoint_id") or "",
                 json.dumps(record.get("artifact_refs", [])), time.time()))
            self._conn.commit()

    def executions(self, match_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM executions"
        args: tuple = ()
        if match_id:
            query += " WHERE match_id=?"
            args = (match_id,)
        query += " ORDER BY created_at"
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["artifact_refs"] = json.loads(data["artifact_refs"])
            data["verified"] = None if data["verified"] is None else bool(data["verified"])
            out.append(data)
        return out

    # ── leaderboard ──────────────────────────────────────────

    def upsert_leaderboard(self, season: str, player_id: str, score: int,
                           dishes: int, matches: int = 1) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO leaderboard(season, player_id, score, dishes, matches,"
                " updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(season, player_id) DO UPDATE SET"
                " score=leaderboard.score+excluded.score,"
                " dishes=leaderboard.dishes+excluded.dishes,"
                " matches=leaderboard.matches+excluded.matches,"
                " updated_at=excluded.updated_at",
                (season, player_id, max(0, score), max(0, dishes), matches,
                 time.time()))
            self._conn.commit()

    def leaderboard(self, season: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM leaderboard WHERE season=? ORDER BY score DESC,"
                " dishes DESC, player_id LIMIT ?", (season, limit)).fetchall()
        return [dict(r) | {"position": i + 1} for i, r in enumerate(rows)]
