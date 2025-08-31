# app.py
from flask import Flask, render_template, jsonify
from datetime import datetime
import time
import traceback

import standings_cascade_points_desc as scpd

app = Flask(__name__)

# =========================
# Config
# =========================
SCHEDULED_GAMES = 13  # Prog(13)

USER_ALIASES = {
    "airamreynoso_": "Yosoyreynoso_",
    # agrega otros si necesitas
}

# =========================
# Helpers
# =========================
def _first_attr(mod, names):
    for n in names:
        if hasattr(mod, n):
            return getattr(mod, n)
    return None

def _normalize(s: str) -> str:
    return (s or "").strip().lower()

def _pick_user_field(row: dict) -> str:
    for key in ("display_user", "user_exact", "user", "username", "player", "manager"):
        v = row.get(key)
        if v:
            return str(v)
    return ""

def _apply_alias_and_metrics(row: dict) -> dict:
    # --- usuario / alias ---
    user_raw = _pick_user_field(row)
    norm = _normalize(user_raw)
    display_user = USER_ALIASES.get(norm, user_raw or "")
    row["display_user"] = display_user
    if not row.get("user_exact"):
        row["user_exact"] = display_user

    # --- métricas alineadas con encabezados ---
    def _as_int(x, default=0):
        try:
            return int(x)
        except Exception:
            return default

    wins = _as_int(row.get("wins", 0))
    losses = _as_int(row.get("losses", 0))
    played = wins + losses
    remaining = max(SCHEDULED_GAMES - played, 0)

    row["wins"] = wins
    row["losses"] = losses
    row["played"] = played
    row["remaining"] = remaining
    row["points"] = _as_int(row.get("points", 0))
    row["scheduled"] = SCHEDULED_GAMES

    if "team" in row:
        row["team"] = str(row["team"])

    return row

def _build_rows_robusto():
    compute_all = _first_attr(scpd, [
        "compute_rows",
        "compute_all_rows",
        "build_rows",
        "compute_standings",
    ])
    if compute_all:
        rows = compute_all()
    else:
        func = _first_attr(scpd, [
            "compute_team_record_for_user",
            "compute_team_record",
            "compute_row_for_user",
            "build_team_row",
            "team_row_for_user",
        ])
        if not func:
            raise RuntimeError(
                "No encontré una función para construir filas. "
                "Define compute_rows() o compute_team_record_for_user(user, team)."
            )
        if not hasattr(scpd, "LEAGUE_ORDER"):
            raise RuntimeError("LEAGUE_ORDER no existe en standings module")

        rows = [func(u, t) for (u, t) in scpd.LEAGUE_ORDER]

    rows = [_apply_alias_and_metrics(dict(r)) for r in rows]

    rows.sort(key=lambda r: (-r.get("points", 0), -r.get("wins", 0), r.get("losses", 0)))

    notes = []
    for r in rows:
        if r.get("points_extra"):
            notes.append({
                "team": r.get("team", ""),
                "points_extra": int(r.get("points_extra", 0)),
                "points_reason": r.get("points_reason", ""),
            })
    return rows, notes

def _games_played_today_scl_safe():
    try:
        from zoneinfo import ZoneInfo
        tz_scl = ZoneInfo("America/Santiago")
        tz_utc = ZoneInfo("UTC")
    except Exception:
        return []

    needed = ["LEAGUE_ORDER", "PAGES", "MODE", "fetch_page", "dedup_by_id", "parse_date"]
    if not all(hasattr(scpd, n) for n in needed):
        return []

    normalize = _first_attr(scpd, ["normalize_user_for_compare", "normalize_name", "norm_user"])
    league_users_norm = getattr(scpd, "LEAGUE_USERS_NORM", None)
    if not (normalize and league_users_norm):
        return []

    today_local = datetime.now(tz_scl).date()

    all_pages = []
    for username_exact, _team in scpd.LEAGUE_ORDER:
        for p in scpd.PAGES:
            all_pages += scpd.fetch_page(username_exact, p)

    seen_ids = set()
    seen_keys = set()
    items = []
    for g in scpd.dedup_by_id(all_pages):
        if (g.get("game_mode") or "").strip().upper() != getattr(scpd, "MODE", "LEAGUE"):
            continue
        d = scpd.parse_date(g.get("display_date", ""))
        if not d:
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=ZoneInfo("UTC"))
        d_local = d.astimezone(tz_scl)
        if d_local.date() != today_local:
            continue

        home_name_raw = (g.get("home_name") or "")
        away_name_raw = (g.get("away_name") or "")
        h_norm = normalize(home_name_raw)
        a_norm = normalize(away_name_raw)
        if not (h_norm in league_users_norm and a_norm in league_users_norm):
            continue

        gid = str(g.get("id") or "")
        if gid and gid in seen_ids:
            continue

        home = (g.get("home_full_name") or "").strip()
        away = (g.get("away_full_name") or "").strip()
        hr = str(g.get("home_runs") or "0")
        ar = str(g.get("away_runs") or "0")

        minute_key = d_local.strftime("%Y-%m-%d %H:%M")
        canon_key = (home, away, hr, ar, minute_key)
        if canon_key in seen_keys:
            continue

        if gid:
            seen_ids.add(gid)
        seen_keys.add(canon_key)

        try:
            fecha_hora = d_local.strftime("%d-%m-%Y - %-I:%M %p").lower()
        except Exception:
            fecha_hora = d_local.strftime("%d-%m-%Y - %#I:%M %p").lower()

        items.append((d_local, f"{home} {hr} - {away} {ar}  - {fecha_hora} (hora Chile)"))

    items.sort(key=lambda x: x[0])
    return [s for _, s in items]

# =========================
# Cache
# =========================
CACHE_TTL = 120
_today_cache_ttl = 60

_cache = {"ts": 0, "rows": [], "notes": []}
_today_cache = {"ts": 0, "items": []}

def get_rows_cached():
    now = time.time()
    if now - _cache["ts"] > CACHE_TTL or not _cache["rows"]:
        try:
            rows, notes = _build_rows_robusto()
        except Exception as e:
            print("[ERROR] Construyendo filas:", e)
            traceback.print_exc()
            rows, notes = [], []
        _cache["rows"] = rows
        _cache["notes"] = notes
        _cache["ts"] = now
    return _cache["rows"], _cache["notes"]

def get_games_today_cached():
    now = time.time()
    if now - _today_cache["ts"] > _today_cache_ttl:
        try:
            mod_func = _first_attr(scpd, ["games_played_today_scl"])
            _today_cache["items"] = mod_func() if mod_func else _games_played_today_scl_safe()
        except Exception as e:
            print("[ERROR] Juegos de hoy:", e)
            traceback.print_exc()
            _today_cache["items"] = []
        _today_cache["ts"] = now
    return _today_cache["items"]

# =========================
# Rutas
# =========================
@app.get("/")
def index():
    rows, notes = get_rows_cached()
    games_today = get_games_today_cached()
    return render_template(
        "index.html",
        rows=rows,
        notes=notes,
        games_today=games_today,
        scheduled=SCHEDULED_GAMES,
        last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

@app.get("/api/standings")
def api_standings():
    rows, notes = get_rows_cached()
    return jsonify({
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "rows": rows,
        "notes": notes,
        "scheduled": SCHEDULED_GAMES,
    })

@app.get("/api/preview_rowmap")
def api_preview_rowmap():
    """Devuelve la vista exacta que usa la tabla, para comparar encabezados vs datos."""
    rows, _ = get_rows_cached()
    sample = rows[:5]
    view = [{
        "team": r.get("team"),
        "display_user": r.get("display_user"),
        "scheduled": r.get("scheduled"),
        "played": r.get("played"),
        "wins": r.get("wins"),
        "losses": r.get("losses"),
        "remaining": r.get("remaining"),
        "points": r.get("points"),
    } for r in sample]
    return jsonify(view)

@app.get("/health")
def health():
    return {"ok": True}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
