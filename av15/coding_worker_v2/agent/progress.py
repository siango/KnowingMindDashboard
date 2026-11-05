"""CLI-only progress observer. stderr is independent of result stdout/SSOT."""
from __future__ import annotations

import sys
import shutil
import textwrap
import threading
import time


class LiveProgress:
    def __init__(self, command, stream=None):
        self.stream = stream if stream is not None else sys.stderr
        self.interactive = self.stream.isatty()
        self.rows = 1
        self.started = time.monotonic()
        self.phase_started = self.started
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.fields = dict(phase=command, state="RUNNING", task="-", model="-",
                           tool="-", done=0, total=0, qc="NOT_RUN",
                           receipt="PENDING", reference="-", timeout=None, result="PENDING", unit="steps", provider="-", reason="-", remote="-", waiting_for="Qwen")
        self.thread = threading.Thread(target=self._tick, daemon=True)

    def __enter__(self):
        self.render()
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type:
            self.update(phase="INTERRUPTED" if exc_type is KeyboardInterrupt else "ERROR",
                        state="BLOCKED", result="INTERRUPTED" if exc_type is KeyboardInterrupt else "ERROR")
        self.stop.set()
        self.thread.join()
        self.render(final=True)

    def update(self, **fields):
        with self.lock:
            if fields.get("phase", self.fields["phase"]) != self.fields["phase"]:
                self.phase_started = time.monotonic()
            self.fields.update(fields)
            self.render()

    def _tick(self):
        while not self.stop.wait(1):
            self.render()

    def render(self, final=False):
        with self.lock:
            f = self.fields
            elapsed = time.monotonic() - self.started
            percent = f"{100 * f['done'] // f['total']}%" if f['total'] else "N/A"
            waiting = (f" Waiting for {f['waiting_for']} wait={time.monotonic() - self.phase_started:.1f}s"
                       f" timeout={f['timeout']}s" if f['state'] == 'WAITING' and f['timeout'] is not None else "")
            line = (f"[km] {f['state']} phase={f['phase']} task={f['task']} "
                    f"provider={f['provider']} model={f['model']} tool={f['tool']} remote={f['remote']} reason={f['reason']} elapsed={elapsed:.1f}s "
                    f"progress={percent} completed_{f['unit']}={f['done']}/{f['total']} "
                    f"qc={f['qc']} receipt={f['receipt']} result={f['result']} ref={f['reference']}{waiting}")
            # Repaint wrapped rows in narrow terminals; pipes get plain lines.
            try:
                if self.interactive:
                    width = max(20, shutil.get_terminal_size((120, 24)).columns - 1)
                    rows = textwrap.wrap(line, width=width) or [""]
                    prefix = "\r" + (f"\033[{self.rows - 1}A" if self.rows > 1 else "") + "\033[2K\033[J"
                    self.stream.write(prefix + "\r\n".join(rows) + ("\n" if final else ""))
                    self.rows = len(rows)
                else:
                    self.stream.write(line + "\n")
                self.stream.flush()
            except (OSError, ValueError):
                pass  # A closed progress stream cannot change execution outcome.
