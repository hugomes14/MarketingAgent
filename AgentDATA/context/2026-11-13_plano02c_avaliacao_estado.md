# Plano 2C — avaliacao cega

- Data: 2026-11-13, Europe/Lisbon.
- Branch: `agent/2026-11-13_plano02c_comparacao`.
- Trabalho herdado: Claude ate `8b1c9b7` e quatro ficheiros de Task 5 ainda nao commitados
  (`handler.py`, `progress.py`, `test_handler.py`, `eval_rig.py`), copiados e validados.
  A worktree original `/home/marketing-agent/MarketingAgent-2c` permanece intacta.
- Estado: Tasks 1–9 implementadas; codigo da Task 10 validado. Fumo Ollama em curso e
  avaliacao/decisoes humanas pendentes.

## Entrega

`/avaliacao` estima/cria corridas. O worker produz ambos os percursos com contexto fixado,
prioridade inferior a posts, recuperacao e custo por familia de triggers. O painel mostra pares
cegos, conserva correcoes antes da revelacao e so revela depois de todos os pares elegiveis
terem resposta. Relatorio HTML/Markdown com Wilson, custo completo, falhas, latencia, erros,
intervalos de edicao e decisoes humanas auditadas.

## Validacao e isolamento

- Base exclusiva: `marketing_agent_2c_codex_test` em Postgres 5543; nao usar a base 5433 do
  Claude nem a de desenvolvimento. Pytest recria o esquema desta base: nunca dois pytest
  concorrentes nela.
- Tasks 5–9 validadas com suites afetadas; ruff, format e mypy src scripts limpos.
- Teste ponta a ponta: 1 passed. Script smoke + e2e: 42 passed.
- Chromium: `test_eval_browser.py`, 1 passed in 3.30s, imagens 1080 px, formularios, revelacao,
  decisao e ausencia de erros JS; conteudo sintetico. Correcao de largura das imagens
  validada RED→GREEN (1 passed in 2.76s).
- Fumo Ollama em curso: `/tmp/marketing-agent-2c-ollama-smoke.log`; `--eval-only`, multiplicador
  5, marketing-qwen2.5-14b:8k e marketing-qwen2.5vl-7b:8k no Windows 172.28.112.1:11434.
  Base descartavel propria no servidor 5543. Sem APIs pagas. Nao iniciar outro fumo concorrente.
- Regressoes posteriores: 609 testes de conteudo/avaliacao aprovados (incluindo teto congelado e Markdown).
- Suite final apos revisao: 2961 passed, 4 deselected in 173.76s;
  `/tmp/marketing-agent-2c-full-suite-reviewed.log`.
- Revisao independente: zero Critical, dois Important corrigidos por testes RED→GREEN;
  dois menores adiados (formularios de correcao e payload interno inconsistente).
  Relatorio: `AgentDATA/docs/2026-11-13_revisao_tecnica_plano02c.md`.
- Chromium apos fixes: 1 passed in 3.71s. Build offline de sdist/wheel aprovado e recursos
  novos confirmados no wheel (`/tmp/marketing-agent-2c-build`).
- O relatorio distingue gasto conhecido de reservas abertas/desconhecidas, numa snapshot
  consistente. Latencia soma execucoes encerradas de todas as tentativas, sem fila/backoff;
  pacotes com tempos incompletos ficam fora da media/maximo, com parcial conhecido separado.

## Decisoes tecnicas

- Manter extensao `Worker.eval_handler` existente: preserva o contrato do lote semanal.
- Usar `EvalViews` e DTO agregado em vez de aumentar PanelQueries: nao transportar itens ou
  proveniencia para templates anteriores a revelacao.
- Numerar so pares elegiveis, pela ordem do conjunto; falha de um lado exclui o par inteiro.
- JPEG fixo nas imagens dos pares, igual ao renderer, sem ETag/Content-Disposition/ids.
- Minutos de edicao mantidos nas categorias medidas (0, 1, 2–5, mais de 5), sem media inventada.
- O teto por pacote confirmado acompanha o pedido; o worker pode reduzi-lo mas nao aumenta-lo.
  Deriva de estimate_usd / numero de pacotes, sem migracao adicional.
- Concordancia e descritiva: OWNER_TO_REVIEWER torna explicito o mapeamento entre vocabularios;
  julgamentos originais ficam preservados e permitem recalcular outra correspondencia.

## Pendente para fechar M2–M5

1. O responsavel adapta/reve os oito briefs de `evals/briefs_v1.yaml` (pilares provisorios e
   referencia `kb-REVER-oferta-temporada`). A versao sobe se o conjunto mudar.
2. Corrida real com 16 pacotes, avaliacao cega dos oito pares, uma correcao e revelacao.
3. Exportar o relatorio real e registar a decisao de cada papel. Nao substituir esta etapa
   por julgamentos automaticos nem publicar um relatorio sintetico como evidencia real.
4. Se a decisao for simplificar/retirar um papel, implementar e validar essa opcao num commit
   separado. Sem decisao humana, o pipeline habitual conserva o comportamento atual.
5. Concluir o fumo real do par. Revisao tecnica e verificacoes automaticas aprovadas;
   M5 continua aberto por falta da corrida e decisoes humanas.
