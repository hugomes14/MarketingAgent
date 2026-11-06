# Marketing AI Manager

Agente de marketing para uso interno que planeia, executa e audita trabalho de marketing com
orçamento controlado. Esta primeira fase (Plano 01) entrega o **núcleo**: o Diretor conduz uma
entrevista de descoberta, gera o perfil da marca, propõe um diagnóstico e uma estratégia, e cada
decisão fica à espera da aprovação explícita do responsável no painel web.

Princípios: o texto do utilizador é dado, nunca instrução; só o código decide estados de
artefactos; todo o custo de LLM passa por um registo único (reserva, acerto e auditoria); o
orçamento mensal é uma referência que informa e não bloqueia; os segredos vivem em ficheiros e
nunca em logs, mensagens de erro ou no `.env`.

## Requisitos

- Python 3.12 e [uv](https://docs.astral.sh/uv/).
- Docker (ou um Postgres 17 próprio) para a base de dados.
- Para correr com um modelo local: [Ollama](https://ollama.com) com o modelo `qwen2.5:14b`
  (ou `llama3.1:8b`). Para usar modelos na nuvem: ficheiros com chaves da Anthropic e/ou da OpenAI.

## Estrutura do repositório

| Caminho | Conteúdo |
| --- | --- |
| `src/marketing_agent/` | Código: `config`, `secrets`, `clock`, `db`, `budget`, `llm`, `harness`, `artifacts`, `interview`, `strategy`, `agents/diretor`, `panel`, `cli` |
| `src/marketing_agent/panel/` | Painel FastAPI (autenticação por proxy, CSRF, páginas) e a fábrica `panel.main` |
| `migrations/`, `alembic.ini` | Migrações da base (Alembic) |
| `config/` | Preços dos modelos (`model_prices.yaml` e `model_prices.local.yaml`) |
| `compose.test.yaml`, `compose.dev.yaml` | Postgres de teste (efémero) e de desenvolvimento (persistente) |
| `.env.example`, `.env.local.example` | Modelos de configuração (valores fictícios) |
| `scripts/smoke_ollama.py` | Teste de fumo do Diretor contra o Ollama |
| `tests/` | Testes unitários, do painel e ponta a ponta (`tests/e2e/`) |
| `AGENTS.md`, `AgentDATA/` | Regras de trabalho do agente de código, procedimentos, conhecimento e contexto |

## Instalação

```bash
uv sync
```

O comando `marketing-agent` fica disponível com `uv run marketing-agent --help`.

## Correr localmente (passo a passo, com Ollama)

Tudo corre em `127.0.0.1`. Execute a partir da raiz do repositório.

1. **Base de dados** (Postgres 17 persistente na porta 5434, só em loopback):

   ```bash
   docker compose -f compose.dev.yaml up -d
   ```

2. **Configuração**: copie o modelo local e ajuste se precisar (o `.env` não é versionado):

   ```bash
   cp .env.local.example .env
   ```

3. **Ficheiros de segredo** (ficam em `AgentDATA/secrets/`, que o Git ignora):

   ```bash
   printf local > AgentDATA/secrets/ollama_key
   uv run python -c "import secrets; print(secrets.token_urlsafe(48))" > AgentDATA/secrets/panel_csrf_secret
   ```

4. **Ollama**: confirme que está a servir em `http://127.0.0.1:11434` e que o modelo existe
   (`ollama pull qwen2.5:14b`).

5. **Migrações, preços e marca**:

   ```bash
   uv run marketing-agent migrate
   uv run marketing-agent sync-prices --file config/model_prices.local.yaml
   uv run marketing-agent create-brand "NOME DA MARCA"
   ```

   Para ter também os preços dos modelos na nuvem:
   `sync-prices --file config/model_prices.yaml --file config/model_prices.local.yaml`.

6. **Painel** (num terminal):

   ```bash
   uv run marketing-agent serve
   ```

7. **Proxy de desenvolvimento** (noutro terminal), que faz o papel do `tailscale serve`:

   ```bash
   uv run marketing-agent dev-proxy --login dono@local
   ```

8. Abra <http://127.0.0.1:8001>. O login `dono@local` tem de estar em `PANEL_ALLOWED_LOGINS`.

A primeira pergunta e cada resposta chamam o modelo local, por isso podem demorar. Se o
processo morrer a meio de uma chamada, a reserva fica `unknown_outcome` (conta no orçamento como
desfecho desconhecido) e a mesma chave de formulário nunca repete a chamada.

## Comandos

| Comando | Função |
| --- | --- |
| `create-brand NOME` | Cria a marca (idempotente por nome) e imprime o seu id |
| `sync-prices [--file CAMINHO ...]` | Sincroniza os preços dos modelos (por omissão `MODEL_PRICES_PATH`) |
| `recover-reservations --older-than-minutes N` | Marca como `unknown_outcome` as reservas abertas há mais de N minutos |
| `migrate` | Aplica as migrações (`upgrade head`) na base de `DATABASE_URL` |
| `serve [--host 127.0.0.1] [--port 8000]` | Serve o painel com o uvicorn (recusa `FORWARDED_ALLOW_IPS` e hosts fora do loopback) |
| `dev-proxy --login LOGIN [--name NOME] [--listen 127.0.0.1:8001] [--upstream 127.0.0.1:8000]` | Só desenvolvimento: proxy local que injeta a identidade |

Códigos de saída: `0` sucesso, `2` erro de uso ou de configuração (uma linha em stderr), `1`
erro inesperado. O arranque do painel marca como `unknown_outcome` as reservas abertas há mais
do que o maior `max_duration_s` dos specs do Diretor. Equivalente direto ao `serve`:
`uvicorn --factory marketing_agent.panel.main:create_app_from_env --no-proxy-headers`.

## Configuração

Copie `.env.example` (ou `.env.local.example`) para `.env`. O `.env` contém apenas campos de
`Settings`: uma chave desconhecida (por exemplo `ANTHROPIC_API_KEY` ou `TEST_DATABASE_URL`) faz o
arranque falhar, sem repetir o valor. `DATABASE_URL` é obrigatório e não tem valor por omissão.
Campos compostos (`ROLE_ROUTES`, `BUDGET_WARN_PERCENTS`, `PANEL_*`) são JSON. As chaves de API e
o segredo CSRF vão em ficheiros apontados por `ANTHROPIC_API_KEY_FILE`, `OPENAI_API_KEY_FILE` e
`PANEL_CSRF_SECRET_FILE` (o segredo CSRF exige pelo menos 32 caracteres). A rota `fake` só existe
nos testes e o `serve` recusa-a.

## Testes e validação

Postgres de teste (porta 5433, base `marketing_agent_test`, dados efémeros):

```bash
docker compose -f compose.test.yaml up -d
export TEST_DATABASE_URL=postgresql+psycopg://marketing_agent:marketing_agent@127.0.0.1:5433/marketing_agent_test
```

Os testes partilham a mesma base: nunca corra dois processos `pytest` ao mesmo tempo. Exporte
`TEST_DATABASE_URL` na shell, nunca no `.env`.

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
```

`tests/e2e/` cobre o percurso completo pelo painel (entrevista até estratégia ativa), a
referência mensal e a recuperação após uma morte do processo.

## Segurança

- O painel só deve ser servido **atrás de um proxy confiável**: em produção o `tailscale serve`,
  que remove os cabeçalhos de identidade enviados pelo cliente e acrescenta `Tailscale-User-Login`.
  A origem da ligação (`PANEL_TRUSTED_PROXY`) é verificada antes do cabeçalho e do login
  (`PANEL_ALLOWED_LOGINS`).
- O servidor tem de correr com `proxy_headers=False` e **sem** `FORWARDED_ALLOW_IPS`; senão um
  cliente forjaria `X-Forwarded-For` e passaria por proxy. O comando `serve` impõe ambos e escuta
  em loopback por omissão.
- O `dev-proxy` é **só para desenvolvimento**: quem consegue ligar-se à sua porta age como o login
  configurado. Só aceita endereços de loopback e não tem opção para o mudar.
- Segredos só em ficheiros `*_FILE`; nunca em variáveis de ambiente do `.env`, logs ou erros.
- Credenciais de `compose.dev.yaml` e `compose.test.yaml` são de desenvolvimento e ficam em loopback.

## Fluxo de trabalho e agentes

As regras de trabalho do agente de código estão em [`AGENTS.md`](AGENTS.md); procedimentos,
conhecimento, contexto e registos ficam em [`AgentDATA/`](AgentDATA/). Detalhes operacionais:
`AgentDATA/procedures/standard-workflow.md` (planear, executar, validar, commitar).

## O que ainda não está feito

- Oficina 3D (Plano 4), publicação (Plano 3), revisão mensal, KB e plano semanal (Plano 2) e
  triagem (Plano 5).
- Verificação no browser: as páginas do painel só foram exercitadas por testes de HTTP.
- Prova real da autenticação com o Tailscale: ver
  `AgentDATA/docs/2026-11-06T14-26-00+00-00_checklist_prova_autenticacao_painel.md`.
