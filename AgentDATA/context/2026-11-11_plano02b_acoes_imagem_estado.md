# Contexto de execucao

- Data: `2026-11-11` (Europe/Lisbon)
- Autor: `Codex`
- Escopo: tarefa 15 e partes automatizaveis da tarefa 16 do Plano 02B.

## Implementacao

- Edicao de texto preserva ativo final, layout, componentes, IA e custos. O hash inclui o
  SHA-256 da imagem; uma edicao sem alteracoes continua recusada. O parecer e limpo.
- Trocar foto recompoe com o kit ativo, sem LLM, e invalida a aprovacao anterior. O titulo
  original passa a ser guardado no componente de texto, sem migracao de colunas. Pacotes
  anteriores sem titulo guardado recusam a troca e precisam de nova geracao.
- Upload manual valida formato, tamanho, autorizacao e declaracao de IA; normaliza, recorta
  em 4:5 e guarda bytes imutaveis. A declaracao e os metadados ficam no payload do job.
- `review_post`: uma passagem do Revisor, sem Designer/Copywriter. Falha do parecer nao impede
  entrega humana. Transacao final verifica estado, revisao e lease antes de escrever.
- A fila inclui ambos os tipos na deteccao e cancelamento de jobs ativos. Tipo desconhecido
  falha sem retry. As novas rotas usam autenticacao, Host, CSRF e multipart limitado; o upload
  de pacote exige o campo `ficheiro`. O painel mostra pareceres de revisoes `upload`.
- Teste HTTP ponta a ponta: duas fotos, kit, plano de tres slots, worker, troca, upload,
  aprovacao, custos simulados por imagem (0,04 USD), fundo corrigido, JPEG sem EXIF e auditoria.
- Configuracao local continua com fundos simulados e sem chave paga; IMAGE_QUALITY explicito.
  README documenta backups de MEDIA_DIR e os tempos reais (lease 6360 s, reservas 480 s).

## Validacao

- Ruff check/format e mypy src limpos (128 ficheiros fonte).
- Suite global: 2470 passaram, 2 live excluidos, em 86,65 s.
- Suite final de imagem/painel: 14 passaram em 1,44 s, incluindo preco simulado e auth/CSRF.
- Suite final de acoes e ponta a ponta: 7 passaram em 2,81 s, incluindo obsolescencia
  durante a revisao manual e tipo de job desconhecido sem retry.
- Base isolada: porta 5543, marketing_agent_test. Sem mudar servicos de desenvolvimento.
- Fumo: `scripts/smoke_ollama.py --server-url ...:5543/postgres --posts 3`, codigo 3:
  Ollama inacessivel em http://127.0.0.1:11434. Nao foi arrancado nem reiniciado.
- Browser: Playwright e Chromium nao instalados; nao ha prova visual nem marco manual fechado.

## Pendencias da tarefa 16

A consulta oficial de precos revelou uma incompatibilidade potencial com a premissa de tarifa
fixa por imagem. GPT Image 1.5 esta marcado Deprecated; os modelos atuais sao tarifados por
tokens de imagem/texto. Nao basta copiar a tabela antiga para uma configuracao nova.

Fontes consultadas em 2026-11-11:
- https://developers.openai.com/api/docs/models/gpt-image-1.5
- https://developers.openai.com/api/docs/pricing

Confirmar um modelo nao obsoleto e a compatibilidade da faturacao/reserva/acerto antes de
adicionar o preco pago e o exemplo concreto de rota. Repetir o fumo quando o Ollama estiver
acessivel e fazer a semana no browser com o responsavel (M3 a M7 conforme o plano). O Plano
02B nao esta declarado fechado. O proximo trabalho e este fecho operacional, antes do 02C.

## Atualizacao: preparar a tarefa 16 e validar depois

Por instrucao posterior do responsavel, a validacao fica adiada. As descargas dos modelos
foram autorizadas e estao em curso no Ollama do Windows. O servico respondeu em
172.18.48.1:11434 (versao 0.40.16); localhost do WSL nao respondia. O README e o exemplo
local agora explicam a diferenca entre OPENAI_BASE_URL (/v1) e --ollama-url (sem /v1).
Checklist: AgentDATA/docs/2026-11-11_checklist_fecho_plano02b.md.
Nao foram executados testes nem fumo nesta atualizacao. A geracao paga e os marcos manuais
continuam explicitamente pendentes; preparar a validacao nao significa fechar o plano.
