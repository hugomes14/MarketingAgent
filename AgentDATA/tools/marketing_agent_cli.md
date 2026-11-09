# Registro de CLI tool

## Metadata

- Nome: `marketing-agent`
- Timestamp: `2026-11-09T18:00:00+00:00`
- Status: `disponivel`
- Runtime: Python 3.12 (entry point `marketing_agent.cli:main`, instalado com `uv sync`)
- Origem: este repositorio (`src/marketing_agent/cli.py`)
- Dono operacional: responsavel pelo projeto

## Objetivo

Operar o sistema localmente: criar a marca, sincronizar precos, recuperar reservas, migrar a base,
servir o painel e processar a fila de geracao de texto (worker).

## Quando usar

Preparar uma instalacao local, recuperar reservas apos uma morte do processo, servir o painel,
gerar em segundo plano o texto das publicacoes aprovadas no plano (`worker`).

## Quando nao usar

Nao usar `dev-proxy` fora de uma maquina pessoal: quem alcanca a porta age como o login configurado.

## Comando

```bash
uv run marketing-agent --help
```

## Subcomandos

| Subcomando | Objetivo | Exemplo |
| --- | --- | --- |
| `create-brand NOME` | Cria a marca (idempotente por nome) e imprime o id | `marketing-agent create-brand "Padaria"` |
| `sync-prices [--file P ...]` | Sincroniza precos de modelos | `marketing-agent sync-prices --file config/model_prices.yaml` |
| `recover-reservations --older-than-minutes N` | Marca reservas abertas antigas como `unknown_outcome` | `marketing-agent recover-reservations --older-than-minutes 10` |
| `migrate` | Alembic `upgrade head` | `marketing-agent migrate` |
| `serve [--host --port --allow-non-loopback]` | Serve o painel (uvicorn, `proxy_headers=False`) | `marketing-agent serve` |
| `worker` | Processa a fila `generate_post` (Copywriter -> Revisor), um job de cada vez, ate `SIGINT`/`SIGTERM` | `marketing-agent worker` |
| `dev-proxy --login L [--name N --listen H:P --upstream H:P]` | Proxy local de desenvolvimento que injeta a identidade | `marketing-agent dev-proxy --login dono@local` |

## Entradas

Argumentos acima e a configuracao de `Settings` (`.env` e variaveis de ambiente). Segredos so em
ficheiros apontados por `*_FILE`.

## Saidas

stdout: dados (id da marca, contagens). stderr: avisos e erros de uma linha (no `worker`, tambem as
linhas de log com ids de job e nomes de classe de erro, nunca texto de legendas, prompts ou modelo).
Saida 0 ok, 2 uso ou configuracao invalida, 1 erro inesperado. O `worker` sai com 0 em
`SIGINT`/`SIGTERM`, sem traceback.

## Efeitos colaterais

Escreve na base de `DATABASE_URL` (marca, precos, reservas, esquema); `serve` e `dev-proxy` abrem
portas em loopback; o arranque do painel marca reservas antigas como `unknown_outcome`. O `worker`
grava `post_revision`, `agent_run` e custos; ao arrancar devolve a `queued` os jobs `running` com
lease vencida e marca como `unknown_outcome` as reservas abertas ha mais do que o maior
`max_duration_s` dos specs. Um job em curso termina antes de o worker sair (a lease dos jobs e
3 x (Copywriter + Revisor) + 60 s = 19 min com os specs atuais); se o processo for morto a meio,
o job volta a fila no arranque seguinte e nao ha revisao duplicada.

## Dependencias

Postgres 17 (`compose.dev.yaml`), uv, Ollama ou chaves de API conforme `ROLE_ROUTES`.

## Variaveis de ambiente

```text
DATABASE_URL=
ROLE_ROUTES=  # o worker exige rotas copywriter e revisor (fornecedor fake recusado)
PANEL_CSRF_SECRET_FILE=
PANEL_ALLOWED_LOGINS=
FORWARDED_ALLOW_IPS=  # nao pode estar definida para `serve`
```

## Exemplos

Ver `README.md`, secao "Correr localmente".

## Validacao

```bash
uv run marketing-agent --help
uv run pytest tests/test_cli.py tests/test_dev_proxy.py tests/jobs/test_worker.py
```

## Riscos e limites

`serve` recusa `FORWARDED_ALLOW_IPS` e hosts fora do loopback; `dev-proxy` so escuta em loopback e
nao e um mecanismo de autenticacao. O `worker` gasta orcamento (chamadas ao LLM) e corre um so job
de cada vez: nao lancar dois workers sobre a mesma base no 2A.

## Documentacao relacionada

`README.md`, `src/marketing_agent/panel/auth.py`.
