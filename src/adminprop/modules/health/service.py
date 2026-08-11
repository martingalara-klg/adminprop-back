import asyncio
from urllib.parse import urlparse


async def check_tcp(url: str, timeout: float) -> str:
    """Chequeo liviano de disponibilidad: conexion TCP al host:puerto de la URL.

    sdd_04 §4.7 — /health verifica DB y Redis. En Fase 0 el chequeo es de
    conectividad; los issues #2/#3 lo profundizan (ping real via driver).
    """
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or {"postgresql": 5432, "redis": 6379}.get(parsed.scheme, 80)
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return "ok"
    except (TimeoutError, OSError):
        return "unreachable"
