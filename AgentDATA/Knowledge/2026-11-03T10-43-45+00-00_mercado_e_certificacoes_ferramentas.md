# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-03T10:43:45+00:00`
- Autor: `agent`
- Assunto: Concorrência, certificações e termos comerciais das ferramentas do projeto (Tailscale, OpenAI, Anthropic, Cloudflare, Meta)
- Status: `ativo`
- Retestar em: `2027-02-03`

## Achado

O plano Tailscale Personal é para uso não comercial; o projeto serve uma empresa real, por isso a tailnet pode exigir plano pago (Standard, 8 USD por utilizador por mês). Buffer e Metricool são os concorrentes de agendamento mais verificáveis. Vender o produto exige Advanced Access, Business Verification e App Review da Meta.

## Contexto

Possível comercialização futura do agente; avaliação de certificações e termos das ferramentas e pesquisa de produtos semelhantes.

## Metodo

`WebFetch` das páginas de preços de Buffer e Metricool e da página de segurança da Tailscale. `WebSearch` (standard e extended) para concorrência, termos do plano Personal, certificações da OpenAI e da Anthropic, Meta Tech Provider, Meta Business AI e HubSpot Breeze, em 2026-11-03.

## Comandos

```text
Sem comandos executados.
```

## Evidencia

Fonte primária:

- Buffer: grátis até 3 canais (10 posts agendados de cada vez, 5 sugestões de IA por semana); Essentials 5 USD por canal e mês; Team 10 USD por canal e mês; limite de uso justo de 5000 posts por canal; canais incluem Instagram, Facebook, TikTok, LinkedIn, X, YouTube, Threads, Pinterest, Google Business Profile, Bluesky, Mastodon e Substack.
- Metricool: grátis (1 marca); Starter desde 20–25 USD; Advanced desde 53–67 USD; marca branca no plano Custom; créditos de IA por marca e mês: 5, 20 e 35; caixa de entrada para Instagram, Facebook, TikTok, YouTube, LinkedIn e Google Business Profile; fluxos de comentário para DM; MCP. A página foi lida só até aos primeiros 100 000 caracteres.
- Tailscale: SOC 2 Type II (segurança, disponibilidade, confidencialidade); relatório sob NDA; auditorias da Latacora a pedido; sem serviços no âmbito de HIPAA; PCI SAQ A; a página não menciona ISO 27001.

Fonte secundária (por confirmar):

- Tailscale Personal: "não destinado a uso comercial"; tailnet com domínio próprio conta como uso empresarial. Padrão de pesquisa sobre páginas oficiais, FAQ não lida por inteiro.
- OpenAI: SOC 2 Type 2; ISO 27001:2022 e 27701:2019; também 27017, 27018, CSA STAR; DPA; residência de dados em várias regiões incluindo a Europa, anúncio de 2025-11-25.
- Anthropic: HIPAA configurável, ISO 27001:2022, ISO/IEC 42001:2023, SOC 2 Type I e II; portal `trust.anthropic.com`; DPA não encontrado.
- Meta Tech Provider: Business Verification e App Review para servir outras empresas; um blog de 2026 diz que a Access Verification foi retirada; sem confirmação oficial.
- Concorrência (blogs): Publer, Later, Hootsuite, Sprout, Predis, Velocity, NoimosAI, Lately, HubSpot Breeze, ManyChat, Looka, Brandmark; Postiz com licença AGPL-3.0; "Business AI" da Meta em piloto.

## Codigo relevante

```text
Sem código executado.
```

## Interpretacao

- Inferência: o custo de ferramentas do MVP sobe de 0 € para cerca de 8 USD por mês se a empresa não tiver plano Tailscale.
- Inferência: o espaço diferenciador está na estratégia primeiro e na triagem para humano, não no agendamento.

## Consequencia operacional

- Perguntar qual é o plano Tailscale da empresa.
- Fazer varredura de licenças das dependências antes de qualquer venda.

## Riscos e limites

- A maior parte da concorrência vem de blogs de fornecedores; preços e capacidades podem estar errados.
- Páginas de confiança (OpenAI, Anthropic, Cloudflare) não foram lidas diretamente.

## Pendencias

- Ler a FAQ de preços da Tailscale e o plano atual da empresa.
- Ler o portal de confiança da Anthropic e da OpenAI, e os DPAs.
- Ler o Trust Hub da Cloudflare.
- Confirmar o estado da "Access Verification" da Meta.
