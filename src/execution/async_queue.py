"""
Non-Blocking Background Event Queue for Fast Mode Execution.
Ensures Telegram dispatches, disk journaling, and chart visualization updates
never sit on the critical order execution path.
"""

from __future__ import annotations
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Any, Dict, List, Optional


@dataclass
class QueuedTask:
    task_type: str  # "TELEGRAM", "JOURNAL", "CHART_DRAW", "CALLBACK"
    fn: Callable[..., Any]
    args: tuple = field(default_factory=tuple)
    kwargs: Dict[str, Any] = field(default_factory=dict)
    enqueued_at_ns: int = field(default_factory=time.perf_counter_ns)


class NonBlockingExecutionQueue:
    """
    Thread-safe, non-blocking asynchronous worker queue.
    Critical path threads call `enqueue()` which returns in < 5 microseconds.
    """
    def __init__(self, max_size: int = 10000, start_worker: bool = True):
        self._queue: queue.Queue[Optional[QueuedTask]] = queue.Queue(maxsize=max_size)
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self.processed_count: int = 0
        self.dropped_count: int = 0
        self.error_count: int = 0
        self._last_enqueue_latency_ns: int = 0

        if start_worker:
            self.start()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="FastModeAsyncQueueWorker",
            daemon=True
        )
        self._worker_thread.start()

    def stop(self, drain_timeout_sec: float = 2.0) -> None:
        if not self._running:
            return
        self._running = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=drain_timeout_sec)

    def enqueue(
        self,
        task_type: str,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any
    ) -> bool:
        """
        Enqueues a task without blocking the caller.
        Returns True if enqueued, False if queue was full.
        """
        t0 = time.perf_counter_ns()
        task = QueuedTask(
            task_type=task_type,
            fn=fn,
            args=args,
            kwargs=kwargs,
            enqueued_at_ns=t0
        )
        try:
            self._queue.put_nowait(task)
            self._last_enqueue_latency_ns = time.perf_counter_ns() - t0
            return True
        except queue.Full:
            self.dropped_count += 1
            self._last_enqueue_latency_ns = time.perf_counter_ns() - t0
            return False

    def flush_sync(self, timeout_sec: float = 2.0) -> int:
        """Drains all currently queued items (useful in deterministic unit tests)."""
        count = 0
        deadline = time.time() + timeout_sec
        while not self._queue.empty() and time.time() < deadline:
            try:
                item = self._queue.get_nowait()
                if item is None:
                    self._queue.task_done()
                    break
                self._execute_item(item)
                self._queue.task_done()
                count += 1
            except queue.Empty:
                break
        return count

    def _execute_item(self, item: QueuedTask) -> None:
        try:
            item.fn(*item.args, **item.kwargs)
            self.processed_count += 1
        except Exception:
            self.error_count += 1

    def _worker_loop(self) -> None:
        while self._running:
            try:
                item = self._queue.get(timeout=0.1)
                if item is None:
                    self._queue.task_done()
                    break
                self._execute_item(item)
                self._queue.task_done()
            except queue.Empty:
                continue
            except Exception:
                self.error_count += 1

    @property
    def last_enqueue_latency_us(self) -> float:
        return round(self._last_enqueue_latency_ns / 1000.0, 2)

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()
