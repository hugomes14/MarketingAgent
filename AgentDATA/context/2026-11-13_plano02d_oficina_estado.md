# Plano 2D — oficina

- Branch: `agent/2026-11-13_plano02d_oficina`; base `e51ff56` (entrega tecnica2C).
- O utilizador autorizou continuar logo para2D; M5 humano do2C continua aberto.
- Skill: executing-plans, inline com TDD e revisao independente final. Plano/spec em
  AgentDATA/docs/2026-11-09T14-07-58+00-00_{plano,spec_plano}_02d_oficina_3d.md.
- Ledger: .superpowers/sdd/2026-11-09T14-07-58+00-00_plano_02d_oficina_3d/progress.md.

## Progresso

1. Personagens e limites por papel: commit c6edb76; 130 testes afetados, suite2983 passou;
   mypy157 ficheiros e ruff/format OK. Config/oficina.yaml tem os quatro papeis; nomes/cores/
   estilos validados no arranque. Tempos nominais lidos dos specs; aplicar multiplicador ao
   montar TeamStateQueries, conservando o contrato anterior do Diretor.
2. Estados/JSON/ETag implementados:33 testes novos, suite3016 passed/4 deselected152.74s;
   mypy158 e ruff/format OK. JSON minimo explicito, datas UTC, prioridades testadas nas16
   combinacoes. Commit a concluir antes da T3.
3. A seguir: leituras readonly do estado, ate10 consultas, com testes de isolamento, falhas,
   pendencias, trabalhador morto e volume. Depois rotas/vista2D, browser, JS, Three/cena,
   ligacao ao estado, desempenho e oficina por omissao (Tasks4–12).

## Ambiente de testes

Base exclusiva `marketing_agent_2d_codex_test` no Postgres5543. Nao tocar em app8000/8001,
desenvolvimento5434 ou base5433 do Claude. Nunca dois pytest concorrentes na base.
Playwright1.65.1 e Chromium158/rev1254 ja instalados. Para testes de browser neste WSL:
`LD_LIBRARY_PATH="$PWD/var/browser-deps/usr/lib/x86_64-linux-gnu"`.
Preservar marcador browser e exclusao atuais; acrescentar e2e_browser (fora por omissao).
Sem chamadas LLM novas e sem migracao no2D.

## Referencia pronta como dados

Conector GitHub verificou o commit073cbc2fdd9c52428e67935e35e0e616c892a258, MIT Ederland Studio2026.
Fontes guardadas em var/oficina-reference: scene.js, minifig.js, navgrid.js, textures.js,
LICENSE, package.json e package-lock.json. Nao estao integradas na aplicacao ainda.
Three.js0.186.1/integrity do lock igual ao plano. Nunca copiar React/Firebase.

## Reconciliacoes para T3

- Artifact nao tem created_at: recencia pela primeira auditoria de transicao ou AgentRun de
  origem; desempate estavel por id quando nao ha tempo conhecido. Nenhuma migracao nova.
- JobQueue.claim nao atualiza updated_at: janela de reclamacao com
  max(updated_at, locked_until-job_lease). Injetar lease na montagem, calculada uma vez.
- Incluir review_post no mapa de jobs com papel revisor, alem do generate_post e eval_package.
- Artefactos e posts expostos apenas por titulos limitados/ids/URLs internas; falhas curtas so
  no HTML do painel, nunca no JSON. Conteudo de modelo escapado, nao executado.
