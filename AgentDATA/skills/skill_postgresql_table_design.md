# Registro de skill

## Metadata

- Nome: postgresql-table-design
- Timestamp: `2026-11-02T12:25:31+00:00`
- Status: `desejada`
- Origem: `wshobson/agents@postgresql-table-design` — https://skills.sh/wshobson/agents/postgresql-table-design (28,3K instalacoes; repositorio ~42,1K estrelas, MIT)
- Versao ou revisao: nao verificada

## Quando usar

Ao transformar o Anexo A (modelo de dados) em migracoes: tipos, constraints, indices parciais, estrategia de chaves.

## Quando nao usar

Para decisoes de dominio ou de retencao RGPD (essas vivem no Anexo A e no registo de conformidade).

## Capacidades esperadas

Boas praticas de desenho de tabelas PostgreSQL.

## Entradas necessarias

Anexo A e volumes esperados.

## Saidas esperadas

Esquema e migracoes Alembic revistos.

## Dependencias

PostgreSQL 16/17, Alembic, SQLAlchemy 2.

## Procedimento relacionado

`AgentDATA/procedures/code-quality.md`, `validation.md`.

## Observacoes de seguranca

Rever a skill antes de instalar; nao executar SQL sugerido sem leitura.

## Como validar disponibilidade

```bash
npx skills find postgresql schema design
```
