"""Background jobs: a queue in PostgreSQL, worker processes, progress, cancellation, a journal.

    enqueue()      app/jobs/queue.py   — an API request puts a job in the queue
    worker         app/jobs/worker.py  — claims jobs (FOR UPDATE SKIP LOCKED) and runs handlers
    handlers       app/jobs/handlers.py — what each job kind does

A handler gets a `JobContext`: `ctx.session` is the transaction its results go into (the worker
commits it together with the "done" status, so a crash never leaves half a result), and
`ctx.progress()` / `ctx.log()` report through separate short transactions. Cancellation is
cooperative: `ctx.progress()` raises `JobCancelled` once a cancel was requested.
"""
