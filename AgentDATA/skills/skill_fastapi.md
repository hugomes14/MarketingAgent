# Registro de skill

## Metadata

- Nome: fastapi
- Timestamp: `2026-11-02T12:25:30+00:00`
- Status: `desejada`
- Origem: `fastapi/fastapi@fastapi` — https://skills.sh/fastapi/fastapi/fastapi (14,2K instalacoes; repositorio oficial, ~104,5K estrelas, MIT)
- Versao ou revisao: nao verificada

## Quando usar

Ao implementar rotas, dependencias, validacao Pydantic, testes e estrutura do backend (Fase 1 em diante).

## Quando nao usar

Para logica de dominio sem I/O, integracao Meta ou prompts de IA.

## Capacidades esperadas

Padroes oficiais de FastAPI: dependencias, tratamento de erros, async, testes.

## Entradas necessarias

Descricao da rota/contrato e modelos Pydantic.

## Saidas esperadas

Codigo de rotas e testes seguindo as convencoes oficiais.

## Dependencias

Python 3.12+, FastAPI, Pydantic v2.

## Procedimento relacionado

`AgentDATA/procedures/standard-workflow.md`, `validation.md`.

## Observacoes de seguranca

Rever o conteudo da skill antes de instalar (instrucoes de terceiros). Nao copiar documentacao longa para o repositorio.

## Como validar disponibilidade

```bash
npx skills find fastapi
```
