# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-02T12:25:25+00:00`
- Autor: `agent`
- Assunto: Meta — Instagram Messaging API: janela de resposta, private replies, formatos e rate limits
- Status: `ativo`
- Retestar em: `2027-02-02`

## Achado

O bot so pode escrever depois de o utilizador escrever primeiro e dentro de uma janela de 24 h (7 dias em casos especificos). Respostas privadas a comentarios: 1 mensagem, ate 7 dias. Estas regras limitam diretamente o desenho do atendimento automatico e dos follow-ups de leads.

## Contexto

Fase 3 do projeto (atendimento e leads). O resumo inicial nao mencionava estas restricoes; elas condicionam o desenho da inbox, dos estados de conversa e da escalacao.

## Metodo

`WebFetch` das paginas oficiais `.../instagram-api-with-instagram-login/messaging-api`, `.../messaging-api/private-replies` e `.../graph-api/overview/rate-limiting` em 2026-11-02; `WebSearch` para contexto secundario.

## Comandos

```bash
POST https://graph.instagram.com/v26.0/<IG_ID>/messages     # enviar DM (recipient.id = IGSID)
POST https://<HOST>/<VERSION>/<IG_ID>/messages              # private reply (recipient.comment_id)
```

## Evidencia

Fonte primaria (documentacao oficial):

- A app so envia depois de o utilizador mensagear a conta primeiro. Janela padrao 24 h; ate 7 dias a partir da mensagem se a primeira mensagem seguiu um anuncio Click-to-Direct. Existe uma janela separada "Human Agent" de 7 dias para resposta de uma pessoa (a pagina nao nomeia a tag).
- Endpoint de envio: `POST /v26.0/<IG_ID>/messages`; parametros `recipient` e `message`.
- Formatos: texto/links UTF-8 ate 1000 bytes; imagens png/jpeg ate 8 MB (ate 10 por mensagem); audio ate 25 MB; video ate 25 MB; ficheiros PDF ate 25 MB; reacoes; partilha de post proprio (`MEDIA_SHARE`); `reply_to`. Quick replies e templates listados mas sem detalhe na pagina.
- Permissoes: `instagram_business_basic`, `instagram_business_manage_messages`. Advanced Access necessario para contas que nao sejam proprias/geridas; Standard Access chega para contas proprias ou adicionadas ao app.
- Webhooks de mensagens: `messages`, `messaging_optins`, `messaging_postbacks`, `messaging_reactions`, `messaging_referrals`, `messaging_seen`.
- Sem mensagens de grupo. Pedidos (Requests folder) inativos ha 30 dias nao sao devolvidos. Pastas da inbox nao suportadas. Mensagens enviadas por API so contam como lidas na app Instagram depois de uma resposta.
- Divulgacao de automacao: a pagina indica que chats automatizados devem declarar que o sao quando a lei exigir (Califórnia e Alemanha mencionadas) e recomenda fazê-lo em geral.
- Handover: uma app → inbox propria; varias apps → Handover Protocol.
- Private replies: ate 7 dias apos o comentario; uma unica mensagem por comentador; follow-up so se o utilizador responder, dentro de 24 h dessa resposta; em Live so durante a transmissao.
- Rate limits (BUC): Send API 100 chamadas/s/conta (texto, links, reacoes, stickers) e 10/s para audio/video (a pagina Messenger-for-Instagram cita 300/s para texto: conflito); Conversations API 2/s/conta; Private Replies 100/s (Live) e 750/h/conta (posts e reels); Instagram Platform nao-messaging: 4800 × impressoes em 24 h por par app-utilizador.
- Headers `X-App-Usage` e `X-Business-Use-Case-Usage` (inclui `estimated_time_to_regain_access` em minutos). Codigos: 4, 17, 32, 613; BUC `80002` (Instagram), `80006` (Messenger), `80001` (Page). Recomendacao oficial: parar chamadas ao atingir o limite.

Fonte secundaria (por confirmar): janela de 7 dias para chats iniciados por anuncio alargada a partir de 2026-09-28; Human Agent tag nao utilizavel por mensagens automaticas nem para follow-ups comerciais; Update tags recusadas no Instagram desde 2026-04-27.

## Codigo relevante

```text
Sem codigo executado.
```

## Interpretacao

- Fato: ver Evidencia.
- Inferencia: o "follow-up automatico de leads" (resumo, secao 5) fora da janela de 24 h **nao e possivel** por DM; so por canais fora da Meta (email/telefone, com base legal RGPD) ou por intervencao humana na janela Human Agent.
- Inferencia: um estado `conversation.window_expires_at` tem de existir no modelo de dados e o worker nunca deve tentar enviar depois dele.
- Inferencia: o rate limit de 750/h para private replies e muito acima do volume de uma PME; o limite relevante e a regra de "1 por comentario".

## Consequencia operacional

- Modelar `window_expires_at` e `owner (bot|human)` por conversa; bloquear envio automatico fora da janela.
- Resposta a comentario: publica curta + private reply unica com detalhe; guardar `comment_id` para garantir uma so private reply.
- Mensagem automatica deve abrir com divulgacao de que e um assistente automatico.
- Limite de 1000 bytes: validar em bytes (UTF-8, acentos custam 2 bytes), nao em caracteres.

## Riscos e limites

- Sem testes reais; conflitos 100 vs 300/s e janela de 7 dias por anuncio por confirmar.
- Regras de politica da Meta mudam; reler antes da Fase 3.

## Pendencias

- Confirmar nome e uso da Human Agent tag e se exige App Review proprio.
- Confirmar se mensagens enviadas pelo humano na app nativa chegam por webhook (echo) para detetar takeover.
