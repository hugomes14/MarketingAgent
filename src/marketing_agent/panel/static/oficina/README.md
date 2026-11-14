# Oficina: cena da referencia e modulos proprios

Origem dos quatro ficheiros portados: ederland/Agentes_IA_3D no commit
073cbc2fdd9c52428e67935e35e0e616c892a258, app/src/office/. Licenca MIT,
Copyright (c) 2026 Ederland Studio, preservada em LICENSE-ederland.txt e nos cabecalhos.
Nao se copiam React, Firebase, paginas, regras ou ativos binarios. Texturas procedimentais.

## Alteracoes por ficheiro

- scene.js: imports relativos para Three local; etiquetas construidas com DOM/textContent;
  CSS das etiquetas local; teclas so no canvas; qualidade injetada alta/baixa, pixelRatio<=2,
  sombras2048 ou desligadas, composer/GTAO apenas em alta. pause/resume cancelam RAF e nao
  acumulam tempo oculto. Temporizacao por performance/RAF; contador real de frames. Dispose
  liberta passes, render targets, environment, luzes/sombras, materiais/texturas/geometrias,
  controls, etiquetas, listeners e renderer/contexto. A DFG_LUT partilhada da versao0.186.1
  e recolhida nos uniforms antes do descarte dos materiais (o renderer nao a liberta). Devolve memoria final e e idempotente.
  Nenhuma atividade escolhida pela cena: so setCharacters. Opcao reducedMotion impede rotas.
- minifig.js: cabecalho MIT/origem e import relativo; geometria/personagem preservada.
- navgrid.js: apenas cabecalho MIT/origem; algoritmo de navegacao preservado.
- textures.js: cabecalho MIT/origem e import relativo; texturas procedimentais preservadas.

## Modulos proprios

oficina3d.js encapsula montagem, pausa oculta, desmontagem e estatisticas. main.js carrega-o
por import dinamico apenas com WebGL2 e sem movimento reduzido. Falha conserva a vista2D.
Nesta tarefa9 a cena e montada sem escolher trabalho/personagens; ligacao ao estado e
configuracao dos quatro papeis pertencem a tarefa10. Nao se inventam atividades.
Apenas estado.js faz fetch; os restantes modulos nao contactam servicos.
CSSOM de posicionamento dos renderers Three usa propriedades de estilo, como o upstream;
HTML servido nao tem style/on*, scripts inline ou importmap. A CSP mantem-se integralmente.
