# Revisao tecnica do Plano 2C

- Data: 2026-11-13, Europe/Lisbon.
- Ramo: `agent/2026-11-13_plano02c_comparacao`.
- Revisao independente: `/root/plano2c_final_review`, modelo gpt-6-astra; leitura apenas,
  intervalo `c8d9d52..46d044e`. Sem alteracoes do revisor, sem pytest/Ollama concorrentes.
- Escopo: implementacao completa do 2C; a avaliacao humana e o fecho operacional nao foram
  apresentados como concluidos.

## Resultado da revisao e passagem unica de correcoes

Zero achados Critical, dois Important aceites e corrigidos com testes de regressao:

1. **Custos conhecidos inconsistentes e exposicao omitida.** O total congelado do item era
   combinado com o custo de imagem atual; um acerto tardio podia criar texto negativo. O
   relatorio passou a ler total/imagem/texto da mesma familia de triggers numa transacao
   REPEATABLE READ. Reservas abertas e de desfecho desconhecido ficam separadas do gasto
   confirmado, com aviso de contabilidade incompleta em HTML e Markdown. Teste RED→GREEN
   cobre exposicao, acerto tardio e texto nunca negativo.
2. **Latencia perdia tentativas anteriores.** O tempo passou a somar execucoes encerradas de
   todas as tentativas, com os timestamps de AgentRun; nao conta fila/backoff. Um teste que
   esperava 12000 ms (10000 de falha + 2000 de sucesso) observou apenas 2000 antes da correcao.
   Falha terminal com 7000 ms conhecidos dava NULL. Ambos RED→GREEN. Execucao sem fim conhecido
   fica marcada como incompleta; nao se inventa duracao, e o pacote nao entra na media/maximo.
   Se existir um parcial medido, ele aparece separado.

Regressoes: 284 passed in 51.03s. Chromium depois dos fixes: 1 passed in 3.71s. Ruff, format e
mypy src scripts (154 ficheiros) limpos. Build offline produziu sdist e wheel; modulos,
templates e spec do papel simples confirmados no wheel. Suite completa apos fixes: 2961 passed, 4 deselected in 173.76s.
Os dois Important ficam encerrados por testes RED→GREEN e suite verde; nao foi pedida uma
segunda revisao, conforme a skill de execucao. Os menores continuam explicitamente adiados.

## Menores adiados

- Reabrir o formulario de correcao nao preenche erros/minutos/nota anteriores; ao corrigir,
  e preciso voltar a introduzir esses campos. Respostas anteriores continuam no historico.
- Um payload de job com forma valida mas ligacao interna inconsistente pode deixar o item
  associado preso; o produtor normal cria as ligacoes corretas numa transacao. Endurecer
  a validacao de `item.job_id == job.id` e reconciliacao e seguimento separado.

## Comportamentos postos de lado pelo revisor e decisoes finais

- **Garantia absoluta de faturacao.** O controlo limita reservas estimadas. A politica herdada
  de acerto pode divergir com preco ou consumo efetivos; foi mantida e a limitacao passou a
  ser explicita na estimativa e relatorio. Risco aceite: nao prometer que a fatura real nunca
  supera a reserva; auditar desvios. Nao se alterou o contrato financeiro herdado nesta tarefa.
- **Representatividade, qualidade e decisoes reais.** Dependem da avaliacao do responsavel.
  Nenhuma resposta ou decisao foi fabricada; M5 permanece aberto. Risco: o fecho operacional
  espera por essa etapa humana.
- **Execucao duplicada de testes/fumo pelo revisor.** Foi evitada para nao concorrer nas bases
  nem nos modelos; a raiz verificou suites e fumo em processos isolados. Risco: a evidencia
  de execucao pertence aos logs desta sessao, nao a um segundo ensaio redundante.

## Decisoes de implementacao da retoma

- Preservar `Worker.eval_handler` em vez de substituir o contrato por um mapping; posts
  conservam compatibilidade. Se houver mais tipos de job, reavaliar o dispatcher.
- Leituras agregadas em `EvalViews`, sem entidades ORM de itens nos templates cegos. Se a
  organizacao mudar, adaptar as rotas de leitura sem alterar a fronteira cega.
- Indices consecutivos apenas dos pares elegiveis, na ordem do conjunto. Se essa regra mudar,
  preservar a ligacao ao brief original e ao sorteio, nunca reinterpretar respostas antigas.
- Imagem cega com MIME JPEG fixo, igual ao compositor. Se o formato final mudar, alinhar rota
  e testes antes de servir os novos bytes.
- Minutos conservados nas categorias 0, 1, 2–5, mais de 5; nao inventar media de intervalo aberto.
  Para obter soma exata no futuro, recolher uma medicao diferente numa nova corrida.
- Concordancia por mapeamento explicito OWNER_TO_REVIEWER, descritiva. Se o mapeamento for
  inadequado, recalcular com os julgamentos originais preservados; nao afirmar causalidade.
- Base de testes dedicada `marketing_agent_2c_codex_test` em 5543; nenhuma alteracao as bases
  do Claude ou do desenvolvimento. Custo operacional: uma base local adicional descartavel.

## Fecho operacional pendente

Rever os oito briefs com estrategia e base reais disponiveis, gerar 16 pacotes, avaliar os
pares e corrigir um, revelar, exportar o relatorio real e registar a decisao de cada papel.
Se for decidido simplificar/retirar, implementar a opcao num commit separado com validacao.
