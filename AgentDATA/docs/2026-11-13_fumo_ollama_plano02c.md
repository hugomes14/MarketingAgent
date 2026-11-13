# Fumo tecnico do Plano 2C com Ollama

- Data: 2026-11-13, Europe/Lisbon.
- Cenario: padaria sintetica do script; **nao e a avaliacao humana dos oito briefs**.
- Resultado: **OK**, saida 0; corrida `ready`, dois pacotes `succeeded`, ambos com imagem.
- Modelos locais: `marketing-qwen2.5-14b:8k` (Diretor/criacao) e
  `marketing-qwen2.5vl-7b:8k` (Revisor com visao); contexto observado 8192.
- Fundos simulados, sem rota paga; custo registado 0 USD. Nenhuma API paga contactada.
- Base `marketing_agent_smoke_<aleatorio>` no Postgres de teste 5543, criada e apagada pelo
  script. Nenhum dado da base de desenvolvimento alterado.
- Log da sessao: `/tmp/marketing-agent-2c-ollama-smoke.log`.

## Comando executado

```bash
NO_PROXY='*' .venv/bin/python -u scripts/smoke_ollama.py \
  --eval-only --time-multiplier 5 \
  --model marketing-qwen2.5-14b:8k \
  --reviewer-model marketing-qwen2.5vl-7b:8k \
  --ollama-url http://172.28.112.1:11434 \
  --server-url postgresql+psycopg://marketing_agent:marketing_agent@127.0.0.1:5543/postgres
```

O endereco Windows/WSL e o observado nesta sessao; confirmar a rota antes de repetir.
As variantes 8k devem existir no anfitriao. O script nao descarrega modelos.

## Resultado medido

| Passo | Tempo |
| --- | ---: |
| Entrevista, seis respostas | 677,8 s |
| Perfil | 259,2 s |
| Diagnostico | 409,7 s |
| Estrategia, tres propostas | 294,7 s |
| Biblioteca e kit | 0,1 s |
| Par de avaliacao 2C | 913,5 s |
| Total | 2555,0 s (42 min 35 s) |

| Percurso | Estado | Imagem | Verificacao visual | Latencia do produtor | Voltas | Invalidas | Custo |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| Simples | succeeded | sim | sem Revisor | 223205 ms | 0 | 0 | 0 USD |
| Especializado | succeeded | sim | sim | 681082 ms | 2 | 0 | 0 USD |

- Saidas invalidas: nenhuma.
- Execucoes: Diretor 10, Copywriter 2, Designer 3, Revisor 3, Simples 1; todas `succeeded`.
- `cost_entry`: 21, todos a precos locais zero.
- Posts e revisoes semanais: 0/0 (consulta de isolamento durante a avaliacao); lote final vazio.
- `/api/ps` final: `marketing-qwen2.5vl-7b:8k`, contexto 8192.

## Alcance da evidencia

Exercita os dois produtores com modelos reais, fila, contexto fixado, composicao, verificacao
visual e persistencia de resultados numa base descartavel. O fumo foi iniciado durante a
implementacao da Tarefa 10; as correcoes posteriores de contabilidade/tempos foram verificadas
por regressao RED→GREEN e suite 2961/2961, Chromium e build. Nao e um ensaio de faturacao paga,
nao mede qualidade humana, nao decide papeis e nao substitui a corrida real de 16 pacotes.

Para fechar M5 continuam pendentes a preparacao da marca/estrategia/kit reais, revisao dos oito
briefs, julgamento cego pelo responsavel, exportacao do relatorio e decisoes por papel.
