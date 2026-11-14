# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-14T11:40:00+00:00`
- Autor: `agent`
- Assunto: Medicao de desempenho da oficina 3D em dispositivos reais (M5, Plano 2D, Tarefa 11)
- Status: `pendente` (a medicao em aparelhos reais e do responsavel; a tabela abaixo esta por preencher)
- Retestar em: sempre que a cena 3D, o Three.js vendored ou a qualidade adaptativa mudarem

## Achado

A oficina tem agora um quadro de medicao ativado por `?medir=1`, para registar o desempenho
real sem ferramentas de programador. Nao ha limites-alvo: a decisao sobre o que e aceitavel e
do responsavel, depois de ver os numeros. O agente apenas implementou o instrumento e a
qualidade adaptativa; nenhum valor abaixo foi medido ainda.

## Contexto

- A cena 3D (Three.js 0.186.1, `static/oficina/`) corre por WebGL2. Em dispositivo lento o
  `ControloQualidade` baixa a qualidade (sem GTAO, sem sombras, pixelRatio 1) e, se continuar
  lento, mostra o aviso "A oficina está lenta neste dispositivo" com o botao "Mudar para 2D".
- Os testes automaticos usam WebGL por software (SwiftShader) e nao representam GPU real.

## Metodo

1. Abrir `/oficina?medir=1` com a sessao normal (no PC: pelo endereco Tailscale/loopback do
   painel; no telemovel: pelo endereco Tailscale do painel, no browser que o responsavel usa).
2. Garantir a vista 3D (botao "Vista 3D" com `aria-pressed` ativo). Se estiver em 2D, carregar
   no botao; a escolha fica em `localStorage["oficina.vista"]`.
3. Deixar o separador visivel e em primeiro plano durante 1 minuto sem interagir. O quadro
   atualiza-se a cada segundo.
4. Ler e copiar para a tabela: FPS (media dos ultimos 5 s), memoria, tempo ate ao primeiro
   frame e qualidade atual. Repetir depois de selecionar um agente na coluna e depois de
   arrastar a vista, se for relevante.
5. Registar o dispositivo, o sistema, o browser e a versao. Se aparecer o aviso de lentidao,
   anotar quanto tempo depois do carregamento.
6. "Memória: indisponível" e normal fora de browsers Chromium (`performance.memory`).

## Comandos

```bash
# Painel local para o PC (exemplo; usar o arranque habitual do projeto):
#   abrir http://127.0.0.1:<porta>/oficina?medir=1
# Telemovel: abrir https://<nome-tailscale-do-painel>/oficina?medir=1
```

## Evidencia

A preencher pelo responsavel (uma linha por dispositivo e browser):

| Data | Dispositivo | Sistema | Browser e versao | Vista | FPS (media 5 s) | Memoria | Primeiro frame | Qualidade apos 1 min | Aviso de lentidao | Notas |
| ---- | ----------- | ------- | ---------------- | ----- | --------------- | ------- | -------------- | -------------------- | ----------------- | ----- |
|      | PC          |         |                  | 3D    |                 |         |                |                      |                   |       |
|      | Telemovel   |         |                  | 3D    |                 |         |                |                      |                   |       |

## Codigo relevante

```text
src/marketing_agent/panel/static/oficina/medidas.js     quadro ?medir=1 (textContent)
src/marketing_agent/panel/static/oficina/qualidade.js   ControloQualidade (mediana em 90 frames)
src/marketing_agent/panel/static/oficina/vista.js       regra de arranque e escolha guardada
tests/browser/test_oficina_desempenho.py                testes automaticos (Chromium, WebGL por software)
```

## Interpretacao

- O tempo ate ao primeiro frame e contado desde o inicio da navegacao (`performance.now()`),
  inclui descarregar e compilar a cena; nao e so o tempo de render.
- O FPS e a media de frames desenhados nos ultimos 5 s; em separador oculto cai para 0 porque o
  render pausa de proposito.
- A memoria e o heap JavaScript (`performance.memory.usedJSHeapSize`), nao a memoria de GPU.

## Consequencia operacional

Quando a tabela estiver preenchida, o responsavel decide se quer limites-alvo e, se quiser,
regista-os aqui e no plano. Ate la, o criterio de aceitacao automatico limita-se a: a
qualidade baixa em frames lentos sustentados e o utilizador pode sempre passar a 2D.

## Riscos e limites

- Um telemovel em poupanca de energia ou com o separador em segundo plano da valores baixos que
  nao refletem o uso normal.
- Software WebGL dos testes automaticos nao e comparavel a um GPU real.

## Pendencias

- Preencher a tabela no PC e no telemovel (M5, responsavel).
