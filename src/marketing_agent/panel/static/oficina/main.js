import {EstadoCliente} from './estado.js';
import {Coluna} from './coluna.js';
import {chaveAparencia, guardar, restaurar, capturarCara} from './capturas.js';

const raiz = document.querySelector('#oficina');
export let oficina = null;
let ultimoEstado = null;
export const coluna = raiz ? new Coluna(raiz, {
  aoSelecionar: (papel, {origem}) => {if (origem !== 'cena') oficina?.selecionar(papel);},
  aoSemLigacao: () => oficina?.marcarSemLigacao()
}) : null;
const personagens = raiz ? [...raiz.querySelectorAll('.avatar')].map(a => ({
  id: a.dataset.papel, name: a.querySelector('.avatar-nome').textContent,
  look: {hair: a.dataset.cabelo, torso: a.dataset.roupa, arms: a.dataset.roupa, style: a.dataset.estilo}
})) : [];
const caras = new Map();
async function restaurarCaras() {
  for (const pessoa of personagens) {
    const chave = await chaveAparencia(pessoa.look);
    const canvas = raiz.querySelector('.avatar[data-papel="' + pessoa.id + '"] .avatar-cara');
    caras.set(pessoa.id, {chave, canvas, restaurada: restaurar(pessoa.id, chave, canvas)});
  }
}
const carasRestauradas = restaurarCaras();
let ultimoSucesso = new Date(), avisoTimer;
function vigiarLigacao() {
  clearTimeout(avisoTimer);
  avisoTimer = setTimeout(() => coluna.marcarSemLigacao(ultimoSucesso), Math.max(0, 30000 - (Date.now() - ultimoSucesso.getTime())));
}
export const cliente = raiz ? new EstadoCliente({
  url: '/oficina/estado',
  aoMudar(estado, transicoes) {
    ultimoEstado = estado;
    oficina?.aplicarEstado(estado, transicoes);
    clearTimeout(avisoTimer);
    ultimoSucesso = new Date();
    raiz.dataset.ligacao = 'ok'; raiz.dataset.falhas = '0';
    coluna.atualizar(estado);
    vigiarLigacao();
  },
  aoFalhar(falhas) {
    raiz.dataset.falhas = falhas;
    if (falhas >= 3 || Date.now() - ultimoSucesso.getTime() >= 30000) coluna.marcarSemLigacao(ultimoSucesso);
    vigiarLigacao();
  },
  aoTerminarSessao() {
    clearTimeout(avisoTimer);
    coluna.marcarSemLigacao(ultimoSucesso);
    document.querySelector('#estado').textContent = 'A sessão terminou. Recarregue a página para voltar a entrar.';
  }
}) : null;
if (cliente) {
  raiz.dataset.ligacao = 'a_ligar';
  cliente.iniciar();
  vigiarLigacao();
  window.addEventListener('pagehide', () => {cliente.parar(); clearTimeout(avisoTimer);});
  window.addEventListener('pageshow', event => {if (event.persisted) cliente.iniciar();});
}

/** A vista 2D permanece completa se WebGL ou o carregamento da cena falhar. */
async function iniciarCena() {
  if (!raiz || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const teste = document.createElement('canvas');
  const gl = teste.getContext('webgl2');
  if (!gl) return;
  gl.getExtension('WEBGL_lose_context')?.loseContext();
  const contentor = raiz.querySelector('#oficina-cena');
  try {
    const {montar} = await import('./oficina3d.js');
    contentor.hidden = false;
    oficina = await montar(contentor, {personagens,
      aoSelecionar: papel => coluna.selecionar(papel, {origem: 'cena'}), reduzirMovimento: false});
    if (ultimoEstado) oficina.aplicarEstado(ultimoEstado, []);
    if (coluna.semLigacao) oficina.marcarSemLigacao();
    oficina.selecionar(coluna.papel, {focar: false});
    await carasRestauradas;
    for (const pessoa of personagens) {
      const cara = caras.get(pessoa.id);
      if (!cara.restaurada) {
        const pixels = await capturarCara(oficina, pessoa.id, cara.canvas);
        if (pixels) guardar(pessoa.id, cara.chave, pixels);
      }
    }
    raiz.dataset.carasProntas = '1';
  } catch (erro) {
    oficina?.desmontar(); oficina = null; contentor.hidden = true;
    document.querySelector('#estado').textContent = 'Não foi possível abrir a vista 3D. A vista 2D continua disponível.';
  }
}
iniciarCena();
window.addEventListener('pagehide', () => oficina?.desmontar());
