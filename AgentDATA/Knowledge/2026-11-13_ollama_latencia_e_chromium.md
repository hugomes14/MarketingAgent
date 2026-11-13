# Ollama: latencia observada; Chromium instalado

- Timestamp: 2026-11-13T00:00:00+00:00 (registo diario, Europe/Lisbon).
- Autor: Codex.
- Estado: investigacao medida; nao e um fecho do fumo completo.
- Base: 92e6b40 + e86601b.

## Instalacao e browser

Playwright 1.65.1 instalado como dependencia dev (uv.lock); Chromium 158.0.8127.18,
revision Playwright 1254, instalado no cache local. O driver precisava de libatomic1 e o
browser de libnspr4/libnss3/libasound2. Sem sudo disponivel, pacotes Ubuntu foram extraidos
em var/browser-deps (ignorado), sem alteracoes ao sistema. Instrucoes reproduziveis em
AgentDATA/tools/playwright.md.

Novo teste tests/e2e/test_batch_browser.py, marcado browser e excluido da suite padrao:
abre o painel num servidor HTTP real loopback, com base isolada e LLM falso. Valida acesso
anonimo recusado, imagem renderizada 1080 px, selo IA, troca de foto, upload manual, worker,
edicao que preserva imagem e nova aprovacao. Verifica ausencia de erros JavaScript.

- Chromium: 1 passed in 5.61s (versao final).
- Suite padrao: 2506 passed, 3 deselected in 103.56s.
- Mypy src scripts, ruff check e ruff format: limpos.
- Isto nao substitui a semana com modelos reais nem valida a entrevista completa no browser.

## Investigacao do timeout

Servidor Windows observado: http://172.28.112.1:11434, Ollama 0.40.16. Chamadas sequenciais
com dados sinteticos, sem API paga; nao se leram dados privados nem logs Windows.
Usou-se /api/generate em streaming para medir tempo ate ao primeiro token e duracoes do
servidor (sem imprimir o texto). Modelo qwen2.5:14b, contexto inicial 4096.

| Ensaio | Tokens gerados | Primeiro token | Geracao no servidor | Total |
| --- | ---: | ---: | ---: | ---: |
| limite 64 | 64 | 0,88 s | 27,14 s | 30,28 s |
| limite 512 | 492 (stop) | 0,48 s | 212,32 s | 213,27 s |

Velocidade: cerca de 2,3 a 2,4 tokens/s. A resposta longa completou normalmente, mas levou
mais de 120 s. O adaptador real usa Chat Completions sem streaming, timeout de 120 s e sem
retries no SDK. Um read timeout e classificado corretamente como resultado desconhecido,
pois o servidor pode ter recebido e processado o pedido. Nao e uma falha de ligacao inicial.

Perfil sintetico com esquema PerfilMarca, contexto 8192 por pedido e prompt sem mapa:
403 tokens, geracao 175,40 s, total 204,93 s, parada normal; falhou a validacao Pydantic.
Este ensaio omitira o mapa de lacunas do projeto, pelo que NAO prova um defeito do esquema
ou a causa da falha original. Repeticao com o mapa completo descrita abaixo.

## Interpretacao e consequencia

A latencia medida e suficiente para explicar o timeout de 120,1 s no perfil do fumo anterior.
Isto e uma inferencia forte apoiada nas medicoes; nao foi recuperado o pedido original nem
ha logs do servidor que provem a causa de cada unknown_outcome da corrida anterior.

Contexto 4096 e um problema separado de capacidade: pode truncar prompts longos. Na chamada
nativa de diagnostico foi pedido num_ctx=8192; /api/ps confirmou 8192. Nao se alterou o valor
global do Windows nem os ficheiros/nomes dos modelos.

Aumentar apenas o timeout do SDK sem alinhar specs, leases e recuperacao de reservas nao e
uma correcao completa. Alternativas: melhorar velocidade efetiva do servidor ou escolher
modelo menor; se se suportar maior duracao local, rever estes contratos em conjunto. Nao
foram alterados limites globais nem repetidas chamadas de resultado desconhecido.

Fontes oficiais consultadas em 2026-11-13:
- https://docs.ollama.com/api/generate (metricas de tempo e options por pedido)
- https://docs.ollama.com/context-length (contexto e alocacao de memoria)

## Repeticao com o mapa completo

Usou-se o prompt_profile.md, render_gap_map com os 14 topicos (quatro confirmados com dados
sinteticos), wrap_answer nas mensagens, PROFILE_REQUEST_MESSAGE e PerfilMarca.model_json_schema.
Pedido nativo /api/chat em streaming, num_ctx=8192 e num_predict=1500; resposta sintetica e
metricas em /tmp/marketing-agent-ollama-diagnostics (nao sao dados de marcas reais).

- Primeiro token: 2,51 s.
- Prompt: 812 tokens; avaliacao 2,36 s.
- Saida: 324 tokens; geracao 141,87 s; done_reason=stop.
- Total: **146,68 s**; **PerfilMarca.model_validate_json passou**.
- A saida estruturada funciona neste ensaio; mesmo o perfil reduzido ultrapassa o timeout
  de 120 s do cliente real. Nao foi preciso inventar JSON nem relaxar validadores.
- Nao foi repetido todo o fumo de nove minutos: a investigacao isolou a latencia e confirmou
  um perfil valido com 8192. O fumo completo continua a precisar da resolucao operacional
  da velocidade/orcamentos de tempo e de nova execucao.
