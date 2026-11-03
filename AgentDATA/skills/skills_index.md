# Indice de skills

Liste aqui as skills esperadas, disponiveis ou desejadas pelo projeto.

Pesquisa feita com `npx skills find` em 2026-11-02. Criterio de qualidade (find-skills): instalacoes >= 1K, origem credivel, repositorio com estrelas. Nada foi instalado; as desejadas aguardam decisao do utilizador (`npx skills add <pacote> -g -y`).

## Skills registradas

| Skill | Status | Quando carregar | Registro |
| --- | --- | --- | --- |
| `claude-api` (built-in) | instalada | Qualquer codigo ou decisao com a API Claude: modelos, precos, structured outputs, caching, batches. | origem local; sem registro proprio |
| `security-review` (built-in) | instalada | Revisao de seguranca de mudancas pendentes (webhooks, tokens, RGPD). | origem local |
| `writing-plans` / `brainstorming` (locais) | instalada | Antes de planear a Fase 0/1; antes de criar funcionalidades. | origem local |
| `fastapi/fastapi@fastapi` | desejada | Implementar backend, rotas, dependencias, testes FastAPI. | `skill_fastapi.md` |
| `wshobson/agents@postgresql-table-design` | desejada | Desenhar tabelas, indices, constraints (Anexo A). | `skill_postgresql_table_design.md` |
| `wshobson/agents@gdpr-data-handling` | desejada | Retencao, direitos dos titulares, minimizacao. | `skill_gdpr_data_handling.md` |
| `addyosmani/agent-skills@security-and-hardening` | desejada | Endurecer endpoints, segredos, dependencias. | `skill_security_and_hardening.md` |
| `coreyhaines31/marketingskills@content-strategy` | desejada | Calendario editorial e estrategia de conteudo (W2). | `skill_content_strategy.md` |
| `coreyhaines31/marketingskills@social-content` | desejada | Escrita de conteudo para redes (W3). | `skill_social_content.md` |
| `affaan-m/ecc@brand-voice` | desejada (rever antes) | Perfil de marca e voz (W1). | `skill_brand_voice.md` |

## Avaliadas e rejeitadas

| Skill | Instalacoes | Estrelas do repo | Motivo |
| --- | --- | --- | --- |
| `hoodini/ai-agents-skills@meta-ads` | 96 | 291, sem licenca | Abaixo do limiar; foco em anuncios |
| `moboutrig/instagram-claude-skill@instagram` e outras de Instagram/Meta | <= 24 | n/d | Abaixo do limiar |
| `patricio0312rev/skills@webhook-receiver-hardener` | 272 | 63 | Abaixo do limiar; ler apenas como inspiracao |
| `okou-ai/okou-skills@cloudflare-tunnel` | 891 | 81, sem licenca | Abaixo do limiar |

Lacuna: nao ha skill credivel para a Graph API da Meta. A fonte de verdade sao os registos em `AgentDATA/Knowledge/` (prefixo `meta_`) e a documentacao oficial.
