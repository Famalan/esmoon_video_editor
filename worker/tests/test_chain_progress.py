import time

from shared.stages import PIPELINE_ORDER, Stage
from worker.tasks.chain import build_pipeline


def test_pipeline_walks_through_all_stages(db_session, capture_progress, fake_job):
    build_pipeline.apply(args=[str(fake_job.id)]).get()

    # give the pubsub reader a moment to drain
    time.sleep(0.3)

    db_session.expire_all()
    job = db_session.get(type(fake_job), fake_job.id)
    assert job.current_stage == Stage.DONE
    assert job.status.value == "succeeded"

    stages = [event["stage"] for event in capture_progress]
    assert stages == [s.value for s in PIPELINE_ORDER] + ["done"]
