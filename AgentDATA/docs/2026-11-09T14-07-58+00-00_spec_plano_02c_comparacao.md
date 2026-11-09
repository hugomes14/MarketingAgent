# Spec do Plano 2C: comparação cega entre o percurso simples e o especializado

- Timestamp: `2026-11-09T14:07:58+00:00`
- Autor: `agent`, com as decisões do responsável (2026-11-09)
- Depende de: Planos 2A e 2B (pipeline especializado completo).
- Fonte de verdade: plano mestre v2 (F03, D20, D35, 8.3, 2.3).
- Estado: **proposta para revisão do responsável**.

## 1. Objetivo e âmbito

Responder com dados à pergunta do plano mestre: **os papéis especializados (Copywriter, Designer, Revisor) melhoram o resultado o suficiente para pagar o custo?** Um papel que não melhore a qualidade, o custo ou o esforço humano sai do percurso habitual (D35).

**Fora do 2C:**

- a revisão mensal da estratégia (W7): passa para **depois do Plano 3**, por decisão do responsável, porque depende de métricas de publicações reais. Fica registado no plano mestre;
- as experiências de marketing junto do público: são outra coisa e não se misturam com esta avaliação técnica (8.3).

## 2. Decisões do responsável

| ID | Decisão |
| --- | --- |
| P2C-1 | **Avaliação cega pelo responsável** de pares lado a lado, com custo, latência e erros juntos pelo sistema |
| P2C-2 | W7 movida para depois do Plano 3 |

## 3. Percursos comparados

- **Especializado:** o pipeline do 2A e do 2B tal como está (Copywriter → Revisor → Designer → Revisor com visão, até 2 voltas).
- **Simples:** **uma chamada** (`role: simples`, mesmo contexto: brief, perfil, propostas aceites, base, lista de fotos, kit de marca) que devolve `PacoteSimples`, com os campos de `RascunhoTexto` e `RascunhoVisual`. Os mesmos validadores em código, a mesma renderização e a mesma política de repetição do motor, **sem Revisor**.
- **Justiça da comparação:** o mesmo brief, o mesmo modelo de base para criar (rota `simples` = rota `copywriter` por omissão), o mesmo teto de custo por pacote e a mesma base e fotos, fixadas no início da corrida.

## 4. Conjunto de avaliação

- **Conjunto fixo e versionado** de briefs (`evals/briefs_v1.yaml`): 8 briefs representativos da estratégia ativa (pilares diferentes, um com preço, um sem foto adequada, um com tema propenso a promessas exageradas). O responsável pode editá-lo; a versão fica gravada em cada corrida.
- **Corrida:** `marketing-agent eval run --set briefs_v1` cria, por brief, os dois pacotes em trabalhos na fila, com a marca de corrida (`eval_run_id`). Os pacotes de avaliação **nunca** entram no lote semanal nem viram posts.
- O custo da corrida é contado como qualquer outro (referência mensal) e o painel mostra a estimativa antes de começar.

## 5. Avaliação cega

- **Página "Avaliar":**
  - mostra um par de cada vez (imagem e texto), com os lados A e B atribuídos ao acaso por par (semente gravada);
  - não mostra qual é o percurso, o custo nem o parecer do Revisor.
- **Respostas por par:**
  - `melhor`: `A`, `B` ou `empate`;
  - por lado, erros marcados: `facto_errado`, `alegacao_sem_apoio`, `voz_errada`, `imagem_inadequada`, `outro`;
  - `minutos_edicao_estimados` por lado: 0, 1, 2–5 ou mais de 5;
  - nota livre opcional.
- **Integridade:**
  - a revelação só aparece depois de todos os pares da corrida estarem avaliados;
  - as respostas são imutáveis depois de gravadas (pode corrigir-se com uma resposta nova que substitui a anterior, ficando ambas gravadas);
  - o tempo por par é medido.

## 6. Relatório

Por corrida, numa página e exportável em Markdown para `AgentDATA/docs/`:

- vitórias, derrotas e empates do especializado, com o intervalo de incerteza (binomial, 95 %);
- erros por percurso e por tipo;
- minutos de edição estimados;
- custo completo por pacote (texto, imagem, voltas), latência média e máxima;
- saídas inválidas e voltas do Revisor;
- quantas vezes o Revisor sinalizou o que tu marcaste como erro (concordância).

**Limitações sempre explícitas:** número pequeno de briefs, um só avaliador, sem causalidade. Não há decisão automática: o relatório termina com a pergunta do D35 para tu decidires (manter, simplificar ou retirar um papel), e a decisão fica registada.

## 7. Dados (migração `0004`)

| Tabela | Colunas |
| --- | --- |
| `eval_run` | `brand_id`, `set_name`, `set_version`, `seed`, `status` (`running`, `ready`, `revealed`), `strategy_artifact_id`, `created_at` |
| `eval_item` | `run_id`, `brief_key`, `path` (`simples`, `especializado`), `revision_snapshot` jsonb (texto e proveniência), `final_asset_id`, `cost_usd`, `latency_ms`, `invalid_outputs`, `review_rounds`, `status` |
| `eval_judgement` | `run_id`, `brief_key`, `side_a_path`, `better` (`a`, `b`, `empate`), `errors_a`, `errors_b`, `edit_a`, `edit_b`, `note`, `seconds_spent`, `decided_by`, `decided_at`, `supersedes_id` NULL |
| `role_decision` | `brand_id`, `run_id`, `role`, `decision` (`manter`, `simplificar`, `retirar`), `justificacao`, `decided_by`, `decided_at` |

## 8. Testes

- O percurso simples usa os mesmos validadores, renderização e teto.
- Os pacotes de avaliação nunca criam posts.
- A atribuição A/B é aleatória mas reproduzível pela semente.
- A revelação fica bloqueada até todos os pares estarem avaliados.
- As respostas são imutáveis (uma substituição fica com rasto).
- Estatística: o intervalo binomial é correto em casos conhecidos.
- O relatório tem as limitações sempre presentes.
- Painel: autenticação, CSRF, escape, e não revela o percurso no HTML (nem em atributos nem nos nomes de ficheiros das imagens).

## 9. Marcos verificáveis

Cada marco fecha com: suite verde, PR fundido e a verificação abaixo feita e registada nas notas de execução do plano. Pré-requisito: o 2B fechado (M7).

| Marco | Entrega | Critério de aceitação | Como se verifica |
| --- | --- | --- | --- |
| **M1 Percurso simples** | Papel `simples` e `PacoteSimples` | Usa o mesmo contexto, validadores, renderização e teto que o especializado; nunca cria posts | `uv run pytest tests/evals`; um pacote simples gerado com o Ollama aparece só na corrida de avaliação |
| **M2 Conjunto e corrida** | `evals/briefs_v1.yaml`, migração `0004`, `marketing-agent eval run` | A corrida gera 8 × 2 pacotes pela fila, com custo e latência gravados; a estimativa de custo aparece antes de começar | `uv run marketing-agent eval run --set briefs_v1` e, no painel, a corrida passa a `ready` com 16 pacotes |
| **M3 Avaliação cega** | Página "Avaliar" | O HTML não revela o percurso (nem atributos nem nomes de ficheiros); A/B aleatório e reproduzível; respostas imutáveis com rasto | Teste que procura `simples`/`especializado` no HTML; no painel avaliar os 8 pares |
| **M4 Relatório e decisão** | Relatório e `role_decision` | Vitórias com intervalo de 95 %, erros, minutos, custo e latência por percurso; limitações sempre presentes; a decisão por papel fica registada | Exportar o relatório para `AgentDATA/docs/` e registar a decisão de manter, simplificar ou retirar cada papel |
| **M5 Fecho do 2C** | Revisão final do ramo e decisão aplicada | Revisão final sem achados Critical/Important em aberto; se um papel for retirado ou simplificado, o pipeline habitual reflete-o | Uma corrida real contigo e o relatório publicado em `AgentDATA/docs/` |
