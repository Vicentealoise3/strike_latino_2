# standings_cascade_points.py
# Tabla de posiciones (2 páginas por jugador) con columnas:
# Pos | Equipo | Jugador | Prog(13) | JJ | W | L | Por jugar | Pts
# Reglas: LEAGUE + fecha, filtro (ambos miembros) o (CPU + miembro), dedup por id, ajustes algebraicos.
# Orden: por puntos (desc). Empates: por W (desc), luego L (asc).

import requests, time, re
from datetime import datetime
from zoneinfo import ZoneInfo

# ===== Config general =====
API = "https://mlb25.theshow.com/apis/game_history.json"
PLATFORM = "psn"
MODE = "LEAGUE"
SINCE = datetime(2025, 8, 30)
PAGES = (1, 2)          # <-- SOLO p1 y p2, como validaste
TIMEOUT = 20
...
LEAGUE_ORDER = [
    # ("usernameExact", "Team Name")
    # EJEMPLO (rellena con tus participantes reales):
    ("AiramReynoso_", "Padres"),
    ("Yosoyreynoso_", "Padres"),
    # ...
]

# ===== Ajustes manuales para W/L por equipo (si algo quedó mal en API) =====
TEAM_RECORD_ADJUSTMENTS = {
    # "Tigers": (+1, -1),   # +1 W, -1 L
    # "Blue Jays": (0, -1),
    # agrega más si hace falta
}

# ===== Ajustes manuales de PUNTOS (desconexiones, sanciones, bonificaciones) =====
# Formato: "Equipo": (ajuste_en_puntos, "razón del ajuste")
TEAM_POINT_ADJUSTMENTS = {
        #"Padres": (-1, "Desconexión vs Blue Jays"),
    # ejemplo de bonificación futura:
    # "Cubs": (+1, "Bonificación fair play"),
}

# ===== Miembros de liga (para el filtro de rival) =====
LEAGUE_USERS = {u for (u, _t) in LEAGUE_ORDER}
# Agrega alias/equivalencias si corresponde a esta liga:
LEAGUE_USERS.update({"AiramReynoso_", "Yosoyreynoso_"})

LEAGUE_USERS_NORM = {u.lower() for u in LEAGUE_USERS}


# ===== Utilidades =====
BXX_RE = re.compile(r"\^(b\d+)\^", flags=re.IGNORECASE)

def normalize_user_for_compare(raw: str) -> str:
    if not raw:
        return ""
    # limpia tags ^bX^ si aparecieran
    raw = BXX_RE.sub("", raw)
    return raw.strip().lower()

def is_cpu(name_raw: str) -> bool:
    s = (name_raw or "").strip().lower()
    return s in {"cpu", "cpu_user", "cpu opponent", "(cpu)"}

def parse_date(display_date: str):
    """
    Intenta parsear display_date que puede venir como:
    - '2025-08-30T19:28:00Z'
    - '2025-08-30 19:28:00'
    """
    if not display_date:
        return None
    s = display_date.strip()
    # formato ISO con Z
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    # formato sin tz (asumimos UTC)
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def fetch_page(username_exact: str, page: int = 1) -> list[dict]:
    url = f"{API}?psn_id={username_exact}&page={page}&platform={PLATFORM}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        j = r.json()
        return j.get("games", []) or []
    except Exception:
        return []

def dedup_by_id(games: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for g in games:
        gid = str(g.get("id") or "")
        if gid and gid in seen:
            continue
        if gid:
            seen.add(gid)
        out.append(g)
    return out

def norm_team(team_name: str) -> str:
    return (team_name or "").strip()

# -------------------------------------------------
# Construcción de fila por usuario/equipo
# -------------------------------------------------
def compute_team_record_for_user(user_exact: str, team_name: str) -> dict:
    # Trae p1 y p2
    pages = []
    for p in PAGES:
        pages += fetch_page(user_exact, p)

    tz_utc = ZoneInfo("UTC")
    games_valid = []
    for g in dedup_by_id(pages):
        # modo
        if (g.get("game_mode") or "").strip().upper() != MODE:
            continue

        d = parse_date(g.get("display_date", ""))
        if not d:
            continue
        # si viene naive => asume UTC
        if d.tzinfo is None:
            d = d.replace(tzinfo=tz_utc)

        # filtro por fecha
        if d.replace(tzinfo=None) < SINCE:
            continue

        home_name_raw = (g.get("home_name") or "")
        away_name_raw = (g.get("away_name") or "")
        h_norm = normalize_user_for_compare(home_name_raw)
        a_norm = normalize_user_for_compare(away_name_raw)

        # Ambos miembros, o CPU con miembro
        cond_members = ((h_norm in LEAGUE_USERS_NORM and a_norm in LEAGUE_USERS_NORM) or
                        (is_cpu(home_name_raw) and a_norm in LEAGUE_USERS_NORM) or
                        (is_cpu(away_name_raw) and h_norm in LEAGUE_USERS_NORM))
        if not cond_members:
            continue
        games_valid.append(g)

    # Acumular W/L del team_name
    team = norm_team(team_name)
    wins = 0
    losses = 0
    for g in games_valid:
        h_team = norm_team(g.get("home_full_name") or g.get("home_team_full_name") or "")
        a_team = norm_team(g.get("away_full_name") or g.get("away_team_full_name") or "")
        h_res = (g.get("home_display_result") or "").strip().upper()
        a_res = (g.get("away_display_result") or "").strip().upper()

        if h_team == team:
            if h_res == "W":
                wins += 1
            elif h_res == "L":
                losses += 1
        if a_team == team:
            if a_res == "W":
                wins += 1
            elif a_res == "L":
                losses += 1

    # Ajustes manuales de récord (si aplica)
    adj = TEAM_RECORD_ADJUSTMENTS.get(team, (0, 0))
    wins += adj[0]
    losses += adj[1]

    played = wins + losses
    # puntos: W=3, L=0 (ajusta a tu sistema si difiere)
    points = wins * 3
    # Ajustes de puntos (si aplica)
    if team in TEAM_POINT_ADJUSTMENTS:
        extra, _reason = TEAM_POINT_ADJUSTMENTS[team]
        points += extra

    remaining = 13 - played  # por defecto (puedes parametrizarlo en app.py)
    return {
        "team": team,
        "user_exact": user_exact,
        "wins": wins,
        "losses": losses,
        "played": played,
        "remaining": remaining,
        "points": points,
        "points_extra": TEAM_POINT_ADJUSTMENTS.get(team, (0, ""))[0],
        "points_reason": TEAM_POINT_ADJUSTMENTS.get(team, (0, ""))[1] if team in TEAM_POINT_ADJUSTMENTS else "",
    }

def main():
    rows = compute_rows()
    for i, r in enumerate(rows, 1):
        print(i, r)

def compute_rows():
    """
    Devuelve la lista completa de filas de la tabla.
    Intenta detectar una función por-equipo existente.
    """
    # intenta varios nombres típicos que puedas tener en tu módulo
    func = globals().get("compute_team_record_for_user") \
        or globals().get("compute_team_record") \
        or globals().get("build_team_row") \
        or globals().get("team_row_for_user")

    if not func:
        raise RuntimeError(
            "No encuentro una función para construir filas por equipo. "
            "Define compute_team_record_for_user(user, team) o compute_team_record(user, team)."
        )
    rows = []
    for user_exact, team_name in LEAGUE_ORDER:
        rows.append(func(user_exact, team_name))

    # Orden habitual
    rows.sort(key=lambda r: (-r.get("points", 0), -r.get("wins", 0), r.get("losses", 0)))
    return rows


# -------------------------------
# Juegos jugados HOY (Chile)
# -------------------------------
# ====== BLOQUE AÑADIDO (dedup robusto) ======
def games_played_today_scl():
    """
    Lista juegos del DÍA (America/Santiago) en formato:
      'Yankees 1 - Brewers 2  - 30-08-2025 - 3:28 pm'
    Arreglos:
      - Si la fecha viene sin tz, se asume UTC y se convierte a America/Santiago.
      - Deduplicación por id y por una clave canónica ***de mismo día*** (home, away, hr, ar, fecha).
        Esto evita duplicados del MISMO juego cuando el API lo lista dos veces con distinta hora.
      - Se requiere que AMBOS participantes pertenezcan a la liga.
    """
    tz_scl = ZoneInfo("America/Santiago")
    tz_utc = ZoneInfo("UTC")
    today_local = datetime.now(tz_scl).date()

    # Traer páginas p1 y p2 de todos los usuarios de la liga
    all_pages = []
    for username_exact, _team in LEAGUE_ORDER:
        for p in PAGES:
            all_pages += fetch_page(username_exact, p)

    seen_ids = set()
    seen_keys = set()
    items = []

    for g in dedup_by_id(all_pages):
        # Modo
        if (g.get("game_mode") or "").strip().upper() != MODE:
            continue

        d = parse_date(g.get("display_date", ""))
        if not d:
            continue

        # Asumir UTC si es naive, luego convertir a SCL
        if d.tzinfo is None:
            d = d.replace(tzinfo=tz_utc)
        d_local = d.astimezone(tz_scl)

        if d_local.date() != today_local:
            continue

        # Ambos jugadores deben pertenecer a la liga
        home_name_raw = (g.get("home_name") or "")
        away_name_raw = (g.get("away_name") or "")
        h_norm = normalize_user_for_compare(home_name_raw)
        a_norm = normalize_user_for_compare(away_name_raw)
        if not (h_norm in LEAGUE_USERS_NORM and a_norm in LEAGUE_USERS_NORM):
            continue

        gid = str(g.get("id") or "")
        if gid and gid in seen_ids:
            continue

        home = (g.get("home_full_name") or "").strip()
        away = (g.get("away_full_name") or "").strip()
        hr = str(g.get("home_runs") or "0")
        ar = str(g.get("away_runs") or "0")

        # Clave canónica por DÍA (sin minutos): evita duplicados del MISMO juego con horas distintas
        date_key = d_local.date()
        canon_key = (home, away, hr, ar, date_key)
        if canon_key in seen_keys:
            # Ya mostramos este juego con otra 'id' duplicada
            continue

        # Marcar vistos
        if gid:
            seen_ids.add(gid)
        seen_keys.add(canon_key)

        # Formato de salida
        try:
            fecha_hora = d_local.strftime("%d-%m-%Y - %-I:%M %p").lower()
        except Exception:
            fecha_hora = d_local.strftime("%d-%m-%Y - %#I:%M %p").lower()

        items.append((d_local, f"{home} {hr} - {away} {ar}  - {fecha_hora} (hora Chile)"))

    items.sort(key=lambda x: x[0])
    return [s for _, s in items]


# ====== FIN DEL BLOQUE AÑADIDO ======
