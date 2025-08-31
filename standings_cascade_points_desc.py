# standings_cascade_points_desc.py
# -----------------------------------------------------------------------------
# Construye la tabla de posiciones a partir del historial de juegos de la API
# de MLB The Show 25, filtrando por modo LEAGUE, fechas, y mapeando usuarios/
# equipos definidos en LEAGUE_ORDER. Pensado para ser importado desde app.py
# (Render/Flask) y también ejecutable por consola para debug.
#
# Columnas impresas:
# Pos | Equipo | Jugador | Prog(SCHEDULED_GAMES) |  JJ |  W |  L | P.Jugar | Pts
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Dict, List, Tuple, Any, Set

import requests

# ===== Config general =========================================================
API = "https://mlb25.theshow.com/apis/game_history.json"
PLATFORM = "psn"
MODE = "LEAGUE"

# FECHA mínima a considerar (juegos anteriores se ignoran)
SINCE = datetime(2025, 8, 30)

# NUEVO: total de juegos programados por equipo
SCHEDULED_GAMES = 12

# Páginas a pedir (por cada usuario)
PAGES = (1, 2)

# Red de peticiones
TIMEOUT = 20
RETRIES = 2
SLEEP_BETWEEN = 0.3  # descanso suave entre requests

# ===== Liga (RELLENAR con tu liga actual) ====================================
# IMPORTANTE: Rellena LEAGUE_ORDER con tu mapeo (usuario PSN, Equipo MLB)
# Ejemplos de formato, borra/edita a tu gusto:
LEAGUE_ORDER: List[Tuple[str, str]] = [
    # ("usuario1", "Astros"),
    # ("usuario2", "Rays"),
    # ...
]

# Aliases opcionales para normalizar jugadores con variación de nombre
# (Por ejemplo: {"AiramReynoso_", "Yosoyreynoso_"} si aplica en ESTA liga)
EXTRA_ALIASES: Set[str] = set()
# Si necesitas activar el alias clásico de tus otras ligas, quita el comentario:
# EXTRA_ALIASES.update({"AiramReynoso_", "Yosoyreynoso_"})

# ===== Derivados de liga (NO TOCAR si no sabes qué haces) ====================
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


def _parse_api_date(display_date: str) -> datetime | None:
    # ejemplo: "08/14/2025 02:30:59"
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
        # La API acostumbra devolver {"data": [...]}
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

            # Normaliza estructura
            out.append({
                "id": str(g.get("id", "")),
                "dt": dt,
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
            # ignora juego malformado
            pass
    return out


def fetch_all_games_for_league() -> List[Dict[str, Any]]:
    """Descarga y filtra todos los juegos de los usuarios de la liga."""
    all_games: List[Dict[str, Any]] = []
    for user, _team in LEAGUE_ORDER:
        try:
            raw = fetch_games_for_user(user)
            all_games.extend(filter_and_normalize_games(raw))
        except Exception:
            # si un usuario falla, seguimos con los demás
            continue
    return all_games


# ===== Cómputo por equipo/usuario ============================================
def compute_team_record_for_user(username_exact: str, team_name: str,
                                 games: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calcula W/L y puntos para el (usuario, equipo) dado, usando solo juegos LEAGUE >= SINCE.
    Aplica un conteo simple a partir de home/away + result (W/L).
    """
    username_norm = username_exact.lower()

    wins = 0
    losses = 0

    # Reglas extra (mercy/abandonos) opcionales:
    mercy_bonus = 0
    abandonos_penalty = 0

    for g in games:
        # detecta si el usuario participó (por nombre en home/away)
        players_norm = {g.get("home_name", "").strip().lower(),
                        g.get("away_name", "").strip().lower()}
        if username_norm not in players_norm:
            continue

        # ¿jugó como home o away?
        is_home = (g.get("home_name", "").strip().lower() == username_norm)

        # Resultado en función de W/L del lado correspondiente
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

        # (Opcional) Leer pistas para mercy/abandonos si las marcas en pitcher_info o anotaciones.
        # Aquí solo dejamos los acumuladores listos.
        # if "MER" in (g["display_pitcher_info"] or ""): mercy_bonus += 1
        # if "ABANDONO" in (g["display_pitcher_info"] or ""): abandonos_penalty += 1

    # Ajustes (por ahora identidades)
    wins_adj = wins
    losses_adj = losses

    # 5) Puntos y métricas de tabla (CORREGIDO: sin tab extra)
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
    # Permite probar rápido: imprime tabla en consola
    if not LEAGUE_ORDER:
        print("ATENCIÓN: LEAGUE_ORDER está vacío. Rellena tus (usuario, equipo) antes de ejecutar.")
    rows = get_table_data() if LEAGUE_ORDER else []
    _print_table(rows)
