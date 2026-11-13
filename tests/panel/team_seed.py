"""Sementes do estado da oficina, partilhadas pelos testes HTTP e de browser."""

import hashlib
from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import FixedClock
from marketing_agent.db.base import new_id
from marketing_agent.db.models import (
    AgentRun,
    Artifact,
    AuditLog,
    EvalRun,
    Job,
    MediaAsset,
    Post,
    PostRevision,
)
from marketing_agent.oficina.config import load_oficina_config
from marketing_agent.oficina.queries import TeamStateQueries
from marketing_agent.startup import content_job_lease, role_max_durations

Factory = sessionmaker[Session]


@dataclass
class TeamSeed:
    factory: Factory
    clock: FixedClock
    brand_id: str
    _plan: str | None = None
    _strategy: str | None = None
    _eval: str | None = None
    _slot: int = 0

    def seed_run(self, role, status, started_at=None, ended_at=None):
        start = started_at or self.clock.now()
        end = (
            ended_at
            if ended_at is not None
            else (None if status == "running" else self.clock.now())
        )
        row = AgentRun(
            brand_id=self.brand_id,
            role=role,
            spec_version=1,
            trigger="team-test",
            status=status,
            started_at=start,
            ended_at=end,
        )
        with self.factory.begin() as session:
            session.add(row)
            session.flush()
        return row

    def seed_artifact(self, kind, state="submitted", *, week="2026-W46"):
        self.clock.advance(timedelta(milliseconds=1))
        row = Artifact(
            brand_id=self.brand_id,
            type=kind,
            schema_version=1,
            state=state,
            payload={"semana": week} if kind == "plano" else {},
        )
        with self.factory.begin() as session:
            session.add(row)
            session.flush()
            session.add(
                AuditLog(
                    actor="OWNER-PRIVATE",
                    action="artifact.submit",
                    subject_type="artifact",
                    subject_id=row.id,
                    detail={},
                    at=self.clock.now(),
                )
            )
        return row

    def _refs(self):
        if self._plan is None:
            self._plan = self.seed_artifact("plano", "approved").id
            self._strategy = self.seed_artifact("estrategia", "approved").id
        return self._plan, self._strategy

    def seed_post(
        self, lifecycle="in_review", slot_key=None, tema="Tema", review_rounds=0, last_error=None
    ):
        plan, strategy = self._refs()
        self._slot += 1
        self.clock.advance(timedelta(milliseconds=1))
        row = Post(
            brand_id=self.brand_id,
            plan_artifact_id=plan,
            strategy_artifact_id=strategy,
            slot_key=slot_key or f"2026-W46-{self._slot}",
            scheduled_date=self.clock.now().date(),
            channels=["instagram"],
            brief={"tema": tema},
            lifecycle=lifecycle,
            review_rounds=review_rounds,
            last_error=last_error,
            created_at=self.clock.now(),
        )
        with self.factory.begin() as session:
            session.add(row)
            session.flush()
        return row

    def seed_asset(self):
        key = new_id()
        row = MediaAsset(
            brand_id=self.brand_id,
            kind="final",
            storage_backend="local",
            storage_key=f"team/{key}.jpg",
            mime="image/jpeg",
            width=1080,
            height=1350,
            bytes=100,
            sha256=hashlib.sha256(key.encode()).hexdigest(),
            origem="composta",
            created_by="OWNER-PRIVATE",
            created_at=self.clock.now(),
        )
        with self.factory.begin() as session:
            session.add(row)
            session.flush()
        return row

    def seed_revision(
        self, post_id, verdict="aprovar", final_asset_id=None, caption="CAPTION-PRIVATE"
    ):
        self.clock.advance(timedelta(milliseconds=1))
        with self.factory.begin() as session:
            number = 1 + (
                session.scalar(
                    select(func.max(PostRevision.rev_no)).where(PostRevision.post_id == post_id)
                )
                or 0
            )
            row = PostRevision(
                brand_id=self.brand_id,
                post_id=post_id,
                rev_no=number,
                revision_hash=hashlib.sha256(f"{post_id}:{number}".encode()).hexdigest(),
                caption=caption,
                hashtags=[],
                claims=[],
                review_verdict=verdict,
                review_violations=[],
                review_notes="NOTES-PRIVATE",
                origin="agente",
                created_at=self.clock.now(),
                final_asset_id=final_asset_id,
                layout="foto_inteira" if final_asset_id else None,
                layout_version=1 if final_asset_id else None,
            )
            session.add(row)
            session.flush()
        return row

    def seed_job(
        self,
        status="running",
        post_id=None,
        kind="generate_post",
        created_at=None,
        updated_at=None,
        locked_until=None,
    ):
        now = self.clock.now()
        if kind == "eval_package":
            if self._eval is None:
                _, strategy = self._refs()
                with self.factory.begin() as session:
                    evaluation = EvalRun(
                        brand_id=self.brand_id,
                        set_name="teste",
                        set_version=1,
                        seed=1,
                        status="running",
                        strategy_artifact_id=strategy,
                        briefs=[],
                        context_snapshot={},
                        estimate_usd=0,
                        created_by="OWNER-PRIVATE",
                        created_at=now,
                    )
                    session.add(evaluation)
                    session.flush()
                    self._eval = evaluation.id
            payload = {"eval_run_id": self._eval, "eval_item_id": new_id()}
        else:
            payload = {"post_id": post_id}
        row = Job(
            type=kind,
            payload=payload,
            status=status,
            run_at=now,
            attempts=1,
            max_attempts=3,
            created_at=created_at or now,
            updated_at=updated_at or now,
            locked_until=locked_until
            if locked_until is not None
            else (now + timedelta(minutes=5) if status == "running" else None),
        )
        with self.factory.begin() as session:
            session.add(row)
            session.flush()
        return row


@pytest.fixture
def team_seed(session_factory, clock, brand_id):
    return TeamSeed(session_factory, clock, brand_id)


@pytest.fixture
def team_queries(session_factory, clock):
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    return TeamStateQueries(
        session_factory,
        clock,
        role_max_durations(),
        load_oficina_config(root / "config/oficina.yaml"),
        job_lease=content_job_lease(time_multiplier=1),
    )
