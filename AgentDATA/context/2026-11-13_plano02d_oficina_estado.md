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
   combinacoes. Commit 04316ea.
3. Leituras implementadas: nove SELECT fixos, 23 testes aprovados; suite3039 passed/4
   deselected143.81s. Codigo em oficina/queries.py e sql.py, dados de teste em team_seed.py.
   Snapshot REPEATABLE READ, ultimas execucoes por papel, pendencias, entregas limitadas,
   atribuicao de jobs e falhas por resolver; nenhuma escrita ou chamada LLM.
4. Rota autenticada /oficina/estado integrada com ETag/304/no-store e multiplicador real de
   duracoes/lease. Testes HTTP de autenticacao, Host, privacidade e imutabilidade de todas as
   tabelas. Contexto do painel exige team/oficina; todos os montadores existentes atualizados.
5. Vista2D implementada em c052fd5 e validada em2026-11-14: suite3059 passed/4 deselected,
   typecheck/lint/format e build aprovados. Quatro avatares, painel por papel, entregas e
   revisao semanal; sem acoes novas ou chamadas LLM.
6. Browser (T6) concluido: sete percursos Chromium aprovados; suite3059 passou,11
   desmarcados. Formularios/HTMX/pageshow/idempotencia/CSRF e oficina2D cobertos.
7. T7 concluida: teclado, sondagem5s/ETag, pausa, backoff e estados por confirmar.
   21 testes Chromium aprovados; suite3059 passou/25 desmarcados.
8. T8 concluida: Three0.186.1 local verificado, CLI e hashes/licenca; suite3073 passou,
   Chromium21 passou, build e verificacoes de qualidade OK.
9. A seguir: cena (T9), ligacao ao estado, desempenho e oficina por
   omissao (Tasks9–12). M1 manual com morte de worker em ambiente real continua pendente;
   nao interromper o worker em uso ou afirmar fecho de marco antes da validacao humana.

## Ambiente de testes

Base exclusiva `marketing_agent_2d_codex_test` no Postgres5543. Nao tocar em app8000/8001,
desenvolvimento5434 ou base5433 do Claude. Nunca dois pytest concorrentes na base.
Playwright1.65.1 e Chromium158/rev1254 ja instalados. Para testes de browser neste WSL:
`LD_LIBRARY_PATH="$PWD/var/browser-deps/usr/lib/x86_64-linux-gnu"`.
Preservar marcador browser e exclusao atuais; acrescentar e2e_browser (fora por omissao).
Sem chamadas LLM novas e sem migracao no2D.

## Referencia pronta como dados

Conector GitHub verificou o commit073cbc2fdd9c52428e67935e35e0e616c892a258, MIT Ederland Studio2026.
Fontes verificadas pelo SHA Git de origem e guardadas em var/oficina-reference: scene.js, minifig.js, navgrid.js, textures.js,
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

T3: NULL no parecer nao e uma devolucao do Revisor; contar apenas rever/rejeitar apos duas
voltas. Esta decisao e de leitura e nao altera posts/revisoes.

## Pausa pedida pelo utilizador — 2026-11-13

- Tarefas1–4 concluidas. T4 commit f62c409; suite3045 passed/4 deselected258.17s;
  teste adicional de duracoes efetivas e conjunto focado30 passed4.46s; ruff/format/mypy161 OK.
- **Tarefa5 em curso:** vista2D implementada (rotas, quatro templates, navegacao e CSS).
  RED observado11 falhas/2 passes; GREEN43 testes de vista+estado8.36s. Ruff/format e
  mypy161 ficheiros OK. Suite completa da T5 interrompida com SIGINT a pedido do utilizador;
  nao considerar T5 concluida antes de repetir a validacao global e task-done.
- Fazer checkpoint Git da implementacao T5, sem marcar o ledger como complete.
- Retomar: suite global na base exclusiva2D; rever/fechar T5 no ledger usando BASE f62c409;
  depois T6 Playwright e percursos do painel no browser. T6 ainda nao integrada nem validada.
- Rascunhos preparatorios T6 guardados apenas em var/oficina-pausa-2026-11-13/ (ignorado),
  para nao perder os ficheiros temporarios. Ler o brief T6 e observar RED antes de integrar.
- Nao ha migracao, chamadas LLM ou alteracoes aos servicos8000/8001/5434 nesta pausa.


## Tarefa 5 concluida — 2026-11-14

- A pausa anterior e historica. Retoma autorizada para concluir T5; T6 por iniciar.
- Suite global:3059 passed/4 deselected414.12s; mypy161, ruff e format OK. Build offline
  sdist/wheel OK; quatro templates, rota e CSS presentes no wheel.
- Base de teste2D ausente no arranque: criada novamente no contentor existente5543;
  nenhuma alteracao aos servicos de desenvolvimento.
- Preview pedido pelo utilizador: marca de demonstracao na base separada
  marketing_agent_2d_preview_20261114; proxy local com portas efemeras. URL atual em
  var/oficina-preview-2026-11-14/url.txt; scripts e capturas no mesmo diretorio ignorado.
  Captura desktop e mobile390px verificadas no Chromium, troca do painel por HTMX OK,
  zero erros JS/CSP e nenhum overflow horizontal no mobile. Preview mantido aberto.
- A cena3D e as caras geradas das personagens continuam nas tarefas seguintes.
  M2 e restantes marcos humanos ainda nao fechados.


## Tarefa 6 concluida — 2026-11-14

- Sete testes Chromium em tests/browser, com servidor real loopback e guioes falsos
  verificados. Cobre a passagem automatizada dos formularios e turnos do Plano1, incluindo
  duplo envio, pageshow, idempotencia/CSRF e erros HTMX; oficina2D abre sem erros JS/CSP.
- GREEN7/7 em5.39s; suite3059 passed/11 deselected404.93s; mypy161/ruff/format405 OK.
- Marcador e2e_browser registado, mantendo exclusoes browser/live anteriores; colecao
  padrao confirma ausencia dos testes de browser. Playwright e lock existentes preservados.
- Nao houve alteracoes ao codigo funcional, chamadas a modelos reais ou migracoes.
  T7 ainda por iniciar; M2/manual e restantes marcos continuam abertos.


## Tarefa 7 concluida — 2026-11-14

- Modulos ES da oficina implementados: teclado, selecao aria-current, GET5s sequencial,
  ETag/304, pausa em separador oculto e retoma imediata, espera10/20/40/60s em erros.
- Estados por confirmar apos tres falhas ou30s sem sucesso, inclusive pedido bloqueado;
  recuperacao304 restaura tambem o painel. Sessao401/403 para a sondagem.
- GREEN21 testes Chromium15.37s; suite3059 passed/25 deselected409.43s; ruff/format406,
  mypy161 e build offline OK. Wheel inclui main/estado/coluna.js.
- Preview38705 atualizado; desktop/mobile390px verificados sem erros JS/CSP ou overflow.
  M2 manual continua aberto, distinto da verificacao automatizada por teclado. T8 por iniciar.

T7: a repeticao de fecho detetou um timeout intermitente no teste de backoff.
Playwright clock.install continua a avancar com o tempo real; acrescentado pause_at antes
da navegacao para medir intervalos exatos deterministicamente. Sem alteracoes ao codigo
funcional; os21 testes Chromium passaram novamente em16.17s.


## Tarefa 8 concluida — 2026-11-14

- Three.js0.186.1:18 ficheiros +README com hashes e MIT. SHA512 npm fixado verificado;
  comparacao independente de origem/local comprova apenas11 imports alterados.
- scripts/vendor_three.py so stdlib: --help, --version fixada e --dest vazio/novo; paths,
  links, duplicados, ficheiros em falta e integridade invalida recusados. Tool registrada.
- Regressoes target relativo na mesma pasta e export const com texto from three observadas
  RED e corrigidas. Excecao estrita em .gitignore para servir/versionar vendor/three/build.
- Suite3073 passed/25 deselected480.47s; Chromium21 passed27.29s; ruff/format411/mypy162 OK.
  Build wheel/sdist OK, todos19 recursos copiados conferidos no wheel.
- Whitespace original do fornecedor mantido e autenticado pelos hashes; codigo proprio
  revisto com diff --check. M3 ainda depende da cena T9, por iniciar. UI2D mantida.
