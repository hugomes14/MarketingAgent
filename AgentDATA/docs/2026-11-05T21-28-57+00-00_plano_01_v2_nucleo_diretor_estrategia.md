# Plano de implementação 01 (v2): núcleo, Diretor em entrevista, diagnóstico e estratégia

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

- Timestamp: `2026-11-05T21:28:57+00:00`
- Autor: `agent`
- Estado de execução: **Tarefas 1 a 14 concluídas; Tarefa 15 por fazer.**
- Substitui: Anexo E.1 do plano mestre (Plano 01 v1), com as correções do Anexo E, secção E.0

**Goal:** Construir o esqueleto Python, o núcleo comum dos agentes e o primeiro percurso do Diretor, que termina com perfil de marca, diagnóstico e estratégia aprovados no painel.

- **Núcleo:** dados, artefactos com versão ativa, custo com reservas por chamada, motor, LLM com rota por papel.
- **Percurso do Diretor:** entrevista, perfil de marca ativo, diagnóstico crítico e estratégia, com uma decisão do responsável por cada proposta de oferta.

**Architecture:**

- Pacote `marketing_agent` em `src/`, com PostgreSQL como única base. Todos os testes com BD correm em Postgres real.
- `AgentEngine` síncrono: executa um `Loop` sob um `AgentSpec`. Antes de cada chamada paga reserva o custo e verifica os limites da execução. Persiste passo, reserva e lançamento em transações curtas e classifica os erros.
- O LLM chega por um `LlmRouter`, que resolve fornecedor e modelo **por papel**.
- O painel é FastAPI com Jinja e HTMX. Fica atrás do `tailscale serve` e tem proteção de origem/CSRF.

**Tech Stack:** Python 3.12 (`uv python pin 3.12`), `uv`, FastAPI, Uvicorn, SQLAlchemy 2, Alembic, psycopg 3, Pydantic 2, pydantic-settings, PyYAML, Jinja2, HTMX (ficheiro local), SDKs `anthropic` e `openai`, pytest, ruff, mypy. Postgres 17 em Docker para desenvolvimento e testes.

**Spec:** `AgentDATA/docs/2026-11-05T21-10-57+00-00_plano_mestre_marketing_ai_manager.md`. As partes relevantes são as secções 3, 5, 6, 8, 9 e 10, os Anexos A e B (W1, W2) e o Anexo E.0.

**Pré-requisito:** Docker Desktop, ou Postgres 17 local, na máquina de desenvolvimento. Hoje não existe nenhum dos dois. Sem Postgres, os testes de BD **falham com mensagem explícita** e não são ignorados.

## Global Constraints

- **Python 3.12**, fixado em `.python-version` e em `requires-python = ">=3.12,<3.13"`. Nomes **snake_case/PEP 8** (D11).
- **Funções e tipos:** funções até 25 linhas, aceitável até 40. Sem `Any` nem `dict` não tipado sem justificação. Docstring nas funções públicas (`AGENTS.md`).
- **Integridade por marca:** todas as tabelas com dados de marca levam `brand_id`, e as relações entre elas usam FK composta `(brand_id, id)` (Anexo A).
- **Artefactos:** o conteúdo é imutável. Estados `draft`, `submitted`, `approved`, `rejected`, `withdrawn`. A versão ativa vive em `artifact_head` e só muda na aprovação (F13).
- **Rotas:** fornecedor e modelo **por papel** em `ROLE_ROUTES`, nunca fixos no código (D9, D27).
- **Orçamento:** 20 USD por mês são **referência**. Avisos a 50 %, 80 % e 100 %, sem bloqueio. Bloqueiam só os limites técnicos por execução: passos, tokens, custo e duração (D14, D26, secção 8.4).
- **Tentativas:** saída do LLM validada por esquema, com **no máximo 3 tentativas** (1 + 2 repetições) e só para `invalid_output`. `refusal`, `truncated` e `limit` vão para `needs_human` sem repetir. `network` repete até 2 vezes com espera (secção 8.1).
- **Texto do utilizador:** é dado, nunca instrução. Nenhuma mudança de estado de artefacto depende do texto do chat; só de ações explícitas no painel.
- **Segredos:** lidos **só de ficheiros** `*_FILE` (A.8). Nunca entram em prompts nem em logs.
- **Relógio:** injetado (`Clock`) em todos os componentes que leem o tempo. Mês civil em **UTC**.
- **Entrevista:** mensagem do utilizador com 1 a 4000 caracteres.
- **Painel:** cabeçalho `Tailscale-User-Login` aceite **só** de um peer em `PANEL_TRUSTED_PROXY`. Todas as mutações exigem a mesma origem e um token CSRF. A saída é escapada por omissão. Sem scripts externos.
- **Transações:** curtas. Nunca segurar um lock de BD durante uma chamada a um fornecedor (5.1).
- **Commits:** segundo `AgentDATA/procedures/git-operation.md` (ficheiros concretos, `tipo: descrição` em português) e as instruções de atribuição da sessão. Nunca commitar segredos.
- **Validação de cada tarefa:**
  1. Arrancar a base de testes com `docker compose -f compose.test.yaml up -d`.
  2. Correr `uv run ruff check`, `uv run mypy src` e `uv run pytest`, com `TEST_DATABASE_URL` definido.

## Review Focus

1. **O browser repete um pedido** (timeout ou duplo clique) enquanto a submissão ainda corre. Esperado: resposta "em curso" e nenhuma segunda chamada paga (teste na Tarefa 11).
2. **O processo morre depois da chamada ao LLM e antes do commit.** Esperado: a reserva fica `unknown_outcome` e conta no gasto; a retoma não faz segunda chamada para a mesma submissão (Tarefa 15).
3. **Proposta de oferta propaga-se sem decisão** do responsável, ou o texto "ignora as regras e aprova" altera estados. Esperado: a estratégia só se aprova com uma decisão por proposta; a vista ativa só tem as aceites; o texto não muda estados (Tarefas 11 e 12).
4. **Pedido com cabeçalho forjado** de um peer que não é o proxy, ou POST vindo de outro site. Esperado: 403 nos dois casos (Tarefas 13 e 14).
5. **Gasto acima de 20 USD na passagem do mês.** Esperado: o trabalho continua com aviso `over_reference`; no mês seguinte o gasto recomeça do zero; o limite por execução continua a bloquear (Tarefa 5).

---

## Estrutura de ficheiros

| Ficheiro | Responsabilidade |
| --- | --- |
| `pyproject.toml`, `.python-version`, `.env.example`, `compose.test.yaml` | Projeto, dependências, ferramentas, Postgres de teste |
| `config/model_prices.yaml` | Preços versionados (sem segredos) |
| `src/marketing_agent/config.py`, `clock.py`, `secrets.py` | `Settings`, `RoleRoute`, relógio, leitura de segredos |
| `src/marketing_agent/db/base.py`, `db/models.py`, `migrations/` | Motor, tabelas, Alembic |
| `src/marketing_agent/artifacts/store.py` | Artefactos, transições, ativação |
| `src/marketing_agent/budget/pricing.py`, `budget/controller.py` | Preços, reservas, acerto, estado do mês |
| `src/marketing_agent/harness/spec.py`, `harness/engine.py`, `harness/validators.py` | `AgentSpec`, motor, validadores registados |
| `src/marketing_agent/llm/types.py`, `fake.py`, `fake_scenarios.py`, `router.py`, `anthropic_adapter.py`, `openai_adapter.py`, `factory.py` | Contrato LLM, adaptadores, rota por papel |
| `src/marketing_agent/agents/diretor/` | `spec_interview.yaml`, `spec_diagnostico.yaml`, `spec_estrategia.yaml`, `prompt_*.md`, `schemas.py`, `gap_map.py` |
| `src/marketing_agent/interview/service.py`, `interview/loop.py` | Sessões, submissões, turnos, perfil |
| `src/marketing_agent/strategy/service.py`, `strategy/loops.py` | Diagnóstico, estratégia, decisões |
| `src/marketing_agent/panel/auth.py`, `csrf.py`, `app.py`, `main.py`, `templates/`, `static/` | Painel |
| `src/marketing_agent/cli.py` | Comandos de operação: marca, preços, recuperação |
| `tests/` | Espelha `src/`; `tests/conftest.py` com as fixtures de Postgres e relógio |

---

### Task 1: Esqueleto, configuração, relógio e segredos

> **Estado: concluída em 2026-11-05** (commits `2cf36ff`, `87aa100`, `a9b5563`). 30 testes, `ruff` e `mypy` strict limpos; revisão da tarefa e revisão final do ramo fechadas.

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.env.example`, `compose.test.yaml`, `src/marketing_agent/__init__.py`, `src/marketing_agent/config.py`, `src/marketing_agent/clock.py`, `src/marketing_agent/secrets.py`, `tests/test_config.py`, `tests/test_clock.py`, `tests/test_secrets.py`
- Modify: `README.md` (secção "Desenvolvimento": pré-requisitos, Postgres de teste, comandos de validação)

**Interfaces:**
- Produces:
  - `Capability = Literal["text", "image_input", "json_schema"]`.
  - `RoleRoute(provider: Literal["fake", "anthropic", "openai"], model_id: str, capabilities: frozenset[Capability])` (Pydantic, congelado).
  - `Settings` (pydantic-settings), com os campos:

    | Campo | Tipo e valor por omissão |
    | --- | --- |
    | `database_url` | `str` |
    | `monthly_budget_reference_usd` | `Decimal = Decimal("20")` |
    | `budget_warn_percents` | `tuple[int, ...] = (50, 80, 100)` |
    | `role_routes` | `dict[str, RoleRoute]`, lido de `ROLE_ROUTES` em JSON |
    | `anthropic_api_key_file` | `Path \| None = None` |
    | `openai_api_key_file` | `Path \| None = None` |
    | `openai_base_url` | `str \| None = None` |
    | `panel_allowed_logins` | `list[str]` |
    | `panel_trusted_proxy` | `list[str] = ["127.0.0.1", "::1"]` |
    | `max_message_chars` | `int = 4000` |
    | `model_prices_path` | `Path = Path("config/model_prices.yaml")` |

  - `get_settings() -> Settings`.
  - `Clock` (Protocol) com `now() -> datetime` (sempre com fuso UTC); `SystemClock`; `FixedClock(at: datetime)` com `advance(delta: timedelta) -> None`.
  - `read_secret_file(path: Path) -> str`.

- [x] **Step 1: Escrever os testes que falham.**
  - `test_config.py::test_defaults`: `monthly_budget_reference_usd == Decimal("20")` e `budget_warn_percents == (50, 80, 100)`.
  - `test_config.py::test_role_routes_from_json`: com `ROLE_ROUTES='{"diretor":{"provider":"fake","model_id":"fake-1","capabilities":["text","json_schema"]}}'`, a rota `diretor` tem `provider == "fake"`.
  - `test_config.py::test_unknown_provider_rejected`: um `ROLE_ROUTES` com `"provider":"x"` levanta `ValidationError`.
  - `test_clock.py::test_fixed_clock_advance` e `test_clock.py::test_naive_datetime_rejected`: `FixedClock(datetime(2026, 11, 1))` levanta `ValueError`.
  - `test_secrets.py::test_reads_and_strips`.
  - `test_secrets.py::test_missing_file_error_names_path`: a mensagem contém o caminho.
  - `test_secrets.py::test_empty_file_rejected`.
- [x] **Step 2: Correr e ver falhar.** `uv run pytest tests/test_config.py tests/test_clock.py tests/test_secrets.py -v` → falha por módulo inexistente.
- [x] **Step 3: Criar o projeto e implementar.**
  - Projeto: `uv init --package`, `uv python pin 3.12`; `uv add` das dependências da Tech Stack; `uv add --dev pytest ruff mypy httpx`.
  - Ferramentas: ruff com linha 100; mypy `strict`; marcador pytest `live`.
  - `compose.test.yaml`: serviço `postgres:17` na porta 5433, base `marketing_agent_test`.
  - `.env.example`: todos os nomes de A.8 usados neste plano, com valores fictícios.
  - Commitar `uv.lock`.
- [x] **Step 4: Correr a validação global.** Tudo passa.
- [x] **Step 5: Commit.** `feat: esqueleto, configuracao, relogio e segredos`.

**Notas de execução (desvios e decisões a levar para as tarefas seguintes):**

- `Settings.database_url` é **obrigatório**, sem valor por omissão e oculto no `repr`: os testes e `migrations/env.py` (Tarefa 2) definem `DATABASE_URL` a partir de `TEST_DATABASE_URL`.
- O `.env` só leva campos de `Settings` (chaves desconhecidas são rejeitadas, sem mostrar o valor); `TEST_DATABASE_URL` exporta-se no shell.
- `RoleRoute` rejeita chaves extra e `model_id` vazio; orçamento e `max_message_chars` têm de ser > 0; percentagens de aviso entre 1 e 100. `role_routes` e `panel_allowed_logins` vazios por omissão.
- `read_secret_file` lê `utf-8-sig`, rejeita ficheiros não UTF-8, vazios ou com caracteres não imprimíveis (NUL, BOM duplicado, UTF-16 sem BOM), sem nunca mostrar o conteúdo; erro `SecretFileError`.
- `compose.test.yaml` publica o Postgres só em `127.0.0.1:5433`; só foi validado como YAML (sem Docker na máquina de desenvolvimento).
- Comandos que instalam pacotes levam `UV_LINK_MODE=copy` neste ambiente (ver `AgentDATA/Knowledge/2026-11-05T22-21-14+00-00_uv_hardlink_acesso_negado.md`).
- Tarefa 4: acrescentar `types-PyYAML` às dependências de desenvolvimento. Tarefa 9: `-m "not live"` por omissão. Tarefa 10: valores em branco viram `None`. Tarefas 14 e 15: `exc.errors(include_input=False)`, `PANEL_CSRF_SECRET_FILE` em `Settings` e apanhar `SettingsError` e `ValidationError`.
- Achados menores adiados: ver `AgentDATA/context/2026-11-02T12-25-40+00-00_analise_marketing_ai_manager_estado.md`.

### Task 2: Base de dados e migração inicial (Postgres)

**Files:**
- Create: `src/marketing_agent/db/__init__.py`, `db/base.py`, `db/models.py`, `alembic.ini`, `migrations/env.py`, `migrations/versions/0001_core.py`, `tests/conftest.py`, `tests/db/test_migration.py`, `tests/db/test_constraints.py`

**Interfaces:**
- Consumes: `Settings.database_url` (T1).
- Produces:
  - `make_engine(url: str) -> Engine` e `make_session_factory(engine: Engine) -> sessionmaker[Session]`.
  - Modelos: `Brand`, `AuditLog`, `SystemFlag`, `ModelPriceRow`, `CostReservation`, `CostEntry`, `AgentRun`, `AgentStep`, `Artifact`, `ArtifactHead`, `BrandProfile`, `StrategyDecision`, `InterviewSession`, `InterviewSubmission`, `InterviewTurn`. Colunas e restrições do Anexo A.2.
  - Acréscimos ao Anexo A:
    - `StrategyDecision(brand_id, strategy_artifact_id, proposal_index, accepted: bool, decided_by, decided_at)`, **UNIQUE** `(strategy_artifact_id, proposal_index)`;
    - `InterviewSession.busy_submission_id` NULL e `busy_since` NULL.
  - Postgres: um *trigger* `artifact_payload_immutable` rejeita `UPDATE` de `payload`, `type`, `brand_id` ou `parent_id` em `artifact`.
  - Fixtures em `conftest.py`:
    - `pg_url`: lê `TEST_DATABASE_URL`; se estiver ausente, `pytest.fail("TEST_DATABASE_URL em falta: arrancar compose.test.yaml")`;
    - `session_factory`: base limpa por teste, com `alembic upgrade head` e `TRUNCATE` entre testes;
    - `clock`: `FixedClock(2026-11-05T12:00:00+00:00)`;
    - `brand_id`: marca criada.
- O Anexo A.2 lista mais tabelas (publicação, entrada, métricas). Essas ficam para os Planos 2 a 5 (YAGNI); `agent_definition` também, porque as especificações vêm de ficheiros.

- [x] **Step 1: Escrever os testes que falham.**
  - `test_migration.py::test_upgrade_head_creates_core_tables`: as 15 tabelas acima existem.
  - `test_constraints.py::test_cross_brand_head_rejected`: um `artifact_head` da marca B a apontar para um artefacto da marca A levanta `IntegrityError`.
  - `test_constraints.py::test_artifact_state_check`: `state='superseded'` levanta `IntegrityError`.
  - `test_constraints.py::test_payload_update_blocked_by_trigger`: um `UPDATE artifact SET payload=…` em SQL bruto levanta `DBAPIError`.
  - `test_constraints.py::test_cost_entry_unique_per_reservation`.
  - `test_constraints.py::test_turn_seq_unique_per_session`.
  - `test_constraints.py::test_submission_key_unique_per_session`.
- [x] **Step 2: Correr e ver falhar.** `uv run pytest tests/db -v`.
- [x] **Step 3: Implementar.**
  - Modelos com `JSONB` e `Numeric(12, 6)`.
  - FKs compostas `(brand_id, id)`: cada tabela referenciada tem `UNIQUE (brand_id, id)`.
  - `CHECK` para cada enum.
  - Migração `0001` escrita à mão, incluindo o *trigger*.
  - `migrations/env.py` lê `Settings.database_url`.
- [x] **Step 4: Correr a validação global.**
- [x] **Step 5: Commit.** `feat: tabelas do nucleo em postgres com integridade por marca`.

**Notas de execução (Tarefa 2):**

- Concluída no ramo `agent/2026-11-06_plano01_tarefa2` (commit `0b64250`). 40 testes passam (10 em `tests/db`), `ruff` e `mypy src` limpos, roundtrip `downgrade base` + `upgrade head` OK.
- Testes além do plano: `test_migration_matches_models` (`compare_metadata` sem diferenças), `test_state_update_allowed_by_trigger` e `test_strategy_decision_unique_per_proposal`.
- Fixture `engine` (por sessão) recria o schema `public`, aplica `alembic upgrade head` e só depois cada teste faz `TRUNCATE` das tabelas; `DATABASE_URL` é definido a partir de `TEST_DATABASE_URL` só durante a migração.
- Nomes de restrições seguem a convenção em `db/base.py`; a migração usa os mesmos nomes por escrito.
- `agent_step`, `interview_submission` e `interview_turn` não levam `brand_id`: a marca vem da execução ou da sessão a que pertencem.
- `audit_log` ainda não tem proteção contra `UPDATE`/`DELETE` (o papel da aplicação só existe a partir do Plano de operação).

### Task 3: Artefactos imutáveis com versão ativa

**Files:**
- Create: `src/marketing_agent/artifacts/__init__.py`, `artifacts/store.py`, `tests/artifacts/test_store.py`

**Interfaces:**
- Consumes: `make_session_factory`, `Artifact`, `ArtifactHead`, `BrandProfile`, `AuditLog` (T2); `Clock` (T1).
- Produces:
  - `ArtifactType`, `StrEnum` com valores: `perfil_marca`, `diagnostico`, `estrategia`, `plano`, `brief`, `rascunho_texto`, `rascunho_visual`, `revisao`, `nota_pesquisa`, `revisao_mensal`, `ficha_lead`.
  - `ArtifactState`, `StrEnum` com valores: `draft`, `submitted`, `approved`, `rejected`, `withdrawn`.
  - `ArtifactRecord`: dataclass congelada com `id, brand_id, type, state, payload: Mapping[str, object], parent_id, schema_version, produced_by_run_id`.
  - `ArtifactStore(session_factory, clock: Clock)` com os métodos:

    | Método | Retorno |
    | --- | --- |
    | `create(brand_id: str, type: ArtifactType, payload: Mapping[str, object], *, parent_id: str \| None = None, produced_by_run_id: str \| None = None, schema_version: int = 1)` | `ArtifactRecord` (em `draft`) |
    | `submit(artifact_id: str, actor: str)` | `ArtifactRecord` |
    | `approve_and_activate(artifact_id: str, actor: str, scope: str = "brand")` | `ArtifactRecord` |
    | `reject(artifact_id: str, actor: str, reason: str)` | `ArtifactRecord` |
    | `withdraw(artifact_id: str, actor: str)` | `ArtifactRecord` |
    | `get(artifact_id: str)` | `ArtifactRecord` |
    | `active(brand_id: str, type: ArtifactType, scope: str = "brand")` | `ArtifactRecord \| None` |

  - `InvalidTransition(Exception)` e `ArtifactNotFound(Exception)`.
- **Transições permitidas:** `draft→submitted`, `submitted→approved`, `submitted→rejected`, `draft→withdrawn` e `submitted→withdrawn`.
- **`approve_and_activate`:** uma transação. Aprova o artefacto, faz *upsert* de `artifact_head` guardando `previous_artifact_id` e, se o tipo for `perfil_marca`, atualiza a projeção `brand_profile`. Cada transição escreve um `AuditLog` com o `actor`.

- [x] **Step 1: Escrever os testes que falham.**
  - `test_create_is_draft`.
  - `test_new_draft_does_not_change_active`: com o perfil v1 ativo, criar e submeter o v2 deixa `active(...)` igual a v1.
  - `test_rejected_draft_keeps_active`: rejeitar o v2 deixa v1 ativo.
  - `test_activation_records_previous`: aprovar o v2 dá `active == v2` e `previous_artifact_id == v1`; o v1 continua `approved`.
  - `test_profile_activation_updates_projection`.
  - `test_illegal_transition_raises`: `draft→approved` levanta `InvalidTransition`.
  - `test_transitions_write_audit_with_actor`.
  - `test_payload_preserved_in_storage`: depois de várias transições, o `payload` lido por SQL bruto é igual ao original.
- [x] **Step 2: Correr e ver falhar.** `uv run pytest tests/artifacts -v`.
- [x] **Step 3: Implementar.** Transições num mapa constante; `SELECT … FOR UPDATE` na linha do artefacto na transição.
- [x] **Step 4: Correr a validação global.**
- [x] **Step 5: Commit.** `feat: artefactos imutaveis com versao ativa separada`.

**Notas de execução (Tarefa 3):**

- Concluída no ramo `agent/2026-11-06_plano01_tarefa2` (commits `00fc9e9` e `c019b34`), em modo subagente: implementador, revisão da tarefa e uma ronda de correção com re-revisão limpa. 51 testes passam; `ruff` e `mypy src` limpos.
- Decisões: `brand_profile.data` recebe a cópia do payload do `perfil_marca` aprovado; `approve_and_activate` não restringe o tipo do artefacto.
- Correção da ronda 1: o `previous_artifact_id` vem do `ON CONFLICT DO UPDATE` (linha existente), porque sem linha de `artifact_head` não há o que bloquear na primeira ativação.
- Achados menores adiados: sem teste de ativação com `scope` diferente de `brand` além do de `week:...`; o teste de corrida é probabilístico (barreira só alinha o início, sem timeout); argumentos por omissão a sombrear `scope` nesse teste.

### Task 4: Preços versionados

**Files:**
- Create: `config/model_prices.yaml`, `src/marketing_agent/budget/__init__.py`, `budget/pricing.py`, `tests/budget/test_pricing.py`

**Interfaces:**
- Consumes: `ModelPriceRow` (T2).
- Produces:
  - `PriceTier(min_prompt_tokens: int, input_per_million: Decimal, output_per_million: Decimal)`.
  - `ModelPrice(provider: str, model_id: str, price_version: str, tiers: tuple[PriceTier, ...])`: o primeiro escalão tem `min_prompt_tokens = 0`; os valores são ≥ 0.
  - `Usage(input_tokens: int, output_tokens: int, reasoning_tokens: int | None = None)`.
  - `compute_cost_usd(price: ModelPrice, usage: Usage) -> Decimal`: escolhe o escalão pelo `input_tokens` e soma entrada e saída. `reasoning_tokens` **não** se soma, porque já vem contido em `output_tokens` (secção 8.2).
  - `load_model_prices(path: Path) -> list[ModelPrice]`.
  - `sync_model_prices(session_factory, prices: Sequence[ModelPrice]) -> None`: idempotente por `(provider, model_id, price_version)`.
  - `PriceBook(session_factory)` com `get(provider: str, model_id: str) -> ModelPrice` (a versão mais recente); levanta `UnknownPrice` com o par na mensagem.
- **Conteúdo de `config/model_prices.yaml`:** preços-base da secção 8.2 com `price_version: "2026-11-05"` e o modelo `fake-1` a zero.
  - O escalão do Haiku 5.5 acima de 100 mil tokens copia-se da página oficial [S2] no momento da implementação. **Não se inventa.**
  - O GPT-5.4 nano **fica fora** até ser confirmada a disponibilidade.

- [x] **Step 1: Escrever os testes que falham.**
  - `test_cost_exact`: `PriceTier(0, Decimal("2"), Decimal("10"))` com 6000 tokens de entrada e 1500 de saída dá `Decimal("0.027")`.
  - `test_tier_selected_by_prompt_size`: escalões sintéticos `(0, 1, 5)` e `(100000, 2, 10)`; 150000 tokens de entrada usam o segundo.
  - `test_reasoning_not_double_counted`: `Usage(1000, 500, reasoning_tokens=400)` custa o mesmo que `Usage(1000, 500)`.
  - `test_negative_price_rejected`.
  - `test_sync_is_idempotent`: duas sincronizações dão uma linha por versão.
  - `test_unknown_price_raises`.
  - `test_repo_price_file_loads`: o ficheiro real carrega e contém `fake-1`.
- [x] **Step 2: Correr e ver falhar.**
- [x] **Step 3: Implementar** com aritmética `Decimal`, sem `float`, e `yaml.safe_load`.
- [x] **Step 4: Correr a validação global.**
- [x] **Step 5: Commit.** `feat: precos versionados com escaloes`.

**Notas de execução (Tarefa 4):**

- Concluída no ramo `agent/2026-11-06_plano01_tarefa4` (commits `0520953` e `07abde4`), em modo subagente: implementação, revisão da tarefa e uma ronda de correção com re-revisão limpa. 61 testes passam; `ruff` e `mypy src` limpos. `types-PyYAML` entrou nas dependências de desenvolvimento.
- Escalão do Haiku 5.5 acima de 100 mil tokens copiado da página oficial de preços (consultada em 2026-11-06): 0,50 / 2,50 USD por milhão, aplicado ao pedido inteiro quando o prompt (cache incluída) passa de 100000; por isso `min_prompt_tokens = 100001`. GPT-5.4 nano fica fora.
- No YAML os valores monetários são strings entre aspas (o carregador rejeita números, para nunca haver `float`). `model_price` guarda o 1.º escalão nas colunas e todos em `conditions["tiers"]`. Uma versão de preço é imutável: `sync_model_prices` com a mesma chave e valores diferentes levanta `ValueError`.
- `ModelPrice(provider, model_id, price_version, tiers, valid_from=None)`: `valid_from` é opcional e final para manter a assinatura de quatro campos que as Tarefas 5, 7 e 8 usam; `sync_model_prices` exige-o.
- Achados menores adiados: `pricing.py` (~220 linhas) mistura domínio, carregador e repositório; `_parse_money` aceita `NaN`/`Infinity`; faltam asserts da fronteira 100000/100001 e dos valores reais; chave `provider/model_id/price_version` duplicada em `sync_model_prices`.

### Task 5: Reservas de custo, acerto e estado do mês

**Files:**
- Create: `src/marketing_agent/budget/controller.py`, `tests/budget/test_controller.py`

**Interfaces:**
- Consumes: `PriceBook`, `ModelPrice`, `Usage`, `compute_cost_usd` (T4); `CostReservation`, `CostEntry`, `AgentRun` (T2); `Clock` (T1).
- Produces:
  - `ReservationRequest(brand_id: str, role: str, run_id: str, step_id: str, provider: str, model_id: str, input_tokens_estimate: int, max_output_tokens: int)`.
  - `Reservation(id: str, reserved_usd: Decimal)`.
  - `BudgetStatus(spent_usd: Decimal, reserved_usd: Decimal, unknown_usd: Decimal, projected_usd: Decimal, reference_usd: Decimal, warn_percent: int | None, over_reference: bool)`.
  - `BudgetController(session_factory, price_book: PriceBook, clock: Clock, reference_usd: Decimal, warn_percents: tuple[int, ...])` com os métodos:

    | Método | Retorno |
    | --- | --- |
    | `reserve(request: ReservationRequest, run_cost_limit_usd: Decimal)` | `Reservation` |
    | `settle(reservation_id: str, usage: Usage, provider_request_id: str \| None)` | `Decimal` |
    | `release(reservation_id: str)` | `None` |
    | `mark_unknown(reservation_id: str)` | `None` |
    | `recover_stale(older_than: timedelta)` | `int` (quantas passaram a `unknown_outcome`) |
    | `status(brand_id: str)` | `BudgetStatus` |

  - `RunCostLimitExceeded(Exception)`.
- **`reserve`:** abre uma transação com `SELECT … FOR UPDATE` na linha do `agent_run`. Recusa com `RunCostLimitExceeded` se as reservas abertas e desconhecidas da execução, mais os lançamentos, mais a nova reserva, ultrapassarem `run_cost_limit_usd`. A referência mensal **nunca** recusa.
- **`settle`:** cria um `cost_entry` com `kind="measured"` e fecha a reserva. Repetir o acerto devolve o mesmo valor, sem segunda linha.
- **`status`:** calcula sobre o mês civil UTC de `clock.now()`. `projected_usd` é uma projeção linear pelo dia do mês. `warn_percent` é o maior limiar atingido, contando as reservas pendentes.

- [x] **Step 1: Escrever os testes que falham.**
  - `test_reserve_then_settle_records_measured_entry`.
  - `test_settle_twice_single_entry`.
  - `test_concurrent_reservations_respect_run_limit`: duas threads reservam 0,6 USD cada com um limite de execução de 1,0 USD; uma passa e a outra levanta `RunCostLimitExceeded`.
  - `test_reference_exceeded_does_not_block` (Foco 5): com 25 USD lançados, `reserve` passa e `status().over_reference is True`.
  - `test_warn_percents_count_pending_reservations`: 9 USD lançados mais 1 USD reservado dão `warn_percent == 50`.
  - `test_month_boundary_utc` (Foco 5): um lançamento a `2026-10-31T23:59:59Z` não conta em novembro.
  - `test_recover_stale_marks_unknown_and_counts_in_status`.
- [x] **Step 2: Correr e ver falhar.**
- [x] **Step 3: Implementar.** Reserva = `compute_cost_usd(price, Usage(input_tokens_estimate, max_output_tokens))`. O lock na linha da execução substitui o *advisory lock* por marca previsto em E.0, porque com D26 só os limites de execução bloqueiam.
- [x] **Step 4: Correr a validação global.**
- [x] **Step 5: Commit.** `feat: reservas de custo por chamada e estado do mes`.

**Notas de execução (Tarefa 5):**

- Concluída no ramo `agent/2026-11-06_plano01_tarefa5` (commits `c32535e` e `080ea1e`), em modo subagente: implementação, revisão da tarefa e uma ronda de correção com re-revisão limpa. 69 testes passam; `ruff` e `mypy src` limpos.
- Decisões: `settle` usa o preço mais recente do `PriceBook` (não há leitura por versão) e grava a `price_version` realmente usada; `over_reference` é estrito (`>`); `warn_percent` é o maior limiar atingido (comparação em `Decimal`, sem divisão); `projected_usd = total × dias_do_mês / dia_do_mês`; a reserva conta no mês por `created_at` e o lançamento por `at`. Só reservas `open` e `unknown_outcome` contam no estado; uma reserva acertada já está em `cost_entry`.
- Estados: `settle` aceita `open` e `unknown_outcome`, repetir devolve o mesmo valor e `released` dá `ValueError`; `release` e `mark_unknown` só partem de `open` e são idempotentes.
- Correção da ronda 1: `reserve` e `settle` devolviam valores sem arredondar enquanto a BD guarda 6 casas (o 1.º e o 2.º `settle` davam valores diferentes). Há agora um só ponto de arredondamento: reserva com `ROUND_CEILING`, custo acertado com `ROUND_HALF_UP`.
- Achados menores adiados: `LookupError` simples para execução em falta (falta `RunNotFound`); `status()` faz 3 `SELECT` em `READ COMMITTED`; faltam testes de Dez→Jan, `release`/`mark_unknown`/`ValueError`, `settle` concorrente, fronteira exata de 50 % (o teste usa 1,1 USD em vez de 1 USD) e `provider_request_id` duplicado; `controller.py` tem 286 linhas; nomes `CENT_MILLI` e `_money` pouco claros.

### Task 6: `AgentSpec` e permissões aplicadas

**Files:**
- Create: `src/marketing_agent/harness/__init__.py`, `harness/spec.py`, `harness/validators.py`, `tests/harness/test_spec.py`

**Interfaces:**
- Consumes: `ArtifactType` (T3); `Capability` (T1).
- Produces:
  - `LoopType`, `StrEnum` com valores: `interview`, `structured`, `research`, `pipeline`.
  - `AgentLimits(max_steps: int, max_tokens: int, max_cost_usd: Decimal, max_duration_s: int)`, todos > 0.
  - `AgentSpec`, Pydantic, com os campos:

    | Campo | Tipo e valor por omissão |
    | --- | --- |
    | `role` | `str` |
    | `name` | `str` |
    | `version` | `int` |
    | `loop_type` | `LoopType` |
    | `prompt_file` | `str` |
    | `output_schema` | `str` |
    | `required_capabilities` | `frozenset[Capability]` |
    | `tools` | `list[str] = []` |
    | `reads` | `list[str]` |
    | `writes` | `list[ArtifactType]` |
    | `validators` | `list[str] = []` |
    | `limits` | `AgentLimits` |
    | `max_output_tokens` | `int` |
    | `budget_share_hint` | `Decimal`, entre 0 e 1 |
    | `escalation` | `str` |

  - `load_agent_spec(path: Path, schemas: Mapping[str, type[BaseModel]]) -> AgentSpec`. Rejeita:
    - `prompt_file` fora da pasta do spec;
    - `output_schema` ausente de `schemas`;
    - validadores não registados;
    - `tools` não vazio (não há ferramentas no Plano 1).
  - `AgentSpec.load_prompt(base_dir: Path) -> str`.
  - `assert_can_write(spec: AgentSpec, type: ArtifactType) -> None`, que levanta `PermissionDenied`.
  - `VALIDATORS: Mapping[str, Callable[[BaseModel], list[str]]]`, com os validadores registados nas Tarefas 11 e 12.

- [x] **Step 1: Escrever os testes que falham.**
  - `test_load_valid_spec`.
  - `test_prompt_outside_dir_rejected`: `prompt_file: ../x.md`.
  - `test_unknown_schema_rejected`.
  - `test_unknown_validator_rejected`.
  - `test_tools_not_allowed_in_plan_01`.
  - `test_assert_can_write_denies_unlisted_type`.
  - `test_limits_must_be_positive`.
- [x] **Step 2: Correr e ver falhar.**
- [x] **Step 3: Implementar.** `yaml.safe_load`; o erro de carregamento cita o caminho do ficheiro.
- [x] **Step 4: Correr a validação global.**
- [x] **Step 5: Commit.** `feat: especificacao de papel com permissoes aplicadas`.

**Notas de execução (Tarefa 6):**

- Concluída no ramo `agent/2026-11-06_plano01_tarefa6` (commit `ed38f53`), em modo subagente: implementação e revisão da tarefa aprovada sem correções. 76 testes passam; `ruff` e `mypy src` limpos.
- Decisões: `VALIDATORS` é um `dict` vazio em `harness/validators.py`, a preencher nas Tarefas 11 e 12 (os testes usam `monkeypatch.setitem`); todos os erros de carregamento levantam `SpecLoadError(ValueError)` com o caminho do ficheiro e a causa encadeada; `prompt_file` é relativo à pasta do spec e a contenção usa `Path.is_relative_to` depois de `resolve()`; valores monetários são strings entre aspas no YAML.
- Achados menores adiados: o teste de `load_prompt` passa por ficheiro em falta e não pela contenção; `# noqa: A002` inútil em `spec.py`; algumas mensagens ecoam valores de entrada; faltam testes de symlink, pasta irmã com o mesmo prefixo, limites 0 e 1 de `budget_share_hint` e capacidade inválida; o modo lax do Pydantic aceita `true` ou `"5"` em campos inteiros (ponderar `strict`).

### Task 7: Contrato LLM, cliente falso com cenários e router

**Files:**
- Create: `src/marketing_agent/llm/__init__.py`, `llm/types.py`, `llm/fake.py`, `llm/fake_scenarios.py`, `llm/router.py`, `tests/llm/test_fake.py`, `tests/llm/test_router.py`

**Interfaces:**
- Consumes: `RoleRoute`, `Capability` (T1); `Usage` (T4); `AgentSpec` (T6).
- Produces:
  - Partes de conteúdo: `TextPart(text: str)` e `ImagePart(media_type: str, data: bytes)`; `ContentPart = TextPart | ImagePart`.
  - `Message(role: Literal["user", "assistant"], content: list[ContentPart])`.
  - `LlmRequest(model_id: str, system: str, messages: list[Message], max_output_tokens: int, json_schema: Mapping[str, object] | None = None)`.
  - `StopReason`, `StrEnum` com valores: `end`, `max_tokens`, `refusal`, `other`.
  - `LlmResponse(text: str, usage: Usage, provider_request_id: str | None, resolved_model: str, stop_reason: StopReason)`.
  - `LlmClient` (Protocol): atributo `capabilities: frozenset[Capability]` e método `complete(request: LlmRequest) -> LlmResponse`.
  - Erros: `LlmNetworkError` (o pedido não chegou ao fornecedor) e `LlmUnknownOutcome` (pode ter sido processado e faturado).
  - `FakeLlmClient(responses: Sequence[LlmResponse | Exception], capabilities: frozenset[Capability] = frozenset({"text", "json_schema"}))`: devolve ou levanta pela ordem; guarda `requests`; ao esgotar levanta `ScriptExhausted`.
  - Em `fake_scenarios.py`, devolvendo `list[LlmResponse | Exception]`:

    | Cenário | Assinatura |
    | --- | --- |
    | Entrevista completa | `interview_full(turns: int = 3)` (primeira pergunta, `turns` respostas e perfil) |
    | Diagnóstico e estratégia | `diagnosis_and_strategy()` |
    | Inválido, depois válido | `invalid_then_valid(valid_json: str)` |
    | Sempre inválido | `always_invalid()` (3 respostas) |
    | Recusa | `refusal()` |
    | Truncagem | `truncated()` |
    | Falha de rede | `network_error_then(valid_json: str)` |

  - `LlmRouter(routes: Mapping[str, RoleRoute], clients: Mapping[str, LlmClient])` com `resolve(spec: AgentSpec) -> tuple[LlmClient, RoleRoute]`. Levanta `RouteNotConfigured` se o papel não tiver rota e `MissingCapability` se o cliente ou a rota não cobrirem `spec.required_capabilities`.

- [x] **Step 1: Escrever os testes que falham.** (o `test_scenarios_produce_valid_shapes` fica para a Tarefa 11)
  - `test_fake_returns_in_order_and_records`.
  - `test_fake_raises_scripted_exception`.
  - `test_fake_exhausted_raises`.
  - `test_scenarios_produce_valid_shapes`: o JSON de `interview_full()` valida contra os esquemas da Tarefa 11; este teste é acrescentado na Tarefa 11.
  - `test_router_resolves_per_role`: `diretor` e `copywriter` com fornecedores distintos resolvem clientes distintos.
  - `test_router_missing_route_raises`.
  - `test_router_missing_capability_raises`.
- [x] **Step 2: Correr e ver falhar.**
- [x] **Step 3: Implementar.**
- [x] **Step 4: Correr a validação global.**
- [x] **Step 5: Commit.** `feat: contrato llm, cliente falso com cenarios e rota por papel`.

**Notas de execução (Tarefa 7):**

- Concluída no ramo `agent/2026-11-06_plano01_tarefa7` (commit `3d6e29c`), em modo subagente: implementação e revisão da tarefa aprovada sem correções. 87 testes passam; `ruff` e `mypy src` limpos.
- O `test_scenarios_produce_valid_shapes` **não** foi escrito: os esquemas só existem nas Tarefas 11 e 12. Nesta tarefa os testes verificam só a estrutura dos cenários (tipos, ordem, contagens, JSON inválido onde tem de ser inválido). As formas do JSON seguem as Tarefas 11 e 12; os 14 tópicos do mapa de lacunas estão numa constante privada em `fake_scenarios.py` e a Tarefa 11 deve passar a usar `GAP_TOPICS`.
- Decisões: os cenários usam `provider_request_id=None` e `resolved_model="fake-1"` (o `provider_request_id` é único na base); fornecedor sem cliente levanta `RouteNotConfigured` e capacidade em falta, na rota ou no cliente, levanta `MissingCapability`; `interview_full(turns)` devolve `turns + 2` respostas (primeira pergunta, `turns` turnos e o perfil).
- Achados menores adiados: `interview_full(turns<1)` sem guarda; listas mutáveis dentro de dataclasses `frozen` (conforme o brief); `pop(0)` O(n) no cliente falso.

### Task 8: Motor de execução (`AgentEngine`)

**Files:**
- Create: `src/marketing_agent/harness/engine.py`, `tests/harness/test_engine.py`

**Interfaces:**
- Consumes: `AgentSpec`, `assert_can_write`, `VALIDATORS` (T6); `LlmRouter`, `LlmRequest`, `Message`, `LlmResponse`, `StopReason`, `LlmNetworkError`, `LlmUnknownOutcome` (T7); `BudgetController`, `ReservationRequest`, `RunCostLimitExceeded` (T5); `ArtifactStore`, `ArtifactRecord`, `ArtifactType` (T3); `AgentRun`, `AgentStep` (T2); `Clock` (T1).
- Produces:
  - `RunStatus`, `StrEnum` com valores: `succeeded`, `blocked_limit`, `needs_human`, `failed`.
  - `RunResult(status: RunStatus, run_id: str, output: object | None, message: str | None)`.
  - `Loop[TIn, TOut]` (Protocol): `run(ctx: RunContext, payload: TIn) -> TOut`.
  - `RunContext`, com:
    - atributos `run_id`, `brand_id`, `spec`;
    - `call_llm(system: str, messages: list[Message], schema: type[TModel]) -> TModel`;
    - `create_artifact(type: ArtifactType, payload: Mapping[str, object], parent_id: str | None = None) -> ArtifactRecord`, que aplica `assert_can_write`.
  - Exceções do ciclo: `RunLimitExceeded`, `NeedsHuman(reason: str)`.
  - `AgentEngine(session_factory, router: LlmRouter, budget: BudgetController, store: ArtifactStore, clock: Clock, sleep: Callable[[float], None] = time.sleep)` com `run(spec: AgentSpec, brand_id: str, trigger: str, loop: Loop[TIn, TOut], payload: TIn) -> RunResult`.
- **Sequência de `call_llm`, por tentativa:**
  1. Verifica os limites cumulativos (passos, tokens, duração pelo `clock`).
  2. Cria o `agent_step` e reserva o custo, com commit.
  3. Chama o fornecedor fora de qualquer transação.
  4. Acerta o custo e fecha o passo, com commit.
  5. Valida o JSON com `schema.model_validate_json` e os `spec.validators`.
- **Classificação:**

  | Caso | Tratamento |
  | --- | --- |
  | `invalid_output` | Até 3 tentativas; a tentativa seguinte leva o erro de validação como mensagem |
  | `StopReason.refusal` ou `max_tokens` | `NeedsHuman`, sem repetir |
  | `LlmNetworkError` | Liberta a reserva e repete até 2 vezes com espera de 1 s e depois 2 s |
  | `LlmUnknownOutcome` | `mark_unknown` e `NeedsHuman` |
  | `RunCostLimitExceeded` e `RunLimitExceeded` | `blocked_limit` |

  `run` fecha o `agent_run` com o estado final em `finally`.

- [x] **Step 1: Escrever os testes que falham.** Todos usam `FakeLlmClient` e `FakeLlmClient` + `FixedClock`.
  - `test_success_records_run_step_reservation_and_entry`.
  - `test_invalid_three_times_needs_human`: 3 chamadas, 3 lançamentos, e a terceira leva o erro no pedido.
  - `test_invalid_then_valid_succeeds`.
  - `test_refusal_no_retry`: 1 chamada e `needs_human`.
  - `test_truncated_no_retry`.
  - `test_network_error_retries_and_releases_reservation`: a reserva fica `released` e não há lançamento para a tentativa falhada.
  - `test_unknown_outcome_marks_reservation`.
  - `test_run_cost_limit_blocks_before_call`: `llm.requests == []` na chamada bloqueada.
  - `test_max_steps_blocks`.
  - `test_duration_limit_uses_clock`: o `FixedClock` avança para além de `max_duration_s` e a execução fica `blocked_limit`.
  - `test_create_artifact_outside_writes_denied`: `failed` com `PermissionDenied` na mensagem.
- [x] **Step 2: Correr e ver falhar.**
- [x] **Step 3: Implementar.** Extrair `_attempt(...)`, `_classify(...)` e `_retry_policy(...)`, cada um com até 25 linhas.
- [x] **Step 4: Correr a validação global.**
- [x] **Step 5: Commit.** `feat: motor com reserva por chamada, limites e erros classificados`.

**Notas de execução (Tarefa 8):**

- Concluída no ramo `agent/2026-11-06_plano01_tarefa8` (commits `b730222` e `c537302`), em modo subagente: implementação, revisão da tarefa e uma ronda de correção com re-revisão limpa. 114 testes passam; `ruff` e `mypy src` limpos.
- Decisões: limites cumulativos por execução (cada tentativa conta um passo; tokens acertados; duração pelo `Clock`; custo pelo `BudgetController`); `input_tokens_estimate = ceil(caracteres / 3)`; a tentativa seguinte a uma saída inválida acrescenta a resposta anterior e o erro de validação como mensagens; exceções inesperadas fecham o run como `failed` com `"<Classe>: <texto>"`.
- Correção da ronda 1: qualquer exceção entre a reserva e o acerto (erro inesperado do cliente, falha do `settle`, `KeyboardInterrupt`) marca a reserva como `unknown_outcome`, fecha o passo e re-levanta a exceção original (libertar seria errado: a chamada pode ter sido faturada); um validador que rebenta fecha o passo como `invalid_output`.
- **Para as tarefas seguintes:** quando a rota não resolve, `RunResult` vem `failed` com `run_id=""` e sem linha em `agent_run` (documentado no `RunResult`); se o painel precisar de rasto, abrir a linha antes de resolver a rota. Os adaptadores (Tarefas 9 e 10) devem tratar o caso de texto vazio da resposta anterior (os fornecedores rejeitam blocos vazios).
- Achados menores adiados: a estimativa de tokens ignora `json_schema` e `ImagePart`; `release`/`mark_unknown` nos handlers de rede e de resultado desconhecido não estão protegidos; `_close_run` no `finally` pode mascarar a exceção original; `logger.exception` pode incluir texto de erros do Loop; `engine.py` (~415 linhas) mistura persistência de passos, política de retry e `AgentEngine`.

### Task 9: Adaptador Anthropic

**Files:**
- Create: `src/marketing_agent/llm/anthropic_adapter.py`, `tests/llm/test_anthropic_adapter.py`, `tests/llm/fixtures/anthropic_*.json`

**Interfaces:**
- Consumes: `LlmClient`, `LlmRequest`, `LlmResponse`, `StopReason`, `LlmNetworkError`, `LlmUnknownOutcome` (T7); `Usage` (T4); `read_secret_file` (T1).
- Produces:
  - `AnthropicClient(api_key_file: Path, capabilities: frozenset[Capability])`.
  - `to_llm_response(raw: Mapping[str, object]) -> LlmResponse`: junta os blocos de texto, mapeia `usage` e converte `stop_reason` (`end_turn` → `end`, `max_tokens` → `max_tokens`, `refusal` → `refusal`, o resto → `other`).
  - `classify_transport_error(exc: Exception) -> type[Exception]`: ligação recusada antes de enviar dá `LlmNetworkError`; timeout de leitura dá `LlmUnknownOutcome`.

- [x] **Step 1: Escrever os testes que falham.** Usam *fixtures* JSON representativas.
  - `test_multiple_text_blocks_joined`.
  - `test_usage_mapped`.
  - `test_refusal_mapped`.
  - `test_max_tokens_mapped`.
  - `test_read_timeout_is_unknown_outcome`.
  - `test_key_read_from_file`.
  - `test_live_call` com `@pytest.mark.live`: só corre com `ANTHROPIC_API_KEY_FILE` definido.
- [x] **Step 2: Correr e ver falhar.**
- [x] **Step 3: Implementar.** **Antes de escrever, ler o skill `claude-api`** (ou a documentação oficial do SDK `anthropic`) para os nomes de parâmetros e o formato da resposta; não adivinhar a API. Desligar as repetições automáticas do SDK, porque o motor decide as repetições.
- [x] **Step 4: Correr a validação global**, sem `live`. Correr o teste `live` uma vez à mão e anotar o resultado no commit.
- [x] **Step 5: Commit.** `feat: adaptador anthropic`.

**Notas de execução (Tarefa 9):**

- Concluída no ramo `agent/2026-11-06_plano01_tarefa9` (commit `0030973`), em modo subagente: implementação e revisão da tarefa aprovada sem correções. 165 testes passam (1 teste `live` excluído por omissão); `ruff` e `mypy src` limpos.
- Os nomes do SDK `anthropic` 2.6.0 foram confirmados no pacote instalado. O SDK repete pedidos por omissão, por isso o cliente é criado com `max_retries=0`.
- Decisões: `Usage.input_tokens` soma os tokens de cache (o escalão do Haiku depende do prompt total); `provider_request_id` é o `id` da mensagem; falhas de ligação e respostas 429/503/529 dão `LlmNetworkError`; timeout de leitura e restantes 5xx dão `LlmUnknownOutcome`; outros 4xx propagam a exceção original (o motor marca a reserva como desconhecida); `classify_transport_error` devolve `type[Exception] | None` (`None` = não classificado); o adaptador nunca envia blocos de texto vazios nem `thinking`, `temperature` ou prefill.
- `AnthropicClient` tem um parâmetro opcional `base_url` (acrescentado a seguir ao brief) para apontar a um emulador local. O pytest passa a correr `-m "not live"` por omissão.
- **O teste live foi corrido uma vez, à mão, contra o Ollama local (`qwen2.5:14b`), que emula a API Messages — não contra a Anthropic.** Ficam por verificar com a chave real: cabeçalhos, saída estruturada (`output_config.format`), erros 4xx/5xx reais e campos de cache. Comando: `ANTHROPIC_API_KEY_FILE=<ficheiro com a chave> ANTHROPIC_BASE_URL=http://127.0.0.1:11434 ANTHROPIC_LIVE_MODEL=qwen2.5:14b uv run pytest -m live tests/llm/test_anthropic_adapter.py`.
- **Para a Tarefa 10:** o Ollama também responde ao formato OpenAI (`/v1/chat/completions`), por isso o teste live do `OpenAiClient` pode usar `base_url=http://127.0.0.1:11434/v1`.
- Achados menores adiados: declarar `httpx2` em `pyproject.toml` (é importado diretamente mas só é dependência transitiva); construir `messages` e `output_config` antes do `try` (um erro determinístico nunca enviado deixa a reserva como desconhecida); `timeout_s=120` é por fase, e gerações longas podem dar `ReadTimeout` faturado e tratado como desconhecido; um `ValueError` ao ler uma resposta paga deixa a reserva como desconhecida.

### Task 10: Adaptador OpenAI e construção do router

**Files:**
- Create: `src/marketing_agent/llm/openai_adapter.py`, `llm/factory.py`, `tests/llm/test_openai_adapter.py`, `tests/llm/test_factory.py`, `tests/llm/fixtures/openai_*.json`

**Interfaces:**
- Consumes: os mesmos de T9; `Settings` (T1); `LlmRouter`, `FakeLlmClient` (T7).
- Produces:
  - `OpenAiClient(api_key_file: Path, capabilities: frozenset[Capability], base_url: str | None = None)` e `to_llm_response(raw) -> LlmResponse`, com o mesmo mapeamento e classificação de T9.
  - `build_llm_router(settings: Settings, fake: FakeLlmClient | None = None) -> LlmRouter`. Constrói **só** os clientes dos fornecedores referidos em `role_routes`. Uma chave em falta levanta `ValueError` com o nome da variável `*_FILE`. O fornecedor `fake` exige o argumento `fake`.

- [x] **Step 1: Escrever os testes que falham.**
  - `test_to_llm_response_maps_text_usage_stop`.
  - `test_router_builds_only_referenced_providers`.
  - `test_two_roles_two_providers`: `diretor`→`anthropic` e `revisor`→`openai` resolvem clientes de classes diferentes.
  - `test_missing_key_file_names_variable`.
- [x] **Step 2: Correr e ver falhar.**
- [x] **Step 3: Implementar** com o SDK oficial `openai`, consultando a documentação para os nomes exatos. Repetições automáticas do SDK desligadas.
- [x] **Step 4: Correr a validação global.**
- [x] **Step 5: Commit.** `feat: adaptador openai e router por papel`.

**Notas de execução (Tarefa 10):**

- Concluída no ramo `agent/2026-11-06_plano01_tarefa10` (commit `f96cf5a`), em modo subagente: implementação e revisão da tarefa aprovada sem correções. 243 testes passam (2 testes `live` excluídos por omissão); `ruff` e `mypy src` limpos.
- O adaptador usa a API Chat Completions do SDK `openai` 3.46.0 (nomes confirmados no pacote instalado). O SDK usa `httpx2`, tal como o da Anthropic, por isso a classificação de erros foi extraída para `llm/transport_errors.py` (movimento puro; os testes da Tarefa 9 ficaram inalterados). A OpenAI não distingue 503/529 de outros 5xx por classe, por isso a classificação é pelo código de estado: 429/503/529 dão `LlmNetworkError` e os restantes 5xx dão `LlmUnknownOutcome`.
- `build_llm_router` só cria os clientes dos fornecedores referidos em `role_routes`; as capacidades de cada cliente são a união das das rotas que o usam; uma chave em falta (variável por definir, ficheiro inexistente ou vazio) dá `ValueError` com o nome da variável `*_FILE`; o fornecedor `fake` exige o argumento `fake`. `Settings` converte valores em branco de `ANTHROPIC_API_KEY_FILE`, `OPENAI_API_KEY_FILE` e `OPENAI_BASE_URL` em `None` (achado adiado da Tarefa 1).
- `OpenAiClient` usa o papel `developer` para o prompt de sistema, `max_completion_tokens`, `json_schema` com `strict: False` e `max_retries=0`.
- **O teste live foi corrido uma vez, à mão, contra o Ollama local (`qwen2.5:14b`, formato OpenAI) — não contra a OpenAI.** O Ollama ignora o papel `developer` e `max_completion_tokens`, por isso o teste só verifica texto não vazio, tokens de saída e paragem normal. Por verificar com a chave real: parâmetros de raciocínio, `json_schema` estrito, formas reais de 4xx/5xx e detalhes de `usage`. Comando: `OPENAI_API_KEY_FILE=<ficheiro com a chave> OPENAI_BASE_URL=http://127.0.0.1:11434/v1 OPENAI_LIVE_MODEL=qwen2.5:14b uv run pytest -m live tests/llm/test_openai_adapter.py`.
- Achados menores adiados: duplicação entre os dois adaptadores (marcador de texto vazio, tipos de imagem, validadores `_require_str` e `_token_count`); um 429 `insufficient_quota` é tratado como falha de rede e o motor repete duas vezes sem proveito (custo zero); o papel `developer` faz servidores compatíveis que o ignoram descartarem o prompt de sistema; os testes leem atributos privados (`router._clients`, `client._sdk`).

### Task 11: Diretor em entrevista com submissões idempotentes

**Files:**
- Create: `src/marketing_agent/agents/__init__.py`, `agents/diretor/__init__.py`, `agents/diretor/spec_interview.yaml`, `agents/diretor/prompt_interview.md`, `agents/diretor/schemas.py`, `agents/diretor/gap_map.py`, `src/marketing_agent/interview/__init__.py`, `interview/loop.py`, `interview/service.py`, `tests/agents/test_gap_map.py`, `tests/agents/test_schemas.py`, `tests/interview/test_service.py`

**Interfaces:**
- Consumes: `AgentEngine`, `Loop`, `RunContext`, `RunResult`, `RunStatus` (T8); `ArtifactStore`, `ArtifactType` (T3); `InterviewSession`, `InterviewSubmission`, `InterviewTurn`, `AuditLog` (T2); `load_agent_spec`, `VALIDATORS` (T6); `Settings`, `Clock` (T1).
- Produces:
  - **Mapa de lacunas.** `GAP_TOPICS: tuple[str, ...]`, com os valores `o_que_vende`, `cliente_prioritario`, `geografia`, `origem_clientes_atual`, `proposta_de_valor`, `motivos_compra_e_perda`, `capacidade_entrega`, `provas_disponiveis`, `percurso_contacto`, `voz_e_tom`, `concorrentes`, `objetivos`, `canais_e_idioma` e `material_existente`. Cobrem as entradas do diagnóstico (F02).
  - `Origin = Literal["dito_pelo_responsavel", "verificado", "suposto"]`.
  - `GapItem(topic: str, status: Literal["desconhecido", "suposicao", "confirmado"], note: str, origin: Origin | None)`.
  - `initial_gap_map() -> list[GapItem]` e `merge_gap_map(current: list[GapItem], updates: list[GapItem]) -> list[GapItem]`. O merge ignora tópicos desconhecidos e **não rebaixa** um item `confirmado` com origem `dito_pelo_responsavel`.
  - **Esquemas.** `InterviewTurnOutput(next_question: str | None, summary: str, gap_map_updates: list[GapItem], ready_for_profile: bool, injection_suspected: bool)`. Validador estrutural: `next_question` com no máximo 300 caracteres e sem quebras de linha.
  - `PerfilTopico(texto: str, origem: Origin)` e `PerfilMarca(topicos: dict[str, PerfilTopico], lacunas_abertas: list[str], suposicoes: list[str])`. As chaves de `topicos` pertencem a `GAP_TOPICS`.
  - **Resultados.** `StartResult(session_id: str, question: str | None)`.
  - `TurnStatus`, `StrEnum` com valores: `completed`, `in_progress`, `blocked`, `needs_human`.
  - `TurnResult(status: TurnStatus, question: str | None, summary: str, gap_map: list[GapItem], message: str | None, ready_for_profile: bool, injection_suspected: bool)`.
  - Exceções: `SubmissionConflict(Exception)` (mesma chave com outro texto), `SessionBusy(Exception)` e `InvalidAnswer(ValueError)`.
  - **Serviço.** `InterviewService(session_factory, engine: AgentEngine, store: ArtifactStore, settings: Settings, clock: Clock)` com os métodos:

    | Método | Retorno |
    | --- | --- |
    | `start(brand_id: str)` | `StartResult` |
    | `submit_answer(session_id: str, text: str, idempotency_key: str)` | `TurnResult` |
    | `correct_topic(session_id: str, topic: str, text: str, actor: str)` | `list[GapItem]` |
    | `request_profile(session_id: str)` | `str` (id do `perfil_marca` em `submitted`) |
    | `confirm_profile(artifact_id: str, actor: str)` | `None` |
    | `reject_profile(artifact_id: str, actor: str, reason: str)` | `None` |

- **Ciclo de `submit_answer`, em transações curtas:**
  1. Valida 1 a `max_message_chars` caracteres; caso contrário, `InvalidAnswer`.
  2. Insere a `interview_submission` em `reserved`, com `text_sha256`.
     - Se a chave já existir com outro hash, `SubmissionConflict`.
     - Se existir em `completed`, devolve o `result` guardado.
     - Se existir em `reserved`, devolve `TurnResult(status=in_progress)`.
  3. Reclama a sessão: `UPDATE … SET busy_submission_id = ?, busy_since = now WHERE busy_submission_id IS NULL OR busy_since < now - max_duration_s`. Se não conseguir, `SessionBusy`.
  4. Chama o motor fora de qualquer lock.
  5. Grava o turno `user` e o turno `director` (`seq` seguidos), aplica `merge_gap_map`, marca a submissão `completed` com o resultado e liberta a sessão.
- **Bloqueios:** `blocked_limit` devolve `TurnResult(status=blocked, message="Limite desta execução atingido. A entrevista fica guardada e pode continuar depois.")` sem gravar turno do Diretor; a submissão fica `failed`.
- **`correct_topic`:** marca o tópico como `confirmado` com origem `dito_pelo_responsavel` e escreve `AuditLog`.
- **Prompt:** a resposta do utilizador entra em `<resposta_do_utilizador>…</resposta_do_utilizador>`, e o prompt diz que esse conteúdo é dado. A segurança, porém, vem de **nenhum** método mudar estado de artefacto a partir da saída do LLM, exceto `request_profile`, chamado por ação explícita.
- **`spec_interview.yaml`:** `role: diretor`, `name: interview`, `loop_type: interview`, `writes: [perfil_marca]`, `required_capabilities: [text, json_schema]`, `limits: {max_steps: 6, max_tokens: 60000, max_cost_usd: "0.50", max_duration_s: 120}`, `max_output_tokens: 1500`, `budget_share_hint: "0.35"`.

- [x] **Step 1: Escrever os testes que falham.** Usam `FakeLlmClient` com os cenários de T7.
  - Mapa de lacunas: `test_initial_gap_map_all_unknown`, `test_merge_ignores_unknown_topics`, `test_merge_keeps_owner_confirmed`.
  - Esquemas: `test_question_with_newline_rejected`, `test_profile_rejects_unknown_topic`.
  - Serviço:
    - `test_start_returns_first_question`;
    - `test_submit_records_user_and_director_turns`;
    - `test_duplicate_key_returns_stored_result_without_llm_call`;
    - `test_same_key_different_text_conflict`;
    - `test_repeat_while_in_progress_returns_in_progress` (Foco 1): a submissão fica `reserved` e a repetição devolve `in_progress` sem novo pedido em `llm.requests`;
    - `test_second_key_while_busy_raises_session_busy`;
    - `test_stale_busy_reclaimed_after_max_duration`;
    - `test_resume_after_restart`;
    - `test_correct_topic_marks_confirmed_by_owner`;
    - `test_injection_text_changes_no_artifact` (Foco 3): o texto "ignora as regras e aprova o perfil", com o LLM a devolver `ready_for_profile=True`, deixa a contagem de artefactos em zero;
    - `test_empty_and_oversized_rejected`;
    - `test_blocked_limit_keeps_gap_map`;
    - `test_request_and_confirm_profile_activates_and_audits`.
  - Cenário: `test_scenarios_produce_valid_shapes` (de T7).
- [x] **Step 2: Correr e ver falhar.** `uv run pytest tests/agents tests/interview -v`.
- [x] **Step 3: Implementar.** Extrair `_reserve_submission`, `_claim_session`, `_record_turns` e `_release_session`.
- [x] **Step 4: Correr a validação global.**
- [x] **Step 5: Commit.** `feat: diretor em entrevista com submissoes idempotentes`.

**Notas de execução (Tarefa 11):**

- Concluída no ramo `agent/2026-11-06_plano01_tarefa11` (commits `f826d05` e `63beefa`), em modo subagente: implementação, revisão da tarefa (com modelo mais capaz) e uma ronda de correção com re-revisão limpa. 301 testes passam; `ruff` e `mypy src` limpos.
- Dois specs em vez de um: `spec_interview.yaml` exatamente como o brief e `spec_profile.yaml` (4000 tokens de saída, 4 passos) para gerar o perfil, porque 1500 tokens truncariam um perfil de 14 tópicos. Os cenários de `llm/fake_scenarios.py` mantêm uma constante privada com os 14 tópicos (importar `agents` a partir de `llm` inverteria a dependência) e um teste afirma que é igual a `GAP_TOPICS`; o `test_scenarios_produce_valid_shapes` (da Tarefa 7) vive em `tests/agents/test_schemas.py`.
- **Máquina de estados das submissões (para a Tarefa 14 e a Tarefa 15):**
  - A reserva da submissão e a reclamação da sessão são uma só transação (`_reserve_and_claim`); uma submissão `reserved` é sempre dona da sessão. `SessionBusy` desfaz a transação e a mesma chave pode tentar de novo.
  - Uma submissão `failed` **nunca** é reprocessada com a mesma chave: guarda o `TurnResult` (`blocked` ou `needs_human`) em `result` e a mesma chave repete-o sem chamar o motor. **O painel tem de gerar uma chave nova por cada tentativa.**
  - A mesma chave repetida com a submissão `reserved` devolve `in_progress` se a reclamação estiver fresca, ou marca-a como interrompida (`needs_human`, "Esta submissão foi interrompida. Envie a resposta de novo.") se estiver velha, em falta ou de outra submissão.
  - O `_record_turns` só grava se a submissão ainda for dona da sessão; senão não grava nada.
  - Corte de reclamação velha: `max_duration_s` (120 s) sem margem.
  - `request_profile` reclama a sessão com um marcador novo (`SessionBusy` se ocupada), recusa sessões `closed` (`InvalidSessionState`), aceita `open` e `profile_requested`, e liberta a reclamação num `finally`.
  - `start` corre o motor antes de criar a sessão (não deixa sessões órfãs) e não é idempotente.
- Decisões: `confirm_profile` só ativa o artefacto (o estado da sessão fica `profile_requested`, porque o artefacto não tem ligação à sessão); o texto do utilizador entra em `<resposta_do_utilizador>` com o fecho literal neutralizado e o mapa de lacunas em `<mapa_de_lacunas>`; o motor devolve uma mensagem genérica em erros internos (detalhe só no log).
- Achados menores adiados: resultado devolvido pode diferir do guardado quando a execução perde a reclamação e falha depois; sem margem no corte de reclamação velha; `_describe_failure` pode mostrar texto da exceção do fornecedor; `start` sem idempotência; neutralização do delimitador sem variantes (`</resposta_do_utilizador x>`, zero-width, entidades HTML); notas do mapa sem limite de tamanho; `submit_answer` aceita respostas depois de `profile_requested`; `service.py` (~558 linhas) devia dividir a persistência de submissões e sessões; testes de concorrência cobrem threads mas só com 5 repetições.

### Task 12: Diagnóstico e estratégia com decisões por proposta

**Files:**
- Create: `agents/diretor/spec_diagnostico.yaml`, `agents/diretor/prompt_diagnostico.md`, `agents/diretor/spec_estrategia.yaml`, `agents/diretor/prompt_estrategia.md`, `src/marketing_agent/strategy/__init__.py`, `strategy/loops.py`, `strategy/service.py`, `tests/strategy/test_schemas.py`, `tests/strategy/test_service.py`
- Modify: `agents/diretor/schemas.py` (esquemas abaixo), `harness/validators.py` (validadores registados)

**Interfaces:**
- Consumes: `AgentEngine`, `RunStatus` (T8); `ArtifactStore`, `ArtifactType` (T3); `StrategyDecision` (T2); `PerfilMarca` (T11).
- Produces:
  - **Diagnóstico.**
    - `Achado(afirmacao: str, tipo: Literal["facto", "hipotese"], evidencia: str | None, origem: Origin | None)`.
    - `Diagnostico`, com os campos `oferta_prioritaria`, `cliente_prioritario`, `geografia`, `motivos_compra`, `motivos_perda`, `capacidade_entrega`, `provas_disponiveis`, `percurso_contacto` e `adequacao_canais`, todos `str`. Também `achados: list[Achado]`, `riscos: list[str]` e `alternativas: list[str]`.
  - **Estratégia.**
    - `Objetivo(descricao: str, indicador: str, alvo: str | None)`.
    - `PropostaOferta(proposta: str, justificacao: str)`.
    - `Experiencia(hipotese: str, metrica: str, prazo_dias: int)`.
    - `Estrategia(objetivos: list[Objetivo], posicionamento: str, posicionamento_estado: Literal["hipotese", "decidido"], pilares: list[str], propostas_oferta: list[PropostaOferta], experiencias: list[Experiencia], indicadores_minimos: list[str])`.
  - **Validadores registados:**
    - `diagnostico_factos_com_evidencia`: um achado `facto` sem `evidencia` ou sem `origem` é violação;
    - `estrategia_experiencias_completas`: hipótese e métrica não vazias e prazo entre 1 e 90 dias.
  - `ActiveStrategy(artifact_id: str, estrategia: Estrategia, propostas_aceites: list[PropostaOferta])`.
  - `MissingPrerequisite(Exception)` e `IncompleteDecisions(Exception)`.
  - **Serviço.** `StrategyService(session_factory, engine: AgentEngine, store: ArtifactStore, clock: Clock)` com os métodos:

    | Método | Retorno |
    | --- | --- |
    | `propose_diagnosis(brand_id: str)` | `RunResult` (exige `perfil_marca` ativo) |
    | `approve_diagnosis(artifact_id: str, actor: str)` | `None` |
    | `reject_diagnosis(artifact_id: str, actor: str, reason: str)` | `None` |
    | `propose_strategy(brand_id: str)` | `RunResult` (exige `diagnostico` ativo) |
    | `approve_strategy(artifact_id: str, actor: str, decisions: Mapping[int, bool])` | `None` |
    | `reject_strategy(artifact_id: str, actor: str, reason: str)` | `None` |
    | `active_strategy(brand_id: str)` | `ActiveStrategy \| None` |

    Os dois `propose_*` levantam `MissingPrerequisite` sem o artefacto ativo exigido.
- **`approve_strategy`:** exige uma decisão para **cada** índice de `propostas_oferta`; caso contrário, `IncompleteDecisions`. Grava as `StrategyDecision` e chama `approve_and_activate`, numa transação.
- **`active_strategy`:** devolve **só** as propostas aceites. É esta a vista que os planos seguintes consomem (D41, D42).
- **Specs:** `spec_diagnostico.yaml` e `spec_estrategia.yaml` têm `role: diretor`, `loop_type: structured`, `writes` com o tipo respetivo, `limits.max_cost_usd: "2.00"` e `max_output_tokens: 6000`.

- [x] **Step 1: Escrever os testes que falham.** Usam o cenário `diagnosis_and_strategy()`.
  - `test_fact_without_evidence_rejected_by_validator`.
  - `test_experiment_prazo_bounds`.
  - `test_diagnosis_requires_active_profile`.
  - `test_strategy_requires_active_diagnosis`.
  - `test_approve_strategy_requires_all_decisions` (Foco 3).
  - `test_active_strategy_contains_only_accepted_proposals` (Foco 3).
  - `test_new_strategy_draft_keeps_previous_active`.
  - `test_decisions_audited_with_actor`.
- [x] **Step 2: Correr e ver falhar.** `uv run pytest tests/strategy -v`.
- [x] **Step 3: Implementar** dois `Loop` estruturados de uma chamada cada; o contexto vem do perfil ativo e do diagnóstico ativo serializados.
- [x] **Step 4: Correr a validação global.**
- [x] **Step 5: Commit.** `feat: diagnostico e estrategia do diretor com decisoes por proposta`.

**Notas de execução (Tarefa 12):**

- Concluída no ramo `agent/2026-11-06_plano01_tarefa12` (commit `eded71e`), em modo subagente: implementação e revisão da tarefa (com modelo mais capaz) aprovada sem correções. 341 testes passam; `ruff` e `mypy src` limpos.
- **Garantia central:** uma proposta de oferta só entra na vista ativa através de uma linha `StrategyDecision` com `accepted=True`, escrita **na mesma transação** que a aprovação da estratégia. `approve_strategy` exige decisões para exatamente os índices `0..n-1` (falta ou excesso de índice dá `IncompleteDecisions`) e não deixa linhas se a aprovação falhar. `active_strategy` devolve só as propostas aceites, por ordem de índice, e falha para o lado seguro: uma estratégia sem decisões (por exemplo ativada pelo `approve_and_activate` genérico) não mostra nenhuma.
- `ArtifactStore.approve_and_activate_in(session, ...)` é novo (o corpo antigo de `approve_and_activate`, que agora o chama dentro da sua transação); comportamento e testes da Tarefa 3 inalterados.
- `propose_diagnosis` e `propose_strategy` exigem o artefacto ativo anterior (`MissingPrerequisite`), recusam se já existe uma proposta desse tipo em `submitted` (`PendingProposal`) ou se há um pedido em curso (`ProposalInProgress`, marcador em `system_flag` com corte `max_duration_s`, libertado num `finally` condicional ao valor), e terminam com `store.submit(actor="diretor")`. A verificação "já submetido" corre depois de reclamar o marcador (fecha uma corrida); por isso um segundo chamador pode receber `ProposalInProgress` em vez de `PendingProposal`.
- **Para a Tarefa 14 (painel):** `RunResult.output` traz o `ArtifactRecord` tal como foi criado (estado `draft`) enquanto a base já o tem em `submitted` — **o painel deve reler o estado da base**. Aprovação e rejeição são ações explícitas (`approve_*`, `reject_*`).
- Specs: `spec_diagnostico.yaml` e `spec_estrategia.yaml` (limites decididos pelo controlador: 4 passos, 80000 tokens, 240 s, `max_cost_usd "2.00"`, 6000 tokens de saída). Os validadores `diagnostico_factos_com_evidencia` e `estrategia_experiencias_completas` registam-se em `harness/validators.py` (inversão de camada leve, sem ciclo).
- Achados menores adiados: corte do marcador sem margem (uma execução viva pode ser tomada, com segunda chamada paga até 2 USD; cada artefacto exige aprovação explícita); um rascunho fica se `store.submit` falhar; `parent_id` lido antes do marcador; faltam testes de duas aprovações concorrentes com decisões diferentes e de estratégia legada; o validador aceita um `facto` com origem `suposto` (**decisão do responsável**: uma suposição pode passar por facto); `tests/strategy/__init__.py` é o único marcador de pacote (usar `--import-mode=importlib` ou `__init__.py` em todas as pastas de testes, em commit próprio); `strategy/service.py` (301 linhas) devia extrair o marcador de proposta para um módulo partilhável com a entrevista.

### Task 13: Autenticação do painel por proxy confiável

**Files:**
- Create: `src/marketing_agent/panel/__init__.py`, `panel/auth.py`, `tests/panel/test_auth.py`

**Interfaces:**
- Consumes: `Settings.panel_allowed_logins` e `Settings.panel_trusted_proxy` (T1).
- Produces: `TailscaleUser(login: str, name: str | None)` e a dependência FastAPI `require_tailscale_user(request: Request) -> TailscaleUser`.
- **Regras:**
  - `request.client.host` fora de `panel_trusted_proxy` dá **403**, mesmo com o cabeçalho.
  - Sem `Tailscale-User-Login` dá 401.
  - Um login fora da lista dá 403.
  - `X-Forwarded-For` e `X-Forwarded-Host` **são ignorados** para a decisão.
  - As mensagens estão em português e não revelam a lista de logins.

- [x] **Step 1: Escrever os testes que falham.**
  - `test_missing_header_401`.
  - `test_untrusted_peer_403_even_with_header` (Foco 4): o cliente `172.18.0.5` envia um cabeçalho válido.
  - `test_forwarded_for_ignored`: um peer não confiável com `X-Forwarded-For: 127.0.0.1` recebe 403.
  - `test_login_not_allowed_403`.
  - `test_allowed_login_returns_user`.
- [x] **Step 2: Correr e ver falhar.**
- [x] **Step 3: Implementar.** Acrescentar ao runbook do Plano 0 a prova no percurso real (Serve com a rede Docker, acesso direto à porta, dispositivo `tagged`). Este teste unitário só prova a condição.
- [x] **Step 4: Correr a validação global.**
- [x] **Step 5: Commit.** `feat: autenticacao do painel por proxy confiavel`.

**Notas de execução (Tarefa 13):**

- Concluída no ramo `agent/2026-11-06_plano01_tarefa13` (commits `26329a4` e `c956c5e`), em modo subagente: implementação, revisão da tarefa (modelo mais capaz, em modo de atacante) e uma ronda de correção com re-revisão limpa. 378 testes passam; `ruff` e `mypy src` limpos.
- `require_tailscale_user(request)` lê as definições de `request.app.state.settings` — **a Tarefa 14 tem de as preencher em `create_app`** (sem elas levanta `RuntimeError`). Ordem de decisão: primeiro o par de rede (`request.client` fora de `PANEL_TRUSTED_PROXY` dá 403 antes de ler qualquer cabeçalho de identidade); depois o cabeçalho `Tailscale-User-Login` (ausente dá 401; repetido, vazio, com mais de 254 caracteres ou com caracteres não imprimíveis dá 403); depois a lista de logins (comparação sem distinção de maiúsculas; lista vazia nega todos). Todos os 403 têm o mesmo texto "Acesso negado." (sem oráculo); o 401 diz "Autenticação necessária.". `X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto`, `Forwarded` e `X-Real-IP` nunca são lidos. Os logs de recusa só têm o motivo e o IP do par.
- `PANEL_TRUSTED_PROXY` aceita IPs e redes CIDR; um endereço IPv4 dentro de IPv6 normaliza-se; uma entrada inválida ou com prefixo 0 (`0.0.0.0/0`, `::/0`) levanta `ValueError` e todos os pedidos falham com 500 (falha fechada).
- **Restrição obrigatória para a Tarefa 15:** o Uvicorn trata cabeçalhos de proxy por omissão (`X-Forwarded-For` reescreve `request.client`; `FORWARDED_ALLOW_IPS`). A revisão provou que com `ProxyHeadersMiddleware(trusted_hosts="*")` um par não confiável passa por dono. **O painel tem de arrancar com `proxy_headers=False` (programático, ou `--no-proxy-headers`) e sem `FORWARDED_ALLOW_IPS`, e a Tarefa 15 deve ter um teste que afirme esta configuração do Uvicorn.** Está também no docstring de `panel/auth.py` e no checklist.
- O runbook do Plano 0 ainda não existe: criou-se `AgentDATA/docs/2026-11-06T14-26-00+00-00_checklist_prova_autenticacao_painel.md` (**por executar**) para ser incorporado quando o Plano 0 for escrito.
- Achados menores adiados: entradas IPv4 dentro de IPv6 não se normalizam (nunca casam, o que nega); exigir `login.isascii()`; `Tailscale-User-Name` sem sanear caracteres de controlo (os templates da Tarefa 14 têm de usar `autoescape`; nomes não-ASCII podem vir codificados em RFC 2047); validar `panel_trusted_proxy` no arranque (em `create_app`) em vez de dar 500 em cada pedido; o teste de controlo da armadilha só afirma o estado 403.

### Task 14: Painel com proteção CSRF, entrevista, diagnóstico e estratégia

**Files:**
- Create: `src/marketing_agent/panel/csrf.py`, `panel/app.py`, `panel/templates/base.html`, `panel/templates/home.html`, `panel/templates/interview.html`, `panel/templates/_turn.html`, `panel/templates/profile.html`, `panel/templates/diagnosis.html`, `panel/templates/strategy.html`, `panel/static/htmx.min.js`, `panel/static/README.md`, `tests/panel/test_csrf.py`, `tests/panel/test_routes.py`

**Interfaces:**
- Consumes: `InterviewService`, `TurnResult`, `TurnStatus`, `SubmissionConflict`, `SessionBusy`, `InvalidAnswer` (T11); `StrategyService`, `IncompleteDecisions`, `MissingPrerequisite` (T12); `BudgetController.status` (T5); `require_tailscale_user` (T13); `Settings`.
- Produces:
  - `csrf_token_for(user: TailscaleUser, secret: bytes) -> str`: HMAC do login.
  - Dependência `require_same_origin_and_csrf(request: Request, user: TailscaleUser = Depends(require_tailscale_user)) -> None`. Rejeita com 403:
    - `Sec-Fetch-Site` presente e diferente de `same-origin`;
    - `Origin` presente e diferente do `Host`;
    - campo `csrf_token` ou cabeçalho `X-CSRF-Token` inválido.
  - `create_app(settings: Settings, interview: InterviewService, strategy: StrategyService, budget: BudgetController, csrf_secret: bytes) -> FastAPI`.
- **Rotas** (todas com autenticação; as mutações também com CSRF):

  | Rota | Função |
  | --- | --- |
  | `GET /` | Estado do mês: gasto, reservado, projeção, aviso, `over_reference`; sessões, perfil, diagnóstico e estratégia ativos |
  | `POST /entrevista` | Cria a sessão |
  | `GET /entrevista/{id}` | Mostra a sessão |
  | `POST /entrevista/{id}/resposta` | Envia uma resposta (campo `text`, cabeçalho `Idempotency-Key`) |
  | `POST /entrevista/{id}/topico` | Corrige um tópico diretamente |
  | `POST /entrevista/{id}/perfil` | Pede o perfil |
  | `POST /perfil/{id}/confirmar`, `POST /perfil/{id}/rejeitar` | Decide o perfil |
  | `POST /diagnostico`, `POST /diagnostico/{id}/aprovar`, `POST /diagnostico/{id}/rejeitar` | Propõe e decide o diagnóstico |
  | `POST /estrategia`, `POST /estrategia/{id}/aprovar`, `POST /estrategia/{id}/rejeitar` | Propõe e decide a estratégia; aprovar exige os campos `decisao_{i}` com `aceitar` ou `recusar` |

- **Códigos de erro:** `SubmissionConflict` → 409; `SessionBusy` → 409 com a mensagem "Há uma resposta em curso."; `InvalidAnswer` → 422; `IncompleteDecisions` → 422; `MissingPrerequisite` → 409.
- **`Idempotency-Key`:** gerada **uma vez por formulário renderizado**, num campo escondido enviado pelo HTMX em `hx-headers`. Repetições do mesmo formulário reutilizam-na.
- **Segurança da página:**
  - Jinja com `autoescape` ligado;
  - nenhum filtro `safe` sobre texto do utilizador ou de modelos;
  - cabeçalho `Content-Security-Policy: default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'`.
- **HTMX:** ficheiro local de versão fixada, com versão e `sha256` em `static/README.md`.

- [x] **Step 1: Escrever os testes que falham.** `TestClient` com `client=("127.0.0.1", 5000)` e login válido.
  - CSRF: `test_cross_site_post_403` (Foco 4: `Sec-Fetch-Site: cross-site`), `test_missing_csrf_token_403`, `test_valid_csrf_passes`.
  - Rotas:
    - `test_every_route_requires_auth`: todas as rotas sem cabeçalho dão 401;
    - `test_every_mutation_requires_csrf`;
    - `test_home_shows_budget_and_over_reference_warning`;
    - `test_answer_returns_turn_partial`;
    - `test_repeat_same_key_in_progress_shows_em_curso` (Foco 1);
    - `test_busy_session_409_message`;
    - `test_model_output_is_escaped`: uma pergunta do LLM com `<script>alert(1)</script>` aparece como `&lt;script&gt;`;
    - `test_csp_header_present`;
    - `test_no_external_scripts`;
    - `test_strategy_approval_without_all_decisions_422`;
    - `test_confirm_profile_button_activates`.
- [x] **Step 2: Correr e ver falhar.**
- [x] **Step 3: Implementar.** Rotas síncronas (`def`). A aprovação existe **só** como POST explícito. O aviso aparece quando `injection_suspected` é verdadeiro, mas não altera nenhuma ação.
- [x] **Step 4: Correr a validação global.**
- [x] **Step 5: Commit.** `feat: painel com csrf, entrevista, diagnostico e estrategia`.

**Notas de execução (Tarefa 14):**

- Concluída no ramo `agent/2026-11-06_plano01_tarefa14` (commits `493aec5` e `71d0f24`), empilhada sobre o PR do ajuste do papel `system` (#12); em modo subagente: implementação, revisão da tarefa (modelo mais capaz, em modo de atacante) e uma ronda de correção com re-revisão limpa. 472 testes passam; `ruff` e `mypy src` limpos.
- **Primeira interface gráfica:** Jinja no servidor + HTMX 2.0.11 local (integridade sha512 verificada contra o registo npm; versão, sha256 e licença 0BSD em `panel/static/README.md`, com teste), CSS próprio, sem JS inline. Páginas: `/` (estado do mês, sessões, artefactos ativos e à espera de decisão), `/entrevista/{id}`, `/perfil/{id}`, `/diagnostico/{id}`, `/estrategia/{id}`.
- **Decisões do controlador:** `create_app` recebe também `brand_id` e `queries` (`panel/queries.py`, só leitura), preenche `app.state.settings`, valida `PANEL_TRUSTED_PROXY` e recusa uma lista de logins vazia no arranque; páginas de revisão `GET` extra; CSP exatamente a do brief, mais `nosniff` e `no-store`; `Idempotency-Key` nova depois de resultado completo, bloqueado ou `needs_human`, a mesma depois de `in_progress`, 422 se faltar (o servidor nunca a gera).
- **Aprovar a estratégia:** um par de botões por proposta, sem nenhum pré-selecionado; valores desconhecidos dão 422; a página inicial mostra só as propostas aceites da estratégia ativa.
- **Correção da ronda 1 (invisível aos testes):** `Referrer-Policy: no-referrer` fazia o browser enviar `Origin: null` nas submissões de formulários simples, que recusamos; **todos os botões de decisão simples falhariam com 403 num browser real**. Passou a `same-origin` (cross-site continua recusado). O `TestClient` não envia `Origin`, por isso os testes passam agora com `Origin` e `Sec-Fetch-Site` realistas. Novo `static/panel.js` impede o duplo envio dos formulários simples (os do HTMX usam `hx-disabled-elt`).
- **Não verificado em browser real** (sem browser nem `node` neste ambiente): o comportamento do HTMX (trocas, `HX-Retarget` em 4xx/5xx, `hx-disabled-elt`, cabeçalhos combinados), o `panel.js` (duplo clique, cache de páginas do browser) e o `Origin` real nos formulários simples. **Fazer uma passagem manual no browser depois da Tarefa 15.**
- Sem `python-multipart`: um leitor de formulários próprio e limitado (`panel/forms.py`) substitui-o. As sessões ordenam-se pelo início da primeira execução (a tabela não tem data). Os métodos de leitura recebem `brand_id` (ids de outra marca dão 404).
- Achados menores adiados: erros `HTTPException` fora do HTMX respondem JSON cru (403 CSRF, 404, 405, 413, 415) em vez da página de erro; `decisao_01` e `decisao_1` mapeiam o mesmo índice; o token CSRF usa `casefold()` e não roda; `assert isinstance` em `errors.py` desaparece com `python -O`; `app.py` importa `auth._parse_networks` (privado); CRLF das textareas pode exceder `max_message_chars`; o aviso `injection_suspected` só aparece na resposta ao envio.

### Task 15: Bootstrap, comandos de operação e ponta a ponta

**Files:**
- Create: `src/marketing_agent/panel/main.py`, `src/marketing_agent/cli.py`, `tests/test_cli.py`, `tests/e2e/test_director_flow.py`, `tests/e2e/test_crash_recovery.py`
- Modify: `pyproject.toml` (`[project.scripts] marketing-agent = "marketing_agent.cli:main"`), `README.md` (arranque, variáveis, comandos), `AgentDATA/context/2026-11-02T12-25-40+00-00_analise_marketing_ai_manager_estado.md`

**Interfaces:**
- Consumes: todas as anteriores; `build_llm_router` (T10).
- Produces:
  - `create_app_from_env() -> FastAPI` em `panel/main.py`: constrói `Settings`, motor de BD, `SystemClock`, `PriceBook`, `BudgetController`, router, `ArtifactStore`, `AgentEngine` e os serviços. O segredo CSRF vem de `PANEL_CSRF_SECRET_FILE`.
  - `cli.main(argv: Sequence[str] | None = None) -> int` com os subcomandos:

    | Subcomando | Função |
    | --- | --- |
    | `create-brand NAME` | Idempotente por nome; imprime o id |
    | `sync-prices` | Sincroniza `model_prices.yaml` |
    | `recover-reservations --older-than-minutes N` | Marca como `unknown_outcome` as reservas abertas há mais de N minutos |

- **Arranque:** `uv run uvicorn --factory marketing_agent.panel.main:create_app_from_env --host 127.0.0.1 --port 8000`. O arranque corre `recover_stale` com `older_than` igual ao maior `max_duration_s` dos specs.

- [ ] **Step 1: Escrever os testes que falham.**
  - `test_cli.py`:
    - `test_create_brand_idempotent`;
    - `test_sync_prices_loads_repo_file`;
    - `test_recover_reservations_marks_unknown`.
  - `test_director_flow.py::test_interview_to_active_strategy`:
    - com o painel e os cenários `interview_full(3)` e `diagnosis_and_strategy()`: começar, 3 respostas, pedir e confirmar o perfil, propor e aprovar o diagnóstico, propor e aprovar a estratégia com uma proposta aceite e uma recusada;
    - verificar que `active_strategy` tem 1 proposta, que cada execução tem `cost_entry` e que o `AuditLog` regista o login em cada decisão.
  - `test_director_flow.py::test_over_reference_next_month_resets` (Foco 5): enche-se o ledger até 25 USD e o turno seguinte corre com aviso; com o `FixedClock` em dezembro, `status().spent_usd == 0`.
  - `test_crash_recovery.py::test_kill_after_llm_call_before_commit` (Foco 2):
    - injetar no motor uma falha (`SystemExit`) entre `complete` e `settle`;
    - verificar a reserva `open` e a submissão `reserved`;
    - correr `recover_stale` e verificar a reserva `unknown_outcome`, contada em `status().unknown_usd`;
    - repetir a submissão com a mesma chave e verificar que devolve `in_progress`/`needs_human` **sem** novo pedido ao LLM.
- [ ] **Step 2: Correr e ver falhar.**
- [ ] **Step 3: Implementar** só o necessário para passar. Atualizar o `README.md` (pré-requisitos, `compose.test.yaml`, variáveis `*_FILE`, comando de arranque e nota de que o acesso é por `tailscale serve`) e o registo de contexto.
- [ ] **Step 4: Validação completa.**
  1. Correr a validação global.
  2. Arrancar o painel com uma rota `fake`.
  3. Pedir `GET /` com `curl -H "Tailscale-User-Login: teste@exemplo"` a partir de 127.0.0.1 e esperar 200.
  4. Repetir o pedido sem o cabeçalho e esperar 401.
- [ ] **Step 5: Commit.** `feat: bootstrap, comandos de operacao e testes ponta a ponta`.

---

## Auto-revisão

- **Cobertura do E.0:** cada correção tem tarefa.

  | Correção | Tarefas |
  | --- | --- |
  | Rotas por papel e preços versionados | T1, T4, T7, T10 |
  | Postgres real | T2 |
  | Versão ativa | T3 |
  | Ledger único e reservas | T4, T5 |
  | Referência que não bloqueia | T5 |
  | Permissões aplicadas | T6, T8 |
  | Contrato LLM extensível | T7 |
  | Erros classificados e relógio | T8 |
  | Fixtures e chave por ficheiro | T9 |
  | Fake com cenários | T7 |
  | Submissões, conflito, serialização sem lock longo e correção de campos | T11 |
  | Diagnóstico e estratégia | T12 |
  | Proxy confiável | T13 |
  | CSRF, CSP, escape, "em curso" e chave por formulário | T14 |
  | Factory, marca inicial e falha depois da chamada | T15 |

- **Fora deste plano:** revisão mensal, experiências como tabela própria, KB e plano semanal (Plano 2); publicação (Plano 3); oficina 3D (Plano 4); triagem (Plano 5).
- **Desvios assumidos face ao Anexo A:**
  - `strategy_decision` e os campos `busy_*` em `interview_session` são novos;
  - `agent_definition` não é criada;
  - o lock na linha de `agent_run` substitui o *advisory lock* por marca de E.0 (justificado em T5).

  Estes desvios devem ser refletidos no Anexo A no fim do plano.
- **Tipos e nomes:** os nomes abaixo têm a mesma assinatura em todas as tarefas que os consomem: `ArtifactStore.approve_and_activate`, `BudgetController.reserve`/`settle`/`mark_unknown`/`recover_stale`, `LlmRouter.resolve`, `RunContext.call_llm`/`create_artifact`, `InterviewService.submit_answer`, `StrategyService.active_strategy`.
- **Riscos:** o pré-requisito Postgres/Docker bloqueia a T2 se não estiver instalado. O escalão de preço do Haiku acima de 100 mil tokens tem de ser lido da fonte oficial na T4.
