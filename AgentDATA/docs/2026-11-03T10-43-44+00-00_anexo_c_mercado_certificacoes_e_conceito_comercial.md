# Anexo C — Mercado, certificações e conceito comercial

- Timestamp: `2026-11-03T10:43:44+00:00`
- Autor: `agent`
- Assunto: Ferramentas semelhantes no mercado, certificações e licenças das ferramentas do projeto, e conceito comercial se o produto vier a ser vendido
- Documentos irmãos: [Relatório](2026-11-02T12-25-21+00-00_relatorio_analise_marketing_ai_manager.md) · [Adenda 01](2026-11-02T14-23-57+00-00_adenda_01_decisoes_agendamento_proprio_e_triagem.md)

Legenda de confiança: **[V]** verificado em página oficial do fornecedor nesta sessão; **[S]** fonte secundária (blogs, comparadores, resultados de pesquisa); **[I]** inferência minha.

**Aviso sobre as fontes de mercado.** Quase todas as comparações que encontrei são blogs de fornecedores que se classificam a si próprios. Preços e capacidades de concorrentes que não vêm da página oficial estão marcados **[S]** e podem estar errados. "Não encontrei" nunca significa "não existe".

## Contexto

O responsável indicou que o projeto não nasce para ser vendido, mas pode vir a sê-lo, e pediu: (1) pesquisa de mercado por ferramentas semelhantes; (2) avaliação dos "certificados" das ferramentas usadas, para o caso de comercialização; (3) um conceito comercial. Li "certificados" como certificações de segurança e conformidade (SOC 2, ISO 27001, DPA) e, por extensão, como licenças de software e termos de uso comercial.

Ponto de partida: uma empresa real, sem marketing, que serve de primeiro caso de estudo.

## Decisão ou análise

### 1. Correção a uma afirmação anterior sobre custos

Na ronda anterior pus o Tailscale Personal a 0 €/mês. **Isso pode não se aplicar.** As páginas oficiais da Tailscale, segundo os resultados da pesquisa, dizem que o plano Personal é para uso **não comercial**, que uma tailnet criada com domínio próprio conta como uso empresarial e que depois do período de avaliação é preciso escolher um plano pago **[S, a partir de páginas oficiais; não li a FAQ completa]**. O plano Standard custa 8 USD por utilizador por mês **[V]**.

Como o responsável disse "usamos Tailscale" para a empresa, a tailnet da empresa já terá um plano. Para utilizador único, o custo esperado é cerca de 8 USD/mês, se a empresa ainda não tiver plano. Falta confirmar com o responsável qual é o plano e se o Funnel está incluído.

### 2. Ferramentas semelhantes

#### 2.1 Mapa por categoria

| Categoria | Exemplos | O que fazem | Preço de referência | Fonte |
| --- | --- | --- | --- | --- |
| Agendamento multicanal com IA de legendas | **Buffer** | Calendário, publicação, assistente de IA | Grátis até 3 canais (10 posts agendados de cada vez; 5 sugestões de IA por semana). Essentials 5 USD por canal e mês; Team 10 USD por canal e mês; IA sem limite de créditos nos pagos | **[V]** página de preços |
| | **Metricool** (empresa espanhola **[I]**) | Agendamento, análise, caixa de entrada, fluxos de comentário para DM, créditos de IA, MCP | Grátis (1 marca); Starter a partir de 20–25 USD; Advanced a partir de 53–67 USD; marca branca no plano Custom. IA: 5 / 20 / 35 créditos por marca e mês consoante o plano | **[V]** página de preços (lida só em parte) |
| | Publer, Later, Hootsuite, Sprout Social | Agendamento e análise; Hootsuite e Sprout para equipas maiores | Publer desde cerca de 5–12 USD; Later desde cerca de 25 USD com créditos de IA limitados; Hootsuite desde cerca de 99 USD; Sprout desde cerca de 199 USD por utilizador | **[S]** |
| Criação de visuais e vídeo | Predis.ai, Canva | Gera imagens e vídeos curtos com IA | Predis desde cerca de 19 USD; Canva Pro cerca de 13–15 USD | **[S]** |
| Agentes de marketing "autónomos" | Velocity, NoimosAI, Lately, HubSpot Breeze (agentes de conteúdo) | Prometem planear, escrever e publicar com a voz da marca | Velocity desde cerca de 39 USD; NoimosAI desde cerca de 99 USD; HubSpot Content Agent incluído em planos de topo, preços discordantes | **[S]** |
| Automação de DMs e comentários | ManyChat, Chatfuel, outros | Respondem a DMs e comentários, qualificam e enviam para CRM | ManyChat tem plano grátis até 1000 contactos segundo um blog; outros desde cerca de 10 USD | **[S]** |
| Agente de atendimento da própria Meta | "Business AI" da Meta (Facebook, Instagram, WhatsApp) | Agente de atendimento a clientes para pequenas e médias empresas, em piloto gratuito | Não confirmado | **[S]**, sem fonte oficial |
| Identidade visual de marca | Looka, Brandmark, Canva Brand Kit, Adobe Express | Logótipo e kit de marca; pouco ou nada de estratégia ou voz | Looka cerca de 96–129 USD/ano; Brandmark cerca de 25–65 USD uma vez | **[S]** |
| Código aberto | **Postiz** (licença AGPL-3.0 **[S]**), Mixpost | Calendário e agendamento auto-alojados | Gratuitos; Postiz também tem versão paga alojada | **[S]** |

#### 2.2 O que o mercado já tem de base

Segundo as fontes, hashtags, geração de imagem e calendários com IA são hoje funcionalidades de base **[S]**. Diferenciam, segundo a mesma literatura, a deteção de tendências, a escrita com a voz da marca e a triagem de interações **[S]**.

#### 2.3 Onde este projeto se pode diferenciar [I]

O que **não encontrei** num produto único, e que o projeto já propõe:

1. **Parte da estratégia, não do calendário.** As ferramentas acima assumem que a marca já sabe o que quer dizer. Este agente constrói primeiro o diagnóstico e a identidade e deriva delas o plano.
2. **Triagem para o humano, com proposta de solução**, em vez de atender o cliente. O mercado de DMs vende automação de resposta; esta é uma posição mais prudente e mais fácil de aceitar numa empresa pequena.
3. **Aprovação humana como princípio de desenho**, com `approved_content_hash`, auditoria e orçamento de LLM como regra de código.
4. **Ligação entre os dois lados:** o que se aprende na triagem (perguntas e objeções reais) alimenta a estratégia seguinte.

Fraquezas honestas [I]: ferramentas como Buffer e Metricool já existem, são baratas e estáveis; a Meta pode integrar IA de atendimento e de conteúdo nos seus produtos gratuitos; "identidade da marca com IA" é uma promessa fácil de fazer e difícil de cumprir com qualidade. O diferencial só é real se o resultado for visivelmente melhor do que um prompt solto num assistente genérico.

### 3. Conceito comercial (hipóteses a validar)

**Cliente-alvo [I].** Micro e pequenas empresas, de início em Portugal, com produto ou serviço real e **sem equipa de marketing**; segundo mercado: pequenas agências que gerem várias marcas.

**Proposta de valor [I].** "Um diretor de marketing com um gestor de comunidade, ao preço de uma ferramenta de agendamento, com tu a aprovares tudo." Entregas:

| Funcionalidade | Para o cliente | Estado no projeto |
| --- | --- | --- |
| **Construtor de identidade de marca** | Diagnóstico, posicionamento, público, voz, pilares, regras de comunicação, guardados como perfil versionado | Passa a ser **funcionalidade do produto**, não só trabalho manual (decisão do responsável, 2026-11-03) |
| Estratégia e plano trimestral | Objetivos mensuráveis e plano derivado | No âmbito (D12) |
| Calendário com revisão humana | Propostas de publicações, aprovação, agendamento | MVP |
| Caixa de triagem com proposta | Leads, reclamações e spam explicados, com proposta | MVP (D2) |
| Relatório mensal | O que se publicou, o que funcionou, o que mudar | Fase 4 |

**Modelos de receita possíveis [I]:**

1. **Subscrição por marca** (ex.: 49–149 €/mês). Referências: ferramentas de agendamento custam 5–25 USD por canal ou por pacote; agentes "autónomos" listados custam 39–99 USD. O preço só se justifica se a estratégia e a triagem forem percebidas como valor.
2. **Arranque pago** (diagnóstico e identidade, uma vez) mais subscrição mais barata. Dá cashflow e reduz o risco de churn.
3. **Marca branca para agências**, só depois de o produto estar estável.

**Economia unitária [I].** Com os volumes assumidos, o custo de LLM por marca ronda poucos USD por mês (Adenda 01). O custo dominante passa a ser **suporte, onboarding e conformidade**, não infraestrutura.

**Validação mais barata [I].** Primeiro 8–12 semanas na empresa-caso, com métricas de processo (tempo de aprovação, taxa de edição, leads detetadas e leads perdidas). Depois 3–5 pilotos pagos ou subsidiados com outras empresas, antes de qualquer investimento em escala.

### 4. O que muda se vier a ser comercializado

| Área | Hoje (uso interno) | Com clientes externos |
| --- | --- | --- |
| **Acesso Meta** | Utilizador único com papel na app, App Review provavelmente dispensado **[V/I]** | Passa a ser uma app usada por quem **não tem papel** nela: Advanced Access por permissão, **Business Verification** e **App Review** **[V]**. Para apps que servem várias empresas há ainda o estatuto de Tech Provider; uma fonte de 2026 diz que a "Access Verification" foi retirada, mas **não está confirmado** **[S]**. *Data Use Checkup* anual e *Platform Terms* aplicam-se **[V]** |
| **Revisão da Meta e Tailscale** | Sem conflito | A Meta rejeita a submissão inteira se não conseguir testar a app **[V]**. Uma app só dentro da tailnet não é testável. Seria preciso um ambiente de teste acessível |
| **Multi-cliente** | Uma marca | `tenant_id` em todas as tabelas, isolamento por *Row Level Security* no Postgres, tokens e KB por cliente **[I]** |
| **Alojamento** | Pi com HDD, Funnel com largura de banda limitada e não configurável **[V]** | Ponto único de falha, sem SLA. Seria preciso VPS e armazenamento de objetos |
| **Autenticação** | Identidade da Tailscale | Clientes não estarão na tua tailnet: login próprio com 2FA |
| **RGPD** | Tu e a empresa | Passas a **subcontratante** dos clientes: contrato de tratamento de dados (DPA), lista de subcontratantes, registo de tratamentos, resposta a pedidos dos titulares **[I]** |
| **Termos de uso dos fornecedores de IA** | Uso próprio | Rever termos comerciais e *usage policies* de cada fornecedor para produtos de terceiros **[I]** |
| **Cobrança e legal** | Não existe | Faturação (por exemplo Stripe), termos de serviço, política de privacidade, seguro de responsabilidade **[I]** |
| **Orçamento de LLM** | 20 USD por mês, global | Limite **por cliente** e custo imputado a cada um |
| **Propriedade intelectual** | A esclarecer | Se o desenvolvimento ocorre no âmbito do trabalho na empresa-caso, a titularidade do código e a autorização de usar a empresa como caso podem ser da empresa **[I]**. **Convém esclarecer isto antes de investir** |

### 5. Certificações, termos e licenças das ferramentas

| Ferramenta | Certificações e conformidade | Uso comercial e notas | Fonte |
| --- | --- | --- | --- |
| **Meta (Graph API)** | Não são certificações; são regras: *Platform Terms*, *Developer Policies*, Business Verification, App Review, *Data Use Checkup* | Obrigatórias para servir terceiros (ver secção 4) | **[V/S]** |
| **OpenAI (API)** | SOC 2 Type 2; ISO/IEC 27001:2022 e 27701:2019; a página de dados empresariais lista também 27017 e 27018 e CSA STAR; oferece DPA; residência de dados na Europa (armazenamento em repouso e processamento em endpoints suportados, para clientes elegíveis) | Anúncio da residência é de novembro de 2025; confirmar cobertura atual por endpoint e se o projeto é elegível | **[S]**, declarações da própria OpenAI vistas por pesquisa; relatório e DPA não lidos |
| **Anthropic (API)** | Lista oficial no centro de ajuda: HIPAA configurável, ISO 27001:2022, ISO/IEC 42001:2023, SOC 2 Type I e Type II; portal de confiança com os documentos | Não encontrei o DPA comercial; residência na UE não selecionável na API direta (registo anterior) | **[S]**, resultado de pesquisa sobre páginas oficiais; DPA e portal não lidos |
| **Tailscale** | SOC 2 Type II (segurança, disponibilidade e confidencialidade); auditorias da Latacora a pedido; sem serviços no âmbito de HIPAA; a página **não** menciona ISO 27001 | Plano Personal só para uso não comercial; empresa precisa de plano pago; relatório SOC 2 sob NDA | **[V]** página de segurança; **[S]** restrição do plano |
| **Cloudflare (R2)** | A página consultada **não** nomeia certificações; remete para documentação no painel e no Trust Hub | Preços de R2: 10 GB grátis, saída gratuita **[V]** | **[V]** |
| **Raspberry Pi, HDD** | Não aplicável | Não é plataforma para multi-cliente | **[I]** |
| **PostgreSQL, FastAPI, Pydantic, HTMX, Jinja** | Código aberto; licenças permissivas pelo que conheço | Fazer **varredura de licenças** das dependências antes de vender (ex.: `pip-licenses`) | **[I]** |
| **Ollama, LM Studio, modelos locais** | Software local; cada **modelo** tem licença própria, algumas com restrições comerciais | Só relevante se se vender com modelos locais | **[I]** |
| **Postiz** | Licença AGPL-3.0 | Não incorporar código AGPL num produto fechado sem aconselhamento jurídico | **[S]** |

**O que seria exigido a nós [I].** Antes de um cliente pequeno comprar, bastam: política de privacidade, DPA, lista de subcontratantes, resumo de medidas de segurança e prova de que os dados ficam na UE quando possível. SOC 2 ou ISO 27001 próprios são caros e demorados e só se justificam quando um cliente de maior dimensão os exigir. Ao herdar as certificações dos fornecedores, a nossa cadeia fica documentada, mas **as certificações deles não nos certificam**.

### 6. Recomendação: manter a opção aberta a baixo custo [I]

Decisões baratas agora que não fecham a porta:

1. `tenant_id` (ou `brand_id`) em todas as tabelas desde o início, mesmo com uma só marca.
2. Autenticação atrás de uma interface (`AuthProvider`), de modo a trocar a identidade da Tailscale por login próprio sem reescrever.
3. Custos de LLM imputados por marca no `cost_ledger`.
4. `MediaStore` e `LlmClient` abstratos (já previstos).
5. Perfil de marca como dados versionados, não como prompt fixo, para o construtor de identidade servir várias marcas.
6. Registo de subcontratantes e de dados tratados desde já (útil para o RGPD da empresa-caso).

Não fazer agora: Advanced Access, Business Verification, certificações próprias, faturação, multi-região.

## Impacto

- O construtor de identidade passa a requisito de produto (W1 reescrito como funcionalidade, não como tarefa manual). Os anexos A e B têm de refletir `tenant_id` e `brand_profile` versionado.
- O custo recorrente do MVP sobe de ≈0 € para cerca de 8 USD/mês se a empresa ainda não tiver plano Tailscale, mais até 20 USD de LLM.
- A comercialização seria um **projeto distinto** (Meta, multi-cliente, alojamento, RGPD) e não uma extensão do MVP.

## Referencias

- [V] `buffer.com/pricing`, `metricool.com/pricing`, `tailscale.com/security`, `tailscale.com/pricing`, `developers.cloudflare.com/r2/pricing`.
- [V] Meta: `…/app-review`, `…/business-verification`, `…/access-levels` (registo `Knowledge/2026-11-03T09-59-50…`).
- [S] Pesquisas de mercado e certificações de 2026-11-03: comparadores de IA para redes sociais, resultados sobre Postiz, Looka, DMs, Meta Business AI, HubSpot Breeze, OpenAI (segurança e residência de dados) e Anthropic (centro de ajuda de certificações), Meta Tech Provider.
- Registo de conhecimento: `Knowledge/2026-11-03T10-43-45+00-00_mercado_e_certificacoes_ferramentas.md`.

## Perguntas em aberto

1. Qual é o plano Tailscale da empresa, e o Funnel está incluído?
2. Quem detém a titularidade do código se o projeto vier a ser comercializado, e a empresa-caso aceita ser referência pública?
3. Mercado inicial: só Portugal, ou UE em geral?
4. Cobrir só Instagram e Facebook, ou o produto comercial precisaria de LinkedIn, TikTok ou Google Business Profile? A maioria dos concorrentes cobre vários canais.
