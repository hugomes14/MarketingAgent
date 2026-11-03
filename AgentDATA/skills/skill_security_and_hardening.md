# Registro de skill

## Metadata

- Nome: security-and-hardening
- Timestamp: `2026-11-02T12:25:33+00:00`
- Status: `desejada`
- Origem: `addyosmani/agent-skills@security-and-hardening` — https://skills.sh/addyosmani/agent-skills/security-and-hardening (76,1K instalacoes; repositorio ~112,7K estrelas, MIT)
- Versao ou revisao: nao verificada

## Quando usar

Ao implementar webhooks (assinatura, replay), gestao de tokens/segredos, autenticacao do painel e endurecimento do container.

## Quando nao usar

Em substituicao da skill built-in `security-review` para revisao de diffs pendentes; sao complementares.

## Capacidades esperadas

Praticas de seguranca para aplicacoes web e APIs.

## Entradas necessarias

Descricao do componente e superficie exposta.

## Saidas esperadas

Checklist e correcoes sugeridas.

## Dependencias

Nenhuma.

## Procedimento relacionado

`AgentDATA/procedures/validation.md`, `mcp-servers.md` (credenciais).

## Observacoes de seguranca

Rever a skill antes de instalar. Nunca colocar segredos reais em registos ou exemplos.

## Como validar disponibilidade

```bash
npx skills find security hardening
```
