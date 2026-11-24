# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-19T20:30:00+00:00`
- Autor: `agent`
- Assunto: Porque o Revisor pediu `rever` em todas as voltas do fumo real do 2B
- Status: `ativo` (causa provavel; confirmacao no proximo fumo real)
- Retestar em: proximo `scripts/smoke_ollama.py` com modelos reais

## Achado

O prompt do Copywriter (versao 1) dizia "so afirmas factos que estejam em
`<base_conhecimento>` ou no perfil" e aceitava `kb_ref: null`. A rubrica do Revisor assinala
`alegacao_sem_apoio` para qualquer afirmacao factual sem item da base que a apoie, e o codigo
(`content.rounds._effective`) trata `aprovar` com qualquer violacao como `rever`. A spec do 2A
diz que um topico do perfil so passa a ter apoio quando e promovido a base.

Com a base do fumo (um so item, o preco do pao), o Copywriter escreve factos do perfil, o
Revisor marca-os e o Copywriter, que acha que o perfil chega, repete-os na volta seguinte. Isto
explica `rever` em 6 de 6 voltas sem exigir que o modelo de 7B seja demasiado rigoroso.

## Evidencia

- So analise estatica: o fumo de 2026-11-13 nao guardou as violacoes nem as notas do parecer
  (o relatorio so mostrava o veredicto) e esta sessao nao tem acesso a um Ollama.
- `src/marketing_agent/agents/copywriter/prompt.md` (antes desta mudanca), rubrica 1 de
  `agents/revisor/prompt.md`, `content/rounds.py::_effective`, spec 2A (secao da base: "promover
  topicos do perfil").

## Mudancas feitas

- Copywriter `version: 2`: os factos so podem vir da base; o perfil serve para voz, tom e contexto.
- `scripts/smoke_ollama.py`: o relatorio mostra, por publicacao, as violacoes e as notas do
  parecer mais recente (notas numa linha, cortadas a 300 caracteres).

## Outras causas possiveis (por excluir no proximo fumo)

- Foto e logotipo sinteticos (desenhos simples): `imagem_inadequada` ou `marca_incoerente`
  podem ser parecer legitimo do modelo de visao. Repetir com `PHOTO=<foto real>` se aparecerem.
- Rigor do `qwen2.5vl:7b`: violacoes `outro` com notas vagas indicam excesso de zelo do modelo.

## Pendencias

- Correr de novo o fumo real e ler as linhas `violacoes:` e `notas do revisor:` do relatorio.
