# Anexo A — Modelo de dados, estados e contratos

- Timestamp: `2026-11-02T12:25:22+00:00`
- Autor: `agent`
- Assunto: Proposta de esquema PostgreSQL, máquinas de estados, fila, idempotência e contratos de entrada/saída do Marketing AI Manager
- Relatório principal: [relatório de análise](2026-11-02T12-25-21+00-00_relatorio_analise_marketing_ai_manager.md)

Tudo aqui é **proposta [I]** a validar na Fase 0/1. Tipos e nomes são ilustrativos: o que importa são as **restrições** (únicos, estados, chaves de idempotência). Convenções: `snake_case`, `id uuid` (UUIDv7 se disponível, para ordenação temporal), `created_at/updated_at timestamptz` (UTC) em todas as tabelas, `jsonb` para payloads brutos.

---

## Contexto

O modelo tem de suportar: aprovação auditável, publicação idempotente, atendimento com janelas de resposta, RGPD (retenção/eliminação) e métricas com definições que mudam. A API da Meta não guarda histórico de eventos **[V]**, logo a base de dados é o único arquivo.

## Decisao ou analise

### A.1 Diagrama lógico (resumido)

```text
brand_profile 1─* kb_item
social_account 1─1 credential
campaign 1─* content_plan 1─* post 1─* post_variant (por canal) 1─* publish_attempt
post 1─* media_asset          post 1─* approval
social_account 1─* conversation 1─* message        conversation *─1 contact 1─* lead
social_account 1─* comment                         social_account 1─* insight_snapshot
webhook_event ─(gera)→ job ─(chama)→ llm_call / meta_api_call
audit_log (append-only)   cost_ledger   report
```

### A.2 Tabelas principais

#### `social_account`
| Coluna | Tipo | Notas |
| --- | --- | --- |
| `id` | uuid PK | |
| `channel` | enum `facebook_page` \| `instagram` | |
| `external_id` | text | Page ID / IG User ID |
| `display_name`, `username` | text | |
| `setup` | enum `facebook_login` \| `instagram_login` \| `unified` | Ver relatório 3.1 |
| `status` | enum `active` \| `needs_reauth` \| `paused` \| `restricted` | `needs_reauth` pausa os jobs da conta |
| `autonomy_level` | smallint 0–2 | L0 sombra, L1 FAQ, L2 alargado |
| `kill_switch` | bool | Bloqueia qualquer envio automático |
| **UNIQUE** | `(channel, external_id)` | |

#### `credential`
`account_id` FK UNIQUE · `token_ciphertext` bytea (Fernet/age) · `token_kind` · `scopes` text[] · `issued_at` · `expires_at` timestamptz NULL · `last_checked_at` · `last_error`. A chave de cifra **não** está na BD. Nunca devolver `token_ciphertext` em API/logs.

#### `brand_profile` / `kb_item`
`brand_profile`: `version` int, `voice`, `forbidden_terms` text[], `required_disclaimers`, `languages` text[], `examples_approved` jsonb, `is_active`. Versionado: alterações criam nova versão; `post` guarda a versão usada.
`kb_item`: `kind` (produto, faq, politica, contacto, campanha), `key` (SKU/slug), `title`, `body`, `price_amount` numeric NULL, `price_currency`, `valid_from`, `valid_until`, `source`, `last_verified_at`, `is_active`. **UNIQUE** `(kind, key, valid_from)`. Preço sem `valid_until` ou caducado ⇒ não pode alimentar um *slot*.

#### `campaign` / `content_plan`
`campaign`: objetivo, público, período, orçamento (opcional), estado. `content_plan`: semana, `status` (`draft`, `approved`, `superseded`), `generated_by_llm_call_id`, `approved_by/at`. Cada slot do plano é uma linha em `post` em `status = planned`.

#### `post`
| Coluna | Notas |
| --- | --- |
| `id`, `plan_id` NULL, `campaign_id` NULL | |
| `status` | Ver máquina A.3 |
| `topic`, `objective`, `format` (`image`, `carousel`, `reel`, `text`) | |
| `scheduled_at` timestamptz | UTC |
| `brand_profile_version` | |
| `content_hash` | SHA-256 do conteúdo canónico (texto + media + alt text + flags) |
| `approved_content_hash` | Preenchido na aprovação; **o publicador exige igualdade** |
| `is_ai_generated` | bool, vai para `is_ai_generated` na Meta quando aplicável |
| `created_by` | utilizador ou `agent` |

#### `post_variant`
Uma por canal: `post_id`, `account_id`, `caption`, `hashtags` text[], `alt_text`, `link`, `meta_creation_id` (container IG) NULL, `meta_media_id` NULL, `permalink` NULL, `published_at` NULL, `status`. **UNIQUE** `(post_id, account_id)`.

#### `media_asset`
`post_id`, `kind` (`image`, `video`, `thumbnail`), `storage_backend` (`local`, `s3`), `storage_key`, `mime`, `width`, `height`, `bytes`, `sha256`, `generated_by` (modelo/`prompt` guardados em `provenance` jsonb), `public_url_signed_until` NULL. Imagens para Instagram convertidas para **JPEG** **[V]**.

#### `approval`
`post_id`, `content_hash` (o que foi visto), `decision` (`approved`, `rejected`, `changes_requested`), `comment`, `decided_by`, `decided_at`, `expires_at` NULL. Qualquer alteração ao conteúdo muda `content_hash` e invalida aprovações anteriores. Regra opcional quatro-olhos: `decided_by ≠ post.created_by`.

#### `publish_attempt`
| Coluna | Notas |
| --- | --- |
| `variant_id` FK | |
| `attempt_no` int | **UNIQUE** `(variant_id, attempt_no)` |
| `idempotency_key` text | **UNIQUE**; `variant_id:content_hash` |
| `phase` | `container_created`, `container_ready`, `publish_requested`, `published`, `failed` |
| `meta_creation_id`, `meta_media_id` | Persistir **antes** da chamada seguinte |
| `error_class` | `retryable`, `permanent`, `quota`, `token`, `unknown_outcome` |
| `error_payload` jsonb | Resposta bruta da Meta |
| `started_at`, `finished_at` | |

#### `webhook_event`
| Coluna | Notas |
| --- | --- |
| `id` | |
| `source` | `meta` |
| `object_type` | `instagram` \| `page` |
| `dedupe_key` | **UNIQUE**. Mensagem: `mid`. Comentário: `comment_id` + verbo. Fallback: SHA-256 da entrada canónica **[I]** |
| `signature_valid` | bool (rejeitados nem sequer entram) |
| `raw_body` | bytea/text — **corpo bruto exato** para auditoria e revalidação |
| `received_at`, `processed_at` NULL, `status` (`received`, `processed`, `failed`, `ignored`) | |
| `job_id` NULL | |

#### `contact`, `conversation`, `message`
`contact`: `account_id`, `external_user_id` (IGSID/PSID, **UNIQUE** por conta), `display_name` NULL, `username` NULL, `language`, `consent_marketing` bool + `consent_source/at`, `erased_at` NULL (RGPD).
`conversation`: `account_id`, `contact_id`, `owner` (`bot` \| `human`), `status` (`open`, `waiting_customer`, `waiting_human`, `escalated`, `closed`), **`window_expires_at`** (24 h/7 d conforme a origem **[V]**), `human_window_expires_at` NULL (janela *Human Agent* 7 d **[V]**), `last_inbound_at`, `last_outbound_at`, `risk_flags` text[].
`message`: `conversation_id`, `direction` (`in`, `out`), `external_id` (`mid`, **UNIQUE** por conta), `text`, `attachments` jsonb, `sent_by` (`customer`, `bot`, `human`), `status` (`received`, `queued`, `sent`, `failed`, `blocked`), `blocked_reason` NULL, `bytes_utf8` int (limite 1000 **[V]**), `sent_at`.

#### `comment`
`account_id`, `external_id` UNIQUE, `post_external_id`, `author_external_id`, `text`, `parent_external_id` NULL, `status` (`new`, `classified`, `replied_public`, `replied_private`, `hidden`, `ignored`, `escalated`), `private_reply_sent_at` NULL (**garante 1 private reply por comentário** **[V]**), `created_at_meta`.

#### `lead`
`contact_id`, `status` (`new`, `qualified`, `contacted`, `won`, `lost`, `unsubscribed`), `interest` text[], `source` (`dm`, `comment`, `ad`), `notes`, `next_action_at` NULL, `owner_user_id` NULL, `consent_basis` (`consent`, `legitimate_interest`, `none`). Follow-up automático por DM fora da janela **não existe** **[V/S]**: `next_action_at` gera tarefa humana ou contacto fora da Meta.

#### `insight_snapshot`
`account_id`, `subject_type` (`account`, `post`), `subject_id`, `metric_key` (nome normalizado interno), `provider_metric_name`, `provider_version`, `period` (`day`, `lifetime`…), `value` numeric, `captured_on` date, `raw` jsonb. **UNIQUE** `(account_id, subject_type, subject_id, provider_metric_name, period, captured_on)`. Um registo `metric_definition` mapeia métricas internas ↔ nomes da Meta por versão, porque estas são retiradas/renomeadas **[S]**.

#### `report`
`kind` (`weekly`, `monthly`), `period_start/end`, `inputs_hash`, `content_md`, `llm_call_id`, `reviewed_by` NULL.

#### `job` (fila em Postgres)
| Coluna | Notas |
| --- | --- |
| `id`, `type` (enum fechado), `payload` jsonb, `status` (`queued`, `running`, `succeeded`, `failed`, `dead`, `cancelled`) | |
| `run_at` timestamptz | Quando pode correr |
| `attempts`, `max_attempts`, `backoff_policy` | |
| `locked_by`, `locked_until` | Lease; recuperação de jobs órfãos |
| `dedupe_key` text NULL | **UNIQUE** parcial onde NULL ≠ |
| `correlation_id` | Propagado a logs e chamadas externas |
| `last_error` | |

Reclamar trabalho: `SELECT … WHERE status='queued' AND run_at<=now() ORDER BY run_at FOR UPDATE SKIP LOCKED LIMIT n`; `UPDATE` com `locked_until = now()+lease`. Um job só passa a `succeeded` na mesma transação que grava o efeito local; efeitos externos são idempotentes por construção (chaves acima).

#### `llm_call`
`job_id`, `capability`, `provider`, `model`, `effort`, `prompt_version`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd`, `latency_ms`, `stop_reason`, `refusal_category` NULL, `request_hash`, `output` jsonb. Guardar **prompt e resposta apenas conforme a política de retenção** (podem conter PII); por omissão, guardar *hash* + saída estruturada.

#### `audit_log` (append-only)
`actor_type` (`user`, `agent`, `system`), `actor_id`, `action`, `entity_type`, `entity_id`, `before`/`after` jsonb (sem segredos), `at`, `correlation_id`. Sem `UPDATE`/`DELETE` para o papel da aplicação (privilégios na BD).

#### `cost_ledger`
`day`, `capability`, `provider`, `model`, `tokens_in/out`, `cost_usd`; agregação diária para orçamentos e *circuit breaker*.

### A.3 Máquinas de estados

#### Post
```text
planned → drafting → pending_approval → approved → scheduled → publishing → published
                         │   ▲              │                      │
                         ▼   │              ▼                      ├→ failed_retryable → (volta a publishing)
                   changes_requested   rejected(final)             ├→ failed_permanent (final)
                                                                    └→ missed (final; atraso > tolerância)
         qualquer estado ≤ scheduled → cancelled (final)
```
Invariantes: `scheduled`/`publishing` exigem `content_hash = approved_content_hash`; edição após `approved` volta a `pending_approval` e apaga o agendamento; `published` é terminal.

#### `publish_attempt.phase` (Instagram)
```text
(start) → container_created ──poll──→ container_ready → publish_requested → published
              │ EXPIRED (24h)           │ ERROR            │ resultado desconhecido
              ▼                         ▼                  ▼
          failed(retryable: recriar)  failed(permanent?)  RECONCILE (listar media recente)
                                                            ├ encontrado → published
                                                            └ não encontrado → retry controlado
```

#### Conversa
```text
open ⇄ waiting_customer ⇄ waiting_human → escalated → closed
owner: bot → human (qualquer ação humana ou eco) ; human → bot (explícito ou após N h sem atividade)
```
Invariante: envio automático só se `owner = bot ∧ now < window_expires_at ∧ ¬kill_switch ∧ account.status = active`.

#### Lead
`new → qualified → contacted → (won | lost)`; `unsubscribed` é terminal e **bloqueia** qualquer contacto de marketing.

### A.4 Idempotência (resumo de chaves)

| Operação | Chave | Garantia |
| --- | --- | --- |
| Receber webhook | `webhook_event.dedupe_key` | Duplicados da Meta ignorados |
| Criar job de processamento | `job.dedupe_key = webhook_event.id` | Um job por evento |
| Publicar variante | `publish_attempt.idempotency_key` + `meta_creation_id` persistido antes de `media_publish` | Sem segundo container para o mesmo conteúdo; reconciliação se resultado desconhecido |
| Enviar mensagem | `message.external_id` / `client_message_key` (hash conversa+texto+turno) | Sem dupla resposta ao mesmo `mid` |
| Private reply | `comment.private_reply_sent_at IS NULL` na mesma transação | 1 por comentário **[V]** |
| Chamada LLM | `request_hash` | Reutilizar resultado em retry quando seguro |

### A.5 Índices e desempenho (Pi) **[I]**

Índices parciais para `job(status, run_at) WHERE status IN ('queued','running')`; `conversation(account_id, status, window_expires_at)`; `post(status, scheduled_at)`; `webhook_event(status, received_at)`. Volumes pequenos: não são necessários particionamento nem réplicas. Manter `webhook_event.raw_body` com retenção curta (ex.: 30–90 dias) e apagar/pseudonimizar depois; `autovacuum` por omissão; `shared_buffers` ~512 MB–1 GB.

### A.6 Retenção e RGPD **[I]**

| Dado | Retenção sugerida | Notas |
| --- | --- | --- |
| `webhook_event.raw_body` | 30–90 dias | Contém PII |
| `message.text` | 90–180 dias, configurável | Resumo/lead guardado pode durar mais com base legal |
| `contact` | Enquanto houver relação/base legal | `erased_at` + anonimização (`display_name`, `username` → NULL; manter chaves estatísticas) |
| `llm_call` | Hash + saída estruturada 12 meses; prompts completos só em *debug* curto | |
| `audit_log` | ≥ 12–24 meses | Sem PII desnecessária |
| Backups | Alinhar com retenção; eliminação RGPD propaga-se pela rotação | Documentar |

W14 (pedidos RGPD) exporta e elimina por `contact_id` em todas as tabelas acima e regista no `audit_log`.

### A.7 Contratos HTTP

#### Endpoints públicos (via Cloudflare Tunnel)
| Método e caminho | Autenticação | Contrato |
| --- | --- | --- |
| `GET /hooks/meta` | `hub.verify_token` | Devolve `hub.challenge` em texto simples se o token coincidir; 403 caso contrário |
| `POST /hooks/meta` | `X-Hub-Signature-256` sobre corpo bruto | Valida; grava `webhook_event`; cria `job`; **200 imediato**; 401 se assinatura inválida; nunca processa inline |
| `GET /media/{key}` | URL assinado com expiração | Só leitura; `Cache-Control` curto; sem listagem |
| `POST /legal/data-deletion` | Assinada pela Meta (signed_request) **[I]** | Cria job W14; devolve código de confirmação |
| `GET /legal/privacy` | Pública | Política de privacidade (requisito de app Meta **[I]**) |

#### API interna (atrás de Cloudflare Access + sessão)
Recursos REST em `/api/v1/`: `accounts`, `brand-profile`, `kb-items`, `campaigns`, `plans`, `posts`, `variants`, `media`, `approvals`, `conversations`, `messages`, `comments`, `contacts`, `leads`, `reports`, `jobs`, `settings`, `audit`. Ações de domínio como `POST /posts/{id}/approve`, `/schedule`, `/cancel`, `/conversations/{id}/takeover`, `/release`, `POST /accounts/{id}/kill-switch`. Paginação por cursor, erros em formato único (`type`, `title`, `detail`, `correlation_id`), validação Pydantic nas fronteiras, `Idempotency-Key` aceite em `POST` que criam efeitos.

#### Tipos de `job` (enum fechado)
`ingest_webhook`, `classify_message`, `draft_reply`, `send_message`, `process_comment`, `plan_week`, `generate_post_content`, `generate_media`, `prepare_publish`, `publish_post`, `reconcile_posts`, `reconcile_conversations`, `fetch_insights`, `build_report`, `refresh_token`, `check_token`, `purge_retention`, `process_data_request`, `backup_verify`.

### A.8 Contratos das saídas do LLM (esquemas Pydantic/JSON Schema)

Todas as saídas via `output_config.format` / `messages.parse()` **[V]**; `additionalProperties: false`; campos obrigatórios; enums fechados. Esqueletos:

```text
ClassifyMessageOutput
  intent: enum[faq, price_request, availability, order_status, complaint,
               lead_interest, spam, abuse, handoff_request, other]
  urgency: enum[low, normal, high]
  sentiment: enum[negative, neutral, positive]
  language: str (BCP-47)
  risk_flags: list[enum[legal, health, refund, price_negotiation, minor,
                        hate_or_threat, personal_data, injection_suspected, off_topic]]
  needs_human: bool
  confidence: float [0,1]
  pii_present: bool

DraftReplyOutput
  text_template: str            # pode conter {{price:SKU}} / {{stock:SKU}} / {{link:KEY}}
  referenced_kb_keys: list[str] # obrigatório: tudo o que afirma tem de apontar para a KB
  needs_human: bool
  handoff_reason: str | null
  language: str
  confidence: float

VerifyReplyOutput
  approved: bool
  violations: list[enum[unsupported_claim, price_outside_slot, disallowed_link,
                        tone, too_long, wrong_language, pii_leak, missing_disclosure]]
  explanation: str

PlanWeekOutput
  slots: list[{ date, channel, format, topic, objective, kb_refs, rationale }]
  assumptions: list[str]

PostContentOutput
  caption: str; hashtags: list[str]; cta: str | null; alt_text: str | null
  image_brief: { prompt: str, composition: { overlay_text: str|null, logo_position: str|null } } | null
  reel_script: list[{ scene, visual, voiceover, on_screen_text, seconds }] | null
  claims_used: list[str]         # alegações factuais para revisão

LeadExtractOutput
  name: str|null; interests: list[str]; contact_channels: list[...]; consent_signals: list[str]; summary: str
```

Regras de validação pós-LLM (código, não LLM): tamanho em bytes UTF-8 ≤ 1000 para DMs; resolver *slots*; recusar `referenced_kb_keys` inexistentes; recusar links fora da *allowlist*; recusar `@menções` não permitidas; recusar números de telefone/IBAN fora da KB.

### A.9 Configuração e segredos

Variáveis de ambiente apenas para **referências** (caminhos de ficheiros de segredos, URLs, limites). Valores reais em Docker secrets / `AgentDATA/secrets/` local (ignorado pelo Git, conforme `.gitignore` e `secrets/README.md`). Modelo versionado `*.example.json` com nomes e escopos mínimos, nunca valores. Exemplos de nomes (sem valores): `META_APP_ID`, `META_APP_SECRET_FILE`, `META_WEBHOOK_VERIFY_TOKEN_FILE`, `META_GRAPH_VERSION` (constante fixa), `TOKEN_ENCRYPTION_KEY_FILE`, `LLM_PROVIDER`, `ANTHROPIC_API_KEY_FILE`, `OPENAI_API_KEY_FILE`, `LLM_DAILY_BUDGET_USD`, `MEDIA_BACKEND`, `S3_*`, `CLOUDFLARE_TUNNEL_TOKEN_FILE`, `BACKUP_REPOSITORY_FILE`.

## Impacto

Este anexo fixa o contrato de dados que a Fase 1 vai implementar via Alembic. Principais consequências: `window_expires_at` e `owner` por conversa; `approved_content_hash` como garantia de que só se publica o aprovado; `private_reply_sent_at` para a regra de 1 resposta privada; `webhook_event.raw_body` como arquivo auditável; `metric_definition` para sobreviver a mudanças de métricas.

## Referencias

- [Relatório principal](2026-11-02T12-25-21+00-00_relatorio_analise_marketing_ai_manager.md)
- `AgentDATA/Knowledge/2026-11-02T12-25-24+00-00_meta_instagram_content_publishing.md`
- `AgentDATA/Knowledge/2026-11-02T12-25-25+00-00_meta_messaging_private_replies_rate_limits.md`
- `AgentDATA/Knowledge/2026-11-02T12-25-26+00-00_meta_webhooks_tokens_versions.md`
- [Instagram Content Publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing.md) · [Messaging API](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api) · [Webhooks](https://developers.facebook.com/docs/instagram-platform/webhooks)
