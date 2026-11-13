# Fecho do Plano 02B — validacao adiada

- Data: 2026-11-11 (Europe/Lisbon).
- Autor: Codex.
- Estado: preparacao local concluida; validacao posterior por instrucao do responsavel.
- Nao executar os testes abaixo ate retomar a validacao. Os resultados anteriores continuam
  historicos e nao certificam alteracoes posteriores.

## Preparacao implementada

- Pipeline, biblioteca, kit, imagens no lote e acoes de revisao implementados.
- Teste HTTP ponta a ponta existente: `tests/e2e/test_weekly_image_flow.py`.
- Configuracao local: Diretor/Copywriter/Designer em qwen2.5:14b e Revisor em qwen2.5vl:7b.
- Fundos simulados por omissao; chave paga opcional, separada da chave do texto.
- MEDIA_DIR incluido nas instrucoes de backup; dados e imagens devem ser restaurados juntos.
- Exemplo Windows/WSL documentado sem fixar o IP do anfitriao no codigo.

## Verificar depois das descargas

- [ ] Confirmar ambos os modelos em `http://<IP-do-host-WSL>:11434/api/tags` (o IP varia; ver `ip route show default`).
- [ ] Confirmar o IP atual do Windows; `127.0.0.1:11434` nao respondia neste WSL.
- [ ] Ajustar OPENAI_BASE_URL no .env, preservando as restantes configuracoes e segredos.
- [ ] Confirmar contexto do servidor >= 8192 tokens antes de gerar pacotes longos.
- [ ] Executar ruff check/format, mypy e suite no Postgres exclusivo de testes.
- [ ] Executar o teste de fumo com os argumentos Windows/WSL do README; guardar tempos,
      imagem_verificada, voltas e saidas invalidas por papel, incluindo qualquer falha.
- [ ] Fazer uma semana no browser: biblioteca, kit, plano, worker, imagens, proveniencia,
      troca de foto, upload manual, edicao de texto e aprovacao da revisao mais recente.
- [ ] Confirmar GPS/EXIF removidos e rejeicao de ficheiro disfarçado de imagem.
- [ ] Confirmar selo de IA visivel, custos e parecer visual; uma edicao exige nova aprovacao.
- [ ] Registar resultados nos marcos M1 a M7 do plano; fechar apenas os comprovados.

## Pendencia independente: geracao paga

O contrato atual reserva/acerta custo fixo por imagem, tamanho e qualidade. A documentacao
oficial consultada apresenta modelos recentes com faturacao por tokens; nao se deve tratar
uma estimativa por imagem como tarifa total confirmada. Nenhuma rota paga foi ativada.

- [ ] Confirmar modelo nao obsoleto, tamanho e qualidade suportados e faturacao aplicavel.
- [ ] Se necessario, adaptar reserva/acerto e testes ao usage real antes de configurar precos.
- [ ] Registar fonte, data e precos em config/model_prices.yaml; acrescentar exemplo de rota.
- [ ] So declarar o fecho integral da tarefa 16 depois de resolver esta pendencia e validar.

Fontes: https://developers.openai.com/api/docs/pricing e
https://developers.openai.com/api/docs/models/gpt-image-2 (consulta em 2026-11-11).
