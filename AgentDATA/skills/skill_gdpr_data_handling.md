# Registro de skill

## Metadata

- Nome: gdpr-data-handling
- Timestamp: `2026-11-02T12:25:32+00:00`
- Status: `desejada`
- Origem: `wshobson/agents@gdpr-data-handling` — https://skills.sh/wshobson/agents/gdpr-data-handling (15,2K instalacoes; repositorio ~42,1K estrelas, MIT)
- Versao ou revisao: nao verificada

## Quando usar

Ao desenhar retencao, anonimizacao, exportacao/eliminacao (W14), minimizacao de dados enviados ao LLM.

## Quando nao usar

Como substituto de aconselhamento juridico ou de DPO. Nao decide base legal nem transferencias internacionais.

## Capacidades esperadas

Checklists e padroes tecnicos de tratamento de dados pessoais.

## Entradas necessarias

Tabelas com dados pessoais e fluxos para terceiros (Meta, LLM, Cloudflare, armazenamento).

## Saidas esperadas

Lista de requisitos tecnicos e verificacoes.

## Dependencias

Nenhuma.

## Procedimento relacionado

`AgentDATA/procedures/legacy-and-delete.md` (remocao segura).

## Observacoes de seguranca

Conteudo generico; validar com jurista. Nao guardar dados pessoais reais no repositorio.

## Como validar disponibilidade

```bash
npx skills find gdpr privacy compliance
```
