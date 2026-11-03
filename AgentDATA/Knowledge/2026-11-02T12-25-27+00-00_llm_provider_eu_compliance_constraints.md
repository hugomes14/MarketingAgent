# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-02T12:25:27+00:00`
- Autor: `agent`
- Assunto: Provedores LLM — precos, restricoes de retencao e residencia de dados na UE; EU AI Act Art. 50
- Status: `ativo`
- Retestar em: `2027-01-02`

## Achado

Os modelos Claude atuais tem precos conhecidos que permitem estimar custo por conversa; o modelo topo (Fable 5.1) nao esta disponivel sob Zero Data Retention; a API direta da Anthropic nao tem residencia UE selecionavel (fonte secundaria), ao contrario de OpenAI (projetos elegiveis) e de Bedrock/Vertex em regioes UE. A obrigacao de informar que se interage com IA (AI Act Art. 50) aplica-se desde 2026-08-02.

## Contexto

O agente vai tratar mensagens de clientes (dados pessoais) e enviar texto para APIs na cloud, sob RGPD. Escolha do provedor/modelo afeta custo e conformidade.

## Metodo

Skill `claude-api` (tabela de modelos em cache de 2026-10-06) e `WebSearch` em 2026-11-02 sobre residencia de dados e AI Act.

## Comandos

```bash
# Nenhum. Precos conferir em: shared/live-sources.md da skill claude-api (linha Pricing).
```

## Evidencia

Fonte primaria (skill claude-api, cache 2026-10-06), USD por 1M tokens (entrada/saida):

| Modelo | ID | Entrada | Saida |
| --- | --- | --- | --- |
| Claude Haiku 5.5 | `claude-haiku-5-5` | 0,10 (ate 100K de prompt; 0,50 acima) | 0,50 (2,50 acima) |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | 2,00 | 10,00 |
| Claude Opus 5.5 | `claude-opus-5-5` | 4,00 | 20,00 |
| Claude Fable 5.1 | `claude-fable-5-1` | 10,00 | 50,00 |

- Opus 5.5 e Fable 5.1: `thinking` nao pode ser desativado; forced `tool_choice` (`any`/`tool`) devolve 400; sem prefill; `temperature`/`top_p`/`top_k` nao-default devolvem 400 em varios modelos; `refusal` e um `stop_reason` a tratar; Opus 5.5 tem esforco por omissao `medium`.
- Fable 5.1: exige retencao de 30 dias; **nao disponivel sob ZDR** salvo autorizacao expressa.
- Prompt caching: leitura de cache a ~0,20 USD/MTok em Opus 5.5 e Sonnet 5.5. Message Batches a 50 % do custo.

Fonte secundaria (por confirmar):

- Anthropic API direta: trafego global por omissao; sem residencia UE selecionavel; ZDR por contrato; transferencias via SCCs/DPF; rota UE usual = Bedrock (eu-west-1, eu-central-1) ou Vertex em regiao UE. Retencao por omissao de logs: conflito entre 7 e 30 dias.
- OpenAI: residencia UE para projetos elegiveis (`eu.api.openai.com`), pode exigir aprovacao; DPA disponivel.
- EU AI Act: obrigacao de divulgar interacao com IA (Art. 50) aplica-se desde 2026-08-02; o Digital Omnibus adiou o regime de alto risco (Anexo III para 2027-12-02) e deu ate 2026-12-02 para marcacao legivel por maquina de sistemas generativos ja no mercado. Estado formal do Omnibus por verificar no Jornal Oficial.

## Codigo relevante

```text
Sem codigo.
```

## Interpretacao

- Facto: tabela de precos e restricoes de modelo (primario).
- Inferencia: Haiku 5.5 chega para classificacao/triagem; Sonnet 5.5 para redacao de respostas e legendas; Opus 5.5 so para planeamento e relatorios; Fable 5.1 nao se justifica para este caso (preco 2,5× Opus 5.5, sem ZDR).
- Inferencia: se o RGPD/risco do cliente exigir residencia UE, a via com menos atrito e Claude via Bedrock EU ou OpenAI com residencia UE; confirmar com DPO/jurista.

## Consequencia operacional

- Camada de provedor fina (interface `LlmClient`) para trocar de Anthropic direto para Bedrock ou OpenAI sem tocar no dominio.
- Minimizar PII enviada ao LLM (pseudonimizar IGSID/nome; remover emails/telefones quando nao necessarios).
- Registar `usage` por chamada para custo por conversa e por funcionalidade.

## Riscos e limites

- Precos em cache de 2026-10-06; podem ter mudado.
- Nada aqui e aconselhamento juridico.

## Pendencias

- Decidir provedor e regiao com a empresa (DPO, se existir).
- Verificar Regulamento (UE) 2024/1689 consolidado e o ato do Omnibus.
