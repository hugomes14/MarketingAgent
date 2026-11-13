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
| `recover-reservations [--older-than-minutes N]` | Marca reservas abertas antigas como `unknown_outcome`; N abaixo do minimo seguro e recusado (saida 2) e, omitido, usa o minimo: 8 min com `LLM_TIME_MULTIPLIER=1`, 35 min com 5 (= `worker_stale_after` arredondado para cima) | `marketing-agent recover-reservations --older-than-minutes 10` (so com multiplicador 1) |
| `migrate` | Alembic `upgrade head` | `marketing-agent migrate` |
| `serve [--host --port --allow-non-loopback]` | Serve o painel (uvicorn, `proxy_headers=False`) | `marketing-agent serve` |
| `worker` | Processa a fila de geracao (Copywriter, Designer, Revisor), um job de cada vez, ate `SIGINT`/`SIGTERM`; um so por base de dados | `marketing-agent worker` |
| `eval run --set NOME [--yes]` | Mostra pacotes, teto e referencia mensal; confirma e cria a corrida com jobs de menor prioridade | `marketing-agent eval run --set briefs_v1` |
| `dev-proxy --login L [--name N --listen H:P --upstream H:P]` | Proxy local de desenvolvimento que injeta a identidade | `marketing-agent dev-proxy --login dono@local` |

## Entradas

`eval run` pede `sim` num TTY; fora de um terminal exige `--yes` (sem ele sai com 2, sem criar
jobs). O conjunto fica em `EVALS_DIR` e exige perfil, estrategia, kit e referencias validas.
O worker executa os dois percursos; avaliacoes escrevem apenas tabelas de avaliacao, media e
contabilidade, sem publicacoes semanais. Os custos somam todas as tentativas de cada item.
O arranque e as iteracoes ociosas do worker reconciliam itens com jobs mortos.

Argumentos acima e a configuracao de `Settings` (`.env` e variaveis de ambiente). Segredos so em
ficheiros apontados por `*_FILE`.

## Saidas

stdout: dados (id da marca, contagens). stderr: avisos e erros de uma linha (no `worker`, tambem as
linhas de log com ids de job e nomes de classe de erro, nunca texto de legendas, prompts ou modelo).
Saida 0 ok, 2 uso ou configuracao invalida, 1 erro inesperado. O `worker` sai com 0 em
`SIGINT`/`SIGTERM`, sem traceback; com 2 e mensagem fixa se ja existe outro worker na base
(`ja existe um worker a correr nesta base de dados`) ou se perde a ligacao que guarda o lock
(`a ligacao que guarda o lock do worker perdeu-se: ...`).

## Efeitos colaterais

Escreve na base de `DATABASE_URL` (marca, precos, reservas, esquema); `serve` e `dev-proxy` abrem
portas em loopback; o arranque do painel marca reservas antigas como `unknown_outcome`. O `worker`
grava `post_revision`, `agent_run` e custos. Toma, antes de tudo, um lock de sessao do Postgres
(`pg_try_advisory_lock`, chave `WORKER_LOCK_KEY` em `jobs/singleton.py`) numa ligacao dedicada em
autocommit, aberta durante toda a vida do processo: so existe um worker por base de dados (os
locks consultivos sao por base) e o painel nunca o toma. Com o lock, ao arrancar devolve a
`queued` TODOS os jobs `running` (sao orfaos de um worker morto), mantendo `attempts`; os que
esgotaram as 3 tentativas passam a `dead` e o post `drafting` recebe `last_error` fixo; so
depois recupera as leases vencidas dos outros tipos e marca como `unknown_outcome` as reservas
abertas ha mais do que `worker_stale_after()` (480 s com `LLM_TIME_MULTIPLIER=1`, 2100 s com 5).
As reservas do worker morto NAO sao libertadas no arranque: continuam a contar (a quota de
imagens deriva da base) ate ao limiar. O worker verifica a ligacao do lock (`SELECT 1`) antes de
cada reclamacao, em cada iteracao ociosa e (via `abort_check` do motor) antes de cada chamada de texto ou de imagem; se se perder, nenhuma chamada paga nova sai (a que esta em voo acaba, as escritas sao rejeitadas pela lease) e o worker sai com 2. Os jobs orfaos recolocados no arranque so sao reclamaveis apos a graca (243 s com m=1, 663 s com m=5). Um job em curso
termina antes de o worker sair (lease do job: 6360 s com multiplicador 1, 18600 s com 5; o motor
so verifica a duracao antes de cada passo, por isso uma chamada pode exceder o `max_duration_s`).
O arranque do painel e do worker regista (INFO) `LLM_TIME_MULTIPLIER`, a lease e o limiar das
reservas: ambos tem de usar o mesmo valor. O `dev-proxy` le o multiplicador do `.env`.

## Dependencias

Postgres 17 (`compose.dev.yaml`), uv, Ollama ou chaves de API conforme `ROLE_ROUTES`.

## Variaveis de ambiente

```text
DATABASE_URL=
ROLE_ROUTES=  # o worker exige rotas copywriter, designer e revisor (fornecedor fake recusado)
LLM_TIME_MULTIPLIER=  # 1 a 10, igual no painel, no worker e no dev-proxy
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

Antes de arrancar o `worker` desta versao, parar qualquer worker antigo (sem lock). O `DATABASE_URL` do
worker tem de ser ligacao direta ou em modo de sessao (nao pgbouncer em modo de transacao): o lock e
de sessao.

`serve` recusa `FORWARDED_ALLOW_IPS` e hosts fora do loopback; `dev-proxy` so escuta em loopback e
nao e um mecanismo de autenticacao. O `worker` gasta orcamento (chamadas ao LLM) e corre um so job
de cada vez: um segundo `worker` sobre a mesma base e recusado pelo lock.

## Documentacao relacionada

`README.md`, `src/marketing_agent/panel/auth.py`.
