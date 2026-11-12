"""新启动实例使用生成脚本，已存在的旧实例继续使用原协议。"""

import json

from app import db as database, models
from app.services.scheduler import scheduler as legacy
from app.services.generated_runner import runner


class Execution:
    def engine(self, instance_id):
        with database.SessionLocal() as db:
            instance = db.get(models.Instance, instance_id)
            configuration = json.loads(instance.runtime_json or "{}") if instance else {}
            return runner if configuration.get("engine") == "python/v1" else legacy

    def start(self, instance_id):
        self.engine(instance_id).start(instance_id)

    def submit_review(self, instance_id, review_id, decision):
        self.engine(instance_id).submit_review(instance_id, review_id, decision)

    def rollback(self, instance_id, target, branch, reason, **options):
        self.engine(instance_id).rollback(instance_id, target, branch, reason, **options)

    def terminate(self, instance_id):
        self.engine(instance_id).terminate(instance_id)

    def shutdown(self):
        runner.shutdown()
        legacy._sessions.shutdown()


scheduler = Execution()
