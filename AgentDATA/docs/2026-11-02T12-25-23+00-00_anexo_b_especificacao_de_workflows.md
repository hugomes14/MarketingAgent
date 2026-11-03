# Anexo B — Especificação de workflows

- Timestamp: `2026-11-02T12:25:23+00:00`
- Autor: `agent`
- Assunto: Desenho passo a passo dos 14 workflows do Marketing AI Manager (gatilhos, passos, dados, falhas, idempotência, observabilidade, SLOs)
- Relatório principal: [relatório de análise](2026-11-02T12-25-21+00-00_relatorio_analise_marketing_ai_manager.md) · Modelo de dados: [Anexo A](2026-11-02T12-25-22+00-00_anexo_a_modelo_de_dados_e_contratos.md)

Proposta **[I]**; factos de plataforma marcados **[V]** (documentação oficial) ou **[S]** (fonte secundária). Eventos de log usam nomes estáveis `dominio.acao.resultado` (ex.: `publish.container.created`), com `correlation_id`, `account_id`, `entity_id`, `duration_ms`, `status`, `error` e **sem** segredos ou PII em claro.

---

## Contexto

Cada workflow é uma sequência de *jobs* com: gatilho, pré-condições, passos, escritas na BD, efeitos externos (sempre idempotentes), tratamento de falhas e métricas. Humano no circuito por omissão; autonomia por níveis L0–L2 (relatório 5.6).

## Decisao ou analise

### Convenções comuns

**Classificação de erros** (todas as integrações): `retryable` (rede, 5xx, timeouts), `rate_limited` (códigos 4/17/32/613, BUC 80001/80002/80006 **[V]** — parar e respeitar `estimated_time_to_regain_access`), `token` (invalidação/expiração → conta `needs_reauth`), `permanent` (4xx de validação), `unknown_outcome` (o pedido pode ter tido efeito: reconciliar antes de repetir).

**Backoff:** exponencial com *jitter*, teto por tipo de job; `max_attempts` finito; ao esgotar → `dead` + alerta + visível no painel (nunca desaparecer).

**Notificações:** só para o que exige ação humana (aprovação pendente, escalamento, falha permanente, token a expirar, orçamento excedido, silêncio de webhooks).

**SLOs sugeridos:** webhook respondido < 2 s p95; DM classificada e rascunho pronto < 30 s p95; publicação dentro de ± 2 min do horário (Instagram) quando o sistema está saudável; alerta de token ≥ 14 dias antes.

---

### W1 — Onboarding de marca e KB

- **Gatilho:** manual (painel) na primeira utilização e a cada revisão.
- **Passos:** (1) formulário guiado de perfil de marca (voz, vocabulário, público, valores, proibições, avisos legais, idiomas, exemplos de posts aprovados e rejeitados); (2) importação de produtos/FAQ/políticas (CSV/folha/manual); (3) validação de preços com `valid_until`; (4) o LLM gera um **resumo do perfil** para o responsável confirmar; (5) ativação de nova versão de `brand_profile`.
- **Dados:** `brand_profile` (nova versão), `kb_item`.
- **Falhas:** preço sem validade ⇒ item inutilizável para *slots* e marcado a vermelho no painel.
- **Humano:** obrigatório (aprovar versão).
- **Métricas:** completude do perfil, % de itens de KB com preço válido.

### W2 — Planeamento semanal

- **Gatilho:** agendado (ex.: domingo) ou pedido manual ("plano da semana").
- **Entradas:** perfil ativo, campanhas em curso, datas/eventos, desempenho recente (agregado por SQL), restrições (limite de posts por canal, horários preferidos).
- **Passos:** (1) montar contexto estável (cacheável) e variável; (2) `plan_week` (Opus 5.5, esforço fixo) produz `PlanWeekOutput`; (3) validar regras de código (máx. posts/dia, janelas, sem sobreposição, KB refs existentes); (4) criar `content_plan(draft)` e `post(planned)` por slot; (5) notificar aprovador.
- **Falhas:** `refusal` ou saída inválida ⇒ 1 repetição com mensagem de erro de validação; depois escalar a humano. Sem dados de desempenho ⇒ usar apenas perfil/objetivos.
- **Humano:** aprovar o plano (pode editar slots).
- **Idempotência:** `dedupe_key = plan:{week}:{version}`; novo plano marca o anterior `superseded`.
- **Métricas:** % de slots mantidos sem edição; custo por plano.

### W3 — Geração de conteúdo

- **Gatilho:** `content_plan` aprovado ⇒ um job `generate_post_content` por slot.
- **Passos:** (1) `ideate_post` (opcional, 3 ângulos); (2) `write_caption` + `adapt_to_channel` (variantes FB/IG); (3) `image_brief` e/ou `script_reel`; (4) **geração de media**: modelo de imagem ⇒ composição por código (texto/logótipo) ⇒ JPEG **[V]** ⇒ upload para armazenamento; (5) validar: tamanho, hashtags, links, `alt_text`, marcação `is_ai_generated`; (6) calcular `content_hash`; (7) `post → pending_approval`.
- **Falhas:** erro de imagem ⇒ `post` fica em `drafting` com motivo, notificação, alternativa de carregar media manualmente. Saída com alegações sem referência à KB ⇒ marcada para revisão com destaque.
- **Idempotência:** `dedupe_key = gen:{post_id}:{plan_version}`; regenerar cria nova revisão e muda o hash.
- **Métricas:** taxa de edição humana; taxa de rejeição; custo por post.

### W4 — Aprovação

- **Gatilho:** `post → pending_approval`.
- **Passos:** (1) notificar aprovador(es) com ligação direta; (2) o painel mostra pré-visualização **por canal**, a origem (modelo, versão do perfil), as alegações usadas e os avisos de validação; (3) decisão: aprovar / pedir alterações / rejeitar; (4) ao aprovar, gravar `approval(content_hash)` e `post.approved_content_hash`; (5) opcional quatro-olhos; (6) `post → approved` e definir/confirmar `scheduled_at`.
- **Regras:** qualquer edição posterior invalida a aprovação; aprovação pode expirar (ex.: 7 dias) e exigir reconfirmação.
- **Falhas:** aprovador indisponível ⇒ lembretes e escalamento; post cuja data passou ⇒ sugerir novo horário em vez de publicar tarde.
- **Auditoria:** `audit_log` com quem, o quê, hash visto.
- **Métricas:** tempo até aprovação; % aprovado à primeira.

### W5 — Agendamento e publicação

Passos comuns: um job `prepare_publish` por variante a T−10 min (Instagram) ou já na aprovação (Facebook).

**Facebook (nativo) [V]:** se `scheduled_at` está entre 10 min e 30 dias, `POST /{page_id}/feed` com `published=false` + `scheduled_publish_time` (fotos via `/photos`); guardar o ID do post agendado; reconciliar o estado periodicamente (W13). Cancelamento/edição via API só em posts criados pela app. Vantagem: **sobrevive a quedas do Pi**.

**Instagram (scheduler próprio) [V]:**
1. Pré-condições: conta `active`; token válido; `content_hash = approved_content_hash`; media pública acessível (URL assinado válido ≥ 24 h); dentro do limite diário (consultar `content_publishing_limit` e contador local, assumindo 50 até confirmar).
2. `POST /<IG_ID>/media` (imagem/carrossel/Reel; `alt_text`, `is_ai_generated`); **persistir `meta_creation_id` imediatamente** (`phase = container_created`).
3. Vídeo/Reel: polling de `status_code` ≤ 1×/min, até 5 min **[V]**; `FINISHED` ⇒ seguir; `ERROR`/`EXPIRED` ⇒ falha classificada (recriar se `EXPIRED`).
4. `phase = publish_requested`; `POST /<IG_ID>/media_publish`.
5. Sucesso ⇒ guardar `meta_media_id`, ler `permalink`, `phase = published`, `post → published`, `audit_log`.
6. **Resultado desconhecido** (timeout após `media_publish`) ⇒ `RECONCILE`: listar media recente da conta e comparar janela temporal + *hash* da legenda; encontrado ⇒ `published`; não encontrado ⇒ repetir com a mesma chave, no máximo N vezes.
- **Política de atraso:** `agora − scheduled_at > tolerância` (ex.: 2 h; por tipo de post) ⇒ `missed` + notificação com botão "publicar agora" que **exige confirmação humana**.
- **Falhas:** `rate_limited` ⇒ reagendar no fim da janela; `token` ⇒ `needs_reauth` e pausa; `permanent` ⇒ `failed_permanent` com resposta bruta guardada.
- **Idempotência:** `publish_attempt.idempotency_key`; trinco por `variant_id` para impedir dois workers em simultâneo.
- **Métricas:** pontualidade, taxa de falha por classe, duplicados detetados (alvo 0), tempo de container.

### W6 — Ingestão de webhooks

- **Endpoint** `POST /hooks/meta`: (1) ler o **corpo bruto**; (2) calcular HMAC-SHA256 com o App Secret e comparar em tempo constante com `X-Hub-Signature-256` **[V/S]**; (3) rejeitar 401 se inválido; (4) gravar `webhook_event` com `dedupe_key` único (duplicados ⇒ ignorar, devolver 200); (5) criar `job ingest_webhook`; (6) 200 imediato. Nada de LLM nem de chamadas externas no pedido.
- **Job `ingest_webhook`:** normalizar por tipo (mensagem, comentário, reação, *seen*, etc.), criar/atualizar `contact`, `conversation`, `message`/`comment`, e encadear `classify_message` ou `process_comment`. Eventos sem relevância ⇒ `ignored` com motivo.
- **Handshake GET:** devolve `hub.challenge` se `hub.verify_token` coincide **[V/S]**.
- **Subscrição:** `POST /<IG_ID>/subscribed_apps` com os campos necessários, por conta, no onboarding e verificada periodicamente **[V]**; app em *Live* **[V]**.
- **Falhas:** falha de processamento ⇒ `webhook_event.status = failed` e re-tentativas; o corpo bruto permite *replay* manual a partir do painel.
- **Métricas:** latência de resposta, taxa de assinaturas inválidas, idade do evento mais antigo não processado, **silêncio** (nenhum evento em X h numa conta ativa ⇒ alerta).

### W7 — Atendimento a DMs

- **Gatilho:** `message(in)` novo.
- **Pré-condições:** `owner = bot` e dentro da janela; senão o evento só alimenta a inbox e notifica (ou nada, se `owner = human`).
- **Passos:**
  1. **Sanitizar**: remover/neutralizar marcação, truncar com aviso, extrair URLs como metadados (não seguir); detetar padrões de injeção.
  2. `classify_message` (Haiku 5.5): intenção, urgência, sentimento, riscos, `needs_human`.
  3. **Política (código):** regras duras sobrepõem-se ao modelo. Escalar sempre: reclamação, jurídico, saúde, reembolso, negociação de preço, menor, ódio/ameaça, pedido de pessoa, `injection_suspected`, `confidence` baixa, fora de horário se a empresa o definir, conversa já com ≥ N trocas sem resolução.
  4. `draft_reply` (Sonnet 5.5) com KB em cache ⇒ `DraftReplyOutput`.
  5. **Resolver *slots*** `{{price:SKU}}`/`{{stock:SKU}}`; faltou valor válido ⇒ escalar.
  6. `verify_reply`: regras de código (bytes ≤ 1000 **[V]**, *allowlist*, sem `@menções`/telefones fora da KB) + verificador LLM.
  7. **Nível de autonomia:** L0 ⇒ fila de aprovação; L1 ⇒ envio automático só se `intent ∈ whitelist` e verificação limpa; L2 ⇒ envio com amostragem de revisão.
  8. Primeira resposta da conversa inclui **divulgação** de assistente automático e como falar com uma pessoa.
  9. Envio: `POST /v26.0/<IG_ID>/messages` **[V]**; `message(out)` com `external_id`; *backoff*/limites **[V]**.
- **Escalamento:** `conversation.status = escalated`, `owner = human`, resumo (`summarize_thread`) no cartão, notificação com SLA.
- **Falhas:** `refusal`/saída inválida ⇒ escalar; erro de envio ⇒ classificar; fora da janela ⇒ `blocked` com motivo `window_expired` (nunca tentar).
- **Anti-*loop*:** ignorar `mid` repetido, mensagens do próprio, contas marcadas *bot*; limite de respostas por utilizador/hora; *cool-down* pós-escalamento.
- **Métricas:** tempo até 1.ª resposta, % resolvido sem humano, % escalado, taxa de edição (L0), recall de escalamento, custo por conversa.

### W8 — Comentários

- **Gatilho:** webhook `comments` **[V]** (exige Advanced Access segundo a documentação — a validar na Fase 0).
- **Passos:** (1) classificar (pergunta, elogio, reclamação, *spam*, abuso, intenção de compra); (2) *spam*/abuso ⇒ ocultar (e registar) ou ignorar conforme política; (3) elogio/pergunta simples ⇒ resposta pública curta (sujeita ao nível de autonomia); (4) intenção de compra/pergunta com detalhe ⇒ resposta pública curta + **uma** *private reply* com detalhe **[V]** (`private_reply_sent_at` na mesma transação); (5) reclamação ⇒ escalar, sem debate público.
- **Regras:** *private reply* ≤ 7 dias e só uma por comentador **[V]**; follow-up só se o utilizador responder, dentro de 24 h **[V]**; em Live só durante a transmissão **[V]**.
- **Falhas:** limite 750/h **[V]** (praticamente irrelevante); comentário apagado ⇒ ignorar.
- **Métricas:** tempo de resposta a comentários, % ocultados, conversões para DM.

### W9 — Leads

- **Gatilho:** conversa com `intent = lead_interest` ou intenção de compra repetida.
- **Passos:** `extract_lead` ⇒ criar/atualizar `lead` (`new → qualified`); anotar interesses, canal, origem; capturar **sinais de consentimento** (nunca presumir); criar tarefa humana com `next_action_at`.
- **Restrição:** follow-up automático por DM fora da janela **não é possível** **[V/S]**; contacto por email/telefone só com base legal documentada; `unsubscribed` bloqueia tudo.
- **Falhas:** extração incerta ⇒ lead em `new` com `confidence` baixa, revisão humana.
- **Métricas:** leads/semana, taxa de qualificação, tempo até contacto humano.

### W10 — Recolha de métricas

- **Gatilho:** diário (conta e posts recentes), e T+1 h/24 h/7 d após cada publicação.
- **Passos:** (1) para cada conta ativa, ler *insights* de conta e de posts; (2) mapear nomes da Meta ⇒ métricas internas via `metric_definition(provider_version)`; (3) gravar `insight_snapshot` (valor + `raw` jsonb) com chave única diária; (4) métricas retiradas/inexistentes ⇒ marcar `unavailable` (sem inventar nem preencher com zero) e alertar mudança de definição.
- **Limites:** respeitar uso da plataforma (4800 × impressões / 24 h **[V]**) e *backoff*.
- **Falhas:** erro de permissão ⇒ conta com aviso; *gap* de dias ⇒ aceitar e sinalizar (a API pode não permitir recuperar histórico **[I]**).
- **Métricas:** completude diária de *snapshots*.

### W11 — Relatórios e recomendações

- **Gatilho:** semanal (segunda) e mensal.
- **Passos:** (1) **SQL** agrega KPIs (alcance, interações, crescimento, melhores/piores posts, tempos de resposta, volumes de DMs/leads, custos); (2) comparar com períodos anteriores (código); (3) `weekly_report` (Opus/Sonnet 5.5) recebe **apenas** JSON agregado e redige narrativa e recomendações, devolvendo `ReportOutput` com referências a métricas; (4) validar que **todo o número do texto existe no JSON de entrada**; (5) gerar HTML/PDF e guardar `report`; (6) notificar.
- **Recomendações:** devem apontar para dados (ex.: "Reels à terça tiveram +X % de alcance mediano") e propor ajustes ao `plan_week` seguinte; a decisão de aplicar é humana.
- **Falhas:** dados incompletos ⇒ relatório declara explicitamente o que falta.
- **Métricas:** taxa de números não verificados (alvo 0).

### W12 — Renovação e verificação de tokens

- **Gatilho:** diário.
- **Passos:** (1) `debug_token`/chamada leve por conta para validade e escopos; (2) atualizar `expires_at`; (3) Instagram Login: *refresh* quando idade ≥ 24 h e antes de expirar (60 dias) **[V]**; (4) alertas a D-14 e D-7; (5) falha ⇒ `needs_reauth`, **pausar** jobs da conta, notificar com ligação de reautorização.
- **Segurança:** troca/refresh só no servidor com App Secret **[V]**; guardar cifrado; nunca registar o token.
- **Métricas:** dias mínimos até expirar; falhas de renovação.

### W13 — Reconciliação

- **Gatilho:** horário (e após quedas/arranque do sistema).
- **Passos:** (1) **webhooks perdidos**: listar comentários/conversas recentes por conta e criar eventos ausentes (usar `dedupe_key` para não duplicar) — respeitar limites (Conversations API 2/s **[V]**); (2) **publicações**: confirmar estado de variantes `publishing`/Facebook agendadas, resolver `unknown_outcome`; (3) verificar subscrições de webhooks ativas; (4) detetar *jobs* órfãos (`locked_until` expirado) e recolocá-los.
- **Falhas:** erros por conta não bloqueiam as restantes.
- **Métricas:** nº de eventos recuperados por reconciliação (indicador direto de perda de webhooks), duplicados evitados.

### W14 — Pedidos RGPD (acesso, retificação, eliminação)

- **Gatilho:** manual no painel ou callback de eliminação da Meta **[I]**.
- **Passos:** (1) identificar o titular (`contact_id`/IGSID) e verificar legitimidade; (2) **exportar** dados (contacto, conversas, leads, comentários, `llm_call` associados) em formato legível; (3) **eliminar/anonimizar** por `contact_id` em todas as tabelas (A.6), marcar `erased_at`, remover media associada; (4) registar no `audit_log` sem PII residual; (5) devolver confirmação; (6) lembrar que os **backups** só expiram com a rotação (documentar prazo).
- **Prazos:** o RGPD fixa prazos de resposta; registar datas de receção e resposta.
- **Métricas:** tempo de resposta; pedidos em aberto.

---

### Sequência do fluxo crítico (publicação Instagram)

```text
Aprovador ─approve→ Painel ─grava approval(hash)→ BD
Scheduler ─(T−10min) cria job prepare_publish→ Worker
Worker ─valida hash/token/limite→ BD
Worker ─POST /media→ Meta ─creation_id→ Worker ─persiste container_id→ BD
Worker ─(vídeo) poll status_code ≤1/min→ Meta
Worker ─POST /media_publish→ Meta ─media_id→ Worker ─persiste published→ BD ─notifica→ Equipa
  (timeout) → RECONCILE: lista media recente → decide published | retry
```

### Matriz de tarefas agendadas

| Job | Frequência | Observação |
| --- | --- | --- |
| `plan_week` | Semanal | Pode ser manual |
| `prepare_publish` | Por post (T−10 min) | Instagram |
| `reconcile_posts` / `reconcile_conversations` | Horário | W13 |
| `fetch_insights` | Diário + T+1 h/24 h/7 d | W10 |
| `build_report` | Semanal/mensal | W11 |
| `check_token` / `refresh_token` | Diário | W12 |
| `purge_retention` | Diário (noite) | A.6 |
| `backup_verify` | Semanal | Restauro de amostra |
| *Heartbeat* externo | A cada 5 min | *Dead-man's switch* |

## Impacto

Este anexo é a base dos critérios de aceitação das Fases 1–4 e dos testes de contrato, de idempotência e de *evals*. Define o comportamento em falha, que é onde esta classe de sistemas costuma falhar.

## Referencias

- [Relatório principal](2026-11-02T12-25-21+00-00_relatorio_analise_marketing_ai_manager.md) · [Anexo A](2026-11-02T12-25-22+00-00_anexo_a_modelo_de_dados_e_contratos.md)
- `AgentDATA/Knowledge/` (registos `meta_*`)
- [Messaging API](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api) · [Private Replies](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/private-replies) · [Rate limiting](https://developers.facebook.com/docs/graph-api/overview/rate-limiting) · [Pages API — posts](https://developers.facebook.com/docs/pages-api/posts)
