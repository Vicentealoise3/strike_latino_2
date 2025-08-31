# app.py
from flask import Flask, render_template, jsonify
from datetime import datetime
import time
import traceback

# Importamos el MÓDULO completo (más robusto que importar funciones sueltas)
import standings_cascade_points_desc as scpd

app = Flask(__name__)

# ---------------------------------------
# Pequeñas utilidades de compatibilidad
# ---------------------------------------
def _first_attr(mod, names):
    """Devuelve el primer atributo existente en el módulo con alguno de esos nombres, o None."""
    for n in names:
        if hasattr(mod, n):
            return getattr(mod, n)
    return None

def _build_rows_robusto():
    """
    Construye las filas de la tabla intentando varias rutas:
      1) Si existe compute_rows()/compute_standings() que devuelva la lista completa, úsalo.
      2) Si no, iterar LEAGUE_ORDER con una función por equipo (probando varios nombres).
    Devuelve (rows, notes).
    """
    # 1) ¿Hay una función que ya devuelva TODAS las filas?
    compute_all = _first_attr(scpd, [
        "compute_rows",
        "compute_all_rows",
        "build_rows",
        "compute_standings",
    ])
    if compute_all:
        rows = compute_all()
    else:
        # 2) Por equipo
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
                "Define compute_rows() o compute_team_record_for_user(user, team) en standings_cascade_points_desc.py"
            )

        if not hasattr(scpd, "LEAGUE_ORDER"):
            raise RuntimeError("LEAGUE_ORDER no existe en standings_cascade_points_desc.py")

        rows = []
        for user_exact, team_name in scpd.LEAGUE_ORDER:
            rows.append(func(user_exact, team_name))

    # Orden estándar por puntos desc, W desc, L asc (si existen las claves)
    def _k(r):
        return (-r.get("points", 0), -r.get("wins", 0), r.get("losses", 0))
    rows.sort(key=_k)

    # Notas de puntos (si el módulo las provee)
    notes = []
    for r in rows:
        if r.get("points_extra"):
            notes.append({
                "team": r.get("team", ""),
                "points_extra": r.get("points_extra", 0),
                "points_reason": r.get("points_reason", ""),
            })
    return rows, notes

def _games_played_today_scl_safe():
    """
    Intenta listar juegos del DÍA (America/Santiago) usando helpers del módulo si existen.
    Si faltan, devuelve [] sin romper la app.
    """
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Santiago")
    except Exception:
        tz = None

    # Requisitos mínimos
    needed = ["LEAGUE_ORDER", "PAGES", "MODE", "fetch_page", "dedup_by_id", "parse_date"]
    if not all(hasattr(scpd, n) for n in needed):
        return []

    normalize = _first_attr(scpd, ["normalize_user_for_compare", "normalize_name", "norm_user"])
    league_users_norm = getattr(scpd, "LEAGUE_USERS_NORM", None)

    today_local = datetime.now().date() if tz is None else datetime.now(tz).date()

    # 1) Bajar páginas
    all_pages = []
    for username_exact, _team in scpd.LEAGUE_ORDER:
        for p in scpd.PAGES:
            all_pages += scpd.fetch_page(username_exact, p)

    # 2) Dedup
    all_pages = scpd.dedup_by_id(all_pages)

    # 3) Filtros
    items = []
    for g in all_pages:
        if (g.get("game_mode") or "").strip().upper() != getattr(scpd, "MODE", "LEAGUE"):
            continue

        d = scpd.parse_date(g.get("display_date", ""))
        if not d:
            continue

        if tz:
            if d.tzinfo is None:
                d = d.replace(tzinfo=tz)
            d_local = d.astimezone(tz)
        else:
            d_local = d

        if d_local.date() != today_local:
            continue

        # Al menos 1 miembro de la liga (si tenemos normalizador/lista)
        if normalize and league_users_norm is not None:
            home_name_raw = (g.get("home_name") or "")
            away_name_raw = (g.get("away_name") or "")
            h_norm = normalize(home_name_raw)
            a_norm = normalize(away_name_raw)
            h_mem = h_norm in league_users_norm
            a_mem = a_norm in league_users_norm
            if not (h_mem or a_mem):
                continue

        home = (g.get("home_full_name") or "").strip()
        away = (g.get("away_full_name") or "").strip()
        hr = str(g.get("home_runs") or "0")
        ar = str(g.get("away_runs") or "0")

        # 30-08-2025 - 12:35 pm
        try:
            fecha_hora = d_local.strftime("%d-%m-%Y - %-I:%M %p").lower()
        except Exception:
            fecha_hora = d_local.strftime("%d-%m-%Y - %#I:%M %p").lower()

        items.append((d_local, f"{home} {hr} - {away} {ar}  - {fecha_hora}"))

    items.sort(key=lambda x: x[0])
    return [s for _, s in items]

# ---------------------------------------
# Cachés
# ---------------------------------------
CACHE_TTL = 120
_cache = {"ts": 0, "rows": [], "notes": []}

TODAY_CACHE_TTL = 60
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
    if now - _today_cache["ts"] > TODAY_CACHE_TTL:
        try:
            # Si tu módulo trae la función exacta, úsala; si no, modo seguro.
            mod_func = _first_attr(scpd, ["games_played_today_scl"])
            if mod_func:
                _today_cache["items"] = mod_func()
            else:
                _today_cache["items"] = _games_played_today_scl_safe()
        except Exception as e:
            print("[ERROR] Juegos de hoy:", e)
            traceback.print_exc()
            _today_cache["items"] = []
        _today_cache["ts"] = now
    return _today_cache["items"]

# ----------------------------
# Rutas
# ----------------------------
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
    )

@app.get("/api/standings")
def api_standings():
    rows, notes = get_rows_cached()
    return jsonify({
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "rows": rows,
        "notes": notes,
    })

@app.get("/health")
def health():
    return {"ok": True}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
