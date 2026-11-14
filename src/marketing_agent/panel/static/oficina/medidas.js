const JANELA_FPS_MS = 5000;
const PASSO_MS = 1000;

function memoriaTexto() {
  const usada = performance.memory?.usedJSHeapSize;
  return Number.isFinite(usada) ? (usada / 1048576).toFixed(1) + ' MB (heap JS)' : 'indisponível';
}

/** Media de frames por segundo nos ultimos 5 s, a partir de contadores acumulados. */
export function mediaFps(amostras) {
  const ultima = amostras.at(-1);
  const primeira = amostras.find(a => ultima.t - a.t <= JANELA_FPS_MS);
  const decorrido = ultima.t - primeira.t;
  return decorrido > 0 ? (ultima.quadros - primeira.quadros) * 1000 / decorrido : null;
}

/**
 * Quadro de medicao em texto, so criado com `?medir=1`, para ler no telemovel sem ferramentas
 * de programador. `ler()` devolve {vista, estatisticas} no momento; tudo e escrito com textContent.
 */
export function iniciarMedidas(ancora, ler) {
  const quadro = document.createElement('div');
  quadro.id = 'oficina-medidas';
  const linhas = ['fps', 'memoria', 'primeiro', 'qualidade'].map(chave => {
    const linha = document.createElement('p');
    linha.dataset.medida = chave;
    quadro.append(linha);
    return linha;
  });
  ancora.after(quadro);
  let amostras = [];
  const atualizar = () => {
    const {vista, estatisticas} = ler();
    const [fps, memoria, primeiro, qualidade] = linhas;
    if (estatisticas) {
      amostras.push({t: performance.now(), quadros: estatisticas.quadros});
      amostras = amostras.filter(a => performance.now() - a.t <= JANELA_FPS_MS + PASSO_MS);
      const valor = amostras.length > 1 ? mediaFps(amostras) : null;
      fps.textContent = 'FPS (média 5 s): ' + (valor === null ? 'a medir…' : valor.toFixed(1));
      primeiro.textContent = 'Primeiro frame: ' + (estatisticas.primeiroQuadroMs === null
        ? 'à espera' : Math.round(estatisticas.primeiroQuadroMs) + ' ms desde a abertura');
      qualidade.textContent = 'Qualidade: ' + estatisticas.qualidade;
    } else {
      amostras = [];
      fps.textContent = 'FPS (média 5 s): indisponível na vista 2D';
      primeiro.textContent = 'Primeiro frame: indisponível na vista 2D';
      qualidade.textContent = 'Qualidade: indisponível na vista 2D';
    }
    memoria.textContent = 'Memória: ' + memoriaTexto();
    quadro.dataset.vista = vista;
  };
  atualizar();
  const timer = setInterval(atualizar, PASSO_MS);
  window.addEventListener('pagehide', () => clearInterval(timer));
}
