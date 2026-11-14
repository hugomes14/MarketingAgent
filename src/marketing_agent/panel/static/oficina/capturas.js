const TAMANHO = 96;

/** Hash da aparencia; nomes e estado nao invalidam a captura da cara. */
export async function chaveAparencia(look) {
  const bytes = new TextEncoder().encode(JSON.stringify({versao: 1, look}));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
}

/** Guarda apenas bytes RGBA, sem URLs ou imagens externas. Falha de quota e recuperavel. */
export function guardar(papel, chave, imageData) {
  try {
    let binario = '';
    for (let i = 0; i < imageData.data.length; i += 4096) {
      binario += String.fromCharCode(...imageData.data.subarray(i, i + 4096));
    }
    sessionStorage.setItem('oficina.cara.' + papel + '.' + chave, btoa(binario));
    return true;
  } catch { return false; }
}

/** Restaura uma captura valida; armazenamento indisponivel ou corrompido usa a alternativa. */
export function restaurar(papel, chave, canvas) {
  try {
    const raw = sessionStorage.getItem('oficina.cara.' + papel + '.' + chave);
    if (raw === null || raw.length > TAMANHO * TAMANHO * 6) return false;
    const binario = atob(raw);
    if (binario.length !== TAMANHO * TAMANHO * 4) return false;
    const bytes = Uint8ClampedArray.from(binario, char => char.charCodeAt(0));
    canvas.getContext('2d').putImageData(new ImageData(bytes, TAMANHO, TAMANHO), 0, 0);
    canvas.dataset.origemCara = 'cache';
    return true;
  } catch { return false; }
}

/** Render isolado da cabeca; Three so e descarregado quando existe uma cena3D. */
export async function capturarCara(oficina, papel, canvasDestino) {
  const THREE = await import('../vendor/three/build/three.module.js');
  if (oficina.memoriaFinal) return null;
  const original = oficina.cena.chars.get(papel)?.root.userData.parts.head;
  if (!original) return null;
  const renderer = oficina.cena.renderer;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#e0ecff');
  scene.environment = oficina.cena.scene.environment;
  const head = original.clone(true);
  head.position.set(0, 0, 0); head.rotation.set(0, 0, 0);
  scene.add(head, new THREE.HemisphereLight('#ffffff', '#c8bfae', 1));
  const light = new THREE.DirectionalLight('#fff4e5', 3);
  light.position.set(0.5, 1, 1); scene.add(light);
  const camera = new THREE.OrthographicCamera(-0.18, 0.18, 0.18, -0.18, 0.01, 3);
  camera.position.set(0, -0.015, 0.8); camera.lookAt(0, -0.015, 0);
  const target = new THREE.WebGLRenderTarget(TAMANHO, TAMANHO);
  target.texture.colorSpace = THREE.SRGBColorSpace;
  const previous = renderer.getRenderTarget();
  const viewport = renderer.getViewport(new THREE.Vector4());
  const scissor = renderer.getScissor(new THREE.Vector4());
  const scissorTest = renderer.getScissorTest();
  try {
    renderer.setRenderTarget(target); renderer.setScissorTest(false);
    renderer.render(scene, camera);
    const pixels = new Uint8Array(TAMANHO * TAMANHO * 4);
    renderer.readRenderTargetPixels(target, 0, 0, TAMANHO, TAMANHO, pixels);
    const bytes = new Uint8ClampedArray(pixels.length);
    // WebGL tem origem em baixo; ImageData e o canvas2D tem origem em cima.
    for (let row = 0; row < TAMANHO; row++) {
      bytes.set(pixels.subarray((TAMANHO - 1 - row) * TAMANHO * 4, (TAMANHO - row) * TAMANHO * 4), row * TAMANHO * 4);
    }
    const result = new ImageData(bytes, TAMANHO, TAMANHO);
    canvasDestino.getContext('2d').putImageData(result, 0, 0);
    canvasDestino.dataset.origemCara = 'captura';
    return result;
  } finally {
    renderer.setRenderTarget(previous); renderer.setViewport(viewport);
    renderer.setScissor(scissor); renderer.setScissorTest(scissorTest);
    target.dispose(); light.dispose(); scene.clear();
    // A cabeca clonada partilha materiais/geometrias com a cena; nao os descartar aqui.
  }
}
