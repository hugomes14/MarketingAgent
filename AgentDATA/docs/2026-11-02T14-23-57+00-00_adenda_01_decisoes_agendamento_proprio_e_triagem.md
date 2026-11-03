# Adenda 01 — Decisões do responsável: agendamento próprio e triagem com notificação

- Timestamp: `2026-11-02T14:23:57+00:00`
- Autor: `agent`
- Assunto: Registo de duas decisões de produto e do seu impacto no relatório e nos anexos
- Documentos afetados: [Relatório](2026-11-02T12-25-21+00-00_relatorio_analise_marketing_ai_manager.md) · [Anexo A](2026-11-02T12-25-22+00-00_anexo_a_modelo_de_dados_e_contratos.md) · [Anexo B](2026-11-02T12-25-23+00-00_anexo_b_especificacao_de_workflows.md)

Esta adenda **prevalece** sobre o relatório e os anexos onde houver conflito. Os registos em `Knowledge/` mantêm-se válidos (são factos da plataforma, não decisões). Marcas de confiança como no relatório: **[V]** verificado, **[S]** secundário, **[I]** inferência minha.

## Contexto

Ao ler o relatório, o responsável corrigiu duas premissas minhas.

## Decisão

### D1 — O agendamento é do nosso software, para Facebook e Instagram

O agente planeia, desenvolve e propõe publicações; um humano revê; o nosso scheduler publica à hora marcada. Não se usa o agendamento nativo da Meta (`published=false` + `scheduled_publish_time`).

### D2 — A entrada de interações é triagem e notificação, não atendimento

O agente **não responde a ninguém**. Recebe DMs e comentários, faz a triagem e entrega ao responsável cada assunto relevante já explicado. Se for lead ou ativação, vem com uma proposta de solução. Exemplo dado: "pessoa X pediu orçamento", "pessoa Y enviou spam".

## Impacto de D1

| Área | Antes | Agora |
| --- | --- | --- |
| W5 | Dois caminhos: Facebook nativo, Instagram próprio | **Um só caminho** (o do Instagram, generalizado). Fim do ramo `scheduled_publish_time` |
| W13 (reconciliação) | Também lia posts agendados no Facebook | Só reconcilia o que **nós** publicámos |
| Edição/cancelamento | Limitado a posts criados pela app, via API | Trivial: é estado local até à hora de publicar |
| Fusos horários | Metade delegada à Meta | Nosso: guardar UTC + fuso IANA (ex.: `Europe/Lisbon`), cuidado com mudança de hora |
| Queda do Raspberry Pi | Facebook agendado sobrevivia | **Já não sobrevive** (ver abaixo) |

O que **não** muda: continuamos dependentes da Meta para *publicar*. Só o relógio passa a ser nosso. Continua a ser preciso token válido, URL público da media na hora, limites diários e reconciliação de resultado desconhecido.

**Custo real da decisão.** Perde-se a única garantia que a Meta dava de graça: um post agendado no Facebook sai mesmo com o Pi em baixo. Como tudo é revisto por um humano e a janela de tolerância já existe (W5, política `missed`), considero o custo aceitável **[I]** se houver:

1. Alimentação do scheduler por `job.run_at` em Postgres, com *leader lock* (um só worker publica).
2. **Catch-up no arranque**: ao subir, o worker vê o que ficou por publicar e aplica a política `missed` (tolerância por tipo de post; fora dela, notifica em vez de publicar tarde).
3. **Vigilância externa** (dead-man's switch, ex.: um serviço de heartbeat) para te avisar de que o sistema parou. Um alarme que vive no próprio Pi não avisa quando o Pi cai.
4. Relógio sincronizado (NTP) no Pi **[I]**.
5. `prepare_publish` a T−10 min (criar o contentor do Instagram perto da hora, que expira em 24 h **[V]**), com folga para falhas e retentativas.

## Impacto de D2

D2 retira a parte mais cara e mais arriscada do relatório. Passa-se de "chatbot que fala com clientes" para "triagem interna".

### O que deixa de ser necessário (no MVP)

- Níveis de autonomia L0/L1/L2, `draft_reply` com envio automático, `verify_reply`, *slots* de preço **para respostas a clientes**.
- Divulgação de assistente automático ao cliente. O artigo 50.º do AI Act regula sistemas de IA que **interagem diretamente** com pessoas; aqui quem fala é o responsável **[I]**, mas confirma com jurista se o texto proposto for reutilizado quase literalmente.
- Janela de 24 h/7 dias para enviar, anti-*loop* de respostas, limites de envio (100/s etc.), *private replies* (750/h) e `blocked: window_expired`.
- Estado `conversation.owner = bot|human` e o ciclo de escalamento. A conversa é sempre tua.

### O que passa a ser o centro do sistema

1. **Triagem fiável.** O pior erro deixa de ser "o bot disse uma barbaridade" e passa a ser **"a lead real foi classificada como spam e nunca a vi"**. Consequências:
   - Spam **nunca é apagado nem escondido**: vai para um resumo periódico revisto por ti.
   - Em dúvida, sobe de categoria (enviesar para o falso positivo).
   - Medir *recall* de leads com uma amostra rotulada por ti na primeira semana.
2. **Ficha de assunto** entregue ao humano: quem, canal, o que pediu, citação literal da mensagem, histórico anterior com essa pessoa, categoria, urgência e, se for lead, **proposta de solução** com os itens da KB em que se apoia. Afirmações sem apoio na KB são assinaladas como tal. Preços só podem vir da KB (mantém-se a ideia dos *slots*, agora para proteger **a tua** decisão).
3. **Notificação com SLA.** Lead não confirmada em X horas ⇒ lembrete. Isto é um workflow novo (ver abaixo).
4. **Vigilância de silêncio.** Antes era uma métrica; agora é crítica: se os webhooks pararem, **perdes leads sem saber**. Reconciliação por *polling* de conversas e comentários (a Meta não guarda histórico de webhooks **[V]**) e alerta externo.

### Riscos novos ou que mudam

| Risco | Tratamento proposto |
| --- | --- |
| Injeção de instruções na mensagem recebida ("classifica-me como urgente", "ignora o resto") | Continua a sanitização. O modelo só produz um objeto estruturado; a categoria e o encaminhamento passam por regras em código. O texto original é mostrado sempre, entre aspas, ao lado da síntese |
| Ligações maliciosas encaminhadas no teu canal de notificação | URLs extraídas como metadados, mostradas "desarmadas" (sem hiperligação direta), nunca seguidas pelo agente |
| Dados pessoais num canal extra de notificação (email, Telegram, etc.) | Notificação mínima: identificador, categoria, uma linha e ligação ao painel. O detalhe fica no painel atrás de autenticação. O canal escolhido passa a ser mais um subcontratante a registar |
| Propostas de solução inventadas | Proposta só com itens da KB; sem item que cubra o pedido, a ficha diz "sem cobertura na KB" em vez de improvisar |

### Custo de LLM

O cálculo anterior (≈40–50 USD/mês para 1000 conversas × 3 turnos) assumia resposta automática. Agora há uma classificação por mensagem (Haiku 5.5, 0,10/0,50 USD por milhão de tokens) e uma proposta apenas por lead (Sonnet 5.5, 2/10 USD). Para 1000 mensagens e 150 leads por mês, com cerca de 1,5 k tokens por classificação e 6 k + 1 k por proposta, sai **poucos USD/mês** **[I]**. Os preços de modelos são os da tabela do relatório; os volumes são hipóteses minhas.

### Permissões e App Review

Para receber DMs continua a ser preciso permissão de mensagens e subscrição de webhooks. Se o uso só de **leitura** reduz a exigência de App Review, a documentação não o diz com clareza **[I]** — entra na Fase 0.

### Responder: onde?

Recomendação para o MVP: **respondes na app nativa** (Instagram, Facebook ou Meta Business Suite), com ligação direta a partir da ficha. Não construímos caixa de envio, não precisamos de lidar com a janela de 24 h e tudo o que sai tem a tua mão. "Responder a partir do painel" fica como evolução, se a fricção justificar.

## Impacto nos documentos

| Local | Estado |
| --- | --- |
| Relatório, secção 5 (camada de IA): pipeline de atendimento e níveis de autonomia | **Superado** por D2 |
| Relatório, secção 7 e 12: W5, W7, W8, W9 e fases do roadmap que dependem de atendimento | **Superados** (ver tabela abaixo) |
| Relatório, secção 9: AI Act, divulgação | Reduzido (ver D2) |
| Relatório, secção 14: R-riscos de resposta errada ao cliente | Substituídos pelos riscos novos acima |
| Anexo A: `conversation.owner`, `message(out)`, `blocked: window_expired` | Podem sair do MVP. Faltam `triage_result`, `notification` e `lead.proposal` |
| Anexo B: W5, W7, W8, W9 | Reescritos abaixo, em resumo |

### Workflows revistos (resumo)

| Workflow | Nova definição |
| --- | --- |
| W5 — Publicação | Um caminho para ambas as redes: `prepare_publish` a T−10 min → contentor → `media_publish` (Instagram) / `POST /{page_id}/feed` imediato (Facebook). `missed` + catch-up no arranque. Nunca publica sem `approved_content_hash` válido |
| W7 — Triagem de DMs | Webhook → sanitizar → `classify_message` (categoria, urgência, sentimento, `needs_attention`, `confidence`) → regras em código → **ficha de assunto** → notificação. Sem envio |
| W8 — Triagem de comentários | Igual à W7. Spam e abuso só **sugeridos** para moderação, no resumo. Esconder comentários fica fora do MVP |
| W9 — Leads | `extract_lead` + `propose_solution` (com KB) → ficha com proposta → notificação imediata → lembrete se sem confirmação. Estado do lead atualizado por ti no painel |
| W15 (novo) — Notificações | Entrega, confirmação (*ack*), lembretes, resumo diário/semanal por categoria, falha de entrega ⇒ fallback para outro canal |

## Consequência

- O MVP encolhe de forma clara: sai o módulo de resposta, entra a triagem. O roadmap do relatório (≈6–9 semanas de MVP) deve baixar, mas **não estimo de novo antes de fechares as perguntas abaixo**.
- A Fase 0 (spike Meta) mantém-se e passa a validar: webhooks de DMs e comentários, permissões em modo leitura, publicação nas duas redes pelo nosso scheduler.
- Mantém-se o papel da aprovação humana no centro: nada sai sem `approved_content_hash`.

## Decisões confirmadas pelo responsável

| # | Decisão | Resposta |
| --- | --- | --- |
| D3 | Canal de notificação | **Só o painel.** Sem email, Telegram nem outro subcontratante |
| D4 | Cadência | Lead e reclamação imediatas; perguntas simples em resumo (≈2×/dia); spam em resumo diário |
| D5 | Spam | Só informar e sugerir; não esconder nem apagar automaticamente no MVP. A resposta foi "sim" à pergunta "só informar ou esconder?"; li como aceitação da recomendação (só sugerir). **A confirmar** |
| D6 | Responder | App nativa (Instagram, Facebook, Business Suite) no MVP, com ligação direta a partir da ficha |

| D7 | Confinamento | **Tailscale** para a aplicação e os utilizadores (ver secção D7) |

### Consequências de D3 (só painel)

- Sai o subcontratante extra e o problema de dados pessoais num canal externo. A notificação "mínima" da secção anterior deixa de ser necessária: a ficha completa vive no painel.
- Fica uma lacuna: **o painel só avisa quem o tem aberto**. Uma lead imediata que ninguém vê durante horas não gera nada. Opções que continuam a ser "só painel": contador no título do separador, notificações do navegador/PWA e ordenação por urgência. Os lembretes de lead sem confirmação passam a ser lembretes **no painel**.
- O alerta externo de "sistema parado" (secção D1) precisa de um canal que não esteja no Pi. Proponho um serviço de *heartbeat* que envia um email sem dados pessoais ("o sistema não responde"). **Precisa da tua aprovação**, porque é a única exceção ao "só painel".
- W15 encolhe: estado de leitura, *ack*, lembretes e resumos no painel; sem entrega externa nem *fallback* de canal.

## D7 — Tailscale confina a aplicação e os utilizadores

Decisão do responsável (2026-11-03): a aplicação e os utilizadores ficam dentro da tailnet. Factos lidos na documentação da Tailscale **[V]** em `tailscale.com/kb/1223/funnel` e `/kb/1312/serve`.

### O que muda

| Área | Antes (relatório) | Agora |
| --- | --- | --- |
| Acesso ao painel | Cloudflare Tunnel + Cloudflare Access | **`tailscale serve`**: URL só na tailnet, certificado HTTPS automático no nome `*.ts.net` **[V]** |
| Autenticação | Cloudflare Access + sessão | O `serve` envia `Tailscale-User-Login` e `Tailscale-User-Name` e remove esses cabeçalhos se o cliente os enviar **[V]**. O backend pode usar a identidade para o login e mapear *login* → papel (aprovador, leitor). A ACL da tailnet decide quem chega ao painel |
| `cloudflared` no Compose | Obrigatório | Sai do painel. Ver "Webhook" abaixo |
| Utilizadores | Contas próprias | Identidade vem da Tailscale; ficam só papéis e auditoria (`audit_log` com o *login*) |

Condições para a identidade ser fiável **[V]**: o backend escuta **apenas em localhost**, senão alguém na rede pode falsificar os cabeçalhos. Os cabeçalhos **não** vêm em tráfego de dispositivos com *tag* **[V]**, por isso os utilizadores humanos não devem aceder a partir de máquinas marcadas.

### O que a Tailscale não resolve

A Meta não está na tua tailnet, por isso **três coisas continuam públicas**:

1. **Webhook** `POST /hooks/meta` (e o `GET` de verificação).
2. **URL da media** que o Instagram vai buscar à hora da publicação.
3. **Páginas que a Meta pode exigir públicas** (política de privacidade, instruções de eliminação de dados) **[I]**. A validar na Fase 0, porque a documentação consultada não o confirma.

### Webhook: Funnel ou Cloudflare Tunnel

| | Tailscale Funnel | Cloudflare Tunnel |
| --- | --- | --- |
| Estado | Mesmo produto que já usas | Segundo produto a operar |
| Domínio | Só `<tailnet>.ts.net` **[V]** | Domínio próprio |
| Portas públicas | Só 443, 8443 e 10000 **[V]** | Livre |
| Requisitos | HTTPS ativo, MagicDNS e o atributo `funnel` na política da tailnet **[V]** | Conta e domínio Cloudflare |
| Limites | Largura de banda limitada e **não configurável**; a página não dá números **[V]** | WAF e *rate limiting* disponíveis **[I]** |
| Exposição | Só o recurso partilhado **[V]** | Só as rotas configuradas |
| Na mesma porta que o `serve` | **Não** pode ser **[V]**: o último comando (`serve` ou `funnel`) decide se a porta é privada ou pública | Sem conflito |
| Cabeçalhos de identidade | Não existem em Funnel **[V]** | Não aplicável |

**Recomendação [I]:** começar com **Funnel só para o recetor do webhook**, porque já usas Tailscale e evita um segundo produto. Manter a Cloudflare como alternativa se a Meta recusar a porta, se o Funnel não aguentar a carga ou se quiseres WAF. Como a assinatura HMAC valida cada pedido, o recetor público tem pouca superfície.

### Regras de desenho que isto impõe

1. **Separar o recetor do webhook do painel.** Dois processos (ou dois *listeners*) com portas locais diferentes. O Funnel aponta só para o recetor. O painel fica atrás do `serve` e nunca é exposto por engano. O recetor só valida a assinatura e grava o corpo bruto (como já estava no W6).
2. **O painel e o recetor não podem partilhar a porta pública.** Por exemplo, painel no `serve` na 443 e recetor no Funnel na 8443 ou 10000. Falta confirmar se a Meta aceita um URL de *callback* com porta diferente de 443 **[I]**. Entra na Fase 0.
3. **Media para o Instagram:** a opção B do relatório (armazenamento de objetos com URL assinado) continua a ser a recomendada. Servir imagens pelo Funnel é possível, mas com a largura de banda limitada e desconhecida **[V]**, vídeo e Reels seriam arriscados.
4. **Dependência nova:** se o Funnel cair, deixam de chegar webhooks. A reconciliação por *polling* já prevista cobre isso.
5. O alerta externo de "sistema parado" (D1/D3) continua a ser preciso: a Tailscale não vigia o Pi.

### Efeitos secundários favoráveis [I]

- O HTTPS em `*.ts.net` dá um contexto seguro ao painel, o que permite notificações do navegador e PWA. É a melhor resposta "só painel" para o problema de a lead ninguém ver durante horas.
- Acesso administrativo ao Pi e cópias de segurança para outro nó podem passar pela tailnet, sem abrir portas.

### Respostas do responsável (2026-11-03)

1. A tailnet existe e é administrada pelo responsável. **O Pi ainda não está nela e tem de ser configurado.** Passa a ser tarefa da Fase 0: instalar a Tailscale no Pi, ativar HTTPS e MagicDNS, autorizar o atributo `funnel` na política e confirmar NTP.
2. **Utilizador único.** Não há papéis nem quatro-olhos. A identidade da Tailscale serve de porta de entrada e de autor no `audit_log`. A aprovação passa a ser sempre do mesmo utilizador, por isso o risco de "aprovar sem ler" é maior e as proteções de conteúdo (validações automáticas, `approved_content_hash`, aviso de alegações sem apoio na KB) valem mais.
3. Funnel ou Cloudflare: em discussão (pros e contras no chat). O recetor do webhook é um processo separado, por isso trocar depois só exige mudar o URL de *callback* na app Meta.

## Decisões de 2026-11-03

| # | Decisão | Estado |
| --- | --- | --- |
| D8 | **Funnel** para o recetor do webhook (e URLs de media do MVP) | Decidido. Cloudflare Tunnel fica como plano B |
| D9 | LLM por **API compatível com OpenAI**, com `base_url` configurável. No MVP, **modelos locais** (Ollama ou LM Studio); OpenAI como destino de escala | Decidido. Falta saber em que máquina corre o modelo local |
| D10 | Media: orçamento mínimo com caminho de escala. Proposta: MVP só com imagens servidas pelo Funnel; `MediaStore` abstrato; Cloudflare R2 quando houver vídeo ou volume | Proposta, à espera de confirmação |
| — | Contas Meta (Página, Instagram profissional, Business Portfolio, app de programador) ainda **não existem**; o responsável cria e administra | Ver `Knowledge/2026-11-03T09-59-50…meta_app_review_access_levels.md` |

Factos de suporte em `Knowledge/2026-11-03T09-59-51…infra_tailscale_r2_ollama_custos.md`.

**Disposição de portas proposta [I]:** Funnel na 443 (`/hooks/meta` e `/media/...`, ambos para o recetor) e painel por `tailscale serve --https=8443`, só tailnet. Assim o callback da Meta fica em 443. O recetor escuta numa porta local distinta da do painel. A Fase 0 confirma o encaminhamento por caminho, o Funnel no plano grátis e a porta.

**Risco que a D7 cria com a Meta:** se o App Review vier a ser necessário, a Meta tem de conseguir testar a app e, se não conseguir, rejeita a submissão inteira **[V]**. Um painel só dentro da tailnet não o permite. Por isso a Fase 0 testa primeiro se o Standard Access chega.

## Decisões de 2026-11-03 (segunda ronda)

| # | Decisão | Estado |
| --- | --- | --- |
| D9 (revista) | **Fornecedor de API (OpenAI ou Claude) com orçamento definido no MVP.** Os modelos locais ficam para depois | Decidido. Substitui a D9 anterior. Falta o valor do orçamento e o fornecedor inicial |
| D10 | Media: imagens pelo Funnel no MVP, `MediaStore` abstrato, R2 quando houver vídeo ou volume | Confirmado |
| D10b | Armazenamento do Pi: **disco rígido (HDD) que já existe** no MVP; evolui para SSD se for preciso | Confirmado. Ver riscos |
| D11 | Backend em **Python**. Convenção de nomes **snake_case/PEP 8**, que prevalece sobre o camelCase por omissão do `AGENTS.md` (o próprio `AGENTS.md` manda seguir o padrão idiomático da linguagem). TypeScript só em `AgentDATA/scripts/` | Decidido |
| D12 | **A marca existe e nunca teve marketing.** O sistema serve de caso de estudo: o agente ajuda a "recriar/revitalizar" a empresa e a definir uma estratégia de longo prazo | Decidido. Aumenta o âmbito (ver abaixo) |
| D13 | Disposição de portas (Funnel 443, painel `serve` 8443) e conclusões sobre App Review aceites | Aceite. A resposta "okay documenta" cumpre-se no registo `Knowledge/…meta_app_review_access_levels.md` |

### Decisões de 2026-11-03 (terceira ronda)

| # | Decisão | Estado |
| --- | --- | --- |
| D14 | Orçamento de LLM de **20 USD por mês**, com aviso a 50 % e 80 % e corte das tarefas não críticas ao limite | Decidido |
| D15 | Disco do Pi: **HDD de 2,5" e 1 TB** | Decidido |
| D16 | O **construtor de identidade de marca** é funcionalidade do produto, não só trabalho manual | Decidido. W1 passa a funcionalidade, ver Anexo C |
| D17 | Spam só sugerido; email de heartbeat como única exceção ao "só painel" | Confirmado |
| D18 | Comercialização futura é uma **opção**, não um objetivo. Decisões baratas que a mantêm aberta no [Anexo C](2026-11-03T10-43-44+00-00_anexo_c_mercado_certificacoes_e_conceito_comercial.md) | Aceite como direção |

**Correção de custos:** o Tailscale Personal é para uso não comercial. Para a empresa, o custo esperado é o do plano Standard, 8 USD por utilizador por mês **[V preço; S restrição]**, se a tailnet da empresa ainda não tiver plano. Falta confirmar o plano atual e se o Funnel está incluído.

### Hardware disponível [V: captura de ecrã do LM Studio]

Portátil com Intel Core Ultra 9 275HX, 31,4 GB de RAM e NVIDIA RTX 5080 Laptop com 15,9 GB de VRAM. Cabe um modelo local de gama média (na ordem de 14–24 mil milhões de parâmetros, quantizado) **[I]**. É um portátil, não um servidor 24 h, por isso fica como opção futura para triagem com dados sensíveis, via `base_url` compatível com OpenAI. Não é caminho crítico.

### D9 revista: o que implica

- `LlmClient` passa a ter **dois adaptadores nativos** (OpenAI e Anthropic), porque as APIs diferem. Mantém-se um terceiro, compatível com OpenAI, para o modelo local.
- **Orçamento como regra de código:** `cost_ledger` com limite mensal e diário, aviso a 50 % e 80 %, e corte automático das tarefas não críticas ao atingir o limite. O Anexo A já prevê a tabela.
- **Preços de referência [V para Claude, S para OpenAI]:** Claude por milhão de tokens (entrada/saída): Haiku 5.5 0,10/0,50 USD, Sonnet 5.5 2/10, Opus 5.5 4/20 (tabela do skill `claude-api`, 2026-10-06). OpenAI: GPT-5.4 nano 0,20/1,25 e mini 0,75/4,50 (agregadores, sem confirmação oficial).
- **Recomendação [I]:** começar por **um** fornecedor, com um modelo pequeno para triagem e um modelo forte só para estratégia e planeamento. Estratégia é raro e curto em volume, por isso o modelo caro pesa pouco: um documento de estratégia de 20 mil tokens de entrada e 8 mil de saída em Opus 5.5 custa cerca de 0,24 USD.
- **Dados pessoais:** DMs vão para um fornecedor fora do teu controlo. Mitiga-se mascarando telefones, emails e *handles* antes do envio e registando o fornecedor como subcontratante **[I]**.

### D10b: HDD no Pi — riscos e condições [I]

- Serve para este volume. O Postgres fará poucas escritas e o relógio de publicação não depende de latência de disco.
- **Risco principal: falha ou corrupção.** HDD é mais frágil que SSD, sobretudo com cortes de energia. Backups externos testados passam de recomendação a obrigação.
- Se o disco for de 2,5" por USB, confirmar que a alimentação chega (Pi 5 com fonte de 27 W). Disco de 3,5" precisa de fonte própria.
- O Pi deve arrancar de cartão ou de SSD pequeno e guardar no HDD só dados e registos, para evitar o disco no caminho do arranque. A decisão final fica para a Fase 0.

### D12: marca sem marketing — impacto no âmbito [I]

Antes, o relatório assumia um perfil de marca já existente (W1) e fazia da KB uma entrada. Agora a estratégia é uma **saída** do sistema.

1. **Novo workflow de diagnóstico e estratégia**, anterior ao `plan_week`: entrevista estruturada ao responsável, recolha do material existente (site, catálogo, fotos, clientes), análise de mercado e concorrência com fontes citadas, posicionamento, público, pilares de conteúdo, objetivos mensuráveis, plano trimestral.
2. **Pesquisa na web passa a estar dentro do âmbito** (pergunta 19 do relatório). Exige citar fontes e marcar o que é suposição, porque o risco é uma estratégia assente em factos de mercado inventados.
3. **O `plan_week` passa a derivar da estratégia** e dos objetivos, não só do calendário editorial.
4. **Arranque a frio:** sem público nem histórico, não há métricas de base. Os insights (W10) só ganham valor depois de semanas de publicação, e a primeira avaliação deve ser de processo (consistência, aprovação, tempo) e não de resultados.
5. **Contas novas:** uma Página e um Instagram sem histórico podem ter limites de confiança mais apertados da Meta **[I]**. Não afeta a Fase 0, mas deve entrar nos riscos.
6. **A estratégia pode começar já, sem esperar pela infraestrutura.** É sobretudo texto e pesquisa. Proponho uma **faixa paralela de estratégia** durante a Fase 0, feita em sessões como esta e gravada em `AgentDATA/docs/`. Depois o sistema ingere esse perfil como KB inicial.
7. **Esforço:** soma-se na ordem de 1 a 2 semanas ao roadmap, em grande parte sobreposto à Fase 0 **[I]**.

## Roadmap revisto [I]

Esforço para uma pessoa a tempo inteiro, como no relatório. Não inclui prazos de App Review.

| Fase | Antes | Agora | O que mudou |
| --- | --- | --- | --- |
| 0 — Spike Meta | 3–5 dias | 3–4 dias | Sem teste de *private reply* nem de agendamento nativo. Passa a validar DMs e comentários em modo leitura e a publicação nas duas redes |
| 1 — Núcleo | 2–3 sem. | 2–3 sem. | Entra o scheduler próprio (`run_at`, *leader lock*, catch-up) e o *heartbeat* externo. Compensa o que sai do Facebook nativo |
| 2 — Marketing e conteúdos | 3–4 sem. | 3–4 sem. | Sem alteração |
| 3 — Triagem e leads | 3–4 sem. | 2–3 sem. | Sem pipeline de resposta, autonomia nem *red team* de envio. Entram ficha de assunto, proposta de solução, painel de leitura e 2 semanas em que rotulas a triagem |
| 4 — Análise e consolidação | 2–3 sem. | 2 sem. | Sem RGPD de canal externo nem divulgação de IA |
| **Total** | 11–17 sem. | **≈ 9–14 sem.** | |

O ganho de tempo é pequeno (1 a 3 semanas). O ganho grande é de **risco**: desaparecem os modos de falha com consequência pública. O MVP do resumo (planear → aprovar → publicar) continua a fechar no fim da Fase 2, em 6–9 semanas.

## Perguntas ainda em aberto

Das 20 do relatório ficam respondidas: 11 (painel), 15 (canal) e 18 (moderação). Para desbloquear a Fase 0 faltam sobretudo:

- **6.** A Página e o Instagram já são profissionais e ligados? Existe Business Manager? Quem é administrador?
- **7.** Aceitas submeter App Review/verificação de negócio se for necessário?
- **8.** Python (PEP 8) ou TypeScript? Recomendação: Python no backend, TypeScript só em `AgentDATA/scripts/`.
- **9.** Fornecedor e residência de dados do LLM.
- **1–5.** Setor, volumes, quem aprova, idiomas, fonte de preços: necessárias para a KB e para os testes, não para a Fase 0.

## Riscos

- Falsos negativos na triagem (lead perdida). Mitigados por viés para cima, resumo de spam e amostra rotulada.
- Pi como ponto único de falha agora afeta **também** a entrada de leads. Mitigado por vigilância externa e reconciliação.

## Pendências

- Atualizar relatório e anexos (ou arquivar e reescrever após a discussão).
- Definir a ficha de assunto e o esquema de `classify_message`/`propose_solution` quando a KB existir.
