# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-02T12:25:24+00:00`
- Autor: `agent`
- Assunto: Meta — Instagram Content Publishing API: limites, fluxo e requisitos
- Status: `ativo`
- Retestar em: `2027-02-02` (ou ao mudar a versao da Graph API)

## Achado

A publicacao no Instagram via API e um fluxo de dois passos (criar container, depois publicar), exige URL publica para a media, tem limite rolling de 24 h, e **nao tem agendamento nativo**. A documentacao oficial contradiz-se no limite (100 vs 50 posts/24 h).

## Contexto

Projeto Marketing AI Manager precisa de publicar e agendar em Instagram e Facebook. Este registo evita repetir a leitura da documentacao oficial ao desenhar o worker de publicacao.

## Metodo

`WebFetch` da pagina oficial `developers.facebook.com/documentation/instagram-platform/content-publishing.md` em 2026-11-02, com prompt de extracao de limites, tipos de media, fluxo, permissoes e agendamento.

## Comandos

```bash
# Endpoints citados pela documentacao (nenhum foi executado: sem credenciais Meta nesta fase)
POST /<IG_ID>/media            # cria container (image_url | video_url, media_type)
POST /<IG_ID>/media_publish    # publica (creation_id)
GET  /<CONTAINER_ID>?fields=status_code
GET  /<IG_ID>/content_publishing_limit
```

## Evidencia

Observado na documentacao oficial (fonte primaria):

- Limite: 100 posts publicados por API em janela movel de 24 h; carrosseis contam como 1; aplicado em `media_publish`. A seccao de carrosseis da mesma pagina diz 50. A pagina nao explica a diferenca.
- Media suportada: imagem unica, video unico, Reels, Stories, carrossel ate 20 itens. Imagens tem de ser JPEG (MPO e JPS nao suportados). A media tem de estar num servidor publico no momento da publicacao.
- Shopping tags e filtros nao suportados. Carrossel e recortado ao aspect ratio do primeiro item (1:1 por omissao).
- `status_code` do container: `EXPIRED` (nao publicado em 24 h), `ERROR`, `FINISHED`, `IN_PROGRESS`, `PUBLISHED`. Polling no maximo 1x/minuto, ate 5 minutos.
- Upload resumable (`upload_type=resumable`, `rupload.facebook.com`) exige Facebook Login for Business.
- Tokens/permissoes por tipo de login: Instagram Login → `instagram_business_basic` + `instagram_business_content_publish` (host `graph.instagram.com`); Facebook Login → `instagram_basic` + `instagram_content_publish` + `pages_read_engagement` (host `graph.facebook.com`); Unified Login → System User token. Paginas que exigem Page Publishing Authorization (PPA) nao publicam ate concluirem o PPA.
- Agendamento: a pagina nao descreve parametro nativo; recomenda que a app imponha o rate limit se oferecer agendamento.
- Extras: `alt_text` (so imagens, desde 2025-03-24), `trial_params` para Trial Reels (`MANUAL` | `SS_PERFORMANCE`), `is_ai_generated=true`, `branded_content_sponsor_ids` / `is_paid_partnership` (so Facebook Login).
- A pagina **nao** da especificacoes de Reels (duracao, aspect ratio, tamanho, codec) nem limites de dimensao/peso de imagem.

Fonte secundaria (nao confirmada): codigo de erro de quota excedida `code 9 / subcode 2207042`.

## Codigo relevante

```text
Sem codigo executado.
```

## Interpretacao

Separar fato observado de inferencia tecnica.

- Fato: ver Evidencia.
- Inferencia: um crash entre `media` e `media_publish`, ou uma resposta perdida apos `media_publish` bem-sucedido, pode gerar post duplicado ou ficar sem confirmacao; a API nao oferece chave de idempotencia documentada. O worker tem de persistir `container_id` antes de publicar e reconciliar listando media recente.
- Inferencia: com o Raspberry Pi atras de Cloudflare Tunnel, a "URL publica da media" obriga a expor os ficheiros (path assinado no Pi) ou a usar armazenamento de objetos externo.

## Consequencia operacional

- O scheduler e proprio para Instagram. Publicar com `scheduled_at` vencido ha mais de X horas deve falhar com `MISSED`, nunca publicar conteudo velho em silencio.
- Contar publicacoes por conta/24 h localmente e confirmar com `content_publishing_limit` antes de `media_publish`.
- Converter sempre imagens para JPEG antes de criar o container.
- Specs de Reels tem de ser confirmadas na Fase 0 com um upload real.

## Riscos e limites

- Nao houve chamada real a API; tudo vem de documentacao.
- Contradicao 100 vs 50 por resolver: assumir 50 ate teste (conservador).
- Documentacao pode mudar com nova versao da Graph API (v26.0 em 2026-11-02).

## Pendencias

- Fase 0: publicar 1 imagem, 1 carrossel e 1 Reel numa conta de teste; registar specs reais de Reels e o limite efetivo.
- Confirmar se Standard Access chega para uso interno sem App Review.
