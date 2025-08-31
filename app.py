from __future__ import annotations
from flask import Flask, render_template, jsonify
from datetime import datetime
from zoneinfo import ZoneInfo
import time

# Importa tu módulo de standings
import standings_cascade_points_desc as scpd
# Exponemos nombres para abreviar
get_table_data = scpd.get_table_data
games_played_today_scl = scpd.games_played_today_scl
SCHEDULED_GAMES = scpd.SCHEDULED_GAMES

app = Flask(__name__)

# =========================
# Cache (memoria de proceso)
# =========================
CACHE_TTL = 120            # segundos: tabla de posiciones
_today_cache_ttl = 60      # segundos: "Juegos jugados hoy" (bájalo a 10 si quieres casi en vivo)

_rows_cache = {"at": 0.0, "rows": None, "notes": []}
_today_cache = {"at": 0.0, "items": None}


def get_rows_cached():
    """Devuelve (rows, notes) cacheado por CACHE_TTL."""
    now = time.time()
    if _rows_cache["rows"] is not None and (now - _rows_cache["at"]) < CACHE_TTL:
        return _rows_cache["rows"], _rows_cache["notes"]

    try:
        rows = get_table_data()
        notes = []
        _rows_cache["rows"] = rows
        _rows_cache["notes"] = notes
        _rows_cache["at"] = now
        return rows, notes
    except Exception as e:
        # Falla dura: devolvemos lo último que tengamos
        if _rows_cache["rows"] is not None:
            return _rows_cache["rows"], _rows_cache["notes"]
        # Sin datos previos
        return [], [f"Error al actualizar standings: {e}"]


def get_games_today_cached():
    """Devuelve lista de strings 'juegos jugados hoy (hora Chile)' cacheada."""
    now = time.time()
    if _today_cache["items"] is not None and (now - _today_cache["at"]) < _today_cache_ttl:
        return _today_cache["items"]

    try:
        items = games_played_today_scl()
        _today_cache["items"] = items
        _today_cache["at"] = now
        return items
    except Exception as e:
        # Falla: devolvemos lo último o lista vacía
        return _today_cache["items"] or [f"Error al obtener juegos de hoy: {e}"]


# =========================
# Rutas
# =========================
@app.get("/")
def index():
    rows, notes = get_rows_cached()
    games_today = get_games_today_cached()

    tz_scl = ZoneInfo("America/Santiago")
    last_updated = datetime.now(tz_scl).strftime("%Y-%m-%d %H:%M:%S %Z")

    # NOTA: tu plantilla index.html debe leer estas variables.
    return render_template(
        "index.html",
        rows=rows,
        notes=notes,
        games_today=games_today,       # cada ítem incluye "(hora Chile)"
        scheduled=SCHEDULED_GAMES,     # para mostrar Prog(scheduled) si lo usas en Jinja
        last_updated=last_updated,     # "Última actualización"
    )


@app.get("/api/standings")
def api_standings():
    rows, notes = get_rows_cached()
    tz_scl = ZoneInfo("America/Santiago")
    return jsonify({
        "last_updated": datetime.now(tz_scl).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "rows": rows,
        "notes": notes,
        "scheduled": SCHEDULED_GAMES,
    })


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    # Para pruebas locales: `python app.py`
    # En Render se usa gunicorn (Procfile/comando start).
    app.run(host="0.0.0.0", port=5000, debug=True)
