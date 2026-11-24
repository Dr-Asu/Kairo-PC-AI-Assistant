"""Worker agents — the specialists the boss dispatches to.

Each worker does ONE kind of job and implements ``Worker.run(ctx)``.
Workers never talk to the boss's user directly, and they never
delete/send/install without the ask-first gate.
"""

from .base import TaskContext, Worker, WorkerResult

__all__ = ["TaskContext", "Worker", "WorkerResult"]
