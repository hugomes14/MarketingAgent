# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-06T15:51:51+00:00`
- Autor: `agent`
- Assunto: dois ficheiros `compose` na mesma pasta partilham o nome de projeto e o servico `postgres`: `docker compose up` de um remove o contentor do outro
- Status: `ativo`
- Retestar em: ao acrescentar um novo ficheiro compose a este repositorio

## Achado

`compose.dev.yaml` e `compose.test.yaml` estavam na mesma pasta, sem `name:`. O Docker Compose deriva o nome do projeto da pasta (`marketingagent`), logo ambos eram o mesmo projeto com o mesmo servico `postgres`. `docker compose -f compose.dev.yaml up` recriou o servico e **removeu o contentor de testes**; mais tarde, `compose.test.yaml up` removeu o de desenvolvimento e o painel deu 500. Os dados sobreviveram porque a base de desenvolvimento usa um volume nomeado.

## Contexto

- Descoberto na Tarefa 15 do Plano 01 (2026-11-06), quando a revisao encontrou a base de testes (porta 5433) em baixo.
- Corrigido com `name: marketing_agent_dev` e `name: marketing_agent_test` e um teste (`tests/test_compose.py`).

## Metodo

1. `docker ps -a` mostrou so um contentor; `docker compose -f ... up` imprimia "Recreated".
2. Recuperacao: `pg_dump` da base de desenvolvimento, `docker rm -f` dos contentores, `docker compose -f compose.dev.yaml up -d --wait` e `... compose.test.yaml ...` com os nomes novos, e restauro do dump.

## Comandos

```bash
docker compose -f compose.dev.yaml up -d --wait     # base de desenvolvimento, porta 5434, volume nomeado
docker compose -f compose.test.yaml up -d --wait    # base de testes, porta 5433, tmpfs (apaga-se ao reiniciar)
```

## Evidencia

- Contentores atuais: `marketing_agent_dev_postgres` e `marketing_agent_test-postgres-1`; o volume antigo `marketingagent_marketing_agent_dev_pgdata` foi substituido por um do projeto novo.

## Codigo relevante

- `compose.dev.yaml`, `compose.test.yaml`, `tests/test_compose.py`.

## Interpretacao

- Facto observado: o nome do projeto vem da pasta quando nao ha `name:`; servicos com o mesmo nome no mesmo projeto sao o mesmo servico.
- Consequencia: renomear o projeto muda o nome do contentor de testes (`marketingagent-postgres-1` passa a `marketing_agent_test-postgres-1`) e o do volume (os dados de um volume antigo nao migram sozinhos).

## Riscos e limites

- Quem tiver ainda os contentores antigos tem de os remover antes (conflito de portas 5433/5434).

## Pendencias

- Nenhuma.
