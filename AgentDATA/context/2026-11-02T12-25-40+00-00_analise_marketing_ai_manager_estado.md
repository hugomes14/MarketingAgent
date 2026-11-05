# Contexto de execucao

- Timestamp: `2026-11-05T21:10:57+00:00` (reescrito; criado em `2026-11-02T12:25:40+00:00`)
- Autor: `agent`
- Escopo: Estado atual do projeto Marketing AI Manager. A fonte de verdade e o plano mestre v2; este registo so aponta para ele e para o que falta.

## Estado observado

- Repositorio Git local (ramo `main`), sem remoto. Contem `resumo_marketing_ai_manager.md`, `AGENTS.md`, `README.md` (ainda seed) e `AgentDATA/`. **Nao existe codigo.**
- Fonte de verdade: **`AgentDATA/docs/2026-11-05T21-10-57+00-00_plano_mestre_marketing_ai_manager.md`** (v2), que incorpora a revisao critica e as decisoes Q1 a Q20 (D32 a D48), com os anexos A a F no mesmo ficheiro. A seccao inicial diz que partes sao normativas.
- Documento de apoio ativo: `AgentDATA/docs/revisao_critica_marketing_ai_manager_2026-11-05.md` (achados F01 a F17, rondas Q1 a Q20, avaliacao do repositorio 3D de referencia, fontes S1 a S17).
- Plano mestre v1 e documentos anteriores arquivados em `AgentDATA/docs/arquivo/` (nao sao fonte de verdade).
- Skills recomendadas registadas como `desejada` em `AgentDATA/skills/`; nada instalado.

## Decisoes

Ver a seccao 3 do plano mestre v2. Resumo:

- **Primeira entrega:** Diretor (entrevista, diagnostico critico, estrategia aprovada antes do primeiro plano); criacao de texto e imagem (layout e geracao por IA); Revisor; sessao semanal de aprovacao com reserva; publicacao Facebook e Instagram com recuperacao automatica dentro da janela autorizada; oficina 3D essencial com avatares e painel de entregas, mais alternativa 2D.
- **Triagem:** entra depois do primeiro lancamento; ate la, atendimento na caixa nativa.
- **Orcamento:** 20 USD/mes como referencia (sem corte automatico), com controlo por chamada.
- **Avisos:** email minimo sem dados pessoais para urgencias.
- **Papeis:** um papel sem beneficio medido sai do percurso habitual.
- **CrewAI:** adiado.
- **Venda:** fora de foco.

## Arquivos relacionados

- `AgentDATA/docs/2026-11-05T21-10-57+00-00_plano_mestre_marketing_ai_manager.md`
- `AgentDATA/docs/revisao_critica_marketing_ai_manager_2026-11-05.md`
- `AgentDATA/Knowledge/` (7 registos; onde discordarem da seccao 15 do plano, vale o plano)
- `AgentDATA/skills/skills_index.md` e `skill_*.md`

## Proxima acao sugerida

1. O responsavel reve o plano mestre v2.
2. Reescrever o Plano 01 aplicando o Anexo E, seccao E.0, e escolher o metodo de execucao.
3. Em paralelo, Plano 0 (operacional): Pi na tailnet, Docker e Postgres, contas de teste da Meta, matriz de prova da Fase 0 (seccao 4.6), ensaio de restauro.
4. Pendencias do responsavel: autorizacao minima da empresa (D29), plano Tailscale, RPO/RTO, destinatario do email.
5. Reescrever o `README.md` quando o Plano 1 for executado.
