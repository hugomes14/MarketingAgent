"""Utilitarios dos testes do painel: aparelho (rig), guioes e extracao da chave."""

import hashlib
import itertools
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore
from marketing_agent.budget.controller import BudgetController
from marketing_agent.budget.pricing import (
    Usage,
)
from marketing_agent.clock import FixedClock
from marketing_agent.config import Settings
from marketing_agent.db.models import (
    AgentRun,
    Brand,
    CostEntry,
    CostReservation,
    InterviewSession,
    InterviewSubmission,
)
from marketing_agent.interview.service import InterviewService
from marketing_agent.llm.fake import FakeLlmClient
from marketing_agent.llm.types import LlmResponse, StopReason
from marketing_agent.panel.auth import TailscaleUser
from marketing_agent.panel.csrf import csrf_token_for
from marketing_agent.strategy.service import StrategyService

Factory = sessionmaker[Session]
Script = list[LlmResponse | Exception]
VALID_FROM = datetime(2026, 11, 1, tzinfo=UTC)
LOGIN = "owner@example.com"
SECRET = b"c" * 32
PEER = "127.0.0.1"
_request_ids = itertools.count(1)  # o ledger exige provider_request_id unico
KEY_PATTERN = re.compile(r'"Idempotency-Key": "([0-9a-f-]{36})"')


def scripted(script: Script) -> Script:
    """Garante `provider_request_id` unico nos guioes dos cenarios (o ledger o exige)."""
    return [
        item
        if isinstance(item, Exception)
        else LlmResponse(
            text=item.text,
            usage=Usage(input_tokens=100, output_tokens=50),
            provider_request_id=f"req-{next(_request_ids)}",
            resolved_model="fake-1",
            stop_reason=item.stop_reason,
        )
        for item in script
    ]


def turn_json(
    question: str | None = "Qual e o proximo passo?",
    *,
    notes: dict[str, str] | None = None,
    ready: bool = False,
    injection: bool = False,
) -> LlmResponse:
    """Resposta de turno do Diretor, com notas por topico opcionais."""
    updates = [
        {"topic": topic, "status": "suposicao", "note": note, "origin": "suposto"}
        for topic, note in (notes or {}).items()
    ]
    body = {
        "next_question": question,
        "summary": "Resumo do turno.",
        "gap_map_updates": updates,
        "ready_for_profile": ready,
        "injection_suspected": injection,
    }
    return LlmResponse(
        text=json.dumps(body),
        usage=Usage(input_tokens=1, output_tokens=1),
        provider_request_id=None,
        resolved_model="fake-1",
        stop_reason=StopReason.end,
    )


def json_response(body: dict[str, object]) -> LlmResponse:
    """Resposta do LLM falso com um corpo JSON."""
    return LlmResponse(
        text=json.dumps(body),
        usage=Usage(input_tokens=1, output_tokens=1),
        provider_request_id=None,
        resolved_model="fake-1",
        stop_reason=StopReason.end,
    )


@dataclass
class PanelRig:
    """Aplicacao do painel, servicos reais e utilitarios de pedido."""

    app: FastAPI
    client: TestClient
    factory: Factory
    store: ArtifactStore
    interview: InterviewService
    strategy: StrategyService
    budget: BudgetController
    llm: FakeLlmClient
    clock: FixedClock
    brand_id: str
    settings: Settings

    def token(self, login: str = LOGIN) -> str:
        return csrf_token_for(TailscaleUser(login=login, name=None), SECRET)

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {"Tailscale-User-Login": LOGIN, **(extra or {})}

    def get(self, path: str, headers: dict[str, str] | None = None) -> Response:
        return self.client.get(path, headers=self.headers(headers))

    def post(
        self,
        path: str,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        with_token: bool = True,
    ) -> Response:
        fields = {"csrf_token": self.token(), **(data or {})} if with_token else (data or {})
        return self.client.post(
            path, data=fields, headers=self.headers(headers), follow_redirects=False
        )

    def start_session(self) -> str:
        response = self.post("/entrevista")
        assert response.status_code == 303, response.text
        return response.headers["location"].rsplit("/", 1)[1]

    def answer(self, session_id: str, text: str, key: str) -> Response:
        return self.post(
            f"/entrevista/{session_id}/resposta",
            {"text": text},
            {"Idempotency-Key": key, "HX-Request": "true"},
        )

    def post_id(self, path: str) -> str:
        """POST que redireciona (303) para a pagina do novo artefacto; devolve o id."""
        response = self.post(path)
        assert response.status_code == 303, response.text
        return response.headers["location"].rsplit("/", 1)[1]

    def confirm_profile(self, answers: int = 3) -> str:
        session_id = self.start_session()
        for number in range(answers):
            assert self.answer(session_id, f"Resposta {number}.", f"k{number}").status_code == 200
        profile_id = self.post_id(f"/entrevista/{session_id}/perfil")
        assert self.post(f"/perfil/{profile_id}/confirmar").status_code == 303
        return profile_id

    def approve_diagnosis(self) -> str:
        diagnosis_id = self.post_id("/diagnostico")
        assert self.post(f"/diagnostico/{diagnosis_id}/aprovar").status_code == 303
        return diagnosis_id

    def add_cost(self, cost: Decimal) -> None:
        """Lancamento acertado no mes corrente, como o ledger real."""
        with self.factory.begin() as session:
            run = AgentRun(
                brand_id=self.brand_id,
                role="director",
                spec_version=1,
                trigger="test",
                status="succeeded",
                started_at=self.clock.now(),
            )
            session.add(run)
            session.flush()
            reservation = CostReservation(
                brand_id=self.brand_id,
                run_id=run.id,
                role="director",
                provider="fake",
                model_id="m1",
                price_version="v1",
                reserved_usd=cost,
                status="settled",
                created_at=self.clock.now(),
                settled_at=self.clock.now(),
            )
            session.add(reservation)
            session.flush()
            session.add(
                CostEntry(
                    brand_id=self.brand_id,
                    reservation_id=reservation.id,
                    role="director",
                    run_id=run.id,
                    provider="fake",
                    model_id="m1",
                    price_version="v1",
                    tokens_in=0,
                    tokens_out=0,
                    cost_usd=cost,
                    kind="measured",
                    at=self.clock.now(),
                )
            )

    def mark_in_progress(self, session_id: str, key: str, text: str) -> None:
        """Submissao `reserved` com reclamacao valida, como se outra aba estivesse a processar."""
        row = InterviewSubmission(
            session_id=session_id,
            idempotency_key=key,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            status="reserved",
        )
        with self.factory.begin() as session:
            session.add(row)
            session.flush()
            owner = session.get_one(InterviewSession, session_id)
            owner.busy_submission_id = row.id
            owner.busy_since = self.clock.now()

    def new_brand(self) -> str:
        with self.factory.begin() as session:
            brand = Brand(name="Outra marca")
            session.add(brand)
            session.flush()
            return brand.id


def extract_key(html: str) -> str | None:
    """Chave de idempotencia do `hx-headers` do formulario de resposta, se existir."""
    match = KEY_PATTERN.search(html)
    return match.group(1) if match else None


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://u:p@127.0.0.1:5433/db",
        "panel_allowed_logins": [LOGIN],
        "panel_trusted_proxy": [PEER],
    }
    return Settings(_env_file=None, **{**values, **overrides})  # type: ignore[arg-type]
