# Anexo D — Arquitetura multiagente: a equipa de marketing

- Timestamp: `2026-11-05T18:58:45+00:00`
- Autor: `agent`
- Assunto: Spec de design da divisão do agente de marketing em papéis especializados, cada um com ferramentas e comportamentos próprios
- Estado: **para revisão do responsável** (resultado de sessão de brainstorming, 2026-11-05)
- Documentos irmãos: [Relatório](2026-11-02T12-25-21+00-00_relatorio_analise_marketing_ai_manager.md) · [Anexo A](2026-11-02T12-25-22+00-00_anexo_a_modelo_de_dados_e_contratos.md) · [Anexo B](2026-11-02T12-25-23+00-00_anexo_b_especificacao_de_workflows.md) · [Adenda 01](2026-11-02T14-23-57+00-00_adenda_01_decisoes_agendamento_proprio_e_triagem.md) · [Anexo C](2026-11-03T10-43-44+00-00_anexo_c_mercado_certificacoes_e_conceito_comercial.md)

Legenda: **[V]** verificado em fonte oficial; **[I]** inferência ou estimativa minha. Preços de modelos: tabela do skill `claude-api` (2026-10-06), em USD por milhão de tokens (entrada/saída): Haiku 5.5 0,10/0,50; Sonnet 5.5 2/10; Opus 5.5 4/20. Os custos abaixo **não incluem tokens de raciocínio**, e no Opus 5.5 o raciocínio não pode ser desligado **[V]**.

Onde houver conflito, este anexo prevalece sobre o Anexo B nos workflows W1 a W5 e W7 a W9 quanto a **quem** os executa.

## Contexto

O responsável quer uma equipa de agentes com papéis distintos (gestor de redes sociais, diretor de marketing, agentes que desenvolvem as publicações), cada um com ferramentas e comportamentos específicos, em vez de um agente único. Este anexo regista o que ficou decidido e o desenho resultante.

### Brief acordado

| Tema | Decisão |
| --- | --- |
| Motivo da divisão | **Qualidade por especialização**. Só compensa se cada papel tiver contexto, modelo ou ferramentas realmente diferentes |
| Como se mede | **Comparação cega A/B** (verificação periódica) mais **taxa de edição** guardada em segundo plano |
| Significado de "harness específico" | Perfil por papel (ferramentas e comportamentos próprios) sobre um **motor comum**; não são processos nem bases de código separadas |
| Diretor de Marketing | Estratégia e plano semanal **com datas** |
| Gestor de Redes Sociais | Executa: calendário, lembretes, falhas. Sobretudo código; LLM só nas exceções |
| Triagem | Papel separado, **pipeline fixo**, sem ferramentas de calendário |
| Criadores | **Copywriter** e **Designer Visual** (separação por disciplina) |
| Revisor | Mesmo fornecedor, **modelo e rubrica distintos**, depois de validadores em código |
| Orquestração | **Fluxo fixo em código**, com um Pesquisador isolado sob o Diretor |
| Aprendizagem | Cada edição ou rejeição é guardada como exemplo; sem treino de modelos |
| Aprovações | O **plano semanal** e **cada publicação** precisam da aprovação do responsável |
| Ordem de construção | **Diretor em modo `interview` primeiro** (motor, painel de conversa e Diretor produzem o perfil de marca); depois o núcleo de criação sobre um perfil real. Substitui a decisão anterior "núcleo de criação primeiro" |
| Abordagem do motor | **A: motor próprio fino** com perfis por papel |
| Limite global de custo | 20 USD por mês; ao atingi-lo continuam só a classificação da Triagem e o Publicador |
| Fornecedor e modelo por papel | **Configuráveis e não decididos.** O responsável pode querer trocar de fornecedor para testar. O motor mapeia cada papel a uma classe de modelo por configuração, e a comparação A/B pode também comparar fornecedores |
| Informação da marca | **O Diretor extrai-a em conversa com o responsável** (modo de entrevista no painel); não se pede antecipadamente |
| Venda do produto | Fora de foco. Mantêm-se só decisões baratas que não fecham a porta (`brand_id` nas tabelas, interfaces abstratas) |

### Porquê esta forma [V]

- A Anthropic distingue **workflows** (LLMs e ferramentas orquestrados por caminhos de código predefinidos) de **agentes** (o modelo dirige os seus próprios passos) e aconselha só aumentar a complexidade quando melhora os resultados de forma demonstrável.
- Sistemas multiagente gastam cerca de 15× os tokens de um chat e um agente isolado cerca de 4×. Com 20 USD por mês, cada papel tem de justificar o custo.
- Os frameworks podem acrescentar camadas de abstração que escondem os prompts; a recomendação é começar com chamadas diretas à API.
- No skill `claude-api`: o modelo emite chamadas de ferramentas e o harness trata-as; ações difíceis de reverter (chamadas externas, envio de mensagens) devem ser ferramentas dedicadas e controladas. Trocar de modelo a meio de uma sessão invalida a cache, por isso modelos diferentes ficam em cadeias de chamadas separadas.

## Decisão ou análise

### 1. Elenco, ferramentas e limites

| Papel | Ciclo | Ferramentas | Não pode |
| --- | --- | --- | --- |
| **Diretor** | Entrevista e pesquisa com checkpoints | Conversar com o responsável no painel (entrevista); ler perfil da marca e KB; ler métricas; pedir pesquisa ao Pesquisador; escrever rascunho de perfil de marca, de estratégia e de plano semanal | Publicar; falar com a Meta; ler mensagens de clientes |
| **Pesquisador** | Pesquisa limitada | Pesquisa e leitura na web; gravar nota com fonte | Ler dados privados da marca; escrever em qualquer outro sítio |
| **Gestor de Redes Sociais** | Código, com LLM nas exceções | Ler calendário e estado dos posts; propor novo horário; lembrar aprovações | Escrever texto de publicações; publicar |
| **Copywriter** | Uma chamada com esquema e validação | Ler voz da marca, KB e exemplos aprovados e rejeitados; validar limites de texto | Web; Meta; calendário |
| **Designer Visual** | Uma chamada com esquema e validação | Ler ativos da marca; compor imagens com modelos em código; validar formato (JPEG); escrever texto alternativo | Meta; calendário |
| **Revisor** | Uma chamada com rubrica | Ler rascunho, perfil e KB; verificar alegações; devolver aprovar, rever ou rejeitar | Editar o rascunho |
| **Triagem** | Pipeline fixo (classificar, extrair lead, propor) | Ler mensagem, histórico do contacto e KB; escrever a ficha | Calendário; web; enviar mensagens |
| **Publicador** (código, não é agente) | Determinístico | Única peça com escrita na Meta; só publica com `approved_content_hash` válido | — |

Classes de modelo [I]: Diretor (estratégia trimestral) em modelo forte; Diretor (plano semanal), Pesquisador, Copywriter, Designer e Revisor em modelo intermédio; Triagem em modelo pequeno para classificar e intermédio para propor. O **Revisor usa um modelo diferente do dos criadores**. A escolha concreta de modelo por papel depende do fornecedor (ver Pendências).

**Regras transversais**

1. Cada ferramenta é uma função tipada. O motor só expõe ao agente as da sua lista. **Escrever na Meta nunca é ferramenta de agente.**
2. Texto não confiável (web, DMs, comentários) é tratado como dado, nunca como instrução, e só entra em papéis sem poder: Pesquisador e Triagem.
3. Entre Revisor e criadores há no máximo **2 voltas**. À terceira, o rascunho vai para o responsável com as notas do Revisor.
4. O Analista de métricas só entra na Fase 4.

### 2. Especificação de um papel e tipos de ciclo

Cada papel é uma **especificação versionada** (`AgentSpec`):

| Campo | Conteúdo |
| --- | --- |
| `role` e `version` | Identificador e versão do conjunto prompt, ferramentas e esquema |
| `loop_type` | `interview`, `research`, `structured` ou `pipeline` |
| `model_tier` | Classe de modelo; o motor mapeia para o fornecedor configurado |
| `prompt` | Ficheiro versionado |
| `tools` | Lista fechada de ferramentas permitidas |
| `reads` e `writes` | Âmbitos de dados lidos e tipos de artefacto que pode produzir |
| `output_schema` | Esquema do artefacto de saída |
| `limits` | Máximo de passos, de tokens e de custo por execução |
| `budget_share` | Parte do orçamento mensal |
| `escalation` | Quando devolve a tarefa ao responsável |
| `validators` | Verificações em código antes de entregar |
| `eval_set` | Conjunto fixo de briefs para regressão |

**Quatro tipos de ciclo bastam:**

- `interview`: diálogo de várias sessões com o responsável no painel, com estado gravado. O Diretor mantém um **mapa de lacunas** do perfil de marca (o que já sabe, o que falta, o que é suposição), faz **uma pergunta de cada vez**, resume o que percebeu e só grava o perfil depois de o responsável confirmar. Usado pelo Diretor em W1 e sempre que o plano exigir informação que falta.
- `research`: várias chamadas de ferramentas com checkpoints gravados em base de dados; exige fontes citadas; compacta o contexto. Usado por Diretor e Pesquisador.
- `structured`: uma chamada com esquema de saída, validação e até 2 repetições com a mensagem de erro. Usado por Copywriter, Designer e Revisor.
- `pipeline`: passos fixos em código, cada um uma chamada estruturada. Usado pela Triagem.

### 3. Fluxo, artefactos e aprovação

```
Diretor ─► plano semanal ─► responsável aprova o plano
                                   │
Gestor ─► um brief por slot ◄──────┘
              │
              ├─► Copywriter ─► texto ─► Designer ─► visual
              │                                │
              └─ validadores em código ◄───────┘
                        │
                      Revisor ─► aprovar / rever (máx. 2 voltas) / rejeitar
                        │
        fila de aprovação no painel ─► responsável aprova, edita ou rejeita
                        │
          Gestor agenda ─► Publicador publica ─► Gestor monitoriza falhas
                        │
        cada edição ─► exemplo de feedback ─► prompts dos criadores
```

- Os papéis trocam **artefactos tipados**: `plano`, `brief`, `rascunho_texto`, `rascunho_visual`, `revisao`, `ficha_lead`, `nota_pesquisa`. Nunca conversa livre.
- Os artefactos são **imutáveis**: uma alteração cria uma versão nova com a anterior como pai. Estados: `draft`, `submitted`, `approved`, `rejected`, `superseded`.
- O Designer recebe o texto final do Copywriter, porque o visual depende do gancho. A sequência não pesa num fluxo assíncrono.
- O pacote que chega ao responsável traz texto, visual, parecer do Revisor, alegações usadas e a origem de cada parte.
- A aprovação da publicação grava `approved_content_hash`; qualquer edição posterior invalida-a (como no Anexo B, W4).

**Aprendizagem:** cada edição ou rejeição guarda o rascunho, a versão final e um motivo de um clique em `feedback_example`. O código escolhe os exemplos mais relevantes por pilar e canal, com limite de quantidade, e injeta-os nos prompts do Copywriter e do Designer.

### 4. Dados novos

| Tabela | Conteúdo |
| --- | --- |
| `agent_definition` | Papel, versão, `AgentSpec` em JSON, ativo |
| `agent_run` | Execução: papel, versão, gatilho, marca, estado, custo, início e fim |
| `agent_step` | Passo de uma execução: chamada de LLM ou de ferramenta, nome, resumo da entrada, referência da saída, tokens, custo, erro |
| `artifact` | Artefacto tipado: tipo, versão do esquema, marca, execução de origem, artefacto pai, estado, conteúdo |
| `feedback_example` | Artefacto, ação do responsável, diferença, motivo |

Ligam-se às tabelas já previstas `llm_call`, `cost_ledger` e `job` do Anexo A. Todas levam `brand_id` desde o início (Anexo C, secção 6).

### 5. Orçamento, segurança e avaliação

**Orçamento (20 USD por mês).** Limites suaves por papel, a rever com dados reais **[I]**: núcleo de criação cerca de 7 USD; Diretor e Pesquisador cerca de 7 USD (a estratégia inicial é um pico); Triagem cerca de 4 USD; Gestor e reserva cerca de 2 USD. Antes de cada execução o motor compara o custo do mês, o limite do papel e o global. Avisa no painel a 50 % e a 80 %. Cada execução tem limites de passos e de tokens. Ao atingir o limite de um papel, param as tarefas não críticas dele. **Ao atingir o limite global continuam apenas a classificação da Triagem (modelo mais barato) e o Publicador.**

Estimativa de custo mensal em regime estável **[I]**: núcleo de criação 3 a 4 USD (cerca de 0,06 USD por volta e 0,11 USD com uma volta extra, 30 publicações); Diretor 1 a 2 USD; Triagem cerca de 4 USD; Gestor e Revisor marginais. Total cerca de 9 a 11 USD, mais um arranque de estratégia de 5 a 10 USD uma vez. Cabe nos 20 USD, sem grande folga.

**Segurança.**

- Lista de ferramentas por papel; Meta só pelo Publicador.
- Texto de fora tratado como dado e isolado em Pesquisador e Triagem.
- Toda a saída passa por validação de esquema; segunda falha vai para o painel.
- Telefones, emails e nomes de utilizador são mascarados antes de irem para o LLM na Triagem.
- Todos os passos e chamadas de ferramenta ficam em `agent_step`.
- Segredos nunca entram em prompts.

**Avaliação.**

- **Contínua:** taxa de edição, aprovação à primeira, e se o Revisor sinalizou o que o responsável acabou por editar.
- **Comparação cega A/B:** em pontos de controlo (por exemplo às semanas 4 e 8), vinte pares gerados pela equipa e por um agente único de referência com o mesmo brief e orçamento; cerca de 1 USD por ronda **[I]**. Com um só avaliador, vinte pares só detetam diferenças grandes **[I]**, por isso a A/B é verificação e não medida contínua.
- **Antes de mudar um prompt:** corre-se o `eval_set` do papel e compara-se com a versão anterior.

**Falhas.** Erro do fornecedor: repetição com espera crescente. Recusa do modelo, execução presa por tempo ou custo excedido: a tarefa pára e aparece no painel.

### 6. Mapa de workflows e estrutura de código

| Workflows (Anexo B) | Quem |
| --- | --- |
| W1 identidade de marca, W2 plano semanal | Diretor, com Pesquisador |
| W3 geração de conteúdo | Copywriter, Designer Visual, Revisor |
| W4 aprovação | Responsável; o Gestor lembra |
| W5 agendamento e publicação | Gestor decide exceções; Publicador publica |
| W7, W8, W9 triagem e leads | Triagem (pipeline) |
| W6, W10 a W15 | Código; Analista de métricas na Fase 4 |

Estrutura proposta (a fixar no plano; nomes em snake_case, Python): um pacote `harness` com o motor, os três tipos de ciclo, o orçamento e o registo de ferramentas, e uma pasta por papel com especificação, prompt versionado, esquema, ferramentas e avaliações.

### 7. Fora de âmbito (e porquê)

- Supervisor dinâmico, processos separados por agente, treino de modelos e frameworks de agentes: não há evidência de que compensem.
- Geração de imagem por IA e Reels: decisão pendente, sem impacto no motor.
- Analista de métricas: Fase 4.
- Resposta automática a clientes: já excluída (Adenda 01, D2).

## Impacto

- **Roadmap [I]:** o motor comum e as avaliações por papel somam 1 a 2 semanas, e o painel de conversa com o modo `interview` mais cerca de 1 semana. Ordem: Fase 1 (núcleo e integração) inclui o motor e o painel de conversa; Fase 2: Diretor (entrevista, perfil de marca, estratégia, plano semanal), depois núcleo de criação, depois Gestor operacional; Fase 3: Triagem; Fase 4: Analista. A faixa de estratégia manual da Adenda 01 (D12) deixa de ser necessária, porque o Diretor faz a entrevista no próprio produto.
- **Custo:** estável em cerca de 9 a 11 USD por mês, a somar ao arranque da estratégia.
- **Documentos:** os Anexos A e B têm de refletir as tabelas e os papéis acima quando o plano de implementação for escrito.
- **Comercial:** a venda não é foco. A divisão em papéis só mantém essa porta aberta (papéis configuráveis por marca, Anexo C), sem trabalho extra agora.
- **Interface:** o modo `interview` exige no painel uma conversa com o Diretor, com sessões gravadas e retomáveis.

## Riscos

| Risco | Mitigação |
| --- | --- |
| Custo multiplicado por vários agentes (cerca de 15× um chat [V]) | Fluxo fixo, limites por papel e por execução, orçamento global com corte |
| Papéis a mais para o volume (excesso de engenharia) | Construir por fases; cada papel tem de passar a sua avaliação antes do seguinte |
| Erros correlacionados entre criador e Revisor | Modelo e rubrica distintos; validadores em código antes |
| Estratégia errada propaga-se aos criadores | Aprovação do plano e da estratégia; fontes citadas; suposições marcadas |
| Um só avaliador (viés e cansaço) | Revisor filtra primeiro; taxa de edição em segundo plano; A/B só como verificação |
| Manutenção de muitos prompts | Versionados por papel, com `eval_set` e regressão antes de cada mudança |

## Pendências

1. ~~Fornecedor e modelo por papel~~: **resolvido como configuração**; não bloqueia nada e pode mudar para testes.
2. Ferramenta de imagem: composição por modelos de layout, geração por IA, ou ambas (pergunta 13 do relatório). Em aberto.
3. ~~Informação da marca~~: **passou a capacidade do Diretor** (modo `interview`).
4. Plano Tailscale da empresa e se inclui o Funnel. Em aberto; só o responsável o pode ver; fecha-se na Fase 0.
5. ~~Titularidade do código~~: **fora de foco** enquanto a venda não for objetivo.
6. ~~Ordem de construção~~: **resolvida** (Diretor em modo `interview` primeiro).

## Referencias

- [V] Anthropic: *Building effective agents* e *How we built our multi-agent research system* (`anthropic.com/engineering`), lidos em 2026-11-05.
- [V] Skill `claude-api`, `shared/agent-design.md` e tabela de preços (2026-10-06).
- Brainstorming de 2026-11-05 (decisões acima).
