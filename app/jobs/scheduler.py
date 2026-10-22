"""Agendador simples (serviço `scheduler` do compose): executa os jobs periodicamente.

Em produção pode ser substituído por cron/systemd timers chamando os mesmos módulos.
"""

from __future__ import annotations

import logging
import signal
import threading
from types import FrameType

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.jobs import maintenance, recurrences

logger = logging.getLogger("finpilot.scheduler")
RECURRENCE_INTERVAL_SECONDS = 15 * 60
MAINTENANCE_EVERY_N_CYCLES = 4 * 6  # a cada ~6 horas

_stop = threading.Event()


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    logger.info("sinal recebido; encerrando agendador", extra={"signal": signum})
    _stop.set()


def run_loop(stop: threading.Event) -> None:
    """Laço do agendador; usado pelo serviço `scheduler` e pela thread embutida no web."""
    cycle = 0
    while not stop.is_set():
        try:
            recurrences.run_once()
            if cycle % MAINTENANCE_EVERY_N_CYCLES == 0:
                maintenance.run_once()
        except SQLAlchemyError:
            # Falha temporária (ex.: banco indisponível): a próxima execução recupera o pendente.
            logger.exception("falha temporária no ciclo do agendador")
        cycle += 1
        stop.wait(RECURRENCE_INTERVAL_SECONDS)


def start_embedded() -> threading.Event:
    """Inicia o agendador numa thread daemon do processo atual. Retorna o evento de parada."""
    stop = threading.Event()
    threading.Thread(target=run_loop, args=(stop,), name="finpilot-scheduler", daemon=True).start()
    logger.info("agendador embutido iniciado")
    return stop


def main() -> None:
    configure_logging(get_settings().log_level)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    run_loop(_stop)


if __name__ == "__main__":
    main()
