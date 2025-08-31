# standings_cascade_points_desc.py
# -----------------------------------------------------------------------------
# Construye la tabla de posiciones a partir del historial de juegos de la API
# de MLB The Show 25. Filtra por modo LEAGUE, fecha mínima SINCE y los usuarios
# de la liga (LEAGUE_ORDER). Provee:
#   - get_table_data(): filas ordenadas para la tabla
#   - games_played_today_scl(): lista de strings con juegos de HOY (hora Chile)
#
# Columnas impresas (cuando se ejecuta como script):
# Pos | Equipo | Jugador | Prog(SCHEDULED_GAMES) |  JJ |  W |  L | P.Jugar | Pts
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Dict, List, Tuple, Any, Set, Optional
from zoneinfo import ZoneInfo

import requests

# ===== Config general =========================================================
API = "https://mlb25.theshow.com/apis/game_history.json"
PLATFORM = "psn"
MODE = "LEAGUE"

# FECHA mínima a considerar (juegos anteriores se ignoran)
SINCE = datetime(2025, 8, 30)

# NUEVO: total de juegos programados por equipo
SCHEDULED_GAMES = 12

# Páginas que pediremos para cada usuario
PAGES = (1, 2)

# Red de peticiones
TIMEOUT = 20
RETRIES = 2
SLEEP_BETWEEN = 0.3  # descanso suave entre requests

# ===== Liga (RELLENA ESTO con tu liga actual) =================================
# (usuario_psn, "Equipo")
LEAGUE_ORDER: List[Tuple[str, str]] = [
    # ("usuario1", "Astros"),
    # ("usuario2", "Rays"),
    # ...
]

# Aliases (variaciones del mismo usuario). Ejemplo típico:
EXTRA_ALIASES: Set[str] = set()
# Si aplica en ESTA liga, descomenta:
# EXTRA_ALIASES.update({"AiramReynoso_", "Yosoyreynoso_"})

# ===== Derivados de liga (no tocar si no sabes) ===============================
LEAGUE_USERS: Set[str] = {u for (u, _t) in LEAGUE_ORDER}
if EXTRA_ALIASES:
    LEAGUE_USERS.update(EXTRA_ALIASES)

LEAGUE_USERS_NORM: Set[str] = {u.lower() for u in LEAGUE_USERS}
TEAM_BY_USER: Dict[str, str] = {u: t for (u, t) in LEAGUE_ORDER}
USER_BY_TEAM: Dict[str, str] = {t: u for (u, t) in LEAGUE_ORDER}


# ===== Utilidades =============================================================
def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _parse_api_date(display_date: str) -> Optional[datetime]:
    # ejemplo de la API: "08/14/2025 02:30:59" (sin zona)
    try:
        return datetime.strptime(display_date, "%m/%d/%Y %H:%M:%S")
    except Exception:
        return None


def _req(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    last_err = None
    for _ in range(RETRIES):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            last_err = f"HTTP {r.status_code} - {r.text[:200]}"
        except Exception as e:
            last_err = str(e)
        time.sleep(SLEEP_BETWEEN)
    raise RuntimeError(f"Fallo request {url} params={params}. Ultimo error: {last_err}")


# ===== Descarga y filtrado de juegos =========================================
def fetch_games_for_user(username: str) -> List[Dict[str, Any]]:
    """Descarga páginas del historial de un usuario y devuelve lista cruda de juegos."""
    games: List[Dict[str, Any]] = []
    for page in PAGES:
        payload = {
            "username": username,
            "page": page,
            "platform": PLATFORM,
        }
        data = _req(API, payload)
        chunk = data.get("data") or []
        if not isinstance(chunk, list):
            continue
        games.extend(chunk)
    return games


def filter_and_normalize_games(raw_games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filtra LEAGUE + fecha mínima. Normaliza tipos y campos clave."""
    out: List[Dict[str, Any]] = []
    for g in raw_games:
        try:
            if (g.get("game_mode") or "").upper() != MODE:
                continue
            dt = _parse_api_date(g.get("display_date", ""))
            if not dt or dt < SINCE:
                continue

            home_res = (g.get("home_display_result") or "").upper()
            away_res = (g.get("away_display_result") or "").upper()

            out.append({
                "id": str(g.get("id", "")),
                "dt": dt,  # naive (asumimos UTC luego)
                "home_team": g.get("home_full_name") or "",
                "away_team": g.get("away_full_name") or "",
                "home_res": home_res,
                "away_res": away_res,
                "home_runs": _safe_int(g.get("home_runs"), 0),
                "away_runs": _safe_int(g.get("away_runs"), 0),
                "home_name": (g.get("home_name") or "").strip(),
                "away_name": (g.get("away_name") or "").strip(),
                "display_pitcher_info": g.get("display_pitcher_info") or "",
            })
        except Exception:
            pass  # ignora juego malformado
    return out


def fetch_all_games_for_league() -> List[Dict[str, Any]]:
    """Descarga y filtra todos los juegos de los usuarios de la liga."""
    all_games: List[Dict[str, Any]] = []
    for user, _team in LEAGUE_ORDER:
        try:
            raw = fetch_games_for_user(user)
            all_games.extend(filter_and_normalize_games(raw))
        except Exception:
            continue  # si un usuario falla, seguimos con los demás
    return all_games


# ===== Cómputo por equipo/usuario ============================================
def compute_team_record_for_user(username_exact: str, team_name: str,
                                 games: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calcula W/L y puntos para el (usuario, equipo) dado, usando solo juegos LEAGUE >= SINCE.
    """
    username_norm = username_exact.lower()

    wins = 0
    losses = 0

    # Reglas extra (mercy/abandonos) opcionales:
    mercy_bonus = 0
    abandonos_penalty = 0

    for g in games:
        # detecta si el usuario participó
        players_norm = {g.get("home_name", "").strip().lower(),
                        g.get("away_name", "").strip().lower()}
        if username_norm not in players_norm:
            continue

        is_home = (g.get("home_name", "").strip().lower() == username_norm)

        if is_home:
            if g["home_res"] == "W":
                wins += 1
            elif g["home_res"] == "L":
                losses += 1
        else:
            if g["away_res"] == "W":
                wins += 1
            elif g["away_res"] == "L":
                losses += 1

        # Opcional: detectar "mercy"/"abandono" si decides marcarlo en pitcher_info/notas
        # if "MER" in (g["display_pitcher_info"] or ""): mercy_bonus += 1
        # if "ABANDONO" in (g["display_pitcher_info"] or ""): abandonos_penalty += 1

    wins_adj = wins
    losses_adj = losses

    # 5) Puntos y métricas de tabla (indent correcto)
    points_base = 3 * wins_adj + 2 * losses_adj
    points = points_base + mercy_bonus - abandonos_penalty

    scheduled = SCHEDULED_GAMES
    played = max(wins_adj + losses_adj, 0)
    remaining = max(scheduled - played, 0)

    return {
        "user": username_exact,
        "team": team_name,
        "scheduled": scheduled,
        "played": played,
        "wins": wins_adj,
        "losses": losses_adj,
        "to_play": remaining,
        "points": points,
        "mercy_bonus": mercy_bonus,
        "abandonos_penalty": abandonos_penalty,
    }


# ===== Armado de tabla y ordenamiento ========================================
def build_table_rows(all_games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Construye filas de la tabla para todos los (usuario, equipo) de LEAGUE_ORDER."""
    rows: List[Dict[str, Any]] = []

    for user, team in LEAGUE_ORDER:
        r = compute_team_record_for_user(user, team, all_games)
        rows.append(r)

    # Orden: W (desc), JJ (desc), L (asc)
    rows.sort(key=lambda r: (-r["wins"], -r["played"], r["losses"], r["team"]))
    # Numeración de posiciones
    for i, r in enumerate(rows, start=1):
        r["pos"] = i
    return rows


def get_table_data() -> List[Dict[str, Any]]:
    """API principal para app.py: devuelve las filas ya listas/ordenadas."""
    games = fetch_all_games_for_league()
    return build_table_rows(games)


# ===== "Juegos jugados hoy" (hora Chile) =====================================
def games_played_today_scl(all_games: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """
    Devuelve una lista de strings con los juegos de HOY en horario Chile.
    Solo incluye juegos donde ambos participantes pertenecen a la liga
    (por nombre normalizado en LEAGUE_USERS_NORM).
    """
    tz_scl = ZoneInfo("America/Santiago")
    today_scl = datetime.now(tz_scl).date()

    # Usa juegos ya descargados si te los pasan; si no, los busca.
    games = all_games if all_games is not None else fetch_all_games_for_league()

    items: List[Tuple[datetime, str]] = []
    seen_ids: Set[str] = set()

    for g in games:
        try:
            gid = g.get("id") or ""
            if gid in seen_ids:
                continue

            # ambos participantes deben ser de la liga
            home_n = (g.get("home_name", "").strip().lower())
            away_n = (g.get("away_name", "").strip().lower())
            if home_n not in LEAGUE_USERS_NORM or away_n not in LEAGUE_USERS_NORM:
                continue

            # la fecha de la API es naive; asumimos UTC y convertimos a Chile
            dt_naive = g.get("dt")
            if not isinstance(dt_naive, datetime):
                continue
            dt_scl = dt_naive.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz_scl)

            if dt_scl.date() != today_scl:
                continue

            # armar línea legible
            hhmm = dt_scl.strftime("%I:%M %p").lstrip("0").lower()
            dia = dt_scl.strftime("%d-%m-%Y")
            line = f"{g['home_team']} {g['home_runs']} - {g['away_team']} {g['away_runs']} - {dia} - {hhmm} (hora Chile)"

            items.append((dt_scl, line))
            seen_ids.add(gid)
        except Exception:
            continue

    # Orden por hora en Chile (más antiguo -> más reciente)
    items.sort(key=lambda x: x[0])
    # Numerar como en tu sitio: "1- …"
    result: List[str] = []
    for i, (_dt, txt) in enumerate(items, start=1):
        result.append(f"{i}- {txt}")
    return result


# ===== Pretty print por consola (debug local) =================================
def _print_table(rows: List[Dict[str, Any]]) -> None:
    print("\nTabla de posiciones")
    print(f"Pos | Equipo            | Jugador         | Prog({SCHEDULED_GAMES}) |  JJ |  W |  L | P.Jugar | Pts")
    print("----+-------------------+-----------------+-------------------------+-----+----+----+---------+----")
    for r in rows:
        print(
            f"{r['pos']:>3} | "
            f"{r['team'][:19]:<19} | "
            f"{r['user'][:15]:<15} | "
            f"{r['scheduled']:>9}               | "
            f"{r['played']:>3} | "
            f"{r['wins']:>2} | "
            f"{r['losses']:>2} | "
            f"{r['to_play']:>7} | "
            f"{r['points']:>3}"
        )


if __name__ == "__main__":
    # Permite probar rápido localmente:
    if not LEAGUE_ORDER:
        print("ATENCIÓN: LEAGUE_ORDER está vacío. Rellena tus (usuario, equipo) antes de ejecutar.")
        exit(0)
    rows = get_table_data()
    _print_table(rows)

    print("\nJuegos jugados HOY (hora Chile):")
    for s in games_played_today_scl():
        print(s)
