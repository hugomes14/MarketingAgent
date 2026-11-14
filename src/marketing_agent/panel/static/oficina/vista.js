const CHAVE = 'oficina.vista';

/** WebGL2 disponivel? O contexto de teste e libertado logo para nao gastar um dos contextos do browser. */
export function temWebGL() {
  try {
    const gl = document.createElement('canvas').getContext('webgl2');
    if (!gl) return false;
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return true;
  } catch { return false; }
}

/** Escolha guardada pelo utilizador: '3d', '2d' ou null (armazenamento bloqueado ou valor estranho). */
export function lerEscolha() {
  try {
    const valor = localStorage.getItem(CHAVE);
    return valor === '3d' || valor === '2d' ? valor : null;
  } catch { return null; }
}

/** Guarda a escolha; sem armazenamento a vista muda na mesma, so nao persiste. */
export function guardarEscolha(vista) {
  try { localStorage.setItem(CHAVE, vista); } catch { /* quota ou modo privado */ }
}

/** Movimento reduzido pedido pelo sistema operativo, lido no momento de montar a cena. */
export function prefereMovimentoReduzido() {
  return matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * Regra de arranque: sem WebGL, 2D sempre; senao a escolha guardada; sem escolha, 2D com
 * movimento reduzido e 3D no resto dos casos.
 */
export function vistaInicial({webgl, escolha, movimentoReduzido}) {
  if (!webgl) return '2d';
  return escolha ?? (movimentoReduzido ? '2d' : '3d');
}
