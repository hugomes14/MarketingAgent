# Marketing AI Manager

Agente de marketing para uso interno que planeia, executa e audita trabalho de marketing com
orçamento controlado. O **núcleo** (Plano 01): o Diretor conduz uma entrevista de descoberta,
gera o perfil da marca, propõe um diagnóstico e uma estratégia, e cada decisão fica à espera da
aprovação explícita do responsável no painel web. O **plano semanal de texto** (Plano 02a): uma
base de conhecimento, um plano semanal proposto pelo Diretor, a geração do texto de cada
publicação em segundo plano (Copywriter e Revisor, num processo `worker`) e uma sessão semanal
de revisão no painel (aprovar, editar, pedir alterações, rejeitar), com feedback que volta aos
prompts seguintes. O worker já compõe imagens e o lote mostra o pacote texto e imagem.
Ainda **não publica** nada.

Princípios: o texto do utilizador é dado, nunca instrução; só o código decide estados de
artefactos; todo o custo de LLM passa por um registo único (reserva, acerto e auditoria); o
orçamento mensal é uma referência que informa e não bloqueia; os segredos vivem em ficheiros e
nunca em logs, mensagens de erro ou no `.env`.

## Requisitos

- Python 3.12 e [uv](https://docs.astral.sh/uv/).
- Docker (ou um Postgres 17 próprio) para a base de dados.
- Para correr com modelos locais: [Ollama](https://ollama.com) com `qwen2.5:14b` (Diretor,
  Copywriter e Designer) e `qwen2.5vl:7b` (Revisor com visão, modelo distinto de propósito). Para usar modelos na
  nuvem: ficheiros com chaves da Anthropic e/ou da OpenAI.

## Estrutura do repositório

| Caminho | Conteúdo |
| --- | --- |
| `src/marketing_agent/` | Código: `config`, `secrets`, `clock`, `db`, `budget`, `llm`, `harness`, `artifacts`, `interview`, `strategy`, `kb`, `planning`, `jobs` (fila e worker), `content` (pipeline Copywriter e Revisor), `feedback`, `agents/{diretor,copywriter,revisor,designer}`, `panel`, `cli` |
| `src/marketing_agent/panel/` | Painel FastAPI (autenticação por proxy, CSRF, páginas) e a fábrica `panel.main` |
| `migrations/`, `alembic.ini` | Migrações da base (Alembic) |
| `config/` | Preços dos modelos (`model_prices.yaml` e `model_prices.local.yaml`) |
| `compose.test.yaml`, `compose.dev.yaml` | Postgres de teste (efémero) e de desenvolvimento (persistente) |
| `.env.example`, `.env.local.example` | Modelos de configuração (valores fictícios) |
| `scripts/smoke_ollama.py` | Teste de fumo do texto semanal contra o Ollama (base descartável; ver "Teste de fumo") |
| `tests/` | Testes unitários, do painel e ponta a ponta (`tests/e2e/`) |
| `AGENTS.md`, `AgentDATA/` | Regras de trabalho do agente de código, procedimentos, conhecimento e contexto |

## Instalação

```bash
uv sync
```

Os dois ficheiros compose têm nome de projeto próprio (`marketing_agent_dev` e
`marketing_agent_test`): as pilhas de desenvolvimento (porta 5434) e de teste (porta 5433) são
independentes e subir uma nunca substitui o contentor da outra.

O comando `marketing-agent` fica disponível com `uv run marketing-agent --help`.

## Correr localmente (passo a passo, com Ollama)

Tudo corre em `127.0.0.1`. Execute a partir da raiz do repositório.

1. **Base de dados** (Postgres 17 persistente na porta 5434, só em loopback):

   ```bash
   docker compose -f compose.dev.yaml up -d
   ```

2. **Configuração**: copie o modelo local e ajuste se precisar (o `.env` não é versionado):

   ```bash
   cp .env.local.example .env
   ```

3. **Ficheiros de segredo** (ficam em `AgentDATA/secrets/`, que o Git ignora):

   ```bash
   printf local > AgentDATA/secrets/ollama_key
   uv run python -c "import secrets; print(secrets.token_urlsafe(48))" > AgentDATA/secrets/panel_csrf_secret
   ```

4. **Ollama**: confirme que está a servir em `http://127.0.0.1:11434` e que os dois modelos
   existem (`ollama pull qwen2.5:14b` e `ollama pull qwen2.5vl:7b`). O `.env.local.example` liga
   `diretor`, `copywriter` e `designer` ao primeiro e `revisor` ao segundo. O Revisor recebe a
   imagem final (uma imagem consome cerca de 1900 tokens de contexto): use
   `OLLAMA_CONTEXT_LENGTH` de 8192 ou mais.

   **Contexto do Ollama**: o contexto por omissão é de 4096 tokens e o Ollama **trunca em
   silêncio** os prompts mais longos (perfil, base, exemplos e brief juntos podem ultrapassá-lo).
   O adaptador usa o endpoint compatível com a OpenAI, onde não é possível definir `num_ctx` por
   pedido; defina-o no servidor, por exemplo `OLLAMA_CONTEXT_LENGTH=8192 ollama serve`, ou num
   `Modelfile` com `PARAMETER num_ctx 8192` (`ollama create`). Do lado do painel, cada campo dos
   exemplos de feedback entra no prompt do Copywriter com no máximo 1500 caracteres
   (`…[truncado]`); o que está guardado não muda.

5. **Migrações, preços e marca**:

   ```bash
   uv run marketing-agent migrate
   uv run marketing-agent sync-prices --file config/model_prices.local.yaml
   uv run marketing-agent create-brand "NOME DA MARCA"
   ```

   Para ter também os preços dos modelos na nuvem:
   `sync-prices --file config/model_prices.yaml --file config/model_prices.local.yaml`.

6. **Painel** (num terminal):

   ```bash
   uv run marketing-agent serve
   ```

7. **Worker** (outro terminal; é o terceiro processo, a par do painel e do Ollama): gera o texto
   das publicações do plano aprovado, um trabalho de cada vez. Sem ele o plano aprova-se, mas
   nenhuma publicação sai de "por gerar".

   ```bash
   uv run marketing-agent worker
   ```

   Pára com Ctrl+C ou `SIGTERM` (o trabalho em curso termina primeiro; um segundo Ctrl+C
   interrompe-o). Só pode correr **um** worker por base de dados: o arranque toma um lock de
   sessão do Postgres e recusa arrancar (`erro: ja existe um worker a correr nesta base de
   dados`, saída 2) se outro processo já o tem. Com o lock tomado, um trabalho que ficou
   `running` de um worker morto (Ctrl+C duplo, `kill -9`, falha de energia) é **recolocado na
   fila logo no arranque seguinte**, sem esperar pela lease (6360 s, 106 min com
   `LLM_TIME_MULTIPLIER=1`), mas só pode ser reclamado após um período de graça (243 s com
   multiplicador 1, 663 s com 5: uma chamada paga em voo mais a margem fixa), para o caso de o
   worker anterior ainda estar vivo; a tentativa gasta mantém-se e, esgotadas as três, o
   trabalho passa a `dead` e o post mostra o motivo.

   Se a ligação que guarda o lock se perder com o worker vivo (reinício do Postgres, sessão
   terminada, rede), o worker **não faz nenhuma chamada paga nova**: o motor verifica o lock
   antes de cada chamada de texto ou de imagem e aborta a execução. A chamada que já estava em
   voo termina e é acertada; as escritas do job são rejeitadas se outro worker já lhe tomou a
   lease. Depois disso o worker sai com código 2 e a mensagem fixa «a ligacao que guarda o lock
   do worker perdeu-se...».

   Duas restrições de implantação: (1) **pare qualquer worker antigo** (anterior a esta
   versão, que não toma o lock) antes de arrancar o novo; (2) o `DATABASE_URL` do worker tem de
   ser uma ligação **direta ou em modo de sessão** — não use o modo de transação do pgbouncer
   (nem equivalente), porque o lock é de sessão e seria libertado ou partilhado entre clientes.

8. **Proxy de desenvolvimento** (noutro terminal), que faz o papel do `tailscale serve`:

   ```bash
   uv run marketing-agent dev-proxy --login dono@local
   ```

   O painel só responde a pedidos cujo `Host` esteja em `PANEL_ALLOWED_HOSTS` (por omissão
   `127.0.0.1` e `localhost`, sem porta); o `dev-proxy` só aceita `Host` de loopback (421).

9. Abra <http://127.0.0.1:8001>. O login `dono@local` tem de estar em `PANEL_ALLOWED_LOGINS`.

A primeira pergunta e cada resposta chamam o modelo local, por isso podem demorar (a geração de
um texto, com Copywriter e Revisor, pode levar vários minutos por publicação num CPU). Se o
processo morrer a meio de uma chamada, a reserva fica `unknown_outcome` (conta no orçamento como
desfecho desconhecido) e a mesma chave de formulário nunca repete a chamada.

## Sessão semanal de texto (do plano à aprovação)

Percurso no painel, depois de ter o perfil, o diagnóstico e a estratégia aprovados:

1. **`/base`** (base de conhecimento): promova tópicos do perfil, ou crie itens manuais (preços,
   ofertas, horários, provas...). Um **preço exige validade** (`valid_until` depois de
   `valid_from`) e moeda; sem validade o painel responde 422. Desativar um item faz com que
   deixe de contar. Só entram no texto os itens ativos e válidos na data da publicação.
2. **`/plano`**: "Propor plano da semana" corre o Diretor (por omissão para a semana ISO
   seguinte; o campo `semana` aceita `AAAA-Www`). O plano respeita `PLAN_MAX_POSTS_PER_WEEK` e
   `PLAN_MAX_POSTS_PER_DAY`, as datas da semana e a base de conhecimento; uma violação conta
   como saída inválida e o Diretor repete (até 3 vezes). **`/plano/{id}`** mostra os slots.
   Aprovar cria, numa só transação, uma publicação e um trabalho por slot e redireciona para o
   lote; aprovar o mesmo plano outra vez dá 409 e não duplica nada.

   Se a semana **já tem publicações**, a página do plano submetido mostra primeiro uma
   **previsão** (lugar a lugar: tema antigo → novo, estado e o que acontece: nova, reescrita,
   mantida, mantida com o texto antigo porque o plano mostra outro conteúdo, ou cancelada). Os
   lugares são posicionais (`AAAA-Www-N`): um plano mais curto cancela as publicações dos lugares
   que saem. Cancelar publicações **aprovadas ou por rever** exige marcar a caixa de confirmação
   (`confirm_cancel`); sem ela o servidor responde 409 e não grava nada. A verificação repete-se
   na própria transação da aprovação, por isso a previsão nunca autoriza nada por si.
3. **O worker gera o texto** de cada publicação: Copywriter, validação em código (limite do
   canal, preços, alegações ligadas à base) e Revisor, com no máximo 2 voltas de correção.
   No fim a publicação fica "por rever" (`in_review`).
4. **`/lote/{semana}`** (`/lote` leva à semana com publicações por rever): um cartão por
   publicação, com o texto, as alegações e a origem na base, o parecer do Revisor e as voltas.
   Mostra também a imagem final, o layout, os componentes e a sua proveniência, o selo
   «Gerada por IA», a confirmação de revisão visual e os custos do pacote e da imagem.
   Se não houver imagem, mostra o motivo disponível.

Em cada cartão há quatro ações de revisão (mais «Gerar de novo», abaixo); todas dizem sobre que
revisão decidem e, se entretanto surgiu outra (uma edição noutra aba, uma geração nova), recusam
com 409 ("Recarregue a página"):

- **Aprovar** (`POST /post/{id}/aprovar`): só vale para a revisão **mais recente**. Nada no
  sistema aprova uma publicação sem este pedido do responsável.
- **Editar** (`POST /post/{id}/editar`): cria uma revisão nova do responsável (a aprovação da
  anterior deixa de valer) e **exige o tipo de feedback**, sem valor por omissão. A imagem e
  a proveniência mantêm-se; o parecer automático do pacote é limpo.
- **Pedir alterações** (`POST /post/{id}/alteracoes`): o comentário vai para o Copywriter como
  dado (nunca como instrução) e o worker gera uma revisão nova; fica indisponível enquanto há
  uma geração em curso.
- **Rejeitar** (`POST /post/{id}/rejeitar`): estado final; também exige o tipo de feedback.
- **Gerar de novo** (`POST /post/{id}/gerar`): só quando a geração falhou ou se perdeu (por
  gerar, ou em geração com motivo de falha) e não há trabalho ativo. Enfileira um trabalho novo
  (um pedido de alterações que falhou repete-se com o mesmo comentário) e limpa o motivo da
  falha; dois cliques seguidos geram um só trabalho.

### Fotografias e imagens no lote

Carregue fotografias JPEG, PNG ou WebP em `/biblioteca` (até 15 MB e pelo menos 1080 píxeis
em cada lado), com origem e autorização das pessoas retratadas. Configure as cores e o
logótipo PNG transparente em `/kit`. As cópias normalizadas removem EXIF/GPS e metadados.

- **Trocar foto** (`POST /post/{id}/trocar-foto`) recompõe a publicação com uma fotografia
  ativa, um texto alternativo novo e o kit atual, sem LLM. O título mantém-se; um fundo de IA
  passa a fotografia com título. A revisão nova fica sem parecer e exige nova aprovação; trocar
  para a mesma fotografia com o mesmo texto alternativo é recusado. Pacotes antigos sem o título
  guardado precisam de ser gerados novamente antes desta ação.
- **Carregar imagem à mão** (`POST /post/{id}/imagem`, campo de ficheiro `ficheiro`) exige texto
  alternativo, origem e uma declaração explícita de IA. Recorta em 4:5 e enfileira `review_post`;
  o worker faz uma passagem pelo Revisor, sem Copywriter nem Designer. Mesmo que o parecer falhe,
  entrega a imagem ao responsável. Uma base desatualizada é recusada com 409.

As duas ações só existem com a publicação em revisão (ou com uma geração falhada e nenhum
trabalho ativo, como a edição): publicações aprovadas, rejeitadas ou canceladas não se reabrem
(409). Os custos aparecem só nas revisões do agente; edições, trocas e uploads mostram
«Custo: ver a geração original» (o upload regista apenas o custo da sua própria revisão visual).

Inclua `MEDIA_DIR` nas cópias de segurança juntamente com o Postgres: os bytes das imagens
não estão na base. Preserve a correspondência entre a base e os ficheiros ao restaurar.

### Feedback

| Tipo | Efeito |
| --- | --- |
| `estilo` | O par antes/depois entra nos prompts seguintes do Copywriter (até 5, os mais recentes) |
| `negativo` | O texto entra como exemplo a evitar (até 3) |
| `correcao_factual` | Fica guardado (para sugerir um item da base); não entra no prompt |
| `temporario` | Fica guardado com validade; **só existe ao editar** (rejeitar não o oferece). Expira às **23:59:59 UTC** do dia escolhido (que não é a meia-noite de Lisboa) |
| `nao_usar` | Não grava nada |

Só entram nos prompts os exemplos ativos e não expirados. **`/feedback`** lista-os e permite
desativar (`POST /feedback/{id}/desativar`; um id desconhecido ou de outra marca dá 422).

### Atualização automática do lote

Enquanto houver publicações por gerar ou a gerar, o lote pede `/lote/{semana}/estado` de 5 em
5 segundos (HTMX) e troca os cartões. Quando nada está a ser gerado o fragmento deixa de levar
`hx-trigger` e a atualização para sozinha. Para não apagar o que o responsável escreve, a troca
é **saltada** (e tenta-se no ciclo seguinte) se houver um painel de edição aberto ou um campo
alterado.

### O que o cartão sinaliza

- **"sem apoio válido em AAAA-MM-DD"**: a alegação cita um item da base que, nessa data da
  publicação, já não está ativo, já caducou ou nem fazia parte da seleção do plano. O texto foi
  gerado com um item válido; reveja o preço antes de aprovar, ou peça alterações.
- **"A geração falhou (Classe)"**: o trabalho esgotou as 3 tentativas (`dead`) e o motivo (só o
  nome da classe do erro, nunca texto do modelo) ficou em `last_error` na publicação, que
  continua "em geração" sem polling. Use **«Gerar de novo»** no cartão para repetir só essa
  publicação (também aparece numa publicação "por gerar" cujo trabalho se perdeu); se já existe
  texto, pode-se editá-lo. Reaprovar o plano da semana também regenera as publicações iguais sem
  trabalho ativo, mas passa pela previsão acima.

### Fila, worker e leases

Os trabalhos `generate_post` e `review_post` usam uma **lease de 6360 s (106 min)**,
calculada a partir dos specs e dos timeouts máximos de texto e imagem. Se o worker morrer,
o trabalho regressa à fila no arranque seguinte do worker (que é o único: lock de sessão
do Postgres por base de dados) ou, no pior caso de um segundo worker sem lock, só depois de
vencer a lease. O painel nunca reclama trabalhos nem toma o lock.
O painel e o worker marcam como `unknown_outcome` reservas abertas há mais de 480 s
(`worker_stale_after()`), sem repetir chamadas cujo resultado é desconhecido.

## Comandos

| Comando | Função |
| --- | --- |
| `create-brand NOME` | Cria a marca e imprime o seu id; idempotente por nome, e recusa um nome diferente se já existir uma marca (instalação de marca única) |
| `sync-prices [--file CAMINHO ...]` | Sincroniza os preços dos modelos (por omissão `MODEL_PRICES_PATH`) |
| `recover-reservations [--older-than-minutes N]` | Marca como `unknown_outcome` as reservas abertas há mais de N minutos; N tem um mínimo seguro (`worker_stale_after`: 8 min com `LLM_TIME_MULTIPLIER=1`, 35 min com 5, 70 com 10; valores abaixo são recusados) e, omitido, usa esse mínimo |
| `migrate` | Aplica as migrações (`upgrade head`) na base de `DATABASE_URL` |
| `serve [--host 127.0.0.1] [--port 8000]` | Serve o painel com o uvicorn (recusa `FORWARDED_ALLOW_IPS` e hosts fora do loopback) |
| `worker` | Processa a fila de geração de publicações (pacote texto e imagem: Copywriter, Designer e Revisor com visão) até `SIGINT`/`SIGTERM`; exige as rotas `copywriter`, `designer` e `revisor` e recusa o fornecedor `fake` |
| `dev-proxy --login LOGIN [--name NOME] [--listen 127.0.0.1:8001] [--upstream 127.0.0.1:8000]` | Só desenvolvimento: proxy local que injeta a identidade |

Códigos de saída: `0` sucesso (também no `worker` parado por sinal), `2` erro de uso ou de
configuração (uma linha em stderr), `1` erro inesperado. O arranque do painel marca como `unknown_outcome` as reservas abertas há mais
do que o maior `max_duration_s` de todos os specs mais o timeout de uma chamada de imagem (480 s). Equivalente direto ao `serve`:
`uvicorn --factory marketing_agent.panel.main:create_app_from_env --no-proxy-headers`.

## Configuração

Copie `.env.example` (ou `.env.local.example`) para `.env`. O `.env` contém apenas campos de
`Settings`: uma chave desconhecida (por exemplo `ANTHROPIC_API_KEY` ou `TEST_DATABASE_URL`) faz o
arranque falhar, sem repetir o valor. `DATABASE_URL` é obrigatório e não tem valor por omissão.
Campos compostos (`ROLE_ROUTES`, `BUDGET_WARN_PERCENTS`, `PANEL_*`) são JSON. `ROLE_ROUTES`
tem uma rota por papel: `diretor` (painel), e `copywriter`, `designer` e `revisor` (worker; o pacote
corre sempre o Designer, por isso a rota `designer`, com `text` e `json_schema`, é obrigatória). O Revisor deve
usar um modelo diferente do Copywriter e a rota `revisor` tem de declarar `image_input` (o papel
exige-o mesmo nas publicações só de texto). O arranque do `worker` recusa rotas sem as
capacidades do spec ou sem preço (corra `marketing-agent sync-prices`; para o Ollama use
`--file config/model_prices.local.yaml`). A comparação cega (Plano 2C) usa um quinto papel, `simples` (percurso de uma só chamada): não é
exigido no arranque do `worker` e, sem rota própria em `ROLE_ROUTES`, usa a rota do `copywriter`
(que tem de ter `text` e `json_schema`); uma rota `simples` explícita tem prioridade.
`PLAN_MAX_POSTS_PER_WEEK` (5) e
`PLAN_MAX_POSTS_PER_DAY` (1) limitam o plano do Diretor. As chaves de API e
o segredo CSRF vão em ficheiros apontados por `ANTHROPIC_API_KEY_FILE`, `OPENAI_API_KEY_FILE` e
`PANEL_CSRF_SECRET_FILE` (o segredo CSRF exige pelo menos 32 caracteres). A rota `fake` só existe
nos testes e o `serve` recusa-a.

**Imagens.** Sem rota `designer_imagem` em `ROLE_ROUTES` o motor usa o gerador falso (custo 0):
os fundos são gradientes simulados e o `worker` regista no arranque o aviso
`fundos simulados: sem rota designer_imagem` (em produção, defina a rota). Cada pedido de
geração fatura no máximo 2 imagens, contadas na base (`cost_entry` e reservas), por isso um job
reclamado depois de um worker morto não excede o limite. A lease dos jobs (cerca de 106 minutos)
cobre 3 passagens do pacote com as chamadas de imagem no pior caso: um job de um worker morto só
é recolocado depois disso.
Com a rota (só `openai`, capacidade `image_output`) a chave vem de `OPENAI_IMAGES_API_KEY_FILE`
(nunca a do texto) e o cliente fala sempre com `https://api.openai.com/v1`, sem repetições
automáticas. `base_url` e `api_key` são explícitos, mas o cliente continua a herdar do ambiente do
operador `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID`, `OPENAI_CUSTOM_HEADERS` e `HTTPS_PROXY`. Não há
limite de tamanho da resposta para além do teto de 20 MB da imagem descodificada (o SDK lê o corpo
inteiro) e os 180 s de timeout valem por fase (ligação, escrita, leitura), não pela chamada toda.
`IMAGE_QUALITY` (`low`, `medium`, `high`) escolhe a qualidade pedida.

## Testes e validação

Postgres de teste (porta 5433, base `marketing_agent_test`, dados efémeros):

```bash
docker compose -f compose.test.yaml up -d
export TEST_DATABASE_URL=postgresql+psycopg://marketing_agent:marketing_agent@127.0.0.1:5433/marketing_agent_test
```

Os testes partilham a mesma base: nunca corra dois processos `pytest` ao mesmo tempo. Exporte
`TEST_DATABASE_URL` na shell, nunca no `.env`.

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
```

`tests/e2e/` cobre o percurso completo pelo painel (entrevista até estratégia ativa), a
referência mensal, a recuperação após uma morte do processo e o **fluxo semanal de texto**
(`test_weekly_text_flow.py`: plano, worker, lote, aprovar, editar com feedback e pedir alterações,
com um LLM falso).

### Modelos locais lentos

`LLM_TIME_MULTIPLIER` (inteiro de 1 a 10, por omissão 1) amplia os tempos de chamada e de
execução dos papéis. Tokens, custos, número de passos e timeout das imagens mantêm os seus
limites. O painel e o worker têm de usar a mesma configuração: as leases, os marcadores de
pedidos em curso e o limiar de reservas órfãs acompanham os tempos novos. Não há verificação
entre processos: cada um regista no arranque (INFO) `LLM_TIME_MULTIPLIER`, a lease do job e o
limiar das reservas (`painel: ...` e `worker: ...`); compare as duas linhas. Um multiplicador
menor num dos processos fá-lo dar como órfã uma chamada ainda viva do outro. O `dev-proxy`
lê o mesmo `.env` para ajustar o tempo de espera pelo painel (1ª chamada síncrona do Diretor:
483 s com 1, 1863 s com 5).

Com valor 5, a chamada de texto passa de 120 para 600 segundos, a lease do pacote passa de
106 para 310 minutos e as reservas só são recuperadas após 35 minutos. Os marcadores de
pedidos em curso (entrevista e propostas) caducam após 363 s / 483 s com 1 e 1263 s / 1863 s com
5. Um trabalho de um worker morto volta à fila logo no arranque seguinte (lock de worker
único); só as reservas abertas desse worker esperam pelo limiar (dinheiro nunca é libertado
cedo). O perfil do `.env.local.example` usa 5; a configuração geral
continua com 1. Reinicie painel e worker em conjunto depois de alterar este valor.

O fumo não lê o `.env`; use `--time-multiplier 5` explicitamente. Na instalação investigada
foram criadas variantes locais `marketing-qwen2.5-14b:8k` e `marketing-qwen2.5vl-7b:8k` com
`num_ctx=8192`, reutilizando os modelos originais. Para repetir com essas variantes:

```bash
uv run python scripts/smoke_ollama.py --posts 3 --time-multiplier 5 \
  --model marketing-qwen2.5-14b:8k --reviewer-model marketing-qwen2.5vl-7b:8k \
  --ollama-url http://<IP-do-host-WSL>:11434 \
  --server-url postgresql+psycopg://marketing_agent:marketing_agent@127.0.0.1:5543/postgres
```

Os nomes só existem onde as variantes foram criadas. Para as usar no painel/worker, altere
as rotas e registe preços locais zero com os nomes correspondentes antes de arrancar; o fumo
regista preços zero automaticamente para os modelos locais selecionados.

### Testes no Chromium

Playwright está nas dependências de desenvolvimento. Instale o browser com
`uv run python -m playwright install chromium` e execute separadamente:

```bash
uv run pytest tests/e2e/test_batch_browser.py -m browser -q
```

Use a mesma configuração de TEST_DATABASE_URL da suite, sem outro pytest em paralelo.
O teste abre o painel em loopback e usa modelos falsos. Para dependências Linux sem sudo,
consulte [o registo Playwright](AgentDATA/tools/playwright.md).

### Teste de fumo com o Ollama (fora da suite)

```bash
uv run python scripts/smoke_ollama.py --posts 3     # --help descreve todas as opções
```

Corre o percurso real (entrevista, estratégia, item de preço, foto e kit de marca, plano, worker
uma vez, lote) contra o Ollama e imprime o tempo de cada passo, por publicação o layout, os
componentes, `imagem_verificada`, o parecer, as voltas e o tempo de cada execução, e as saídas
inválidas por papel. `PHOTO=<ficheiro>` usa uma foto sua (por omissão, uma foto sintética) e
`REVIEWER_MODEL` muda o modelo do Revisor (por omissão `qwen2.5vl:7b`). Os fundos são os do
gerador falso: nunca contacta uma API paga.
**Usa uma base descartável** (`marketing_agent_smoke_<aleatório>`) criada e apagada no Postgres de
teste (porta 5433) e recusa as bases de desenvolvimento (porta 5434, base `marketing_agent`) e
do pytest. Com `--database-url` só aceita uma base cujo **nome** comece por
`marketing_agent_smoke_` (nunca a apaga e não assume que está vazia). Se o Ollama estiver em baixo ou faltar um modelo, termina com código 3 e uma mensagem
fixa; nunca descarrega modelos. Pode levar bastante tempo com modelos locais.

### Validação no Windows/WSL

Quando o Ollama corre no Windows e o projeto no WSL, `127.0.0.1` pode apontar apenas para
o Linux. O endereço do anfitrião (`<IP-do-host-WSL>` abaixo) varia entre instalações e a cada
reinício do WSL; confirme-o antes de usar. Consulte a rota com `ip route show default` e verifique o serviço:

```bash
curl --noproxy '*' http://<IP-do-host-WSL>:11434/api/version
curl --noproxy '*' http://<IP-do-host-WSL>:11434/api/tags
```

No `.env`, ajuste `OPENAI_BASE_URL=http://<IP-do-host-WSL>:11434/v1`. O teste de fumo não lê o
`.env`: passe o endereço sem `/v1` explicitamente. Para a base isolada desta instalação:

```bash
uv run python scripts/smoke_ollama.py --posts 3 \
  --ollama-url http://<IP-do-host-WSL>:11434 \
  --server-url postgresql+psycopg://marketing_agent:marketing_agent@127.0.0.1:5543/postgres
```

Antes de executar, ambos os modelos devem constar de `/api/tags`; uma descarga parcial ainda
não os torna disponíveis. A checklist de fecho está em
[`AgentDATA/docs/2026-11-11_checklist_fecho_plano02b.md`](AgentDATA/docs/2026-11-11_checklist_fecho_plano02b.md).

## Segurança

- O painel só deve ser servido **atrás de um proxy confiável**: em produção o `tailscale serve`,
  que remove os cabeçalhos de identidade enviados pelo cliente e acrescenta `Tailscale-User-Login`.
  A origem da ligação (`PANEL_TRUSTED_PROXY`) é verificada antes do cabeçalho e do login
  (`PANEL_ALLOWED_LOGINS`).
- **DNS rebinding**: o painel recusa com 400, antes de qualquer autenticação, todo o pedido cujo
  `Host` não seja exatamente um dos hostnames de `PANEL_ALLOWED_HOSTS` (sem porta; `*` é
  recusado no arranque). Em produção acrescente o hostname do `tailscale serve`; sem isso o dono
  também recebe 400.
- O servidor tem de correr com `proxy_headers=False` e **sem** `FORWARDED_ALLOW_IPS`; senão um
  cliente forjaria `X-Forwarded-For` e passaria por proxy. O comando `serve` impõe ambos e escuta
  em loopback por omissão.
- O `dev-proxy` é **só para desenvolvimento**: quem consegue ligar-se à sua porta age como o login
  configurado. Só aceita endereços de loopback e não tem opção para o mudar.
- Segredos só em ficheiros `*_FILE`; nunca em variáveis de ambiente do `.env`, logs ou erros.
- Credenciais de `compose.dev.yaml` e `compose.test.yaml` são de desenvolvimento e ficam em loopback.

## Fluxo de trabalho e agentes

As regras de trabalho do agente de código estão em [`AGENTS.md`](AGENTS.md); procedimentos,
conhecimento, contexto e registos ficam em [`AgentDATA/`](AgentDATA/). Detalhes operacionais:
`AgentDATA/procedures/standard-workflow.md` (planear, executar, validar, commitar).

## O que ainda não está feito

- Fecho operacional do Plano 02b (tarefa 16), comparação de variantes
  (02c), oficina 3D (02d/Plano 4), publicação (Plano 3), revisão mensal e triagem (Plano 5).
  O lote já mostra a imagem, permite trocar a fotografia e carregar uma imagem à mão;
  editar o texto preserva a imagem. Faltam as verificações manuais dos marcos e a
  confirmação de um modelo pago compatível com o contrato de custos.
- O percurso de imagem no lote foi exercitado no Chromium com modelos falsos (troca, upload,
  edição e aprovação). A semana completa com modelos reais e a entrevista no browser continuam
  por validar.
- Limitações conhecidas do plano semanal de texto:
  - se uma edição for recusada com 422 (por exemplo, hashtags a mais), o texto escrito perde-se
    (a página de erro é genérica); o botão Voltar do browser recupera-o;
  - o painel deixa propor semanas ISO já passadas (gasta orçamento; decisão de produto por
    tomar);
  - o feedback `temporario` aceita qualquer data futura e termina às 23:59:59 UTC;
  - um lugar `rejected` nunca é regenerado por um plano novo: as chaves dos lugares são
    posicionais, por isso a publicação rejeitada fica como está (o plano novo pode mostrar outro
    tema nesse lugar; a previsão assinala-o). O Diretor também ainda não vê as publicações já
    decididas da semana ao propor um plano novo (seguimento);
  - um só worker (um só processo) por base, agora imposto por um lock de sessão do Postgres; o
    painel e o worker são processos separados que partilham a base;
  - a página do lote não mostra se o worker está a correr: sem ele as publicações ficam "por
    gerar" indefinidamente;
  - a base de conhecimento não tem ação de editar: desativar e voltar a acrescentar o mesmo item
    no mesmo dia colide na restrição UNIQUE (mude a chave ou a data de início);
  - o limite do Instagram conta a legenda mais as hashtags, mas a validação verifica-os em
    separado: um texto pode passar a validação e ficar acima do total;
  - as datas (semana ISO, hoje, validade do feedback) calculam-se em UTC, não na hora de Lisboa.
- Prova real da autenticação com o Tailscale: ver
  `AgentDATA/docs/2026-11-06T14-26-00+00-00_checklist_prova_autenticacao_painel.md`.
