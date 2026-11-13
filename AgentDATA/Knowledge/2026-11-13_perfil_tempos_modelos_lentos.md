# Perfil de tempos para modelos locais lentos

- Timestamp: 2026-11-13T00:00:00+00:00 (registo diario, Europe/Lisbon).
- Autor: Codex.
- Contexto: perfil sintetico valido demorou 146,68 s; cliente usava timeout 120 s.

## Contrato implementado

LLM_TIME_MULTIPLIER, inteiro de 1 a 10, omissao 1. Escala o timeout de texto dos adaptadores
OpenAI/Anthropic e max_duration_s dos specs no motor, sem mutar os specs originais. Nao altera
passos, tokens, custos nem o timeout das imagens. O valor e propagado pelo painel, worker e
script de fumo. No fumo usa-se --time-multiplier, pois o script nao le .env.

Leases e recuperacao de reservas derivam do mesmo multiplicador. Marcadores de entrevistas
(incluindo pedido de perfil) e propostas passam a cobrir tambem a ultima chamada em excesso;
antes podiam caducar enquanto o fornecedor ainda respondia. Os testes de recuperacao agora
esperam pelo limite completo, em vez de assumir a duracao do spec isolado.

Com multiplicador 5: chamada de texto 600 s; lease do pacote 18600 s (310 min); recuperacao
de reservas 2100 s (35 min). Os exemplos locais usam 5; o exemplo geral usa 1. Painel e worker
precisam da mesma configuracao e de reinicio conjunto. Recuperar um processo morto fica mais
lento: e o custo de manter protegida uma chamada lenta ainda viva.

## Preparacao operacional

Criadas pelo endpoint /api/create duas variantes locais, sem descarregar pesos novos nem
alterar os originais:
- marketing-qwen2.5-14b:8k, a partir de qwen2.5:14b, parameters.num_ctx=8192.
- marketing-qwen2.5vl-7b:8k, a partir de qwen2.5vl:7b, parameters.num_ctx=8192.

O endereco do servidor nesta sessao e 172.28.112.1:11434. Nenhum servico Windows foi reiniciado.
A configuracao privada .env nao foi sobrescrita. O fumo regista precos locais zero para os
nomes escolhidos; a aplicacao exige precos sincronizados com as rotas configuradas.

## Validacao automatica

- Suite completa: 2511 passed, 3 deselected in 124.79s.
- Chromium: 1 passed in 6.23s.
- Mypy src scripts, ruff check e ruff format: limpos.
- Inclui testes de propagacao do timeout aos dois clientes, limites escalados e ainda
  bloqueantes, imutabilidade do spec original, lease/recuperacao e argumentos do fumo.

## Validacao real

```bash
NO_PROXY=172.28.112.1,127.0.0.1,localhost .venv/bin/python -u scripts/smoke_ollama.py \
  --posts 3 --model marketing-qwen2.5-14b:8k --reviewer-model marketing-qwen2.5vl-7b:8k \
  --time-multiplier 5 --ollama-url http://172.28.112.1:11434 \
  --server-url postgresql+psycopg://marketing_agent:marketing_agent@127.0.0.1:5543/postgres
```

Base descartavel propria, gerador de imagem falso e sem APIs pagas. Entrevista concluida com
seis respostas completed em 726,0 s; perfil aprovado pelo percurso em 276,2 s. O erro original
aos 120 s foi ultrapassado. Resultado final do restante percurso a registar apos a execucao.

Fonte do contrato de variantes: https://docs.ollama.com/api/create (consulta em 2026-11-13).
