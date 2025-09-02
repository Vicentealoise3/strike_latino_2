# standings_cascade_points.py
# Tabla de posiciones (2 páginas por jugador) con columnas:
# Pos | Equipo | Jugador | Prog(13) | JJ | W | L | Por jugar | Pts
# Reglas: LEAGUE + fecha, filtro (ambos miembros) o (CPU + miembro), dedup por id, ajustes algebraicos.
# Orden: por puntos (desc). Empates: por W (desc), luego L (asc).

import requests, time, re, os, json
from datetime import datetime
# ===== Config general =====

# ===== MODO DE EJECUCIÓN (switch) =====
# Valores posibles: "DEBUG" o "ONLINE"
MODE = "ONLINE"  # ← déjalo en DEBUG para que se comporte igual que ahora

CFG = {
    "DEBUG": dict(
        PRINT_DETAILS=False,          # igual que ahora
        PRINT_CAPTURE_SUMMARY=True,   # imprime resumen por equipo
        PRINT_CAPTURE_LIST=False,     # NO lista juego por juego
        DUMP_ENABLED=True,            # genera JSON en carpeta out/
        STOP_AFTER_N=None,            # procesa todos
        DAY_WINDOW_MODE="calendar",   # "hoy" = día calendario Chile (00:00–23:59)
    ),
    "ONLINE": dict(
        PRINT_DETAILS=False,          # silencioso en prod
        PRINT_CAPTURE_SUMMARY=False,  # sin resúmenes
        PRINT_CAPTURE_LIST=False,     # sin listado
        DUMP_ENABLED=False,           # sin JSONs
        STOP_AFTER_N=None,            # todos
        DAY_WINDOW_MODE="sports",     # "hoy" = 06:00–05:59 (día deportivo Chile)
    ),
}

# === Aplicar la config del modo seleccionado ===
conf = CFG.get(MODE, CFG["DEBUG"])
PRINT_DETAILS = conf["PRINT_DETAILS"]
# Si ya tenías estas variables definidas arriba, estas líneas las sobreescriben según el modo:
try:
    PRINT_CAPTURE_SUMMARY
except NameError:
    PRINT_CAPTURE_SUMMARY = conf["PRINT_CAPTURE_SUMMARY"]
else:
    PRINT_CAPTURE_SUMMARY = conf["PRINT_CAPTURE_SUMMARY"]

try:
    PRINT_CAPTURE_LIST
except NameError:
    PRINT_CAPTURE_LIST = conf["PRINT_CAPTURE_LIST"]
else:
    PRINT_CAPTURE_LIST = conf["PRINT_CAPTURE_LIST"]

try:
    DUMP_ENABLED
except NameError:
    DUMP_ENABLED = conf["DUMP_ENABLED"]
else:
    DUMP_ENABLED = conf["DUMP_ENABLED"]

try:
    STOP_AFTER_N
except NameError:
    STOP_AFTER_N = conf["STOP_AFTER_N"]
else:
    STOP_AFTER_N = conf["STOP_AFTER_N"]

DAY_WINDOW_MODE = conf["DAY_WINDOW_MODE"]  # "calendar" o "sports"

API = "https://mlb25.theshow.com/apis/game_history.json"
PLATFORM = "psn"
MODE = "LEAGUE"
SINCE = datetime(2025, 8, 30)
PAGES = (1, 2, 3)          # <-- SOLO p1 y p2, como validaste
TIMEOUT = 20
RETRIES = 2

# Mostrar detalle por equipo (línea a línea). Deja False para tabla limpia.
PRINT_DETAILS = False

# Procesar solo los primeros N para ir validando en cascada (None = todos)
STOP_AFTER_N = None

# === Capturas / dumps ===
DUMP_ENABLED = True
DUMP_DIR = "out"
PRINT_CAPTURE_SUMMARY = True   # imprime resumen capturas por equipo
PRINT_CAPTURE_LIST = False     # lista cada juego capturado (puede ser muy verboso)

# ===== Liga (username EXACTO → equipo) =====
LEAGUE_ORDER = [
    ("THELSURICATO", "Mets"),
    ("machado_seba-03", "Reds"),
    ("zancudo99", "Rangers"),
    ("havanavcr10", "Brewers"),
    ("Solbbracho", "Tigers"),
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

# ====== IDs alternativos por participante (para sumar sin duplicar) ======
# Clave = username principal EXACTO en LEAGUE_ORDER; Valor = lista de cuentas alternas
FETCH_ALIASES = {
    # ejemplo real:
    "Tu_Pauta2000": ["Lachi_1991"],  # Braves puede aparecer con esta otra cuenta
    # agrega más si hace falta:
    # "OtroPrincipal": ["Alias1", "Alias2"],
}

# ===== Ajustes algebraicos por equipo (resets W/L) =====
TEAM_RECORD_ADJUSTMENTS = {
    # "Blue Jays": (0, -1),
    # agrega más si hace falta
}

# ===== Ajustes manuales de PUNTOS (desconexiones, sanciones, bonificaciones) =====
# Formato: "Equipo": (ajuste_en_puntos, "razón del ajuste")
TEAM_POINT_ADJUSTMENTS = {
    # "Padres": (-1, "Desconexión vs Blue Jays"),
    # "Cubs": (+1, "Bonificación fair play"),
}

# ===== Miembros de liga (para el filtro de rival) =====
# Incluye principales + alias para que NINGÚN partido válido se descarte por “no miembro”
LEAGUE_USERS = {u for (u, _t) in LEAGUE_ORDER}
for base, alts in FETCH_ALIASES.items():
    LEAGUE_USERS.add(base)
    LEAGUE_USERS.update(alts)
# Agrega alias/equivalencias históricas si corresponde a esta liga:
LEAGUE_USERS.update({"AiramReynoso_", "Yosoyreynoso_"})
LEAGUE_USERS_NORM = {u.lower() for u in LEAGUE_USERS}

# ===== Utilidades =====
BXX_RE = re.compile(r"\^(b\d+)\^", flags=re.IGNORECASE)

def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s or "")

def _dump_json(filename: str, data):
    if not DUMP_ENABLED:
        return
    os.makedirs(DUMP_DIR, exist_ok=True)
    path = os.path.join(DUMP_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path

def normalize_user_for_compare(raw: str) -> str:
    if not raw: return ""
    return BXX_RE.sub("", raw).strip().lower()

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
    # 1) Descargar páginas del usuario PRINCIPAL y de sus ALIAS; luego deduplicar globalmente por id
    pages_raw = []
    usernames_to_fetch = [username_exact] + FETCH_ALIASES.get(username_exact, [])
    for uname in usernames_to_fetch:
        for p in PAGES:
            page_items = fetch_page(uname, p)
            pages_raw += page_items
            if PRINT_CAPTURE_LIST:
                for g in page_items:
                    print(f"    [cap] {uname} p{p} id={g.get('id')}  {g.get('away_full_name','')} @ {g.get('home_full_name','')}  {g.get('display_date','')}")
    pages_dedup = dedup_by_id(pages_raw)

    # 2) Filtrar: LEAGUE + fecha + que juegue ese equipo + rival válido
    considered = []
    for g in pages_dedup:
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

    # === Captura/dumps por usuario principal ===
    if PRINT_CAPTURE_SUMMARY:
        print(f"    [capturas] {team_name} ({username_exact}): raw={len(pages_raw)}  dedup={len(pages_dedup)}  considerados={len(considered)}")
    if DUMP_ENABLED:
        base = _safe_name(username_exact)
        _dump_json(f"{base}_raw.json", pages_raw)
        _dump_json(f"{base}_dedup.json", pages_dedup)
        _dump_json(f"{base}_considered.json", considered)

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
    scheduled = 13
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
    os.makedirs(DUMP_DIR, exist_ok=True)

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

    # Dump standings
    _dump_json("standings.json", rows)

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

    # Reporte de juegos de HOY (Chile) + dump
    try:
        games_today = games_played_today_scl()
    except Exception as e:
        games_today = []
        print(f"\n[WARN] games_played_today_scl falló: {e}")

    _dump_json("games_today.json", {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": games_today
    })

    print("\nJuegos jugados HOY (hora Chile)")
    if not games_today:
        print(" — No hay registros hoy —")
    else:
        for i, s in enumerate(games_today, 1):
            print(f"{i:>2}- {s}")

    print(f"\nÚltima actualización: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"JSON generados en: .\\{DUMP_DIR}\\")
    print("  - standings.json")
    print("  - games_today.json")
    print("  - <usuario>_raw.json / _dedup.json / _considered.json")

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

    rows.sort(key=lambda r: (-r.get("points", 0), -r.get("wins", 0), r.get("losses", 0)))
    return rows


# -------------------------------
# Juegos jugados HOY (Chile) - FIX TZ + DEDUP EXTRA
# -------------------------------
def games_played_today_scl():
    """
    Lista juegos del DÍA (America/Santiago) en formato:
      'Yankees 1 - Brewers 2  - 30-08-2025 - 3:28 pm (hora Chile)'
    Arreglos:
      - Si la fecha viene sin tz, se asume UTC y se convierte a America/Santiago.
      - Deduplicación por id y por una clave canónica (home, away, hr, ar, yyyy-mm-dd HH:MM).
      - Se requiere que AMBOS participantes pertenezcan a la liga, o CPU + miembro.
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

        # Ambos jugadores deben pertenecer a la liga, o CPU + miembro
        home_name_raw = (g.get("home_name") or "")
        away_name_raw = (g.get("away_name") or "")
        h_norm = normalize_user_for_compare(home_name_raw)
        a_norm = normalize_user_for_compare(away_name_raw)

        h_mem = h_norm in LEAGUE_USERS_NORM
        a_mem = a_norm in LEAGUE_USERS_NORM

        if not ((h_mem and a_mem) or (is_cpu(home_name_raw) and a_mem) or (is_cpu(away_name_raw) and h_mem)):
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

# ====== FIN DEL BLOQUE ======
