# Registro de CLI tool

## Metadata

- Nome: `scripts/smoke_ollama.py`
- Timestamp: `2026-11-10T10:00:00+00:00`
- Status: `disponivel`
- Runtime: Python 3.12 (`uv run python scripts/smoke_ollama.py`)
- Origem: este repositorio (`scripts/smoke_ollama.py`)
- Dono operacional: responsavel pelo projeto

## Objetivo

Teste de fumo do texto semanal contra modelos locais do Ollama: entrevista, perfil, diagnostico e
estrategia, item de preco na base, plano semanal, worker uma vez (Copywriter e Revisor) e lote.
Regista o tempo por passo, as voltas do Revisor por publicacao e as saidas invalidas por papel.
Com `--eval-only`, gera um par da comparacao 2C (simples e especializado), depois do perfil,
estrategia e kit, sem plano semanal nem posts. Nao cria julgamentos nem decisoes humanas.

## Quando usar

Depois de alterar prompts, specs, validadores ou o pipeline de texto, e antes de um marco que
dependa de modelos reais (M5 do Plano 02a). Nao faz parte da suite (`pytest`).

## Quando nao usar

Nunca sobre a base de desenvolvimento (porta 5434) nem a do pytest: o script recusa-as. Nao serve
para validar a qualidade dos prompts com modelos comerciais.

## Comando

```bash
uv run python scripts/smoke_ollama.py --help
uv run python scripts/smoke_ollama.py --posts 3
```

## Subcomandos

| Opcao | Objetivo | Exemplo |
| --- | --- | --- |
| `--posts N` | Publicacoes do plano (1 a 14, por omissao 3) | `--posts 2` |
| `--eval-only` | Um par 2C, sem plano semanal; ambos os pacotes com imagem e o especializado com verificacao visual | `--eval-only --time-multiplier 5` |
| `--database-url URL` | Usar uma base existente (nunca apagada); recusa dev, pytest e hosts fora do loopback | `--database-url postgresql+psycopg://u:p@127.0.0.1:5433/x` |
| `--server-url URL` | Servidor onde criar a base descartavel (por omissao o Postgres de teste, 5433) | |
| `--ollama-url URL` | Endereco do Ollama (por omissao `http://127.0.0.1:11434`) | |
| `--model`, `--reviewer-model` | Modelos do Diretor/Copywriter e do Revisor | `--model qwen2.5:14b --reviewer-model qwen2.5vl:7b` |

`--time-multiplier N` (1 a 10, omissão 1) amplia os tempos de chamada, execução e guardas
no mesmo perfil da aplicação. Não aumenta tokens nem custos e não lê o .env. Exemplo:
`uv run python scripts/smoke_ollama.py --time-multiplier 5 --posts 3`.

## Entradas

Argumentos acima e a variavel `MODEL` (modelo por omissao do Diretor e do Copywriter). Nao le o
`.env` (as definicoes sao construidas no codigo).

## Saidas

stdout: progresso e relatorio final (tempo por passo, publicacoes e voltas do Revisor, saidas
invalidas por papel, execucoes, `cost_entry`). stderr: mensagem de falha. Saida 0 tudo correu e as
publicacoes ficaram `in_review`; 1 um passo ou publicacao falhou; 2 argumentos invalidos; 3 Ollama
inacessivel ou modelo em falta (mensagem fixa; nunca descarrega modelos).
No modo `--eval-only`, o sucesso exige exatamente um pacote de cada percurso, ambos
`succeeded` e com imagem, e imagem verificada no especializado. O relatorio inclui estado,
latencia, voltas, saidas invalidas e custo de cada pacote; isto nao mede a qualidade humana.

## Efeitos colaterais

Cria, migra e apaga uma base `marketing_agent_smoke_<hex>` no Postgres de teste; escreve um
ficheiro de chave descartavel num diretorio temporario; chama o Ollama (CPU/GPU locais, minutos).

## Dependencias

Postgres de `compose.test.yaml` (5433), Ollama com `qwen2.5:14b` e `qwen2.5vl:7b` (o Revisor exige um modelo com visão).

## Variaveis de ambiente

```text
MODEL=qwen2.5:14b
```

## Exemplos

Ver o topo do script e a seccao "Teste de fumo com o Ollama" do `README.md`.

## Validacao

```bash
uv run pytest tests/test_smoke_ollama.py
```

## Riscos e limites

As partes puras (argumentos, recusa da base de desenvolvimento, relatorio) tem testes; o percurso
completo so se valida correndo-o com o Ollama. Os tempos dependem do hardware.

## Documentacao relacionada

`README.md`, `AgentDATA/Knowledge/` (registos de tempos do teste de fumo).
