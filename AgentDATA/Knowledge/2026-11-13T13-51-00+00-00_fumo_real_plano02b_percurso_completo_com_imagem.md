# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-13T13:51:00+00:00`
- Autor: `agent`
- Assunto: Teste de fumo do Plano 2B com modelos locais reais, percurso completo com imagem
- Status: `ativo`
- Retestar em: depois de mudar modelos, `LLM_TIME_MULTIPLIER`, o Ollama ou o pipeline do pacote

## Achado

O percurso completo do 2B correu de ponta a ponta com modelos locais reais, sem APIs pagas:
entrevista, perfil, diagnostico, estrategia, base, plano semanal de 3 publicacoes, worker
(Copywriter, Designer, Revisor com visao) e lote. Resultado do script: `RESULTADO: OK`, tres
publicacoes em `in_review`, todas com imagem composta e `imagem_verificada = sim`.

## Contexto

- Commit testado: `0b8eb47` (ramo `agent/2026-11-09_plano02b_store`), com o multiplicador de
  tempos 5 e as variantes locais com contexto 8192: `marketing-qwen2.5-14b:8k` (Diretor,
  Copywriter, Designer) e `marketing-qwen2.5vl-7b:8k` (Revisor, com `image_input`).
- Ollama no Windows, acedido a partir do WSL. Gerador de imagem falso (sem rota
  `designer_imagem`): o log regista "fundos simulados: sem rota designer_imagem". Nenhuma
  chamada paga. Base descartavel criada e eliminada pelo script no Postgres exclusivo do 2B.

## Metodo

Script `scripts/smoke_ollama.py` com 3 publicacoes, foto sintetica e kit de marca com logotipo
sintetico. O processo foi lancado fora desta sessao e o log foi lido no fim. Esta nota
transcreve os numeros do log `/tmp/marketing-agent-close-2b-smoke.log`.

## Comandos

```bash
NO_PROXY=172.28.112.1,127.0.0.1,localhost .venv/bin/python -u scripts/smoke_ollama.py \
  --posts 3 --model marketing-qwen2.5-14b:8k --reviewer-model marketing-qwen2.5vl-7b:8k \
  --time-multiplier 5 --ollama-url http://<IP-do-host-WSL>:11434 \
  --server-url postgresql+psycopg://marketing_agent:marketing_agent@127.0.0.1:5543/postgres
```

## Evidencia

| Passo | Tempo (s) | Resultado |
| --- | ---: | --- |
| Entrevista | 683,5 | 6 respostas |
| Perfil | 262,3 | ok |
| Diagnostico | 459,2 | ok |
| Estrategia | 331,9 | 3 propostas |
| Biblioteca e kit | 0,1 | ok |
| Base de conhecimento | 0,1 | ok |
| Plano semanal | 549,4 | 3 publicacoes, 2026-W47 |
| Worker, job 1 / 2 / 3 | 845,2 / 857,9 / 751,6 | ok |
| Lote | 0,0 | 3 `in_review` |
| **Total** | **4741,3** (79 min) | **OK** |

- Publicacoes: as 3 com layout `foto_titulo`, componentes `fotografia:<id>`, `logotipo:<id>`,
  `texto`; `imagem_verificada = sim` em 3 de 3; 2 voltas do Revisor em cada (6 no total); o
  parecer final de cada uma e `rever` (seguem para o responsavel com as notas).
- Tempos por passo de um pacote: Copywriter 130 a 163 s, Designer 39 a 100 s, Revisor 52 a 88 s.
- Execucoes: copywriter 8, designer 9, diretor 11, revisor 9, todas `succeeded`;
  `cost_entry` = 39; saidas invalidas: `diretor invalid_output = 1`. Contexto efetivo no Ollama
  (`/api/ps`): `marketing-qwen2.5vl-7b:8k` = 8192.

## Interpretacao

Fatos: o percurso completo passou e a mensagem com imagem (`image_url` com dados em base64) foi
aceite pelo `qwen2.5vl` atraves do endpoint compativel, porque o Revisor devolveu parecer com
`imagem_verificada = sim` nos tres pacotes. O erro de ids repetidos do Ollama (corrigido em
`0b8eb47`) nao reapareceu: 39 `cost_entry` sem colisao.

Inferencias (nao provadas por este teste): `imagem_verificada` e auto-declaracao do modelo;
o teste nao prova que o Revisor "viu" o conteudo da imagem. O Revisor pediu `rever` em todas as
rondas, incluindo a ultima (pode ser rigor excessivo do modelo de 7B ou regras demasiado
estritas; nao foi investigado). Nenhum pacote usou `fundo_ia_titulo`, por isso a geracao de
fundo (`designer_imagem`) e a quota de imagens nao foram exercitadas contra modelos reais.

## Consequencia operacional

O 2B tem agora prova de funcionamento ponta a ponta com modelos reais locais, com a duracao
esperada de cerca de 80 min para entrevista, perfil e uma semana de 3 publicacoes neste hardware
(cerca de 2 tokens por segundo no 14B). O M5 do 2B (texto revisto com imagem) fica provado
automaticamente para o percurso com foto.

## Riscos e limites

- Um so percurso, um so run, foto sintetica, kit sintetico, gerador de imagem falso.
- Nao cobre: fundo gerado por IA com modelo pago, quota de 2 imagens em condicoes reais,
  verificacao visual da imagem final por uma pessoa, painel no browser com modelos reais.
- A latencia depende do Ollama do Windows (2 a 3 tokens por segundo no 14B).

## Pendencias

- Verificacao manual no browser (M1 a M4, M6, M7) e uma semana real com o responsavel.
- Escolher e confirmar o preco de um modelo pago de imagem antes de ativar `designer_imagem`.
- Perceber porque o Revisor nunca aprova (6 voltas, 6 `rever`).
