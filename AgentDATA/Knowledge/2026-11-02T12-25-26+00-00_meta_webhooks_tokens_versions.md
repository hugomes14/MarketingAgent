# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-02T12:25:26+00:00`
- Autor: `agent`
- Assunto: Meta — webhooks, tokens, App Review, versoes da Graph API e publicacao em Paginas Facebook
- Status: `ativo`
- Retestar em: `2027-02-02`

## Achado

A integracao Meta tem tres variantes de login com hosts, tokens e permissoes diferentes; webhooks exigem verificacao de assinatura, tratamento de duplicados e subscricao por conta; tokens do Instagram Login expiram em 60 dias; a Graph API esta na v26.0. Para Paginas Facebook existe agendamento nativo (10 min a 30 dias).

## Contexto

Decisoes da Fase 0/1: qual API setup usar, como guardar e renovar tokens, como desenhar o endpoint de webhooks e quanto do agendamento delegar a Meta.

## Metodo

`WebSearch` (standard e extended) + `WebFetch` das paginas oficiais de webhooks do Instagram e `pages-api/posts`, em 2026-11-02.

## Comandos

```bash
POST /<IG_USER_ID>/subscribed_apps?subscribed_fields=comments,messages
POST /<PAGE_ID>/feed   # message, published=false, scheduled_publish_time
```

## Evidencia

Fonte primaria:

- Tres setups de Instagram: Instagram Login (`graph.instagram.com`, permissoes `instagram_business_*`, token de utilizador Instagram), Facebook Login (`graph.facebook.com`, `instagram_basic` etc., Page token, conta profissional ligada a uma Pagina) e Unified Login (System User token, `graph.facebook.com`).
- Webhooks Instagram: o endpoint tem de validar assinaturas, responder HTTPS depressa e tratar entregas duplicadas. "Webhooks do not provide a historical notification store": reter localmente o que o produto precisa. Os campos disponiveis dependem do setup.
- Mensagens: webhooks `messages`, `messaging_postbacks`, etc. (ver registo de messaging).
- Paginas Facebook: `POST /page_id/feed` com `published=false` e `scheduled_publish_time` — "between 10 minutes and 30 days from the time of the API request". Fotos via `/page_id/photos`. Permissoes: `pages_manage_posts`, `pages_manage_engagement`, `pages_read_engagement`, `pages_read_user_engagement`, `publish_video` (so video). Page access token para quem gere a Pagina. So se podem atualizar/apagar posts criados pela propria app.

Fonte secundaria / resultados de pesquisa (por confirmar):

- Versao atual Graph API **v26.0** (2026-07-29); v25.0 disponivel ate 2028-07-29; v19.0 terminou 2026-05-21 e v20.0 em 2026-09-24.
- Assinatura: cabecalho `X-Hub-Signature-256` = HMAC-SHA256 do payload com o App Secret; validar sobre o corpo bruto antes de fazer parse JSON. Handshake GET com `hub.verify_token` e `hub.challenge`.
- Subscricao por conta via `subscribed_apps`; evento so flui com a conta subscrita; app em modo Live; webhook de comentarios exige Advanced Access; sem personalizacao de webhooks por conta.
- Instagram Login: token curto (~1 h) → longo (60 dias); refresh so apos 24 h de idade e antes de expirar; troca e refresh usam o App Secret (so servidor).
- Page tokens: fonte secundaria diz 60 dias; outra premissa corrente diz "nao expiram". **Por confirmar na documentacao oficial.**
- App Review necessario para Advanced Access; Business Verification quando a app serve utilizadores sem papel na app.

## Codigo relevante

```text
Sem codigo executado.
```

## Interpretacao

- Facto: ver Evidencia (primaria).
- Inferencia: para uso interno numa unica empresa, Standard Access + papeis no app pode dispensar App Review, mas a exigencia de Advanced Access para webhooks de comentarios pode contradizer isso. So um teste real o esclarece.
- Inferencia: como o produto precisa de Facebook **e** Instagram, o setup Facebook Login (ou Unified Login) cobre os dois num so app; Instagram Login so cobre Instagram.
- Inferencia: delegar o agendamento do Facebook ao Meta torna-o resiliente a quedas do Raspberry Pi; Instagram continua dependente do scheduler proprio.

## Consequencia operacional

- Fixar a versao da Graph API numa constante e ter tarefa trimestral de revisao de changelog.
- Guardar `expires_at` de cada token; alertar a D-14 e D-7; renovar automaticamente quando aplicavel; `debug_token` diario.
- Guardar o corpo bruto do webhook antes de qualquer processamento (auditoria, replay, validacao de assinatura).
- Fase 0 obrigatoria antes de construir.

## Riscos e limites

- Resultados de pesquisa nao substituem a leitura direta do changelog v26.0.
- Politicas de App Review mudam e o tempo de aprovacao e desconhecido.

## Pendencias

- Ler changelog v26.0 diretamente.
- Confirmar vida util dos Page tokens e dos System User tokens.
- Confirmar requisito do callback de eliminacao de dados (data deletion) para apps Meta.
