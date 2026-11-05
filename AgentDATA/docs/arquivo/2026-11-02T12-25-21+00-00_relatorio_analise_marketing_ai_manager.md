# Relatório de análise — Marketing AI Manager

- Timestamp: `2026-11-02T12:25:21+00:00`
- Autor: `agent`
- Assunto: Análise exaustiva da ideia (viabilidade, arquitetura, implementação, workflows, segurança, operação, custos, riscos) para discussão
- Documentos irmãos: [Anexo A — modelo de dados e contratos](2026-11-02T12-25-22+00-00_anexo_a_modelo_de_dados_e_contratos.md) · [Anexo B — especificação de workflows](2026-11-02T12-25-23+00-00_anexo_b_especificacao_de_workflows.md)
- Registos de conhecimento: `AgentDATA/Knowledge/2026-11-02T12-25-24…27_*.md`

> **Atualização (2026-11-02T14:23):** duas decisões do responsável alteram este relatório: agendamento sempre pelo nosso software (D1) e interações tratadas por triagem e notificação, sem resposta automática (D2). Ver [Adenda 01](2026-11-02T14-23-57+00-00_adenda_01_decisoes_agendamento_proprio_e_triagem.md), que prevalece onde houver conflito (W5, W7, W8, W9, níveis de autonomia, AI Act).

**Legenda de confiança** (usada em todo o documento)

| Marca | Significado |
| --- | --- |
| **[V]** | Verificado nesta sessão em documentação oficial (Meta, Anthropic) ou nos ficheiros do repositório. |
| **[S]** | Fonte secundária (blogs, resultados de pesquisa). Plausível, por confirmar. |
| **[I]** | Inferência ou estimativa minha. Tem de ser validada antes de ser tratada como facto. |

Nada do que se segue foi testado contra APIs reais: não há credenciais Meta nem código nesta fase.

---

## Sumário executivo

**Veredicto.** A ideia é viável e a arquitetura do resumo é, no essencial, sensata (monólito modular em FastAPI + PostgreSQL + worker, inferência na cloud, Raspberry Pi como anfitrião). O que o resumo não mostra é que **o maior risco do projeto não é técnico nem de hardware: é o acesso à plataforma Meta e as regras dela**. Várias funcionalidades descritas só são possíveis com limitações importantes.

**As seis conclusões que mais mudam o plano**

1. **Falta uma Fase 0.** Antes de construir, é preciso provar com chamadas reais: publicar uma imagem, um carrossel e um Reel numa conta de teste; receber um webhook de DM e de comentário; e descobrir se, para uso interno, o *Standard Access* basta ou se é preciso App Review e Business Verification. A documentação é ambígua sobre os webhooks de comentários **[V]**. Uma semana aqui poupa meses de retrabalho **[I]**.
2. **O follow-up automático de leads por DM não existe.** O bot só pode escrever depois de o cliente escrever, dentro de 24 h (7 dias em casos de anúncios Click-to-Direct); a tag *Human Agent* (7 dias) é para pessoas, não para automação **[V/S]**. Qualquer "reativação" de leads tem de usar outro canal (email, telefone, com base legal RGPD) ou intervenção humana.
3. **O Instagram não tem agendamento nativo na API** **[V]**. O Facebook tem (10 min a 30 dias) **[V]**. Proposta: delegar o agendamento do Facebook à Meta (sobrevive a quedas do Pi) e ter um scheduler próprio, com política explícita de "atraso", para o Instagram.
4. **A media tem de estar num URL público** quando se publica no Instagram **[V]**. Com um Pi atrás de Cloudflare Tunnel isto obriga a escolher entre expor ficheiros do Pi ou usar armazenamento de objetos externo. É uma decisão de arquitetura que o resumo não tocou.
5. **Comentários e DMs são texto não confiável.** O risco real do atendimento automático não é "a IA responder mal", é *prompt injection* e a IA afirmar preços ou condições inventados. A defesa tem de ser estrutural: o modelo nunca escreve preços (preenchem-se campos validados depois), nunca tem ferramentas com efeitos reais acessíveis a texto de terceiros, e começa em modo sombra (rascunhos aprovados por humanos).
6. **O Raspberry Pi serve, mas é um ponto único de falha.** A Meta não guarda histórico de webhooks **[V]**; se o Pi estiver em baixo, eventos podem perder-se. Mitigações possíveis vão de "aceitar o risco com reconciliação por polling" até um buffer barato na borda (ex.: Cloudflare Worker + fila) **[I]**. Compose portátil e backups externos tornam a mudança para um VPS pequeno trivial se o Pi não aguentar.

**Riscos mais altos (detalhe na secção 14):** acesso/aprovação Meta (R1); restrição da conta pela Meta por automação mal comportada (R2); injeção de prompt e alucinação comercial (R3); perda de eventos com o Pi em baixo (R4); expiração silenciosa de tokens (R5); conformidade RGPD/AI Act no tratamento de mensagens (R6).

**O que preciso que decidas** (lista priorizada na secção 15): setor e volumes; se as contas Facebook/Instagram já são profissionais e ligadas a um Business Manager; painel web vs chat; Python vs TypeScript (há um conflito com o AGENTS.md); provedor de LLM e residência de dados; estratégia de media; e se Reels entram no MVP.

---

## Contexto

### Fonte e escopo

Este relatório analisa o [resumo do projeto](../../resumo_marketing_ai_manager.md), o `AGENTS.md`, o `README.md` (ainda em estado *seed*) e a estrutura `AgentDATA/` do repositório. Para os factos de plataforma, consultei documentação oficial da Meta e a skill `claude-api` (preços e comportamento de modelos), e pesquisei skills no ecossistema com o `find-skills`.

### Skills: o que o `find-skills` encontrou

Pesquisei por Meta/Instagram, FastAPI, marketing, PostgreSQL, RGPD, webhooks, Docker/Pi, Cloudflare Tunnel, agendamento, atendimento, voz de marca, prompts, calendário editorial. Critério do próprio skill: instalações ≥ 1K, origem credível, repositório com estrelas.

**Achado importante: não existe nenhuma skill credível para a Graph API da Meta.** As melhores candidatas (`hoodini/ai-agents-skills@meta-ads` com 96 instalações e 291★; skills de Instagram com 24, 8 e 6 instalações) ficam abaixo do limiar de confiança. Para a parte Meta, a fonte de verdade tem de ser a documentação oficial, daí os registos em `Knowledge/`.

| Skill | Instalações | Repositório (★) | Decisão | Uso no projeto |
| --- | --- | --- | --- | --- |
| `fastapi/fastapi@fastapi` | 14,2K | 104 557 (oficial) | **Recomendada** | Backend |
| `wshobson/agents@postgresql-table-design` | 28,3K | 42 097 | **Recomendada** | Modelo de dados |
| `wshobson/agents@gdpr-data-handling` | 15,2K | 42 097 | **Recomendada** | RGPD (não substitui jurista) |
| `addyosmani/agent-skills@security-and-hardening` | 76,1K | 112 677 | **Recomendada** | Endurecimento |
| `coreyhaines31/marketingskills@content-strategy` | 169,2K | 57 957 | **Recomendada** | Calendário e estratégia editorial |
| `coreyhaines31/marketingskills@social-content` | 67K | 57 957 | **Recomendada** | Conteúdo para redes |
| `affaan-m/ecc@brand-voice` | 10,2K | 295 153 | Recomendada com revisão | Perfil de marca |
| `wshobson/agents@prompt-engineering-patterns` | 23,9K | 42 097 | Opcional | Já coberto pela skill `claude-api` |
| `patricio0312rev/skills@webhook-receiver-hardener` | 272 | 63 | **Rejeitada** (abaixo do limiar) | — ler só como inspiração |
| `okou-ai/okou-skills@cloudflare-tunnel` | 891 | 81, sem licença | **Rejeitada** | — |
| Skills Instagram/Meta | ≤ 96 | ≤ 291 | **Rejeitadas** | — |

Já disponíveis localmente e relevantes: `claude-api` (modelos, preços, structured outputs, caching), `security-review`, `code-review`, `writing-plans`, `brainstorming`. **Não instalei nada**: registei as candidatas em `AgentDATA/skills/` como *desejadas*, e a instalação fica à tua decisão (`npx skills add <pacote> -g -y`).

### Lacunas e conflitos no que existe

- O `README.md` ainda é o *seed* e manda parar e perguntar o que é o projeto, a stack e os comandos. As respostas estão em parte no resumo; comandos de setup/validação só fazem sentido depois das decisões da secção 15.
- **Conflito de convenções:** o `AGENTS.md` diz *"se não houver convenção, preferir camelCase"* e o `AgentDATA/scripts/` é em TypeScript; o backend proposto é Python, cuja convenção idiomática é `snake_case` (PEP 8). Proposta: backend Python segue PEP 8 (a regra do AGENTS.md remete para "padrão idiomático da linguagem" noutros pontos); `AgentDATA/scripts/` fica TypeScript para utilitários auxiliares. Decisão tua (secção 15).
- O repositório **não é um repositório Git** (não há `.git`). O `AGENTS.md` exige commits; antes de implementar, convém `git init` e primeiro commit do seed.
- O resumo lista como decisão em aberto "painel web vs chat". Analiso abaixo.

---

## Decisão ou análise

### 1. Viabilidade por funcionalidade

| # | Funcionalidade (resumo) | Viável com API oficial? | Principais restrições | Risco | Nota |
| --- | --- | --- | --- | --- | --- |
| 1 | Planeamento de campanhas e calendário | Sim (100 % interno) | Nenhuma da Meta | Baixo | Valor está na qualidade do perfil de marca |
| 2 | Criação de textos | Sim | Marcação de conteúdo gerado por IA (`is_ai_generated`) **[V]**; limites de legenda/hashtags por validar na Fase 0 | Baixo | |
| 2b | Criação de imagens | Sim | Instagram só aceita JPEG **[V]**; texto dentro de imagens gerado por IA é pouco fiável **[I]** | Médio | Sobrepor logótipo/texto com código, não com o modelo |
| 2c | Criação de Reels/vídeo | Parcial | Specs de Reels não documentadas na página oficial **[V]**; o Pi 5 não tem encoder H.264 por hardware **[I]**; vídeo por IA é caro e inconsistente | Alto | Fora do MVP: guião + upload humano do vídeo |
| 3 | Publicar no Instagram | Sim | Fluxo em 2 passos; URL público; ≤ 100 (ou 50) posts/24 h; sem agendamento nativo **[V]** | Médio | |
| 3b | Publicar no Facebook (Página) | Sim | Agendamento nativo 10 min–30 dias **[V]**; vídeo tem fluxo à parte | Baixo | |
| 4 | Atendimento a DMs | Sim, com limites | Só após o utilizador escrever; janela 24 h; 1000 bytes; sem grupos **[V]** | Médio | Divulgar que é automático |
| 4b | Resposta a comentários | Sim | *Private reply*: 1 por comentário, ≤ 7 dias **[V]** | Médio | Resposta pública + privada |
| 5 | Gestão de leads | Parcial | Follow-up por DM fora da janela impossível **[V/S]**; RGPD | Médio | CRM-lite interno |
| 6 | Métricas e relatórios | Sim | Métricas são retiradas/renomeadas com frequência **[S]**; histórico limitado **[I]** | Médio | Guardar *snapshots* diários e payload bruto |

**Fora do âmbito mas que costuma aparecer:** anúncios pagos (Marketing API), WhatsApp (API própria com regras próprias), TikTok/LinkedIn, moderação em massa. Não analisados; ver perguntas na secção 15.

### 2. Alternativas "construir vs. comprar"

O resumo já decidiu "à medida". Convém explicitar o que se ganha e perde, porque a concorrência gratuita é forte.

| Opção | Ganha | Perde |
| --- | --- | --- |
| **Meta Business Suite** (nativo, gratuito) | Agendamento, inbox unificada, respostas automáticas básicas, zero manutenção | Sem IA com perfil de marca, sem aprovações com auditoria, sem relatórios próprios |
| **Ferramentas SaaS de agendamento** (Buffer, Later, etc.) | Publicação robusta e multicanal | Custo mensal, IA genérica, dados fora |
| **Postiz auto-alojado** | Calendário/agendamento prontos | Pesado para um Pi (o resumo já o descarta) **[V]**; esquema próprio de dados |
| **ManyChat/n8n para DMs** | Fluxos de DM visuais e rápidos | Lógica de IA limitada, custo/lock-in; n8n é razoável como cola |
| **Construir à medida** (o plano) | IA fundamentada na marca, aprovações auditáveis, dados teus, métricas cruzadas | Tempo de desenvolvimento e manutenção contínua da integração Meta |

Conclusão: construir à medida só compensa se o valor estiver nas partes onde o SaaS não chega: **perfil de marca + aprovação + atendimento fundamentado + relatórios**. Recomendo medir o MVP contra "usar o Business Suite + um chat com IA" e confirmar que a diferença justifica a manutenção. A decisão já é tua; fica o critério.

### 3. Restrições da plataforma Meta (factos que condicionam o desenho)

Detalhe e evidência em `AgentDATA/Knowledge/` (registos `meta_*`).

#### 3.1 Três variantes de integração do Instagram **[V]**

| Setup | Host | Token | Permissões-chave | Cobre Facebook? |
| --- | --- | --- | --- | --- |
| Instagram Login | `graph.instagram.com` | Token de utilizador Instagram (60 dias) | `instagram_business_*` | Não |
| Facebook Login | `graph.facebook.com` | Page token | `instagram_basic`, `instagram_content_publish`, `pages_*` | Sim |
| Unified Login | `graph.facebook.com` | System User token | `instagram_basic`, etc. | Sim |

**Recomendação [I]:** como o projeto precisa de Facebook **e** Instagram, usar **Facebook Login ou Unified Login** (uma só app Meta cobre os dois). Instagram Login só faria sentido num produto só de Instagram. Um *System User* num Business Manager tende a dar tokens mais estáveis para uso empresarial; a duração exata **por confirmar na Fase 0**.

#### 3.2 Níveis de acesso, App Review e verificação **[V/S]**

- *Standard Access* funciona para contas próprias ou adicionadas à app no dashboard **[V]**.
- *Advanced Access* exige App Review e é necessário para servir contas sem papel na app **[V]**.
- Webhook de comentários: "Advanced Access is required" **[V]**; a app tem de estar em modo *Live* **[V]**.
- *Business Verification* necessária quando a app serve utilizadores sem papel na app **[V]**.
- Apps Meta exigem tipicamente política de privacidade pública e URL de callback de eliminação de dados **[I]**, a confirmar.

**Pergunta crítica da Fase 0:** para **uma única empresa, uso interno**, dá para ficar em Standard Access e ainda receber webhooks de comentários? Se não, o projeto passa a incluir App Review (screencasts, justificação de cada permissão, prazos fora do teu controlo **[I]**).

#### 3.3 Publicação no Instagram **[V]**

- Fluxo: `POST /<IG_ID>/media` (cria *container*) → opcionalmente poll de `status_code` (máx. 1×/min, até 5 min) → `POST /<IG_ID>/media_publish`.
- Tipos: imagem, vídeo, Reels, Stories, carrossel até 20 itens. Imagem tem de ser **JPEG**.
- Limite: **100 posts/24 h** móveis por conta (carrossel = 1), mas a mesma página diz **50** na secção de carrosséis. Assumir 50 até teste. Consultar `GET /<IG_ID>/content_publishing_limit`.
- Container expira em 24 h (`EXPIRED`).
- Campos úteis: `alt_text` (imagens), `is_ai_generated=true`, `trial_params` (Trial Reels), `user_tags` (detalhe pouco documentado), parcerias pagas (só Facebook Login).
- Não suportados: *shopping tags*, filtros. Upload *resumable* só com Facebook Login for Business.
- **Sem especificações oficiais de Reels** na página (duração, rácio, tamanho, codec): descobrir por teste.

#### 3.4 Publicação em Páginas Facebook **[V]**

`POST /{page_id}/feed` com `published=false` e `scheduled_publish_time` (entre 10 min e 30 dias); fotos em `/{page_id}/photos`; vídeo e Reels têm guias próprios. Permissões `pages_manage_posts`, `pages_read_engagement`, etc.; Page token. Só se atualizam/apagam posts criados pela própria app.

#### 3.5 Mensagens e comentários **[V]**

- Janela: 24 h após a mensagem do utilizador; até 7 dias se veio de anúncio Click-to-Direct; janela *Human Agent* de 7 dias para respostas humanas.
- Envio: `POST /v26.0/<IG_ID>/messages`; texto ≤ **1000 bytes**; imagens ≤ 8 MB; áudio/vídeo/PDF ≤ 25 MB; sem grupos.
- *Private reply* a comentário: ≤ 7 dias; **uma** mensagem por comentador; follow-up só se o utilizador responder, nas 24 h seguintes.
- Pedidos (*Requests*) inativos há 30 dias não são devolvidos.
- Divulgação de automação: a Meta indica que chats automáticos devem declarar que o são quando a lei o exigir e recomenda fazê-lo sempre.
- Handover: uma app → inbox própria; várias apps → *Handover Protocol*.

#### 3.6 Webhooks **[V/S]**

Handshake GET (`hub.verify_token` → `hub.challenge`); assinatura `X-Hub-Signature-256` (HMAC-SHA256 com App Secret, sobre corpo **bruto**); resposta rápida; **entregas duplicadas**; sem histórico de eventos; subscrição por conta (`subscribed_apps`); app em *Live*; sem personalização por conta. Política de reenvio da Meta quando o endpoint falha: **não verificada** nesta sessão.

#### 3.7 Rate limits **[V]**

| API | Limite |
| --- | --- |
| Send API (texto) | 100 chamadas/s/conta (outra página diz 300) |
| Send API (áudio/vídeo) | 10/s |
| Conversations API | 2/s/conta |
| Private replies | 100/s (Live), 750/h (posts e reels) |
| Plataforma (não-mensagens) | 4800 × impressões / 24 h por par app-utilizador |
| Páginas | 4800 × utilizadores envolvidos / 24 h |

Cabeçalhos `X-App-Usage` e `X-Business-Use-Case-Usage` (inclui `estimated_time_to_regain_access`). Códigos 4, 17, 32, 613 e BUC 80001/80002/80006. **A Meta recomenda parar** ao atingir o limite. Para uma PME, estes limites raramente são o gargalo; o gargalo é a regra "1 private reply por comentário" e a janela de 24 h.

#### 3.8 Versões e deprecações **[S]**

Graph API **v26.0** (2026-07-29); v25.0 até 2028-07-29; v19.0 e v20.0 já terminaram. **Fixar a versão numa constante**, ter tarefa trimestral de revisão do *changelog* e testes de contrato que falhem de forma ruidosa. Métricas do Instagram/Marketing API são retiradas/renomeadas com frequência **[S]**: o modelo de métricas tem de ser versionado (secção 6).

#### 3.9 Ciclo de vida dos tokens **[V/S]**

- Instagram Login: token longo = 60 dias; *refresh* só com ≥ 24 h de idade e antes de expirar; troca e *refresh* só no servidor (usam App Secret) **[V]**.
- Page tokens: fontes divergem (60 dias vs. "não expiram"). **Tratar como expiráveis**, ler `expires_at` com `debug_token` e alertar a D-14/D-7.
- Acesso a dados de utilizador pode exigir renovação periódica ("data access") **[I]**.

#### 3.10 Políticas e automação **[S]**

Publicar e responder via API oficial é permitido; automação que imita comportamento humano (follow/unfollow) não. Meta tem etiquetas de IA e está a limitar alcance de perfis com pessoa gerada por IA sem etiqueta **[S]**; para conteúdo, o campo `is_ai_generated` existe na API **[V]**. Rever a política vigente antes de lançar.

---

### 4. Arquitetura proposta (detalhe)

#### 4.1 Princípios

1. **Monólito modular**, uma imagem Docker, dois processos (`web` e `worker`). Sem microserviços.
2. **PostgreSQL como única fonte de verdade e como fila** (sem Redis), via `SELECT … FOR UPDATE SKIP LOCKED` **[I]**.
3. **Tudo o que escreve no mundo exterior é assíncrono, idempotente e auditado** (publicar, enviar DM).
4. **Humano no circuito por omissão**; autonomia sobe por níveis, com *kill switch*.
5. **Estado explícito**: máquinas de estados para posts, conversas, leads, aprovações e *jobs* (Anexo A).
6. **Payload bruto guardado** (webhooks e respostas da Meta) antes de qualquer interpretação.
7. **Portabilidade**: Docker Compose idêntico no PC, no Pi e num VPS.

#### 4.2 Componentes

```text
                       ┌──────────────── Cloudflare (DNS, Tunnel, Access, WAF) ────────────────┐
                       │                                                                         │
  Browser (equipa) ────┤  /app/*  (Cloudflare Access: SSO/MFA)                                   │
  Meta webhooks ───────┤  /hooks/meta  (aberto, assinatura HMAC, rate-limit WAF)                 │
  Meta busca media ────┤  /media/*     (URLs assinados, só leitura)  — ou bucket externo         │
                       └──────────────────────────────────┬──────────────────────────────────────┘
                                                          │ cloudflared (container)
┌─────────────────────────── Raspberry Pi 5 / Docker Compose ───────────────────────────────────┐
│  web (FastAPI)                          worker (mesma imagem)                  postgres         │
│  ├ painel HTMX+Jinja                    ├ jobs: ingest_event, plan, generate,  ├ dados + fila   │
│  ├ API interna                          │  publish, reply, insights, report,   └ backups → off- │
│  ├ /hooks/meta (só valida e grava)      │  token_refresh, reconcile              site          │
│  └ /media (assinados)                   └ chama: Meta Graph API, LLM, storage                   │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Regra de ouro do endpoint de webhooks [I]:** validar assinatura, gravar o corpo bruto numa tabela `webhook_events` com chave única, devolver 200, e **nada mais**. O processamento é um *job*. Isto cumpre o requisito de resposta rápida da Meta, torna replays triviais e isola falhas do LLM.

#### 4.3 Estrutura de código (proposta)

```text
src/marketing_agent/
  domain/            # entidades, máquinas de estados, regras puras (sem I/O)
  application/       # casos de uso: plan_week, approve_post, handle_message…
  agents/            # prompts, schemas de saída, orquestração das chamadas LLM
  integrations/
    meta/            # cliente Graph API, assinatura, mapeamento de payloads
    llm/             # interface LlmClient + adaptadores (Anthropic, OpenAI)
    storage/         # media: local assinado | S3/R2
  infrastructure/    # db (SQLAlchemy), fila Postgres, config, logging, segredos
  interfaces/
    web/             # FastAPI: rotas, templates, auth
    worker/          # runner de jobs, scheduler
  migrations/        # Alembic
tests/               # unit, contract (fixtures Meta), integration, evals
```

Separação cumpre o `AGENTS.md` (domínio / infraestrutura / interface; funções ≤ 25–40 linhas; sem `dict`/`Any` sem justificação → modelos Pydantic e `TypedDict`).

#### 4.4 Stack: decisões e alternativas

| Área | Escolha | Alternativas | Porquê |
| --- | --- | --- | --- |
| Linguagem | Python 3.12+ | TypeScript | Ecossistema de IA e FastAPI; conflito de convenção a decidir |
| Web | FastAPI + Uvicorn (1–2 workers) | Django | FastAPI já escolhido; leve; tipagem com Pydantic v2 |
| ORM/migrações | SQLAlchemy 2 + Alembic + psycopg 3 | SQLModel | Maturidade, controlo de transações |
| Fila/agendador | Fila em Postgres (tabela `jobs`, `SKIP LOCKED`) ou lib como *procrastinate* | APScheduler, Celery+Redis, n8n | Evita Redis e um 2.º estado; transacional com o domínio. Estado atual do APScheduler 4.x **por verificar** |
| HTTP cliente | HTTPX (async) com timeouts e retries explícitos | requests | Já referido no resumo |
| LLM | Interface `LlmClient`; adaptador Anthropic primeiro | SDK OpenAI, Bedrock | Ver secção 5 |
| Painel | Jinja2 + HTMX (+ pouco JS) | React/Next, SPA | Menos RAM/CI no Pi, uma só stack, bastante para calendário/aprovações |
| Autenticação | Cloudflare Access (SSO/MFA) à frente + sessão local com papéis | Auth própria completa | Menos superfície de ataque; webhooks ficam fora do Access |
| Logs | `structlog` em JSON | logging padrão | Contrato de logs do `AgentDATA` |
| Qualidade | `ruff`, `pyright`/`mypy` estrito, `pytest`, `pytest-asyncio` | | AGENTS.md: typecheck + lint + testes |
| Segredos | Docker secrets/ficheiros fora do Git; tokens Meta cifrados na BD (Fernet/age) com chave fora da BD | Vault | Proporcional ao tamanho |

#### 4.5 Media: onde vivem os ficheiros **[V/I]**

O Instagram tem de **descarregar** a imagem/vídeo de um URL público no momento da publicação **[V]**.

| Opção | Prós | Contras |
| --- | --- | --- |
| **A. Servir do Pi** em `/media/<id>?sig=…` via Tunnel | Sem serviço extra, dados locais | Pi em baixo ⇒ publicação falha; vídeos pesados no uplink de casa; mais superfície exposta |
| **B. Armazenamento de objetos** (ex.: Cloudflare R2, S3, B2) com URLs assinados | Disponível 24/7, escala, o Pi só envia | Custo baixo mas existe; mais uma credencial |
| **C. Híbrido** | Pi guarda master; publica cópia em B por post | Dois locais para gerir |

Recomendação **[I]**: **B** para tudo o que vai ser publicado, com limpeza após N dias; Pi guarda apenas metadados e miniaturas. Aceitável começar com A na Fase 1 se o MVP for só imagens.

#### 4.6 Resiliência de ingestão de webhooks **[I]**

Se o Pi estiver em baixo, a Meta pode desistir de reenviar (política **não verificada**) e não há histórico **[V]**. Camadas:

1. **Reconciliação por polling**: job periódico que lista comentários/conversas recentes e cria eventos em falta (cobre qualquer perda; custa chamadas à API).
2. **Buffer na borda** (opcional): Cloudflare Worker + fila que valida assinatura, enfileira e o Pi consome. Custo/complexidade baixos, mas é mais um componente.
3. **Aceitar o risco** no MVP e medir: a primeira semana mostra quantos eventos se perderiam.

Recomendo 1 desde o início e 2 só se a Fase 3 for crítica para o negócio.

#### 4.7 Execução no Raspberry Pi 5 (8 GB) **[I]**

**Hardware**: alimentação oficial 27 W (5 V/5 A), arrefecimento ativo, SSD (NVMe via HAT ou SATA/USB) em vez de microSD, Ethernet, OS 64-bit. UPS pequeno ou, no mínimo, `fsync` seguro e backup externo. Relógio sincronizado (NTP): assinaturas, agendamento e *tokens* dependem de hora certa.

**Orçamento de memória (estimativas, medir!)**

| Serviço | RAM típica | Limite Compose sugerido |
| --- | --- | --- |
| PostgreSQL 16/17 | 300–800 MB | 1,5 GB (`shared_buffers` ~512 MB–1 GB) |
| `web` (FastAPI, 1–2 workers) | 150–300 MB | 512 MB |
| `worker` | 150–400 MB (picos com Pillow/ffmpeg) | 1 GB |
| `cloudflared` | 30–60 MB | 128 MB |
| SO + Docker | ~600 MB | — |
| **Total típico** | **~1,5–2,5 GB** | folga ampla em 8 GB |

Conclusão: o Pi **aguenta** esta arquitetura com folga **[I]**; os limites reais são I/O de disco, CPU em processamento de imagem/vídeo e disponibilidade (energia/Internet).

**Operação**: `restart: unless-stopped`, *healthchecks*, rotação de logs (evitar desgaste do SSD), `unattended-upgrades` para segurança, imagens `arm64` multi-arch (`docker buildx` no PC), *watchdog* de hardware, *dead-man's switch* externo (healthchecks.io ou semelhante: o Pi "pinga" a cada N min; se parar, alerta).

**Backups** (RPO/RTO a decidir): `pg_dump` noturno cifrado → armazenamento externo (restic para B2/R2), retenção 7 diários + 4 semanais + 6 mensais; arquivar WAL se RPO < 24 h for necessário; **teste de restauro trimestral** documentado em `AgentDATA/procedures/`.

**Pi vs VPS pequeno [I]**: o Pi custa zero recorrente e mantém os dados em casa; um VPS ARM/x86 barato dá IP estável, SLA e independência de energia/ISP. Com Cloudflare Tunnel o IP deixa de importar; o argumento restante é **disponibilidade**. Como o stack é portátil, começar no Pi não fecha portas. Critério de migração sugerido: > 2 quedas não planeadas/mês ou necessidade de SLA.

#### 4.8 Ambientes e CI

- **dev**: PC Windows com Docker Desktop; Meta em modo *Development* com contas de teste.
- **staging**: o próprio Pi com uma segunda base de dados e uma segunda app Meta de teste **[I]**.
- **prod**: Pi (ou VPS) com app Meta *Live*.
- CI (quando houver Git/remote): lint, typecheck, testes, build multi-arch, `pip-audit`, verificação de migrações.

---

### 5. Camada de IA: desenho e custos

#### 5.1 Padrão: *workflows* com chamadas LLM, não agente autónomo

Pela regra "começar simples": cada capacidade é uma **chamada (ou pequena cadeia) com entrada e saída tipadas**, orquestrada por código. Só o planeamento de campanhas tem decisão aberta suficiente para justificar um ciclo com ferramentas (pesquisa web, consulta de histórico). O "agente" percebido pelo utilizador é a soma de workflows determinísticos mais um orquestrador de planeamento.

#### 5.2 Catálogo de capacidades

| Capacidade | Entrada | Saída (esquema) | Modelo sugerido | Porta humana |
| --- | --- | --- | --- | --- |
| `plan_week` | perfil de marca, objetivos, histórico de desempenho, eventos/datas | lista de slots (data, canal, formato, tema, objetivo) | Opus 5.5, esforço médio/alto | Aprovação do plano |
| `ideate_post` | slot + KB de produtos | 3 ângulos | Sonnet 5.5 | — |
| `write_caption` | ângulo + regras de marca + KB | legenda, hashtags, CTA, alt text | Sonnet 5.5 | Aprovação do post |
| `adapt_to_channel` | legenda mestre | variantes FB/IG | Haiku/Sonnet 5.5 | Aprovação |
| `image_brief` | post | prompt de imagem + instruções de composição | Sonnet 5.5 | Aprovação |
| `script_reel` | post | guião cena a cena + legendas + áudio sugerido | Sonnet 5.5 | Aprovação |
| `classify_message` | mensagem (sanitizada) + contexto curto | intenção, urgência, sentimento, idioma, risco, PII | **Haiku 5.5** | — |
| `draft_reply` | conversa + KB + política | rascunho com *slots* de preço/stock | Sonnet 5.5 | Nível de autonomia |
| `verify_reply` | rascunho + KB + regras | aprova/reprova + motivos | Haiku/Sonnet 5.5 | Automática |
| `extract_lead` | conversa | campos de lead + consentimento | Haiku 5.5 | — |
| `summarize_thread` | conversa longa | resumo para handover | Haiku 5.5 | — |
| `weekly_report` | métricas **já agregadas por SQL** | narrativa + recomendações | Opus 5.5 ou Sonnet 5.5 | Revisão |

**Regra [I]:** números nunca são calculados pelo LLM; chegam pré-agregados e o LLM só narra e sugere.

#### 5.3 Modelos e custo **[V para preços, I para volumes]**

Preços por 1M tokens (entrada/saída; cache de 2026-10-06 na skill `claude-api`): **Haiku 5.5** 0,10/0,50 USD; **Sonnet 5.5** 2/10; **Opus 5.5** 4/20; **Fable 5.1** 10/50. Cache de leitura ≈ 0,20 USD/MTok em Sonnet/Opus; *Batches* a 50 %.

Cenário ilustrativo (premissas minhas): 

| Atividade | Premissa por unidade | Custo unitário aprox. | Volume | Custo/mês aprox. |
| --- | --- | --- | --- | --- |
| Classificar mensagem (Haiku) | 2K in + 300 out | 0,00035 USD | 3 000 | ~1 USD |
| Redigir resposta (Sonnet) | 4K in + 400 out | 0,012 USD | 3 000 | ~36 USD |
| Plano semanal (Opus) | 20K in + 6K out | 0,20 USD | 4 | ~1 USD |
| Legendas (Sonnet) | 6K in + 1,5K out | 0,027 USD | 60 | ~2 USD |
| Relatório semanal (Opus) | 15K in + 4K out | 0,14 USD | 4 | ~0,6 USD |

**Total de texto: ordem de 40–50 USD/mês para 1 000 conversas de 3 turnos**, antes de *thinking* e de *caching*. *Tokens* de *thinking* contam como saída, e o Opus 5.5 e os modelos Fable não permitem desligar *thinking* **[V]**, por isso **fixar o esforço explicitamente por capacidade** (o padrão do Opus 5.5 é `medium`) e multiplicar estas estimativas por 1,5–3 como margem. Com *prompt caching* do perfil de marca/KB e *batches* para tarefas não urgentes, reduz-se bastante. **Imagens e vídeo** não estão incluídos: preço dependente do provedor, **por verificar**.

**Guarda de custo [I]:** orçamento diário/mensal por capacidade; *circuit breaker* que passa o atendimento a modo "só rascunho" e avisa quando se ultrapassa.

**Fable 5.1 não se justifica**: 2,5× o preço do Opus 5.5 e **não está disponível com ZDR** **[V]**.

#### 5.4 Boas práticas de implementação com a API Claude **[V]**

- Saídas estruturadas com `output_config.format` / `messages.parse()` e Pydantic em vez de *parsing* de texto. Em Opus 5.5/Fable 5.1, `tool_choice` forçado devolve 400: usar `auto` + `strict: true` ou saídas estruturadas.
- Sem *prefill*; sem `temperature`/`top_p`/`top_k` não-default em vários modelos.
- Tratar `stop_reason == "refusal"` e considerar `fallbacks`.
- *Prompt caching*: prefixo estável (instruções + perfil de marca + KB) primeiro; variável (mensagem do cliente) no fim. Verificar `cache_read_input_tokens`. Prefixo mínimo cacheável depende do modelo.
- Streaming para saídas longas; registar `usage` em cada chamada.
- Ferramenta *web search* do servidor para pesquisa de tendências no `plan_week` (restrita por domínios).
- Quando for escrever o código, ler `python/claude-api/README.md` e `tool-use.md` da skill; **não** reconstruir de memória.

#### 5.5 Conhecimento da marca e "informação validada"

- **Perfil de marca** estruturado: voz e tom, vocabulário permitido/proibido, valores, público, *do/don't*, exemplos aprovados, hashtags, regras de emojis, idiomas, avisos legais.
- **Base de conhecimento (KB)** em tabelas: produtos/serviços, FAQs, políticas (devoluções, horários, entregas), contactos, campanhas ativas. Com centenas de itens cabe no contexto e fica em cache; **não é preciso vetor/pgvector no MVP** **[I]**. Adicionar RAG só quando o volume obrigar.
- **Preços e disponibilidade: *slots* fundamentados.** O modelo escreve `{{price:SKU123}}`; um passo determinístico substitui pelo valor da tabela com data de validade. Se o valor não existir ou estiver caducado, a resposta **não sai** e vai para humano. Isto resolve "não inventar preços" por construção, não por instrução.
- Fonte de verdade dos preços: a decidir (folha, ERP, painel). **Pergunta aberta.**

#### 5.6 Pipeline de atendimento e guardrails

```text
mensagem → sanitizar → classificar → política (regras determinísticas) →
   ├ escalar a humano (reclamação, jurídico, saúde, reembolso, negociação de preço,
   │   menor, ódio/ameaça, pedido de falar com pessoa, baixa confiança, fora da janela)
   └ redigir → verificar (sem afirmações fora da KB, sem preços fora de slots,
              ≤ 1000 bytes, idioma, tom, sem links/menções fora de allowlist) →
        nível de autonomia → enviar | fila de aprovação
```

**Níveis de autonomia [I]:** L0 modo sombra (só rascunhos; humano envia); L1 envio automático apenas para FAQs numa lista branca; L2 envio automático alargado com amostragem de revisão. Cada nível exige métricas mínimas (secção 10) antes de subir. *Kill switch* global e por conta.

**Anti-*loop* e abuso:** não responder a si próprio nem a contas marcadas como *bot*; deduplicar por `mid`; limite de respostas por utilizador/hora; `cool-down` após escalamento; deteção de *spam* e de utilizadores abusivos (silenciar, não apagar).

**Handover bot ↔ humano [I]:** `conversation.owner ∈ {bot, human}`; qualquer ação humana (resposta na inbox própria, ou eco de mensagem enviada na app nativa, se o webhook o der) passa o dono a `human`, o bot cala-se e só regressa por ação explícita ou após N horas sem atividade. **Por confirmar** se as respostas feitas na app nativa chegam por webhook.

**Divulgação:** primeira mensagem automática identifica-se como assistente automático e indica como falar com uma pessoa (cumpre a recomendação da Meta e o Art. 50 do AI Act **[V/S]**).

#### 5.7 Injeção de prompt e segurança do LLM

Todo o texto de clientes é **dados, nunca instruções**.

- O LLM que redige **não tem ferramentas** com efeitos; devolve texto estruturado.
- Quem executa efeitos (enviar, atualizar lead, escalar) é código determinístico que valida o esquema e a política.
- Sem seguir URLs enviados por utilizadores; sem executar código.
- Filtro de saída: *allowlist* de domínios, remoção de `@menções` e de números de telefone/IBAN não presentes na KB.
- Limites de tamanho de entrada; truncagem **declarada** (avisar quando houver truncagem; nunca silenciosa).
- Conjunto de testes *red team* (instruções escondidas, "ignora as regras", pedidos de descontos, exfiltração de prompt) a correr em CI.

#### 5.8 Imagens e vídeo

- **Imagens:** gerar o fundo/ilustração com modelo de imagem; **compor texto e logótipo por código** (Pillow) para exatidão tipográfica; exportar JPEG; guardar *prompt*, modelo e semente; marcar `is_ai_generated=true` quando for caso. Alternativa: banco de imagens próprio da marca.
- **Vídeo/Reels:** o Pi 5 não tem encoder H.264 por hardware **[I]**; codificar com `ffmpeg` em CPU é lento. Opções por ordem de simplicidade: (1) *upload* humano do vídeo com legenda/guião gerados; (2) montagem automática a partir de clips próprios com render fora do Pi; (3) serviço de vídeo gerado por IA (custo e coerência de marca incertos). **MVP = (1).**

---

### 6. Modelo de dados (síntese)

Detalhe completo, estados, índices, SQL-chave e contratos no [Anexo A](2026-11-02T12-25-22+00-00_anexo_a_modelo_de_dados_e_contratos.md).

Entidades: `brand_profile`, `kb_item`, `social_account`, `credential` (tokens cifrados + `expires_at`), `campaign`, `content_plan`, `post` (+ `post_variant`, `media_asset`), `approval`, `publish_attempt`, `webhook_event`, `contact`, `conversation`, `message`, `lead`, `comment`, `insight_snapshot`, `report`, `job`, `audit_log`, `llm_call`, `cost_ledger`.

Decisões de desenho: UTC na BD e `Europe/Lisbon` na apresentação; *soft delete* + política de retenção para dados pessoais; hash do conteúdo aprovado (`approved_content_hash`) igual ao publicado; chaves de idempotência únicas por evento e por tentativa de publicação; JSON bruto (`jsonb`) para respostas Meta + colunas normalizadas para o que se consulta.

---

### 7. Workflows (síntese)

Especificação completa (gatilhos, passos, falhas, idempotência, métricas) no [Anexo B](2026-11-02T12-25-23+00-00_anexo_b_especificacao_de_workflows.md).

| ID | Workflow | Gatilho | Resultado |
| --- | --- | --- | --- |
| W1 | Onboarding de marca e KB | Manual | Perfil de marca e KB validados |
| W2 | Planeamento semanal | Pedido/agenda | Plano aprovado |
| W3 | Geração de conteúdo | Slot do plano aprovado | Posts em rascunho |
| W4 | Aprovação | Rascunho pronto | Post aprovado (hash fixo) |
| W5 | Agendamento e publicação | Post aprovado | Publicado ou falha tratada |
| W6 | Ingestão de webhooks | Evento Meta | Evento gravado e *job* criado |
| W7 | Atendimento a DMs | Mensagem recebida | Resposta ou escalamento |
| W8 | Comentários | Comentário recebido | Resposta pública/privada, moderação |
| W9 | Leads | Conversa qualificada | Lead criado/atualizado |
| W10 | Recolha de métricas | Diário | *Snapshots* |
| W11 | Relatórios | Semanal/mensal | Relatório e recomendações |
| W12 | Renovação de tokens | Diário | Tokens válidos ou alerta |
| W13 | Reconciliação | Horária | Eventos/posts em falta corrigidos |
| W14 | Pedidos RGPD | Manual | Exportação/eliminação auditada |

**Fluxo crítico (W5) em resumo:** `APPROVED → SCHEDULED`; a T−10 min cria-se o *container* e persiste-se o `container_id`; polling do estado; verificação do limite diário; `media_publish`; `PUBLISHED`. Em falha: classificar (retentável / permanente / quota / token) e aplicar *backoff* com teto; se `agora − scheduled_at > tolerância`, marcar `MISSED` e notificar (nunca publicar conteúdo velho em silêncio). Antes de re-tentar um `media_publish` de resultado desconhecido, reconciliar listando a media recente da conta e comparando *hash* de legenda e janela temporal, para evitar duplicados **[I]**.

---

### 8. Painel web (interface própria)

**Decisão em aberto no resumo: painel web vs chat.** Recomendação **[I]**: painel mínimo para **aprovações, calendário e inbox**, mais **notificações** (email/Telegram/Teams, à escolha) com ligações diretas. O chat serve bem para *pedir* ("faz-me o plano da semana") mas é mau para **rever e aprovar** conteúdo visual, e a aprovação com auditoria é o núcleo do produto. Pode haver ambos mais tarde; o painel vem primeiro porque a aprovação é bloqueante.

Páginas: Dashboard · Calendário editorial · Editor de post (pré-visualização por canal) · Fila de aprovações · Inbox (conversas, *takeover*, SLA) · Leads · Perfil de marca/KB · Campanhas · Relatórios · Definições (contas, estado dos tokens, níveis de autonomia, *kill switch*, orçamentos) · Auditoria e custos LLM.

Papéis: `admin`, `editor`, `approver`, `viewer`. Aprovador não pode ser o autor, se a empresa quiser quatro olhos (configurável).

---

### 9. Segurança, privacidade e conformidade

#### 9.1 Modelo de ameaças (resumido)

| Ameaça | Vetor | Mitigação |
| --- | --- | --- |
| Falsificação de webhooks | POST direto ao endpoint | HMAC `X-Hub-Signature-256` sobre corpo bruto; comparação em tempo constante; rejeitar sem assinatura; WAF/rate-limit |
| *Replay* | Reenvio de payload válido | Chave única por evento; janela temporal quando aplicável |
| Roubo de tokens | Acesso ao Pi/BD/backups | Cifrar tokens em repouso, chave fora da BD, backups cifrados, permissões mínimas por token |
| Injeção de prompt | Texto de clientes | Secção 5.7 |
| SSRF/abuso de URLs | URLs de media/links | Só buscar de *allowlist* e de armazenamento próprio |
| Acesso ao painel | Credenciais fracas | Cloudflare Access + MFA; papéis; sessão curta; auditoria |
| Fuga de PII para o LLM | Mensagens com dados pessoais | Minimizar/pseudonimizar; DPA com o provedor; retenção curta |
| Cadeia de fornecimento | Dependências/skills de terceiros | `pip-audit`, versões fixas, rever skills antes de instalar |
| Perda de dados | Falha de SSD/energia | Secção 4.7 |

#### 9.2 RGPD (não é aconselhamento jurídico)

- **Papéis [I]:** a empresa é responsável pelo tratamento; Meta, provedores LLM, Cloudflare e armazenamento são subcontratantes ou responsáveis conjuntos conforme o caso. Precisa de **DPA** com cada subcontratante.
- **Base legal** para guardar mensagens e *leads* (interesse legítimo vs consentimento) e para qualquer contacto de marketing fora do Instagram/Facebook.
- **Minimização:** guardar `IGSID` (identificador da app) e o mínimo de perfil; evitar guardar texto integral para além da necessidade; **retenção** definida (ex.: mensagens 90 dias, *leads* até retirada de interesse) **[I]**.
- **Direitos dos titulares:** exportação/apagamento (W14); registo de tratamento (RoPA); considerar uma triagem de DPIA pelo uso de IA sobre comunicações de clientes.
- **Transferências:** a API direta da Anthropic não tem residência UE selecionável **[S]**; OpenAI tem para projetos elegíveis **[S]**; Claude via Bedrock/Vertex em região UE é a rota comum **[S]**. Se a empresa exigir dados na UE, isto decide o provedor.
- **Callback de eliminação de dados** da Meta e **política de privacidade** pública para a app **[I]**.
- Autoridade de controlo em Portugal: CNPD.

#### 9.3 AI Act e transparência

O dever de informar que se interage com IA (Art. 50) aplica-se desde **2026-08-02** **[S]**; o *Digital Omnibus* adiou outras partes, não esta **[S]**. Medidas: declaração no início de conversas automáticas; `is_ai_generated` nas publicações geradas quando aplicável **[V]**; rever com jurista a obrigação de rotulagem de conteúdo gerado.

#### 9.4 Publicidade e consumo

Regras de identificação publicitária, práticas comerciais desleais e promoções aplicam-se ao conteúdo que o agente produzir. O agente não deve gerar alegações de saúde, comparativas ou promocionais sem regra explícita da empresa. **A validar com jurista [I].**

---

### 10. Testes e qualidade

| Camada | O quê | Ferramentas |
| --- | --- | --- |
| Unitários | Máquinas de estados, política de escalamento, *slots* de preço, validação de 1000 bytes UTF-8 | `pytest` |
| Contrato | Payloads reais da Meta como *fixtures* (webhooks, respostas, erros de quota/token); falham alto quando a Meta muda | `pytest` + gravações anonimizadas |
| Integração | Postgres real em Docker; **Meta simulada** (servidor fake) para publicar/receber | `pytest`, Compose de testes |
| Idempotência | Duplicar eventos, matar o worker a meio, repetir *jobs* | testes de caos simples |
| Segurança | Assinaturas inválidas, *replay*, injeção, SSRF | testes dedicados + `security-review` |
| *Evals* de IA | Conjunto dourado (≥ 100 mensagens/comentários reais anonimizados e rotulados); métricas por capacidade | skill `claude-api build-eval` |
| E2E | Conta de teste real na Fase 0/1 e antes de cada subida de autonomia | manual guiado + script |
| Pi *smoke test* | Arranque, `docker compose up`, *healthchecks*, restauro de backup | script em `AgentDATA/scripts/` |

**Métricas de *evals* sugeridas [I]:** precisão do escalamento (recall de casos de risco ≥ 99 % antes de L1), taxa de afirmações fora da KB (alvo 0), taxa de edição humana dos rascunhos (distância de edição), tempo até primeira resposta, e custo por conversa.

---

### 11. Observabilidade e operação

- **Logs JSON** com os campos do contrato do `AgentDATA` (timestamp, módulo, função, evento, contexto, estado, erro, duração, id de correlação) **[V]**; `correlation_id` percorre `webhook_event → job → chamada LLM → chamada Meta`. Sem segredos nem PII em claro.
- **Métricas** simples (tabelas + painel): idade da fila, taxa de erro por integração, latência de webhook, posts por estado, custo LLM/dia, validade dos tokens.
- **Alertas** (email/Telegram): token a expirar (D-14/D-7), falha de publicação, silêncio de webhooks > X h, fila parada, disco > 80 %, temperatura do Pi, orçamento LLM, *dead-man's switch*.
- **Runbooks** em `AgentDATA/procedures/`: token expirado/revogado, Meta em baixo, LLM em baixo, restauro de BD, rotação de segredos, conta restringida pela Meta.
- **Cenários de falha:**

| Cenário | Efeito | Resposta |
| --- | --- | --- |
| Sem Internet/energia | Webhooks perdem-se; agendados atrasam | Reconciliação ao voltar; política `MISSED`; Facebook agendado nativamente continua |
| Token revogado/expirado | Todas as chamadas falham | Alerta; estado `NEEDS_REAUTH`; pausar jobs dessa conta |
| Meta/Graph em baixo | Publicação/envio falha | *Backoff* com teto; fila persiste |
| LLM em baixo ou *refusal* | Sem rascunhos | Fallback de modelo/provedor; atendimento passa a humano |
| Conta restringida pela Meta | Sem publicação/DM | Reduzir ritmo, parar automação, revisão humana, contactar suporte |
| Corrupção de BD/SSD | Perda de estado | Restauro do último backup + reconciliação |
| Túnel em baixo | Painel/webhooks inacessíveis | Healthcheck externo + reinício |

---

### 12. Roadmap detalhado

Estimativas em esforço relativo e dias úteis para **uma pessoa a tempo inteiro** **[I]**; ajustar à tua disponibilidade real. Prazos de aprovação da Meta não estão incluídos e não são controláveis.

#### Fase 0 — *Spike* de plataforma e decisões (3–5 dias)

Objetivo: reduzir o risco R1 antes de escrever o produto.

- Criar app Meta (modo *Development*), ligar Página + conta Instagram profissional de teste; escolher setup (3.1).
- Publicar imagem, carrossel e Reel; registar specs e limite reais; testar `content_publishing_limit`.
- Receber webhook de DM e de comentário via Tunnel; validar assinatura; testar *private reply*.
- Determinar: Standard Access chega? Webhooks de comentários exigem Advanced? Tempo de vida de tokens.
- Decidir: Python/TS, provedor LLM e residência, media (A/B/C), fonte de preços.
- Entregáveis: registos `Knowledge/` atualizados; ADRs (decisões) curtos; `git init`; README real.
- **Critério de saída:** todas as perguntas do R1 respondidas com evidência.

#### Fase 1 — Núcleo e integração (≈ 2–3 semanas)

Compose (web, worker, postgres, cloudflared) no PC e no Pi; esquema inicial + Alembic; `webhook_events`; fila Postgres; autenticação; cliente Meta com *retries*/*backoff* e leitura de cabeçalhos de uso; cifra de tokens; publicação manual de um post (sem IA) com estados e auditoria; logs/healthchecks; backup + restauro testado.
**Aceitação:** publicar um post real FB+IG via fila; matar o worker a meio sem duplicar; receber e gravar uma DM de teste; restauro de backup em máquina limpa.

#### Fase 2 — Marketing e conteúdos (≈ 3–4 semanas)

Perfil de marca/KB (W1); `plan_week` (W2); geração de legendas/adaptação/briefs (W3); aprovação com hash (W4); agendamento e publicação completos (W5); imagem com composição por código; *evals* iniciais; painel mínimo (calendário + aprovações).
**MVP do resumo cumprido aqui:** plano semanal → propostas → aprovação → agendar/publicar → estado e falhas.
**Aceitação:** 2 semanas de uso real em modo supervisionado; taxa de edição humana medida; zero publicações sem aprovação.

#### Fase 3 — Atendimento e leads (≈ 3–4 semanas)

Ingestão completa (W6) com reconciliação (W13); classificação e pipeline de rascunho (W7); comentários (W8); inbox com *takeover*; leads (W9); *evals* e *red team*; **L0 (sombra) por ≥ 2–4 semanas** antes de L1.
**Aceitação:** recall de escalamento ≥ 99 % no conjunto dourado; zero afirmações de preço fora de *slots*; divulgação de automação presente.

#### Fase 4 — Análise e consolidação (≈ 2–3 semanas)

Insights (W10) com *snapshots*; relatórios (W11); recomendações; painel de custos e auditoria; RGPD operacional (W14); revisão de segurança; documentação final; critério de migração Pi→VPS.

**Total aproximado: 11–17 semanas de uma pessoa**, mais a espera de revisão da Meta se for necessária **[I]**. O MVP do resumo cai tipicamente no fim da Fase 2 (≈ 6–9 semanas).

---

### 13. Custos (indicativos **[I]**, a confirmar)

| Item | Tipo | Ordem de grandeza |
| --- | --- | --- |
| Raspberry Pi 5 8 GB + PSU + arrefecimento + caixa | Único | 110–150 € |
| SSD (NVMe + HAT ou SATA/USB) | Único | 40–80 € |
| UPS pequeno (opcional) | Único | 30–80 € |
| Domínio | Anual | 10–15 € |
| Cloudflare (DNS, Tunnel, Access até pequenas equipas, WAF básico) | Mensal | 0 € (plano gratuito, conforme limites atuais) |
| Armazenamento de media + backups externos | Mensal | 0–5 € |
| LLM texto | Mensal | ~40–120 USD no cenário de 1 000 conversas, com margem de *thinking* (secção 5.3) |
| Imagens/vídeo | Mensal | **por verificar** |
| Monitorização externa (dead-man's switch) | Mensal | 0 € (planos gratuitos) |

O custo dominante é o **tempo de desenvolvimento e manutenção**, não o hardware.

---

### 14. Registo de riscos

| ID | Risco | Prob. | Impacto | Mitigação | Gatilho/indicador |
| --- | --- | --- | --- | --- | --- |
| R1 | Acesso Meta (Standard vs Advanced, App Review, Business Verification) bloqueia ou atrasa | Média | Alto | Fase 0 antes de tudo; contas de teste; submeter cedo se necessário | Resultado da Fase 0 |
| R2 | Conta restringida pela Meta por comportamento automatizado | Baixa–Média | Alto | Só API oficial; respeitar janelas; ritmo humano; sem ações em massa; *kill switch* | Avisos/erros 4/17/80002 |
| R3 | Injeção de prompt / alucinação comercial | Média | Alto | Secção 5.5–5.7; modo sombra; *slots* de preço | Edições humanas; *red team* |
| R4 | Perda de eventos com Pi em baixo | Média | Médio–Alto | Reconciliação; buffer opcional; *dead-man's switch* | Silêncio de webhooks |
| R5 | Expiração silenciosa de tokens | Média | Alto | `expires_at`, `debug_token`, alertas D-14/D-7, refresh | Dias até expirar |
| R6 | Não conformidade RGPD/AI Act | Média | Alto | Minimização, DPA, retenção, divulgação, DPIA triagem, jurista | Revisão pré-lançamento |
| R7 | Mudança de API/versão/métricas | Alta | Médio | Versão fixa, testes de contrato, revisão trimestral, payload bruto | Erros 4xx novos |
| R8 | Posts duplicados ou publicados fora de tempo | Média | Médio | Idempotência, reconciliação, `MISSED` | Duplicados detetados |
| R9 | Custo LLM descontrolado | Média | Médio | Orçamentos, caching, batches, modelo por tarefa, esforço fixo | Custo/dia |
| R10 | Qualidade de marca insuficiente (conteúdo genérico) | Média | Médio | Perfil de marca rico, exemplos aprovados, aprovação humana, *evals* | Taxa de edição |
| R11 | Falha de SSD/energia/ISP | Média | Médio | SSD, UPS, backups externos, restauro testado | Quedas/mês |
| R12 | Reels fora do alcance do Pi | Alta | Baixo–Médio | Fora do MVP; render externo ou upload humano | — |
| R13 | Dependência de skills/terceiros de baixa qualidade | Baixa | Médio | Limiar de instalações/estrelas; revisão antes de instalar | — |
| R14 | Âmbito inflado (atendimento + CRM + analytics + painel) | Alta | Alto | Fases com critérios de saída; MVP estrito | Atraso entre fases |
| R15 | Pessoa-chave única (*bus factor*) | Média | Médio | Documentação em `AgentDATA`, runbooks, ADRs | — |
| R16 | Políticas de IA/rotulagem da Meta mudam | Média | Baixo–Médio | `is_ai_generated`, rever política por trimestre | Alterações de política |

---

### 15. Decisões em aberto e perguntas

Ordem de prioridade; cada uma com a minha recomendação.

**Negócio e contexto**

1. **Setor, público e tom de marca?** Condiciona KB, riscos (saúde/finanças exigem regras mais duras) e *evals*.
2. **Volumes esperados** (posts/semana, DMs e comentários/mês, contactos)? Define se L1 compensa e o custo LLM.
3. **Quem aprova** e com que SLA? Uma pessoa, duas (quatro olhos)? Fora do horário?
4. **Idiomas** (PT-PT, EN, outros)? Afeta classificação, divulgação e KB.
5. **Fonte de verdade dos preços/stock** (folha, ERP, site)? Obrigatória para o atendimento (secção 5.5).

**Contas e acesso Meta (Fase 0)**

6. A Página Facebook e a conta Instagram já são **profissionais e ligadas**? Existe **Business Manager**? Quem é administrador e pode criar a app e verificar o negócio?
7. Aceitas **submeter App Review/Business Verification** se for necessário? Disponibilidade para screencasts e prazos?

**Arquitetura e stack**

8. **Python (PEP 8) vs TypeScript** (o `AGENTS.md` sugere camelCase por omissão e scripts em TS). *Recomendo:* Python/PEP 8 para o backend, TS só em `AgentDATA/scripts/`.
9. **Provedor e residência de dados:** Anthropic direto, Claude via Bedrock UE, ou OpenAI com residência UE? *Recomendo:* camada `LlmClient` e decisão com a empresa (RGPD); começar por Anthropic direto em dev.
10. **Media:** opção A (Pi), B (objetos) ou C? *Recomendo:* B.
11. **Painel vs chat primeiro:** *Recomendo* painel mínimo + notificações (secção 8).
12. **Reels no MVP?** *Recomendo:* não (guião + upload humano).
13. **Imagens:** gerar com IA ou usar banco da marca? Orçamento para geração?
14. **Pi permanente ou ambiente de testes?** *Recomendo:* produção no Pi com backups externos e critério de migração definido.
15. **Canal de notificações** (email, Telegram, Teams)?
16. **Orçamento mensal de LLM** e tolerância a *thinking* mais caro?

**Escopo**

17. WhatsApp, anúncios pagos e outras redes entram em fases futuras? Se sim, desenhar a abstração de canal já.
18. Moderação de comentários (ocultar/apagar *spam*) entra na Fase 3?
19. Pesquisa de tendências e da concorrência faz parte do `plan_week`? (Implica ferramenta web e cuidados de fonte.)
20. O projeto *Adaptive LLM Router* que tens em `Ideias/` é para reutilizar aqui como camada de seleção de modelos, ou fica separado? (Vi apenas o nome da pasta; não li o conteúdo.)

---

### 16. Próximos passos propostos

1. Discutir este relatório e responder às perguntas 1–8 (as que desbloqueiam a Fase 0).
2. `git init` + primeiro commit do seed e deste relatório.
3. Executar a **Fase 0** (spike Meta) e atualizar os registos `Knowledge/`.
4. Reescrever o `README.md` (deixar de ser *seed*) com objetivo, stack, comandos e validação.
5. Instalar as skills recomendadas, se concordares (`AgentDATA/skills/` já as lista como *desejadas*).
6. Criar o plano de implementação da Fase 1 (skill `writing-plans`).

---

## Impacto

- **No repositório:** novos ficheiros em `AgentDATA/docs/` (este relatório e dois anexos), `AgentDATA/Knowledge/` (4 registos), `AgentDATA/skills/` (índice e registos) e `AgentDATA/context/` (estado desta análise). Nada de código, nada instalado, nenhum segredo guardado.
- **No plano do projeto:** acrescenta uma Fase 0, torna explícitas as restrições da Meta, muda a abordagem de preços (slots), de media (URL público), de agendamento (FB nativo + IG próprio) e de ingestão (reconciliação), e propõe níveis de autonomia no atendimento.
- **Pendente de verificação (não tratar como facto):** tudo marcado **[S]** e **[I]**, em especial: vida útil dos Page tokens e System User tokens, necessidade de Advanced Access para uso interno, política de reenvio de webhooks, specs de Reels, valores 100 vs 50 de publicação, janela de 7 dias por anúncio, e requisitos de callback de eliminação de dados.

## Referências

**Documentação Meta (consultada nesta sessão)**

- [Instagram Content Publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing.md)
- [Instagram Webhooks](https://developers.facebook.com/docs/instagram-platform/webhooks)
- [Instagram Messaging API (Instagram Login)](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api)
- [Private Replies](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/private-replies)
- [Graph API Rate Limiting](https://developers.facebook.com/docs/graph-api/overview/rate-limiting)
- [Pages API — Posts](https://developers.facebook.com/docs/pages-api/posts)
- [Graph API v26.0 changelog](https://developers.facebook.com/docs/graph-api/changelog/version26.0) (apenas referido nos resultados de pesquisa; **ler diretamente**)

**Fontes secundárias (usadas para pontos marcados [S])**

- [Janela de 24 h e Human Agent (inro.social)](https://www.inro.social/glossary/24-hour-messaging-window), [KeyCRM](https://help.keycrm.app/en/communications-sms-email-instagram-telegram-viber-marketplace-chats-telephony/response-timeframes-on-facebook-and-instagram), [Spur](https://www.spurnow.com/en/blogs/instagram-messenger-ads-7-day-messaging-window)
- [Refresh de tokens Instagram](https://developers.facebook.com/documentation/instagram-platform/reference/refresh_access_token.md)
- [Hookdeck — Facebook Webhooks](https://hookdeck.com/webhooks/skills/facebook-webhooks.md)
- [EU AI Act — Omnibus e Art. 50 (Jones Walker)](https://www.joneswalker.com/en/insights/blogs/ai-law-blog/yes-august-2-still-matters-the-eu-approved-a-high-risk-ai-delay-but-most-trans.html), [Gibson Dunn](https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/)
- [OpenAI — data residency na Europa](https://openai.com/index/introducing-data-residency-in-europe/)
- [Etiquetas de IA no Instagram](https://www.meta.com/en-gb/help/artificial-intelligence/1783222608822690/)

**Skills (pesquisa `npx skills find`)**

- [fastapi/fastapi](https://skills.sh/fastapi/fastapi/fastapi) · [postgresql-table-design](https://skills.sh/wshobson/agents/postgresql-table-design) · [gdpr-data-handling](https://skills.sh/wshobson/agents/gdpr-data-handling) · [security-and-hardening](https://skills.sh/addyosmani/agent-skills/security-and-hardening) · [content-strategy](https://skills.sh/coreyhaines31/marketingskills/content-strategy) · [social-content](https://skills.sh/coreyhaines31/marketingskills/social-content) · [brand-voice](https://skills.sh/affaan-m/ecc/brand-voice)

**Internos**

- [resumo_marketing_ai_manager.md](../../resumo_marketing_ai_manager.md) · [AGENTS.md](../../AGENTS.md) · `AgentDATA/Knowledge/` (registos `meta_*` e `llm_provider_*`)
