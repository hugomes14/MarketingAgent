# Contexto de execucao

- Timestamp: `2026-11-05T22:21:14+00:00` (reescrito; criado em `2026-11-02T12:25:40+00:00`)
- Autor: `agent`
- Escopo: Estado atual do projeto Marketing AI Manager. A fonte de verdade e o plano mestre v2; este registo so aponta para ele e para o que falta.

## Estado observado

- Repositorio Git local, sem remoto. `main` contem so documentacao (plano mestre v2, revisao critica, Plano 01 v2).
- **Tarefa 1 do Plano 01 implementada, ainda nao integrada em `main`.** Esta no ramo `agent/2026-11-05T21-40-00_plano01_tarefa1` (commits `bc55ba4`, `0f50a22`, `be714e2`).
  - Contem: projeto Python 3.12 com `uv`, `config.py` (`Settings`, `RoleRoute`), `clock.py`, `secrets.py`, `compose.test.yaml`, `.env.example`, e a seccao "Desenvolvimento" do README.
  - Validacao no ultimo commit: 30 testes, `ruff` e `mypy` strict limpos.
  - Teve revisao da tarefa, re-revisao e revisao final do ramo, todas fechadas.
  - `compose.test.yaml` so foi validado como YAML: nao ha Docker nesta maquina.
- Fonte de verdade: `AgentDATA/docs/2026-11-05T21-10-57+00-00_plano_mestre_marketing_ai_manager.md` (v2); plano de implementacao: `AgentDATA/docs/2026-11-05T21-28-57+00-00_plano_01_v2_nucleo_diretor_estrategia.md` (Tarefas 2 a 15 por fazer).
- Documento de apoio ativo: `AgentDATA/docs/revisao_critica_marketing_ai_manager_2026-11-05.md`. Versoes anteriores em `AgentDATA/docs/arquivo/`.
- Skills recomendadas registadas como `desejada` em `AgentDATA/skills/`; nada instalado.

## Decisoes

- Plano mestre v2, seccao 3 (D1 a D49): primeira entrega com diagnostico e estrategia, criacao de texto e imagem, sessao semanal, publicacao com recuperacao automatica e oficina 3D essencial; triagem depois; 20 USD/mes como referencia sem corte; email minimo para urgencias; RPO 1 h e RTO 24 h; CrewAI adiado; venda fora de foco.
- Tarefa 1: `Settings.database_url` e obrigatorio (sem valor por omissao, para nunca apontar a aplicacao a base de testes); `.env` so leva campos de `Settings`; segredos so por ficheiro (`*_FILE`, em `AgentDATA/secrets/`).

## Arquivos relacionados

- `AgentDATA/docs/2026-11-05T21-10-57+00-00_plano_mestre_marketing_ai_manager.md`
- `AgentDATA/docs/2026-11-05T21-28-57+00-00_plano_01_v2_nucleo_diretor_estrategia.md`
- `AgentDATA/docs/revisao_critica_marketing_ai_manager_2026-11-05.md`
- `AgentDATA/Knowledge/2026-11-05T22-21-14+00-00_uv_hardlink_acesso_negado.md`
- `AgentDATA/Knowledge/` (7 registos anteriores; onde discordarem da seccao 15 do plano, vale o plano)
- `AgentDATA/skills/skills_index.md` e `skill_*.md`

## Achados menores adiados da Tarefa 1

Resultado das revisoes; nenhum bloqueia a Tarefa 2. Quem os trata:

- Tarefa 9: a marca `live` nao e excluida por omissao (`-m "not live"`).
- Tarefa 10: valor em branco em `OPENAI_API_KEY_FILE`, `ANTHROPIC_API_KEY_FILE` e `OPENAI_BASE_URL` vira `Path('.')` ou `''` em vez de `None`.
- Tarefa 15: `SecretFileError` nao indica o motivo do sistema operativo nem resolve caminhos relativos; JSON invalido numa variavel composta levanta `SettingsError`.
- Sem dono (corrigir quando se tocar nos ficheiros):
  - `# type: ignore[call-arg]` inuteis e `ROUTE_OK: dict[str, Any]` em `tests/test_config.py`;
  - a constante de teste `TEST_DATABASE_URL` tem o nome da variavel de shell da Tarefa 2;
  - o caso `TEST_DATABASE_URL` so afirma sobre `str(exc)`;
  - testes pouco afiados: falta fuso nao UTC no `FixedClock`, o erro nao e ligado ao campo `provider`, um teste le o `.env` real, falta paridade `.env.example` e `Settings`;
  - percentagens de aviso aceitam `[true]`, `"50"`, `50.0` e repetidas, e um item invalido gera tambem um erro `too_short` enganador;
  - `model_id` so com espacos, nome de papel vazio e `capabilities` vazio sao aceites;
  - o `from None` esconde a excecao, mas ela continua em `__context__`;
  - comentario em `config.py` diz `extra="forbid"` mas e o valor por omissao da biblioteca;
  - a mensagem de `secrets.py` diz "caracteres de controlo" tambem para BOM e espacos de largura zero;
  - `.env.example` nao avisa que o python-dotenv altera caminhos Windows entre aspas duplas;
  - README sem `docker compose up -d --wait` nem nota sobre `UV_LINK_MODE=copy`.

## Proxima acao sugerida

1. Decidir como integrar o ramo da Tarefa 1 (merge, PR ou manter).
2. Antes da Tarefa 2: instalar Docker Desktop ou Postgres 17 na maquina de desenvolvimento.
3. Notas a levar para as tarefas seguintes (resultado das revisoes da Tarefa 1):
   - Despachos que instalem pacotes levam `UV_LINK_MODE=copy` (ver o registo de conhecimento acima).
   - Tarefa 2: os testes e `migrations/env.py` definem `DATABASE_URL` explicitamente, a partir de `TEST_DATABASE_URL`; `.env.example` aponta `DATABASE_URL` a base de testes, o que torna dev e teste a mesma base, a decidir quando existir uma base de desenvolvimento.
   - Tarefa 4: acrescentar `types-PyYAML` as dependencias de desenvolvimento (o `mypy` strict falha ao importar `yaml`).
   - Tarefa 9: `-m "not live"` nas opcoes do pytest por omissao; o `skipif` le `os.environ`, porque o `.env` nao o preenche.
   - Tarefa 10: valores em branco em `OPENAI_API_KEY_FILE`, `ANTHROPIC_API_KEY_FILE` e `OPENAI_BASE_URL` devem virar `None`, para a mensagem de chave em falta nomear a variavel.
   - Tarefas 14 e 15: erros de validacao tratados com `exc.errors(include_input=False)`; acrescentar `PANEL_CSRF_SECRET_FILE` a `Settings`; no arranque, apanhar `SettingsError` e `ValidationError`; resolver `.env` e ficheiros de chave a partir de um diretorio de trabalho conhecido.
4. Pendencias do responsavel: plano Tailscale gratuito (Personal, uso nao comercial) por confirmar; destinatario do email ja guardado em `AgentDATA/secrets/alert_email_to`.
5. Reescrever o `README.md` por completo quando o Plano 1 estiver executado.
