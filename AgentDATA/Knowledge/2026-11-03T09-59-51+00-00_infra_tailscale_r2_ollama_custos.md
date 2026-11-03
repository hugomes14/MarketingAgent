# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-03T09:59:51+00:00`
- Autor: `agent`
- Assunto: Infraestrutura de baixo custo — Tailscale Serve/Funnel, Cloudflare R2, Ollama (API compatível com OpenAI) e LLM no Raspberry Pi
- Status: `ativo`
- Retestar em: `2027-02-03`

## Achado

O plano de infraestrutura (painel por `tailscale serve`, webhook e media por Funnel, LLM local por Ollama atrás de uma API compatível com OpenAI) tem custo recorrente quase nulo. O Raspberry Pi 5 não serve para correr o LLM. Os preços de modelos OpenAI não foram confirmados na fonte oficial.

## Contexto

Orçamento mínimo com caminho de escala. Decisões D7 (Tailscale), media e LLM local no MVP.

## Metodo

`WebFetch` das páginas Tailscale (`kb/1223/funnel`, `kb/1312/serve`, `kb/1242/tailscale-serve`, `pricing`), Cloudflare (`r2/pricing`) e Ollama (`api/openai-compatibility`, `capabilities/structured-outputs`) em 2026-11-02 e 2026-11-03. `WebSearch` (standard) para preços OpenAI, Unsloth e velocidade de LLM no Pi 5.

## Comandos

```bash
tailscale serve --https=<porta> <alvo>        # privado, só tailnet
tailscale serve --https=443 --set-path=/foo off
tailscale funnel ...                           # público; sintaxe por confirmar
```

## Evidencia

Fonte primária:

- **Funnel:** só portas 443, 8443 e 10000; só nomes `<tailnet>.ts.net`; largura de banda limitada e não configurável (sem números); exige HTTPS, MagicDNS e o atributo `funnel`; a mesma porta não serve Serve e Funnel ao mesmo tempo; o TLS termina no dispositivo; o URL expõe só o recurso partilhado; não documenta encaminhamento por caminho nem tratamento de cabeçalhos.
- **Serve:** certificado HTTPS automático; `--https=<porta>`, `--set-path`, `--bg`; só `http://127.0.0.1` é suportado como alvo de proxy; envia `Tailscale-User-Login` e `Tailscale-User-Name` e remove-os se o cliente os enviar; os cabeçalhos não existem em tráfego Funnel nem de dispositivos com *tag*.
- **Tailscale Personal (CORRIGIDO em 2026-11-03: o plano é para uso não comercial; ver `2026-11-03T10-43-45…mercado_e_certificacoes_ferramentas.md`; uma empresa precisa de plano pago):** grátis, até 6 utilizadores, dispositivos de utilizador ilimitados; planos pagos 8 USD e 18 USD por utilizador por mês. A página consultada **não confirma** que o Funnel está incluído no plano grátis.
- **Cloudflare R2 (Standard):** grátis 10 GB-mês, 1 milhão de operações Class A e 10 milhões de Class B por mês; depois 0,015 USD por GB-mês, 4,50 USD por milhão (Class A) e 0,36 USD por milhão (Class B); egress grátis, incluindo `r2.dev`.
- **Ollama:** API compatível com OpenAI em `http://localhost:11434/v1/`; chat completions com *streaming*, modo JSON, visão (base64), tools e controlo de raciocínio; **sem** `tool_choice`, `logprobs`; Responses API só sem estado. Saídas estruturadas por JSON Schema via parâmetro `format` na API nativa; o suporte a `json_schema` em `response_format` não está documentado nessa página; a página não garante conformidade e os exemplos validam no cliente; a cloud do Ollama não suporta saídas estruturadas.

Fonte secundária (por confirmar):

- Preços OpenAI (agregadores, por 1 M de tokens): GPT-5.4 nano 0,20 entrada / 1,25 saída USD; GPT-5.4 mini 0,75 / 4,50; GPT-5.5 5 / 30. Os agregadores discordam sobre GPT-5 mini e nano. A página oficial devolveu 403.
- Pi 5 (8 GB): modelos de 3 B quantizados cerca de 2–12 tokens/s; 7 B cerca de 1–7 tokens/s; *throttling* térmico sem arrefecimento ativo. Números variam muito por fonte.
- Unsloth surge como ferramenta de *fine-tuning*; não confirmei que sirva modelos. LM Studio e Ollama correm GGUF sobre llama.cpp.

## Codigo relevante

```text
Sem código executado.
```

## Interpretacao

- Inferência: painel por `serve` na porta 8443 e Funnel na 443 evita o problema de porta diferente de 443 no callback da Meta. Por testar.
- Inferência: o LLM local tem de correr noutra máquina, acessível pela tailnet.
- Inferência: com modelos pequenos, o custo mensal de LLM via API fica na ordem de poucos USD para os volumes assumidos, o que torna a poupança do LLM local pequena e o seu valor principal a privacidade.

## Consequencia operacional

- `LlmClient` sobre Chat Completions com `base_url` configurável; validar sempre a saída contra o esquema e repetir em caso de falha.
- Interface `MediaStore` com duas implementações possíveis (Funnel e R2).
- Fase 0 testa: Funnel por caminho, Funnel no plano grátis, porta do callback.

## Riscos e limites

- Limite de banda do Funnel desconhecido.
- Preços e nomes de modelos OpenAI mudam; confirmar antes de orçamentar.
- Velocidade de LLM no Pi sem medição própria.

## Pendencias

- Confirmar preços na página oficial da OpenAI (ou via conta).
- Ler `kb/funnel` com a sintaxe do comando e a política de limites.
- Medir o LLM escolhido no hardware real.
