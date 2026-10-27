"""Bounded serial job queue. A running action is never silently retried.

No prompts/DOM content are written to job metadata; caller manages retention.
"""
from __future__ import annotations
import asyncio
import json
import uuid
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class JobQueue:
    def __init__(self, store, executor, max_pending=20):
        self.store, self.executor = store, executor
        self.queue = asyncio.Queue(maxsize=max_pending)
        self.worker = None
        self.results = {}  # process-only, not persisted in SQLite

    async def start(self):
        if self.worker and not self.worker.done():
            return
        self.worker = asyncio.create_task(self._work())

    async def stop(self):
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
            self.worker = None
        self.store.interrupt_jobs()
        self.results.clear()

    def submit(self, action, params):
        if action not in ('navigate','chat','fill','click','read','probe'):
            raise ValueError('unsupported_job_action')
        if self.queue.full():
            raise ValueError('job_queue_full')
        job_id = uuid.uuid4().hex
        self.store.new_job(job_id,action)
        self.queue.put_nowait((job_id,action,params))
        return self.store.get_job(job_id)

    async def _work(self):
        while True:
            job_id, action, params = await self.queue.get()
            try:
                if self.store.get_job(job_id)['status'] != 'queued':
                    continue
                self.store.update_job(job_id,'running')
                try:
                    result = await self.executor(action, params)
                except asyncio.CancelledError:
                    self.store.update_job(job_id,'interrupted',error='worker_cancelled')
                    raise
                except ValueError as exc:
                    error = str(exc)
                    if not error.isascii() or not error.replace('_','').isalnum():
                        error = 'action_rejected'
                    self.store.update_job(job_id,'failed',error=error[:120])
                except Exception:
                    self.store.update_job(job_id,'failed',error='browser_or_internal_failure')
                else:
                    # Avoid persisting raw page text, prompts, or secrets in job history.
                    summary = {'success':True,'status':result.get('status','ok') if isinstance(result,dict) else 'ok'}
                    self.store.update_job(job_id,'completed',result=summary)
                    self.results[job_id] = result
                    if len(self.results) > 100:
                        self.results.pop(next(iter(self.results)))
            finally:
                self.queue.task_done()

    def cancel(self, job_id):
        job = self.store.get_job(job_id)
        if not job:
            raise ValueError('unknown_job')
        if job['status'] == 'queued':
            self.store.update_job(job_id,'cancelled')
        elif job['status'] == 'running':
            # A potentially submitted form/chat cannot be assumed safe to retry.
            raise ValueError('running_job_cannot_be_safely_cancelled')
        return self.store.get_job(job_id)
