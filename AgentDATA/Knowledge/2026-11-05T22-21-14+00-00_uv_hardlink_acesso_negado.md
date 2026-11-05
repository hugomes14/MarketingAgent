# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-05T22:21:14+00:00`
- Autor: `agent`
- Assunto: `uv` falha com "Acesso negado" ao criar hardlinks na cache quando instala pacotes num ambiente novo (Windows, ambiente de execucao dos agentes)
- Status: `ativo`
- Retestar em: ao atualizar o `uv` ou ao mudar o ambiente de execucao dos agentes

## Achado

`uv add` e `uv sync` num ambiente virtual novo falham com `failed to hardlink file ... Acesso negado. (os error 5)` neste ambiente de execucao dos agentes. Com `UV_LINK_MODE=copy` a instalacao funciona. `uv run` num `.venv` ja existente nao instala nada e funciona sem a variavel.

## Contexto

- Windows 11, `uv 0.13.6`, cache em `C:\Users\<utilizador>\AppData\Local\uv\cache`.
- Projeto em `C:\Users\<utilizador>\Desktop\03_Projetos\MarktingAgent`.
- Visto duas vezes na execucao da Tarefa 1 do Plano 01: pelo implementador, em `uv add` no `.venv` do projeto, e pelo controlador, em `uv sync --frozen` para um venv temporario.

## Metodo

1. `UV_PROJECT_ENVIRONMENT=<pasta temporaria> uv sync --frozen`: falha.
2. O mesmo comando com `UV_LINK_MODE=copy`: o implementador confirmou que `uv add` passa com a variavel.

## Comandos

```bash
UV_LINK_MODE=copy uv add <pacote>
UV_LINK_MODE=copy uv sync --frozen
```

## Evidencia

Mensagem do `uv`: `failed to hardlink file from <venv>\...\top_level.txt to <cache>\archive-v0\...: Acesso negado. (os error 5)`.

## Codigo relevante

```text
Nenhum. O ficheiro pyproject.toml nao define link-mode.
```

## Interpretacao

- Fato observado: a criacao do hardlink e negada com o erro 5 neste ambiente de execucao; o modo `copy` evita-a.
- Inferencia: restricao do ambiente de execucao dos agentes sobre hardlinks. Nao foi confirmado se o terminal normal do utilizador falha da mesma forma.

## Consequencia operacional

- Despachos de agentes que instalem pacotes levam `UV_LINK_MODE=copy`.
- Nao se grava `link-mode` no `pyproject.toml`, porque o problema parece depender do ambiente (decisao registada no ledger do Plano 01, linha 15 da verificacao previa).

## Riscos e limites

O modo `copy` e mais lento e usa mais disco. Se o terminal do utilizador tambem falhar, a variavel tem de constar do README.

## Pendencias

Confirmar no terminal do utilizador se `uv sync` funciona sem a variavel.
