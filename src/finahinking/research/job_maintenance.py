"""Read-only scoped metadata pages and observed local task maintenance facts."""
import base64
import json
import math
import shutil
import sqlite3
import time

from .application_service import WORKFLOW_VERSION, _utc

_WHERE = "json_extract(r.payload, '$.research_plan.validation_spec.scope_id')=? AND json_extract(r.payload, '$.workflow_version')=?"
_JOIN = "jobs j JOIN request_snapshots r ON r.task_ref=j.task_ref"


class JobMaintenance:
    def __init__(self, app):
        self.app = app

    def _cursor(self, cursor):
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048:
                raise ValueError()
            data = json.loads(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True))
            if set(data) != {"v", "scope", "workflow", "watermark", "created", "id"} or data["v"] != 1 or data["scope"] != self.app.scope_id or data["workflow"] != WORKFLOW_VERSION or type(data["watermark"]) is not int or data["watermark"] < 0 or type(data["created"]) not in (int, float) or not math.isfinite(data["created"]) or not isinstance(data["id"], str) or not 1 <= len(data["id"]) <= 128:
                raise ValueError()
            return data
        except (ValueError, TypeError, UnicodeError, KeyError) as exc:
            raise ValueError("任务分页游标无效或不属于当前工作区，请重新读取第一页") from exc

    def summaries(self, *, limit=20, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("每页任务数应在 1 至 100 之间")
        position = self._cursor(cursor) if cursor is not None else None
        args = (self.app.scope_id, WORKFLOW_VERSION)
        with self.app.queue._connect() as db:
            db.execute("BEGIN")
            watermark = position["watermark"] if position else db.execute(f"SELECT coalesce(max(j.rowid),0) FROM {_JOIN} WHERE {_WHERE}", args).fetchone()[0]
            where = _WHERE + " AND j.rowid<=?"
            values = (*args, watermark)
            total = db.execute(f"SELECT count(*) FROM {_JOIN} WHERE {where}", values).fetchone()[0]
            if position:
                where += " AND (j.created_at,j.job_id)<(?,?)"
                values += (position["created"], position["id"])
            rows = db.execute(f"SELECT j.job_id,j.status,j.created_at,j.updated_at,j.attempts,j.cancel_requested,json_extract(r.payload,'$.run_id') run_id,json_extract(r.payload,'$.instrument') instrument,json_extract(r.payload,'$.research_plan.hypotheses[0]') question,json_extract(r.payload,'$.research_plan.validation_spec.execution_mode') execution_mode FROM {_JOIN} WHERE {where} ORDER BY j.created_at DESC,j.job_id DESC LIMIT ?", (*values, limit+1)).fetchall()
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit-1]
            raw = {"v": 1, "scope": self.app.scope_id, "workflow": WORKFLOW_VERSION, "watermark": watermark, "created": last["created_at"], "id": last["job_id"]}
            next_cursor = base64.urlsafe_b64encode(json.dumps(raw, separators=(",", ":")).encode()).decode()
        jobs = [{**dict(row), "created_at": _utc(row["created_at"]), "updated_at": _utc(row["updated_at"]), "report_status": "NOT_CHECKED", "report_url": None, "run_url": f"/research/{row['run_id']}", "paper_only": True} for row in rows[:limit]]
        return {"schema_version": 1, "paper_only": True, "total": total, "limit": limit, "next_cursor": next_cursor, "jobs": jobs, "report_status": "NOT_CHECKED"}

    def status(self):
        now = time.time()
        args = (self.app.scope_id, WORKFLOW_VERSION)
        with self.app.queue._connect() as db:
            rows = db.execute(f"SELECT j.status,count(*) n FROM {_JOIN} WHERE {_WHERE} GROUP BY j.status", args).fetchall()
            counts = {row["status"]: row["n"] for row in rows}
            facts = db.execute(f"SELECT sum(j.status='running' AND j.lease_until<=?),sum(j.status='retryable' AND j.available_at>?),sum(j.cancel_requested=1 AND j.status NOT IN ('completed','failed','cancelled')) FROM {_JOIN} WHERE {_WHERE}", (now, now, *args)).fetchone()
            recent = db.execute(f"SELECT j.job_id,j.status,j.attempts,j.created_at FROM {_JOIN} WHERE {_WHERE} ORDER BY j.created_at DESC,j.job_id DESC LIMIT 100", args).fetchall()
        jobs, slow = [], []
        for row in recent:
            metrics = self.app.queue.load_metrics(row["job_id"])
            jobs.append({"job_id": row["job_id"], "status": row["status"], "attempts": row["attempts"], "runtime_metrics": metrics})
            if metrics is not None:
                for stage, seconds in metrics.get("stage_durations", {}).items():
                    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and math.isfinite(seconds) and seconds >= 0:
                        slow.append({"job_id": row["job_id"], "stage": stage, "seconds": seconds})
        def size(path):
            try:
                return path.stat().st_size
            except FileNotFoundError:
                return 0
            except OSError:
                return None
        try:
            free = shutil.disk_usage(self.app.root).free
        except OSError:
            free = None
        db_path = self.app.queue.path
        return {"counts": counts, "expired_leases": facts[0] or 0, "backoff_waiting": facts[1] or 0, "cancel_pending": facts[2] or 0,
            "recent": jobs, "recent_limit": 100, "slow_stages": sorted(slow, key=lambda row: (-row["seconds"], row["job_id"], row["stage"]))[:20],
            "resources": {"disk_free_bytes": free, "database_bytes": size(db_path), "wal_bytes": size(db_path.with_name(db_path.name+"-wal")), "worker_rss_bytes": None, "worker_cpu_seconds": None},
            "cache": self.app.runs.verification_cache.statistics(), "sqlite_version": sqlite3.sqlite_version,
            "as_of": _utc(now), "paper_only": True, "limits": ["队列与最近100项任务按当前工作区统计；磁盘余量和数据库大小属于本地存储。", "阶段耗时来自已保存的运行指标，缺少指标表示未知；页面读取不恢复或调度任务。", "缓存仅复用两秒内且文件路径和元数据未变的成功校验。"]}
