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

# =========================
# Cache
# =========================
_cache = {"rows": None, "notes": None, "ts": 0}
_today_cache = {"items": [], "ts": 0}

_cache_ttl = 60  # 1 minuto
_today_cache_ttl = 30  # 30 segundos

# =========================
# Adaptadores / Normalización de filas
# =========================
def normalize_rows(rows_raw):
    """
    Normaliza filas para la tabla (alinear con encabezados).
    Espera dicts con: team, user_exact, wins, losses, played, remaining, points, points_extra, points_reason
    """
    rows = []
    for r in rows_raw:
        row = dict(r)
        # mostrar alias en display_user
        user_raw = row.get("user_exact") or ""
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
        row["wins"] = wins
        row["losses"] = losses
        row["played"] = played

        # remaining: usa valor del módulo si lo trae, si no, calcula con SCHEDULED_GAMES
        remaining = row.get("remaining")
        if remaining is None:
            remaining = max(SCHEDULED_GAMES - played, 0)
        row["remaining"] = remaining

        # points
        row["points"] = _as_int(row.get("points", 0))

        # Extras (para notas)
        row["points_extra"] = _as_int(row.get("points_extra", 0))
        row["points_reason"] = row.get("points_reason", "")

        rows.append(row)

    # Orden por puntos desc, wins desc, losses asc
    rows.sort(key=lambda r: (-r.get("points", 0), -r.get("wins", 0), r.get("losses", 0)))
    return rows

def build_notes(rows):
    """
    Construye notas de ajustes de puntos para el recuadro inferior.
    """
    out = []
    for r in rows:
        extra = int(r.get("points_extra", 0) or 0)
        if extra != 0:
            out.append({
                "team": r.get("team", ""),
                "points_extra": extra,
                "points_reason": r.get("points_reason", ""),
            })
    return out

# =========================
# “Juegos de hoy” – fallback si el módulo no expone games_played_today_scl()
# =========================
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
            d = d.replace(tzinfo=tz_utc)
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

        # *** DEDUP POR DÍA (sin minutos) ***
        date_key = d_local.date()
        canon_key = (home, away, hr, ar, date_key)
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
# Caches
# =========================
def get_rows_cached():
    now = time.time()
    if now - _cache["ts"] > _cache_ttl:
        try:
            mod_func = _first_attr(scpd, ["compute_rows", "build_rows", "rows"])
            raw = mod_func() if callable(mod_func) else scpd.compute_rows()
            rows = normalize_rows(raw)
            notes = build_notes(rows)
        except Exception as e:
            print("[ERROR] compute_rows:", e)
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
        last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        scheduled=SCHEDULED_GAMES,
    )

@app.get("/api/sample")
def api_sample():
    rows, _ = get_rows_cached()
    sample = rows[:10]
    view = [{
        "team": r.get("team", ""),
        "user": r.get("display_user", r.get("user_exact", "")),
        "scheduled": SCHEDULED_GAMES,
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
