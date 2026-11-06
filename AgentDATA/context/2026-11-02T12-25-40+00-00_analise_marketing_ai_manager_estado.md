# Contexto de execucao

- Timestamp: `2026-11-06T10:09:27+00:00` (reescrito; criado em `2026-11-02T12:25:40+00:00`)
- Autor: `agent`
- Escopo: Estado atual do projeto Marketing AI Manager. A fonte de verdade e o plano mestre v2; este registo so aponta para ele e para o que falta.

## Estado observado

- **Tarefa 1 do Plano 01 concluida e integrada em `main`** (commits `2cf36ff`, `87aa100`, `a9b5563`). Remoto `origin` = `git@github.com:hugomes14/MarketingAgent.git` (SSH); `main` sincronizado com `origin/main`.
  - Contem: projeto Python 3.12 com `uv`, `config.py` (`Settings`, `RoleRoute`), `clock.py`, `secrets.py`, `compose.test.yaml`, `.env.example`, e a seccao "Desenvolvimento" do README.
  - Teve revisao da tarefa, re-revisao e revisao final do ramo, todas fechadas.
- **Tarefa 2 do Plano 01 implementada** no ramo `agent/2026-11-06_plano01_tarefa2` (commit `0b64250`), ainda nao integrada em `main`: pacote `db` (`base.py`, `models.py` com 15 tabelas), `alembic.ini`, `migrations/` (migracao `0001` escrita a mao, com o trigger `artifact_payload_immutable`), `tests/conftest.py` e `tests/db/`. 40 testes passam; `ruff` e `mypy src` limpos; o roundtrip `downgrade base` + `upgrade head` funciona. Falta a revisao final do ramo.
- **Tarefa 12 do Plano 01 implementada** no ramo `agent/2026-11-06_plano01_tarefa12` (`eded71e`): `strategy/`, specs/prompts de diagnostico e estrategia, validadores e `ArtifactStore.approve_and_activate_in`. 341 testes passam. As Tarefas 1 a 11 ja estao em `main` (PRs #1 a #9).
- (historico) **Tarefa 11 do Plano 01 implementada** no ramo `agent/2026-11-06_plano01_tarefa11` (`f826d05`, `63beefa`): `agents/diretor/`, `interview/` e testes. 301 testes passam. As Tarefas 1 a 10 ja estao em `main` (PRs #1 a #8).
- (historico) **Tarefa 10 do Plano 01 implementada** no ramo `agent/2026-11-06_plano01_tarefa10` (`f96cf5a`): `llm/openai_adapter.py`, `llm/factory.py`, `llm/transport_errors.py` e testes. 243 testes passam. Live so contra o Ollama local (emulador). As Tarefas 1 a 9 ja estao em `main` (PRs #1 a #7).
- (historico) **Tarefa 9 do Plano 01 implementada** no ramo `agent/2026-11-06_plano01_tarefa9` (`0030973`): `llm/anthropic_adapter.py` e `tests/llm/`. 165 testes passam. Live corrido so contra um Ollama local (emulador), nao contra a Anthropic. As Tarefas 1 a 8 ja estao em `main` (PRs #1 a #6).
- (historico) **Tarefa 8 do Plano 01 implementada** no ramo `agent/2026-11-06_plano01_tarefa8` (`b730222`, `c537302`): `harness/engine.py` e `tests/harness/test_engine.py`. 114 testes passam. As Tarefas 1 a 7 ja estao em `main` (PRs #1 a #5).
- (historico) **Tarefa 7 do Plano 01 implementada** no ramo `agent/2026-11-06_plano01_tarefa7` (`3d6e29c`): `llm/` (tipos, `FakeLlmClient`, cenarios, `LlmRouter`) e `tests/llm/`. 87 testes passam. As Tarefas 1 a 6 ja estao em `main` (PRs #1 a #4).
- (historico) **Tarefa 6 do Plano 01 implementada** no ramo `agent/2026-11-06_plano01_tarefa6` (`ed38f53`): `harness/spec.py`, `harness/validators.py` e `tests/harness/`. 76 testes passam. As Tarefas 1 a 5 ja estao em `main` (PRs #1 a #3).
- (historico) **Tarefa 5 do Plano 01 implementada** no ramo `agent/2026-11-06_plano01_tarefa5` (`c32535e`, `080ea1e`): `budget/controller.py` e `tests/budget/test_controller.py`. 69 testes passam. As Tarefas 1 a 4 ja estao em `main` (PRs #1 e #2).
- (historico) **Tarefa 4 do Plano 01 implementada** no ramo `agent/2026-11-06_plano01_tarefa4` (`0520953`, `07abde4`): `budget/pricing.py`, `config/model_prices.yaml`, `tests/budget/`. 61 testes passam. As Tarefas 1 a 3 ja estao em `main` (PR #1).
- (historico) **Tarefa 3 do Plano 01 implementada** no mesmo ramo (`00fc9e9`, `c019b34`): `artifacts/store.py` (`ArtifactStore`, transicoes, versao ativa com `previous_artifact_id` atomico) e `tests/artifacts/`. 51 testes passam. Falta a revisao final do ramo (Tarefas 2 e 3).
- **Push:** o bloqueio de chave so de leitura ficou resolvido em `2026-11-06`; `main` e o ramo estao no GitHub (PR por abrir: `gh` nao esta instalado).
- (historico) **Push bloqueado:** a chave SSH deste ambiente esta marcada como so de leitura no GitHub (`git push` falha com "key ... marked as read only"). Os commits ficam locais (`main` esta 1 commit a frente de `origin/main`) ate o responsavel dar escrita a chave ou configurar outra.
- **Ambiente de execucao mudou.** A Tarefa 1 foi feita em Windows 11 (`uv 0.13.6`). Desde `2026-11-06` o agente corre em WSL2 Ubuntu 24.04, utilizador `marketing-agent`, projeto em `~/MarketingAgent`.
  - Ferramentas: `uv 0.13.7`, Python 3.12.3 do sistema, Docker 29.1.3, Docker Compose 2.40.3.
  - `.venv` criado neste ambiente (Linux). `uv sync --frozen` para um venv novo funciona sem `UV_LINK_MODE=copy` (ver o registo de conhecimento do `uv`).
  - Validacao em `2026-11-06`: `pytest` 30 passados; `ruff check` e `ruff format --check` limpos; `mypy src` limpo. `mypy src tests` da 9 erros ja conhecidos (8 `unused-ignore` em `tests/test_config.py` e falta de `types-PyYAML`), que estao nos achados adiados abaixo.
  - Postgres de teste: o contentor `marketingagent-postgres-1` (`postgres:17`, 17.11) esta ativo e saudavel em `127.0.0.1:5433`. Fica assim desbloqueada a Tarefa 2.
  - Nao ha `.env` local. Em `AgentDATA/secrets/` so existe `alert_email_to`, sem chaves de API, que so serao precisas nas Tarefas 9 e 10.
- Fonte de verdade: `AgentDATA/docs/2026-11-05T21-10-57+00-00_plano_mestre_marketing_ai_manager.md` (v2); plano de implementacao: `AgentDATA/docs/2026-11-05T21-28-57+00-00_plano_01_v2_nucleo_diretor_estrategia.md` (Tarefas 13 a 15 por fazer).
- Documento de apoio ativo: `AgentDATA/docs/revisao_critica_marketing_ai_manager_2026-11-05.md`. Versoes anteriores em `AgentDATA/docs/arquivo/`.
- Skills recomendadas registadas como `desejada` em `AgentDATA/skills/`; nada instalado.

## Decisoes

- Plano mestre v2, seccao 3 (D1 a D49): primeira entrega com diagnostico e estrategia, criacao de texto e imagem, sessao semanal, publicacao com recuperacao automatica e oficina 3D essencial; triagem depois; 20 USD/mes como referencia sem corte; email minimo para urgencias; RPO 1 h e RTO 24 h; CrewAI adiado; venda fora de foco.
- Tarefa 1: `Settings.database_url` e obrigatorio (sem valor por omissao, para nunca apontar a aplicacao a base de testes); `.env` so leva campos de `Settings`; segredos so por ficheiro (`*_FILE`, em `AgentDATA/secrets/`).

## Arquivos relacionados

- `AgentDATA/docs/2026-11-05T21-10-57+00-00_plano_mestre_marketing_ai_manager.md`
- `AgentDATA/docs/2026-11-05T21-28-57+00-00_plano_01_v2_nucleo_diretor_estrategia.md`
- `AgentDATA/docs/revisao_critica_marketing_ai_manager_2026-11-05.md`
- `AgentDATA/Knowledge/2026-11-05T22-21-14+00-00_uv_hardlink_acesso_negado.md`
- `AgentDATA/Knowledge/` (7 registos anteriores; onde discordarem da seccao 15 do plano, vale o plano)
- `AgentDATA/skills/skills_index.md` e `skill_*.md`

## Achados menores adiados da Tarefa 1

Resultado das revisoes; nenhum bloqueia a Tarefa 2. Quem os trata:

- Tarefa 9: a marca `live` nao e excluida por omissao (`-m "not live"`).
- Tarefa 10: valor em branco em `OPENAI_API_KEY_FILE`, `ANTHROPIC_API_KEY_FILE` e `OPENAI_BASE_URL` vira `Path('.')` ou `''` em vez de `None`.
- Tarefa 15: `SecretFileError` nao indica o motivo do sistema operativo nem resolve caminhos relativos; JSON invalido numa variavel composta levanta `SettingsError`.
- Sem dono (corrigir quando se tocar nos ficheiros):
  - `# type: ignore[call-arg]` inuteis e `ROUTE_OK: dict[str, Any]` em `tests/test_config.py`;
  - a constante de teste `TEST_DATABASE_URL` tem o nome da variavel de shell da Tarefa 2;
  - o caso `TEST_DATABASE_URL` so afirma sobre `str(exc)`;
  - testes pouco afiados: falta fuso nao UTC no `FixedClock`, o erro nao e ligado ao campo `provider`, um teste le o `.env` real, falta paridade `.env.example` e `Settings`;
  - percentagens de aviso aceitam `[true]`, `"50"`, `50.0` e repetidas, e um item invalido gera tambem um erro `too_short` enganador;
  - `model_id` so com espacos, nome de papel vazio e `capabilities` vazio sao aceites;
  - o `from None` esconde a excecao, mas ela continua em `__context__`;
  - comentario em `config.py` diz `extra="forbid"` mas e o valor por omissao da biblioteca;
  - a mensagem de `secrets.py` diz "caracteres de controlo" tambem para BOM e espacos de largura zero;
  - `.env.example` nao avisa que o python-dotenv altera caminhos Windows entre aspas duplas;
  - README sem `docker compose up -d --wait` nem nota sobre `UV_LINK_MODE=copy`.

## Proxima acao sugerida

1. Tarefa 13 do Plano 01 (autenticacao do painel por proxy confiavel), depois do merge do PR da Tarefa 12. A Tarefa 14 (painel): chave de idempotencia NOVA por cada tentativa de resposta; reler o estado dos artefactos da base (o `RunResult.output` das propostas traz o estado `draft`). Pendente do responsavel: o validador aceita `facto` com origem `suposto` (decidir se e violacao). Pendente: validar os adaptadores Anthropic e OpenAI com chaves reais.
2. Notas a levar para as tarefas seguintes (resultado das revisoes da Tarefa 1):
   - Em Windows, despachos que instalem pacotes levam `UV_LINK_MODE=copy`. Em WSL2 nao e preciso.
   - Tarefa 2: os testes e `migrations/env.py` definem `DATABASE_URL` explicitamente, a partir de `TEST_DATABASE_URL`; `.env.example` aponta `DATABASE_URL` a base de testes, o que torna dev e teste a mesma base, a decidir quando existir uma base de desenvolvimento.
   - Tarefa 4: acrescentar `types-PyYAML` as dependencias de desenvolvimento (o `mypy` strict falha ao importar `yaml`).
   - Tarefa 9: `-m "not live"` nas opcoes do pytest por omissao; o `skipif` le `os.environ`, porque o `.env` nao o preenche.
   - Tarefa 10: valores em branco em `OPENAI_API_KEY_FILE`, `ANTHROPIC_API_KEY_FILE` e `OPENAI_BASE_URL` devem virar `None`, para a mensagem de chave em falta nomear a variavel.
   - Tarefas 14 e 15: erros de validacao tratados com `exc.errors(include_input=False)`; acrescentar `PANEL_CSRF_SECRET_FILE` a `Settings`; no arranque, apanhar `SettingsError` e `ValidationError`; resolver `.env` e ficheiros de chave a partir de um diretorio de trabalho conhecido.
3. Pendencias do responsavel: plano Tailscale gratuito (Personal, uso nao comercial) por confirmar; base de desenvolvimento separada da de testes por decidir; destinatario do email ja guardado em `AgentDATA/secrets/alert_email_to`.
4. Reescrever o `README.md` por completo quando o Plano 1 estiver executado.
