# standings_cascade_points.py
# Tabla de posiciones (2 páginas por jugador) con columnas:
# Pos | Equipo | Jugador | Prog(12) | JJ | W | L | Por jugar | Pts
# Reglas: LEAGUE + fecha, filtro (ambos miembros) o (CPU + miembro), dedup por id, ajustes algebraicos.
# Orden: por puntos (desc). Empates: por W (desc), luego L (asc).

import requests, time, re
from datetime import datetime

# ===== Config general =====
API = "https://mlb25.theshow.com/apis/game_history.json"
PLATFORM = "psn"
MODE = "LEAGUE"
SINCE = datetime(2025, 8, 30)
PAGES = (1, 2)          # <-- SOLO p1 y p2, como validaste
TIMEOUT = 20
RETRIES = 2

# Mostrar detalle por equipo (línea a línea). Deja False para tabla limpia.
PRINT_DETAILS = False

# Procesar solo los primeros N para ir validando en cascada (None = todos)
STOP_AFTER_N = None

# ===== Liga (username EXACTO → equipo) =====
LEAGUE_ORDER = [
    ("THELSURICATO", "Mets"),
    ("machado_seba-03", "Reds"),
    ("zancudo99", "Rangers"),
    ("vicentealoise", "Brewers"),
    ("Solbracho", "Tigers"),
    ("WILZULIA", "Royals"),
    ("Daviddiaz030425", "Guardians"),
    ("Juanchojs28", "Giants"),
    ("Dev Read", "Marlins"),
    ("Bufon3-0", "Athletics"),
    ("edwar13-21", "Blue Jays"),
    ("mrguerrillas", "Pirates"),
    ("Diamondmanager", "Astros"),
    ("Tu_Pauta2000", "Braves"),
]

# ===== Ajustes algebraicos por equipo (resets W/L) =====
TEAM_RECORD_ADJUSTMENTS = {
      #  "Phillies": (-1, 0),
        #"Padres": (-1, 0),
        #"Blue Jays": (0, -1),
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
    #LEAGUE_USERS = {u for (u, _t) in LEAGUE_ORDER}
# Si corresponde, agrega extras manuales:
    #LEAGUE_USERS.update({"AiramReynoso_"})  # quítalo si no es parte

# ===== Utilidades =====
BXX_RE = re.compile(r"\^(b\d+)\^", flags=re.IGNORECASE)

def normalize_user_for_compare(raw: str) -> str:
    if not raw: return ""
    return BXX_RE.sub("", raw).strip().lower()

LEAGUE_USERS_NORM = {u.lower() for u in LEAGUE_USERS}

def is_cpu(raw: str) -> bool:
    return normalize_user_for_compare(raw) == "cpu"

def parse_date(s: str):
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except:
            pass
    return None

def fetch_page(username: str, page: int):
    params = {"username": username, "platform": PLATFORM, "page": page}
    last = None
    for _ in range(RETRIES):
        try:
            r = requests.get(API, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return (r.json() or {}).get("game_history") or []
        except Exception as e:
            last = e
            time.sleep(0.4)
    print(f"[WARN] {username} p{page} sin datos ({last})")
    return []

def dedup_by_id(gs):
    seen = set(); out = []
    for g in gs:
        gid = str(g.get("id") or "")
        if gid and gid in seen:
            continue
        if gid:
            seen.add(gid)
        out.append(g)
    return out

def norm_team(s: str) -> str:
    return (s or "").strip().lower()

def compute_team_record_for_user(username_exact: str, team_name: str):
    # 1) Descargar p1–p2 del usuario y deduplicar
    pages = []
    for p in PAGES:
        pages += fetch_page(username_exact, p)
    pages = dedup_by_id(pages)

    # 2) Filtrar: LEAGUE + fecha + que juegue ese equipo + rival válido
    considered = []
    for g in pages:
        if (g.get("game_mode") or "").strip().upper() != MODE:
            continue
        d = parse_date(g.get("display_date",""))
        if not d or d < SINCE:
            continue

        home = (g.get("home_full_name") or "").strip()
        away = (g.get("away_full_name") or "").strip()
        if norm_team(team_name) not in (norm_team(home), norm_team(away)):
            continue

        # Filtro: ambos miembros o CPU + miembro
        home_name_raw = g.get("home_name","")
        away_name_raw = g.get("away_name","")
        h_norm = normalize_user_for_compare(home_name_raw)
        a_norm = normalize_user_for_compare(away_name_raw)
        h_mem = h_norm in LEAGUE_USERS_NORM
        a_mem = a_norm in LEAGUE_USERS_NORM
        if not ( (h_mem and a_mem) or (is_cpu(home_name_raw) and a_mem) or (is_cpu(away_name_raw) and h_mem) ):
            continue

        considered.append(g)

    # 3) Contar W/L
    wins = losses = 0
    detail_lines = []
    for g in considered:
        home = (g.get("home_full_name") or "").strip()
        away = (g.get("away_full_name") or "").strip()
        hr = (g.get("home_display_result") or "").strip().upper()
        ar = (g.get("away_display_result") or "").strip().upper()
        dt = g.get("display_date","")
        if hr == "W":
            win, lose = home, away
        elif ar == "W":
            win, lose = away, home
        else:
            continue

        if norm_team(win) == norm_team(team_name):
            wins += 1
        elif norm_team(lose) == norm_team(team_name):
            losses += 1

        if PRINT_DETAILS:
            detail_lines.append(f"{dt}  {away} @ {home} -> ganó {win}")

    # 4) Ajuste algebraico del equipo (W/L)
    adj_w, adj_l = TEAM_RECORD_ADJUSTMENTS.get(team_name, (0, 0))
    wins_adj, losses_adj = wins + adj_w, losses + adj_l

    # 5) Puntos y métricas de tabla
    scheduled = 12
    played = max(wins_adj + losses_adj, 0)
    remaining = max(scheduled - played, 0)
    points_base = 3 * wins_adj + 2 * losses_adj

    # 6) Ajuste manual de PUNTOS (desconexiones, sanciones, etc.)
    pts_extra, pts_reason = TEAM_POINT_ADJUSTMENTS.get(team_name, (0, ""))
    points_final = points_base + pts_extra

    return {
        "user": username_exact,
        "team": team_name,
        "scheduled": scheduled,
        "played": played,
        "wins": wins_adj,
        "losses": losses_adj,
        "remaining": remaining,
        "points": points_final,      # << lo que se usa para ordenar y mostrar
        "points_base": points_base,  # info útil por si quieres comparar
        "points_extra": pts_extra,   # ej: -1
        "points_reason": pts_reason, # ej: "Desconexión vs Blue Jays"
        "detail": detail_lines,
    }

def main():
    take = len(LEAGUE_ORDER) if STOP_AFTER_N is None else min(STOP_AFTER_N, len(LEAGUE_ORDER))
    rows = []
    print(f"Procesando {take} equipos (páginas {PAGES})...\n")
    for i, (user, team) in enumerate(LEAGUE_ORDER[:take], start=1):
        print(f"[{i}/{take}] {team} ({user})...")
        row = compute_team_record_for_user(user, team)
        rows.append(row)
        # Muestra Pts y, si hay ajuste, indícalo
        adj_note = f" (ajuste pts {row['points_extra']}: {row['points_reason']})" if row["points_extra"] else ""
        print(f"  => {row['team']}: {row['wins']}-{row['losses']} (Pts {row['points']}){adj_note}\n")

    # Orden por puntos desc; desempates: W desc, L asc
    rows.sort(key=lambda r: (-r["points"], -r["wins"], r["losses"]))

    # Print tabla con posiciones
    print("\nTabla de posiciones")
    print("Pos | Equipo            | Jugador         | Prog |  JJ |  W |  L | P.Jugar | Pts")
    print("----+-------------------+-----------------+------+-----+----+----+---------+----")
    for pos, r in enumerate(rows, start=1):
        print(f"{pos:>3} | {r['team']:<19} | {r['user']:<15} | {r['scheduled']:>4} | {r['played']:>3} | "
              f"{r['wins']:>2} | {r['losses']:>2} | {r['remaining']:>7} | {r['points']:>3}")

    # Notas de ajustes de puntos (si existen)
    notes = [r for r in rows if r["points_extra"]]
    if notes:
        print("\nNotas de puntos (ajustes manuales):")
        for r in notes:
            signo = "+" if r["points_extra"] > 0 else ""
            print(f" - {r['team']}: {signo}{r['points_extra']} — {r['points_reason']}")

    print(f"\nÚltima actualización: {datetime.now():%Y-%m-%d %H:%M:%S}")

if __name__ == "__main__":
    main()
# ====== AÑADIR AL FINAL DE standings_cascade_points_desc.py ======
from zoneinfo import ZoneInfo
from datetime import datetime

# ==============================
# Compatibilidad: filas completas
# ==============================
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

    if "LEAGUE_ORDER" not in globals():
        raise RuntimeError("LEAGUE_ORDER no existe en standings_cascade_points_desc.py")

    rows = []
    for user_exact, team_name in LEAGUE_ORDER:
        rows.append(func(user_exact, team_name))

    # Orden habitual
    rows.sort(key=lambda r: (-r.get("points", 0), -r.get("wins", 0), r.get("losses", 0)))
    return rows


# -------------------------------
# Juegos jugados HOY (Chile)
# -------------------------------
# -------------------------------
# Juegos jugados HOY (Chile) - FIX TZ + DEDUP EXTRA
# -------------------------------
from zoneinfo import ZoneInfo
from datetime import datetime

def games_played_today_scl():
    """
    Lista juegos del DÍA (America/Santiago) en formato:
      'Yankees 1 - Brewers 2  - 30-08-2025 - 3:28 pm'
    Arreglos:
      - Si la fecha viene sin tz, se asume UTC y se convierte a America/Santiago.
      - Deduplicación por id y por una clave canónica (home, away, hr, ar, yyyy-mm-dd HH:MM).
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

    # Deduplicadores
    seen_ids = set()
    seen_keys = set()  # (home, away, hr, ar, 'YYYY-MM-DD HH:MM')
    items = []

    for g in dedup_by_id(all_pages):
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

        # Dedup por id
        gid = str(g.get("id") or "")
        if gid and gid in seen_ids:
            continue

        home = (g.get("home_full_name") or "").strip()
        away = (g.get("away_full_name") or "").strip()
        hr = str(g.get("home_runs") or "0")
        ar = str(g.get("away_runs") or "0")

        # Clave canónica por minuto (YYYY-MM-DD HH:MM)
        minute_key = d_local.strftime("%Y-%m-%d %H:%M")
        canon_key = (home, away, hr, ar, minute_key)
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
