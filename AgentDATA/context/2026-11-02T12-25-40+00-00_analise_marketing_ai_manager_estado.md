# Contexto de execucao

- Timestamp: `2026-11-05T19:41:43+00:00` (reescrito; criado em `2026-11-02T12:25:40+00:00`)
- Autor: `agent`
- Escopo: Estado atual do projeto Marketing AI Manager. A fonte de verdade e o plano mestre; este registo so aponta para ele e para o que falta.

## Estado observado

- Repositorio Git local (ramo `main`), sem remoto. Contem `resumo_marketing_ai_manager.md`, `AGENTS.md`, `README.md` (ainda seed) e `AgentDATA/`. **Nao existe codigo.**
- Toda a analise, decisoes (D1 a D31), arquitetura, workflows, roadmap, custos, riscos e plano de implementacao 01 estao em **`AgentDATA/docs/2026-11-05T19-41-43+00-00_plano_mestre_marketing_ai_manager.md`**, com os anexos A a F no mesmo ficheiro.
- Documentos anteriores arquivados em `AgentDATA/docs/arquivo/` (nao sao fonte de verdade).
- Skills recomendadas registadas como `desejada` em `AgentDATA/skills/`; nada instalado. Nao existe skill credivel para a Graph API da Meta.

## Decisoes

Ver a seccao 3 do plano mestre. Resumo: agendamento proprio; triagem e notificacao sem resposta automatica; so painel; Tailscale (serve para o painel, Funnel para o webhook e imagens); API de fornecedor de LLM com orcamento de 20 USD/mes e fornecedor por configuracao; Python; equipa de agentes por papeis com motor proprio fino; Diretor em modo entrevista primeiro; imagens por layout e geracao por IA opcional; CrewAI em aberto no Plano 2; venda fora de foco.

## Arquivos relacionados

- `AgentDATA/docs/2026-11-05T19-41-43+00-00_plano_mestre_marketing_ai_manager.md`
- `AgentDATA/Knowledge/` (7 registos: Meta, LLM e conformidade, App Review, infraestrutura, mercado e certificacoes)
- `AgentDATA/skills/skills_index.md` e `skill_*.md`

## Proxima acao sugerida

1. O responsavel revê o plano mestre e o Anexo E (Plano de implementacao 01) e escolhe o metodo de execucao (recomendado: nativo).
2. Em paralelo, Plano 0 (operacional): Pi na tailnet, contas de teste da Meta, spike da Fase 0; confirmar o plano Tailscale da empresa.
3. Reescrever o `README.md` quando o Plano 1 for executado.
